"""The multi-monitor primary guard: a non-top-left primary would wedge FreeRDP."""

from avd_feed_connect import display


# (name, is_primary, x, y, w, h)
def test_single_monitor_is_fine():
    assert display.primary_breaks_multimon([("eDP-1", True, 0, 0, 2560, 1600)]) is None


def test_empty_or_none_is_fine():
    assert display.primary_breaks_multimon([]) is None
    assert display.primary_breaks_multimon(None) is None


def test_primary_top_left_is_fine():
    # laptop (primary) left, two externals to the right — the working layout
    mons = [("eDP-1", True, 0, 0, 2560, 1600),
            ("DP-2", False, 2560, 0, 1920, 1080),
            ("DP-1", False, 4480, 0, 1920, 1080)]
    assert display.primary_breaks_multimon(mons) is None


def test_primary_in_the_middle_breaks():
    # primary is the middle monitor at x=2560 — the laptop at x=0 goes negative
    mons = [("eDP-1", False, 0, 0, 2560, 1600),
            ("DP-2", True, 2560, 0, 1920, 1080),
            ("DP-1", False, 4480, 0, 1920, 1080)]
    assert display.primary_breaks_multimon(mons) == "DP-2"


def test_primary_above_breaks_on_y():
    # a monitor sits above the primary -> negative Y origin
    mons = [("top", False, 0, -1080, 1920, 1080),
            ("main", True, 0, 0, 1920, 1080)]
    assert display.primary_breaks_multimon(mons) == "main"


def test_no_primary_flag_is_fine():
    # if nothing is marked primary we can't tell -> don't block the connect
    mons = [("A", False, 0, 0, 1920, 1080), ("B", False, 1920, 0, 1920, 1080)]
    assert display.primary_breaks_multimon(mons) is None
