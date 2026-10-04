"""Launch a downloaded .rdp with the bundled sdl-freerdp.

The launch used by the command line is a simple, blocking full-screen session
(using the same AAD/NLA selection as the GUI). The GUI uses sdl-freerdp over
a PTY, so it can resolve the connection-time AAD prompt silently — see
:mod:`avd_feed_connect.gui.connect`; this module is the CLI path.
"""

import json
import os
import shlex
import subprocess
import uuid
from datetime import datetime, timezone

from .. import config
from .security import resolve_security
from .resources import set_rdp_dynamic_resolution, set_rdp_multimon


def session_log_path(output_dir, prefix):
    """Name one connection log without reusing earlier sessions' paths."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return os.path.join(output_dir, f"{prefix}_{stamp}_{uuid.uuid4().hex}.log")


def build_display_args(path, extra, want_multimon):
    """Resolve GUI display flags and conflicting settings in the feed file."""
    options = shlex.split(extra)
    dynamic = False
    for option in options:
        if option in ("/dynamic-resolution", "+dynamic-resolution"):
            dynamic = True
        elif option == "-dynamic-resolution":
            dynamic = False
        elif option in ("/multimon", "+multimon", "/multimon:on", "/multimon:force"):
            want_multimon = True
        elif option in ("-multimon", "/multimon:off"):
            want_multimon = False

    if dynamic:
        set_rdp_dynamic_resolution(path)
        want_multimon = False
        options = [option for option in options
                   if option.lstrip("/+-").partition(":")[0]
                   not in ("smart-sizing", "f", "multimon")]

    set_rdp_multimon(path, want_multimon)
    return ([] if dynamic else ["/f"]) + [
        "/multimon" if want_multimon else "-multimon"] + options


# sdl-freerdp reserves <modifier>+key for its own shortcuts (D disconnects, Enter
# toggles fullscreen, ...), swallowing those keys before the remote sees them.
# The modifier lives in its user config file, not on the command line.
HOTKEY_MODIFIERS = {"on": ["KMOD_RSHIFT"], "off": ["KMOD_NONE"]}

HOTKEY_MODIFIER_NAMES = {
    "KMOD_LCTRL": "Left Ctrl", "KMOD_RCTRL": "Right Ctrl",
    "KMOD_LSHIFT": "Left Shift", "KMOD_RSHIFT": "Right Shift",
    "KMOD_LALT": "Left Alt", "KMOD_RALT": "Right Alt",
    "KMOD_LGUI": "Left Super", "KMOD_RGUI": "Right Super",
}
HOTKEY_DEFAULTS = {
    "SDL_Fullscreen": "SDL_SCANCODE_RETURN", "SDL_Minimize": "SDL_SCANCODE_M",
    "SDL_Resizeable": "SDL_SCANCODE_R", "SDL_Grab": "SDL_SCANCODE_G",
    "SDL_Disconnect": "SDL_SCANCODE_D",
}
HOTKEY_ACTIONS = {
    "SDL_Fullscreen": "Toggle fullscreen", "SDL_Minimize": "Minimize",
    "SDL_Resizeable": "Toggle resizing", "SDL_Grab": "Toggle input grab",
    "SDL_Disconnect": "Disconnect",
}
HOTKEY_KEYS = ["SDL_SCANCODE_" + key for key in (
    list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + [f"F{i}" for i in range(1, 13)]
    + list("0123456789") + ["RETURN", "ESCAPE", "SPACE", "TAB", "BACKSPACE",
                                "INSERT", "DELETE", "HOME", "END", "PAGEUP", "PAGEDOWN"])]


def sdl_config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(config.HOME, ".config")
    return os.path.join(base, "freerdp", "sdl-freerdp.json")


def apply_client_hotkeys(mode, path=None):
    """Apply app-owned shortcuts on connect; preserve unrelated FreeRDP settings.

    'auto' leaves the file alone. 'defaults' removes only managed overrides,
    letting the installed FreeRDP version supply its own defaults.
    """
    bindings = {}
    if isinstance(mode, dict):
        modifiers = mode.get("modifiers")
        bindings = mode.get("bindings")
        if (not isinstance(modifiers, list) or not modifiers
                or any(not isinstance(value, str) or value not in HOTKEY_MODIFIER_NAMES
                       for value in modifiers)
                or not isinstance(bindings, dict) or set(bindings) != set(HOTKEY_DEFAULTS)
                or any(value not in HOTKEY_KEYS for value in bindings.values())
                or len(set(bindings.values())) != len(bindings)):
            return False
    else:
        modifiers = HOTKEY_MODIFIERS.get(mode) if isinstance(mode, str) else None
        if modifiers is None and mode != "defaults":
            return False
    path = path or sdl_config_path()
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        return False
    if mode == "defaults":
        for key in ("SDL_KeyModMask", *HOTKEY_DEFAULTS):
            data.pop(key, None)
    else:
        data["SDL_KeyModMask"] = modifiers
        data.update(bindings)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except OSError:
        return False
    return True


def build_argv(sdl, path, upn):
    """Assemble the sdl-freerdp command line for a full-screen CLI connection."""
    decision = resolve_security(path)
    argv = [sdl, path, f"/gateway:{config.gateway_arg()}", f"/sec:{decision.mode}"]
    if decision.mode == "nla":
        argv.append("/d:")
    if upn:
        argv.append(f"/u:{upn}")
    # Pin the host cert on first use by default; AVD_CERT=ignore turns the check
    # off for host pools whose session-host certs rotate.
    cert_flag = "/cert:ignore" if os.environ.get("AVD_CERT", "").strip().lower() \
        == "ignore" else "/cert:tofu"
    argv += ["/sound:sys:pulse", "/microphone", cert_flag,
             "/f", "/scale-desktop:200", "-multimon", "/log-level:info"]
    return argv


class RdpLauncher:
    """Runs sdl-freerdp for a .rdp file (command-line, blocking)."""

    def __init__(self, sdl=None, sdl_libs=None):
        self.sdl = sdl or config.SDL
        self.sdl_libs = config.SDL_LIBS if sdl_libs is None else sdl_libs

    def launch(self, path, upn=""):
        env = dict(os.environ)
        # Only inject SDL_LIBS when it actually exists (unpackaged builds); inside
        # the Flatpak the loader finds the bundled libs via rpath.
        if self.sdl_libs and os.path.isdir(self.sdl_libs):
            env["LD_LIBRARY_PATH"] = self.sdl_libs + (
                os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        os.makedirs(config.OUT, exist_ok=True)
        log = session_log_path(config.OUT, "feed")
        argv = build_argv(self.sdl, path, upn)
        print("launching:", " ".join(argv))
        print("log:", log)
        with open(log, "x") as lf:
            return subprocess.call(argv, env=env, stdout=lf, stderr=subprocess.STDOUT)
