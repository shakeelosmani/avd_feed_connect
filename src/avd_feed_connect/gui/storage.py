"""On-disk persistence for the GUI.

Holds the saved workspace list (so reopening shows the grid instantly, like the
Windows App), per-resource icon PNGs, and the display/connection preferences,
plus the option lists for the settings dialog. Everything lives next to the
token cache under the app data dir.

These helpers are deliberately free of any session state: ``save_ws_cache`` is
handed the UPN to store, and ``load_ws_cache`` returns the cached UPN for the
caller to adopt — so this module never reaches into the client.
"""

import json
import os
import re
import urllib.request

from .. import config, http

WS_CACHE = os.path.join(os.path.dirname(config.CACHE), "workspaces.json")
ICON_DIR = os.path.join(os.path.dirname(config.CACHE), "icons")
SETTINGS_FILE = os.path.join(os.path.dirname(config.CACHE), "settings.json")

# Option lists for the per-resource settings dialog (label shown, value stored).
SCALE_LABELS = ["Automatic (match display)", "100%", "125%", "150%",
                "175%", "200%", "250%", "300%"]
SCALE_VALUES = ["auto", "100", "125", "150", "175", "200", "250", "300"]
MULTIMON_LABELS = ["Automatic (match monitors)", "Single monitor", "All monitors"]
MULTIMON_VALUES = ["auto", "off", "on"]
# Server-certificate handling. Anything but "ignore" pins the host cert on first
# connect (FreeRDP /cert:tofu); "ignore" turns the check off (/cert:ignore) — the
# escape hatch for host pools whose session-host certs rotate. "auto" inherits the
# default, which is itself verify, so the secure mode is the out-of-the-box one.
CERT_LABELS = ["Automatic (verify)", "Verify", "Don't verify (if you can't connect)"]
CERT_VALUES = ["auto", "verify", "ignore"]
SEC_LABELS = ["Automatic (use feed or remembered mode)", "Microsoft Entra ID", "NLA (username and password)"]
SEC_VALUES = ["auto", "aad", "nla"]
# How a RemoteApp (published single app) tile opens. "auto" leaves the feed's
# RemoteApp behaviour (the app in its own RAIL window); "desktop" opens the
# host's full desktop instead so the app can be launched from inside it — the
# fallback for clients whose RAIL rendering doesn't work. Shown only on RemoteApp
# resources (see _open_settings); desktops are unaffected.
REMOTEAPP_LABELS = ["Automatic (app window)", "Full desktop"]
REMOTEAPP_VALUES = ["auto", "desktop"]
# sdl-freerdp's own Right Shift + key shortcuts (D disconnects the session, Enter
# toggles fullscreen). "auto" leaves FreeRDP's config untouched; "off" passes every
# key through to the remote desktop.
HOTKEY_LABELS = ["Automatic (FreeRDP default)", "Enabled (Right Shift + key)",
                 "Disabled (send all keys to the PC)", "Custom…", "FreeRDP defaults"]
HOTKEY_VALUES = ["auto", "on", "off", "custom", "defaults"]

# Workspace placement can inherit per resource; application defaults cannot.
WORKSPACE_LABELS = ["Use Default settings", "Enabled", "Disabled"]
WORKSPACE_VALUES = ["auto", "on", "off"]
WORKSPACE_DEFAULT_LABELS = ["Disabled", "Enabled"]
WORKSPACE_DEFAULT_VALUES = ["off", "on"]

# User-Agent presented by the embedded sign-in webview. "auto" leaves
# WebKitGTK's own UA, which identifies as Linux — some tenants' Conditional
# Access policies ("require a compliant/Windows device" or "require Microsoft
# Edge") then block the sign-in. Presenting as Edge/Chrome on Windows is the
# same workaround people use with a browser UA-switcher extension (issue #28).
# Account-wide, stored only on the "_default" entry under "user_agent"; every
# sign-in webview shares one SSO cookie session, so the identity must be the
# same everywhere (the account sign-in and every connection) or Entra refuses
# the silent token — hence no per-resource override. The stored value is the
# literal UA string (or a preset below); "Custom…" needs no separate key,
# "auto" means the device's own UA.
UA_WINDOWS_EDGE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0")
UA_WINDOWS_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/132.0.0.0 Safari/537.36")
# Dropdown labels and the fixed-preset value for each; index 3 ("Custom…") takes
# a typed string instead, so it has no entry here.
UA_LABELS = ["Automatic", "Microsoft Edge on Windows",
             "Google Chrome on Windows", "Custom…"]
UA_PRESETS = ["auto", UA_WINDOWS_EDGE, UA_WINDOWS_CHROME]


def icon_path(res_id, directory=None):
    return os.path.join(os.path.join(directory, "icons") if directory else ICON_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", res_id) + ".png")


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(d):
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(d, f)
    except OSError:
        pass


def save_ws_cache(resources, upn, directory=None):
    path = os.path.join(directory, "workspaces.json") if directory else WS_CACHE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"upn": upn, "resources": resources}, f)
    except OSError:
        pass


def load_ws_cache(directory=None):
    """Return ``(resources, cached_upn)``. ``cached_upn`` is ``""`` when absent;
    the caller decides whether to adopt it (only when no UPN is set yet)."""
    try:
        with open(os.path.join(directory, "workspaces.json") if directory else WS_CACHE) as f:
            d = json.load(f)
        return d.get("resources") or [], d.get("upn") or ""
    except (OSError, ValueError):
        return [], ""


def bearer_bytes(url, token):
    """Binary GET with the approved UA headers (for icons; http.get decodes text)."""
    http.check_trusted(url)
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "*/*")
    req.add_header("User-Agent", config.MS_USER_AGENT)
    req.add_header("X-MS-User-Agent", config.MS_USER_AGENT)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()
