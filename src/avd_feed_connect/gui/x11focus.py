"""Bring a running sdl-freerdp window to the front.

The launcher doesn't own FreeRDP's window, and FreeRDP always runs on X11 /
XWayland (see the GUI's launch env), so this asks the window manager over EWMH:
find the top-level whose _NET_WM_PID is the FreeRDP process and send a
_NET_ACTIVE_WINDOW request (source = pager, so focus-stealing prevention lets it
through). Plain ctypes over libX11 — no extra Python dependency. Compositors
decide whether to honor it (Hyprland needs misc:focus_on_activate).
"""

import ctypes
import ctypes.util

_CLIENT_MESSAGE = 33
_XA_CARDINAL = 6
_XA_WINDOW = 33
_SUBSTRUCTURE_MASK = (1 << 19) | (1 << 20)   # SubstructureNotify | SubstructureRedirect
_SOURCE_PAGER = 2


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


_lib = None


def _xlib():
    global _lib
    if _lib is None:
        lib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long,
            ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_void_p)]
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                   ctypes.c_long, ctypes.POINTER(_XEvent)]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        _lib = lib
    return _lib


def _longs(lib, dpy, win, prop, ptype):
    """Read a format-32 property (returned by Xlib as C longs)."""
    atype, afmt = ctypes.c_ulong(), ctypes.c_int()
    n, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
    if lib.XGetWindowProperty(dpy, win, prop, 0, 4096, 0, ptype, ctypes.byref(atype),
                              ctypes.byref(afmt), ctypes.byref(n), ctypes.byref(after),
                              ctypes.byref(data)) != 0 or not data.value:
        return []
    try:
        if afmt.value != 32:
            return []
        return list(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[:n.value])
    finally:
        lib.XFree(data)


def focus_pid(pid):
    """Ask the window manager to activate ``pid``'s window. Returns False if
    there's no X display or no window for that process."""
    try:
        lib = _xlib()
    except OSError:
        return False
    dpy = lib.XOpenDisplay(None)
    if not dpy:
        return False
    try:
        root = lib.XDefaultRootWindow(dpy)
        atom = lambda name: lib.XInternAtom(dpy, name, 0)   # noqa: E731
        wm_pid, active = atom(b"_NET_WM_PID"), atom(b"_NET_ACTIVE_WINDOW")
        target = next((w for w in _longs(lib, dpy, root, atom(b"_NET_CLIENT_LIST"), _XA_WINDOW)
                       if pid in _longs(lib, dpy, w, wm_pid, _XA_CARDINAL)), None)
        if target is None:
            return False
        ev = _XEvent()
        ev.xclient.type = _CLIENT_MESSAGE
        ev.xclient.send_event = 1
        ev.xclient.display = dpy
        ev.xclient.window = target
        ev.xclient.message_type = active
        ev.xclient.format = 32
        ev.xclient.data[0] = _SOURCE_PAGER
        lib.XSendEvent(dpy, root, 0, _SUBSTRUCTURE_MASK, ctypes.byref(ev))
        lib.XFlush(dpy)
        return True
    finally:
        lib.XCloseDisplay(dpy)
