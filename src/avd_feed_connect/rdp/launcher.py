"""Launch a downloaded .rdp with the bundled sdl-freerdp.

The launch used by the command line is a simple, blocking full-screen session
(``/gateway:type:arm /sec:aad``). The GUI drives sdl-freerdp differently — over
a PTY, so it can resolve the connection-time AAD prompt silently — see
:mod:`avd_feed_connect.gui.connect`; this module is the CLI path.
"""

import os
import shlex
import subprocess

from .. import config
from .resources import set_rdp_dynamic_resolution, set_rdp_multimon


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


def build_argv(sdl, path, upn):
    """Assemble the sdl-freerdp command line for a full-screen CLI connection."""
    argv = [sdl, path, "/gateway:type:arm", "/sec:aad"]
    if upn:
        argv.append(f"/u:{upn}")
    argv += ["/sound:sys:pulse", "/microphone", "/cert:tofu",
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
        log = os.path.join(config.OUT, "feed-last-run.log")
        argv = build_argv(self.sdl, path, upn)
        print("launching:", " ".join(argv))
        print("log:", log)
        with open(log, "w") as lf:
            return subprocess.call(argv, env=env, stdout=lf, stderr=subprocess.STDOUT)
