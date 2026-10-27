"""Download a resource's ready-to-use .rdp file from its feed ResourceFile URL."""

import hashlib
import os
import re
import sys

from .. import config, http
from ..i18n import _ as tr


def download_rdp(token, res, output_dir=None):
    """Fetch resource ``res``'s .rdp text and write it under the feed data dir,
    returning the path. ``res`` is a resource dict from the feed."""
    if not res["rdp_url"]:
        sys.exit(tr("Resource {resource} has no ResourceFile URL").format(resource=repr(res["title"])))
    st, _, rdp = http.get(res["rdp_url"], token)
    if st != 200:
        sys.exit(tr("RDP download failed ({status}): {details}").format(status=st, details=rdp[:300]))
    directory = output_dir or config.OUT
    os.makedirs(directory, mode=0o700, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", res["title"])[:80]
    if output_dir:
        safe += "_" + hashlib.sha256(res["id"].encode()).hexdigest()[:16]
    path = os.path.join(directory, safe + ".rdp")
    with open(path, "w") as f:
        f.write(rdp)
    return path


def set_rdp_dynamic_resolution(path):
    """Remove feed settings that conflict with a dynamically resized window."""
    with open(path) as source:
        lines = source.read().splitlines()
    properties = {"smart sizing": "0", "screen mode id": "1"}
    lines = [line for line in lines
             if line.partition(":")[0].strip().lower() not in properties]
    lines.extend(f"{name}:i:{value}" for name, value in properties.items())
    with open(path, "w") as destination:
        destination.write("\n".join(lines) + "\n")


def rdp_is_remoteapp(path):
    """True when the feed .rdp is a RemoteApp (``remoteapplicationmode:i:1``).

    FreeRDP reads this property straight from the connection file and switches
    into RAIL (RemoteApp) mode, so the launcher needs to know before it adds any
    full-desktop flags (``/f``, ``/multimon``, ``/scale-desktop``) that conflict
    with RAIL. Safe to treat any I/O error as "not a RemoteApp"."""
    try:
        with open(path) as f:
            for line in f:
                head, _, val = line.partition(":i:")
                if head.strip().lower() == "remoteapplicationmode":
                    return val.strip() == "1"
    except OSError:
        pass
    return False


def set_rdp_desktop_mode(path):
    """Rewrite a RemoteApp .rdp so FreeRDP connects to the full desktop instead
    of launching the single published app in RAIL. Used by the
    ``AVD_REMOTEAPP=desktop`` fallback: flips ``remoteapplicationmode`` to 0 and
    drops ``alternate shell`` (and the RemoteApp display name/cmdline) so the
    session runs the normal desktop shell rather than the published app.

    ``remoteapplicationprogram`` and ``loadbalanceinfo`` are KEPT — FreeRDP's AVD
    ARM endpoint resolver requires both to broker the connection, and strips them
    out means ``arm_resolve_endpoint: loadBalanceInfo and RemoteApplicationProgram
    needed`` and no connection at all. With the mode at 0, their presence no
    longer triggers RAIL. Safe to no-op on any I/O error."""
    drop = ("remoteapplicationname", "remoteapplicationcmdline", "alternate shell")
    try:
        with open(path) as f:
            lines = f.read().splitlines()
        out, found = [], False
        for ln in lines:
            head = ln.partition(":")[0].strip().lower()
            if head == "remoteapplicationmode":
                out.append("remoteapplicationmode:i:0")
                found = True
            elif head in drop:
                continue
            else:
                out.append(ln)
        if not found:
            out.append("remoteapplicationmode:i:0")
        with open(path, "w") as f:
            f.write("\n".join(out) + "\n")
    except OSError:
        pass


def set_rdp_multimon(path, enabled):
    """Force the .rdp's ``use multimon`` property to match the chosen setting.

    AVD feed .rdp files ship ``use multimon:i:1`` and that connection-file
    property overrides sdl-freerdp's ``-multimon`` / ``/multimon`` CLI flag, so
    the in-app Single/All-monitors choice is only honored by rewriting the file
    itself (the CLI flag alone is ignored). Rewrites in place; safe to no-op on
    any I/O error.
    """
    val = "1" if enabled else "0"
    try:
        with open(path) as f:
            lines = f.read().splitlines()
        out, found = [], False
        for ln in lines:
            if ln.lower().startswith("use multimon:i:"):
                out.append(f"use multimon:i:{val}")
                found = True
            else:
                out.append(ln)
        if not found:
            out.append(f"use multimon:i:{val}")
        with open(path, "w") as f:
            f.write("\n".join(out) + "\n")
    except OSError:
        pass
