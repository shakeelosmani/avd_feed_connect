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


def rdp_property(path, name):
    """Return a .rdp property's value (the part after ``name:<type>:``), or None.

    .rdp lines are ``<name>:<type>:<value>`` (e.g. ``full address:s:host``);
    this returns the ``<value>`` for the first line whose name matches,
    case-insensitively. Any I/O error yields None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                head, sep, rest = line.partition(":")
                if sep and head.strip().lower() == name.lower():
                    return rest.partition(":")[2].strip()
    except OSError:
        return None
    return None


def local_resource(path):
    """Build a GUI resource dict from a user-supplied .rdp/.rdpw file.

    Mirrors a feed resource's shape (``id``/``title``/``tenant``/``type``) so a
    locally opened file flows through the same tile and connect path, for tenants
    whose Conditional Access blocks feed sign-in from Linux but still lets a
    web-client-downloaded .rdp connect (issue #17). ``type`` is ``"RemoteApp"``
    when the file publishes a single app (``remoteapplicationmode:i:1``), else
    ``"Desktop"``. The id is stable for a given absolute path, so re-opening the
    same file updates its tile instead of duplicating it."""
    name = os.path.splitext(os.path.basename(path))[0].strip()
    mode = rdp_property(path, "remoteapplicationmode")
    is_app = mode == "1"
    app_name = (rdp_property(path, "remoteapplicationname")
                or rdp_property(path, "remoteapplicationprogram"))
    title = (app_name if is_app and app_name else name) \
        or rdp_property(path, "full address") or tr("Remote desktop")
    rid = "local-" + hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:16]
    return {"id": rid, "title": title, "tenant": tr("Local file"),
            "type": "RemoteApp" if is_app else "Desktop"}


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
