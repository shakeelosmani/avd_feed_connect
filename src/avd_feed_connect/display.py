"""Read the X11 RandR primary monitor + layout, to guard a multi-monitor connect.

FreeRDP anchors the primary monitor at the virtual-desktop origin (0,0) and shifts
every other monitor by the primary's offset (libfreerdp ``common/settings.c``,
``freerdp_settings_set_monitor_def_array_sorted``). A monitor to the left of / above
the primary then gets a negative origin that falls off the 0-based session surface,
so it never paints and the session wedges at logon. That happens exactly when the
primary is **not** the top-left monitor. Until the FreeRDP side handles that, the
GUI downgrades such a connect to single-monitor and says why, instead of hanging.

GTK4 dropped ``gdk_monitor_is_primary``, so the primary is read from XRandR via
ctypes. Everything here fails open: any error yields "no problem detected", so a
connect is never blocked by a display-probe failure. X11 only (returns ``None``
under a pure Wayland session, where this breakage does not occur the same way).
"""

import ctypes
import os


class _XRRMonitorInfo(ctypes.Structure):
    # Matches XRRMonitorInfo in X11/extensions/Xrandr.h (64-bit layout).
    _fields_ = [
        ("name", ctypes.c_ulong),      # Atom
        ("primary", ctypes.c_int),     # Bool
        ("automatic", ctypes.c_int),   # Bool
        ("noutput", ctypes.c_int),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("mwidth", ctypes.c_int),
        ("mheight", ctypes.c_int),
        ("outputs", ctypes.c_void_p),  # RROutput *
    ]


def monitor_layout():
    """Return ``[(name, is_primary, x, y, w, h), ...]`` from XRandR, or ``None``
    when it cannot be read (no X11, libs missing, probe failed)."""
    if not os.environ.get("DISPLAY"):
        return None
    try:
        x11 = ctypes.CDLL("libX11.so.6")
        xrr = ctypes.CDLL("libXrandr.so.2")
    except OSError:
        return None
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XGetAtomName.restype = ctypes.c_void_p
    x11.XGetAtomName.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XFree.argtypes = [ctypes.c_void_p]
    xrr.XRRGetMonitors.restype = ctypes.POINTER(_XRRMonitorInfo)
    xrr.XRRGetMonitors.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                   ctypes.POINTER(ctypes.c_int)]
    xrr.XRRFreeMonitors.argtypes = [ctypes.POINTER(_XRRMonitorInfo)]

    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return None
    try:
        root = x11.XDefaultRootWindow(dpy)
        count = ctypes.c_int(0)
        arr = xrr.XRRGetMonitors(dpy, root, 1, ctypes.byref(count))
        if not arr or count.value <= 0:
            return None
        out = []
        for i in range(count.value):
            mon = arr[i]
            name_ptr = x11.XGetAtomName(dpy, mon.name)
            name = "?"
            if name_ptr:
                name = ctypes.cast(name_ptr, ctypes.c_char_p).value.decode(
                    errors="replace")
                x11.XFree(name_ptr)
            out.append((name, bool(mon.primary), mon.x, mon.y,
                        mon.width, mon.height))
        xrr.XRRFreeMonitors(arr)
        return out
    except Exception:
        return None
    finally:
        x11.XCloseDisplay(dpy)


def multimon_will_wedge(mons):
    """Given a layout from :func:`monitor_layout`, return ``True`` when a
    multi-monitor session would still wedge even after FreeRDP anchors the layout
    at the top-left monitor — i.e. when **no** monitor sits exactly at the layout's
    top-left corner (a staggered arrangement), so FreeRDP cannot avoid giving some
    monitor a negative origin, which the AVD session host stalls on. ``False`` for
    fewer than two monitors or no layout (callers then just proceed).

    The common cases — a row of monitors, or any layout with a monitor at the
    corner — are handled by the FreeRDP top-left-anchor fix and do **not** wedge,
    so this returns ``False`` for them."""
    if not mons or len(mons) < 2:
        return False
    min_x = min(m[2] for m in mons)
    min_y = min(m[3] for m in mons)
    return not any((m[2] == min_x and m[3] == min_y) for m in mons)


def multimon_will_wedge_now():
    """``True`` if a multi-monitor connect would wedge on the current layout (the
    staggered no-corner-monitor case FreeRDP can't anchor), else ``False`` (also on
    any failure, so a probe error never blocks a connect)."""
    try:
        return multimon_will_wedge(monitor_layout())
    except Exception:
        return False
