"""The multi-monitor guard. FreeRDP anchors the layout at the top-left monitor, so
multi-monitor works for any primary — except a staggered layout where no monitor
sits at the top-left corner, which still wedges; there the app falls back to single."""

from avd_feed_connect import display


# (name, is_primary, x, y, w, h)
def test_single_monitor_is_fine():
    assert display.multimon_will_wedge([("eDP-1", True, 0, 0, 2560, 1600)]) is False


def test_empty_or_none_is_fine():
    assert display.multimon_will_wedge([]) is False
    assert display.multimon_will_wedge(None) is False


def test_row_of_monitors_is_fine_for_any_primary():
    # a row, middle monitor primary — the left-most sits at the corner, so FreeRDP
    # anchors there and it works (this is the case #2 fixed).
    mons = [("eDP-1", False, 0, 0, 2560, 1600),
            ("DP-2", True, 2560, 0, 1920, 1080),
            ("DP-1", False, 4480, 0, 1920, 1080)]
    assert display.multimon_will_wedge(mons) is False


def test_l_shape_with_corner_monitor_is_fine():
    # a monitor sits exactly at (min_x, min_y), so the layout can be anchored
    mons = [("A", True, 0, 0, 1920, 1080),
            ("B", False, 1920, 0, 1920, 1080),
            ("C", False, 1920, 1080, 1920, 1080)]
    assert display.multimon_will_wedge(mons) is False


def test_staggered_without_corner_monitor_wedges():
    # left-most monitor is at y=0 but the other is higher (y=-200): nothing sits at
    # (min_x, min_y) = (0, -200), so FreeRDP can't anchor without a negative origin
    mons = [("left", True, 0, 0, 1920, 1080),
            ("right", False, 1920, -200, 1920, 1080)]
    assert display.multimon_will_wedge(mons) is True
