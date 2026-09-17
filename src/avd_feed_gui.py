#!/usr/bin/env python3
"""Windows-App-style desktop client for Azure Virtual Desktop on Linux.

A GTK4 + WebKitGTK 6.0 front-end over the feed-discovery logic in avdfeed.py:
  * in-app interactive sign-in (embedded WebKit view — the same auth-code+PKCE
    flow the native client uses, so Conditional Access lets it through; it
    catches the …/oauth2/nativeclient?code=… redirect automatically, no paste).
    The modern WebKit engine renders federated org IdP pages (ADFS/Okta/Ping/…)
    that the old WebKit2GTK 4.1 used to freeze on (issue #1).
  * a tiled workspace grid with the real per-resource icons from the feed
  * double-click / Enter a tile to connect via the bundled sdl-freerdp
  * persistent session (cached workspaces + icons) + silent token refresh +
    sign out. When the tenant's Conditional Access sign-in frequency rejects
    the refresh (AADSTS70043, "every time" = 5 min), the saved grid stays,
    the status says why, and connecting re-authenticates (account picker,
    prompt=select_account — the flow that works everywhere, including tenants
    with Seamless SSO) and then connects on its own.

The connection itself is still sdl-freerdp with /gateway:type:arm /sec:aad, so
camera/mic/gfx behave exactly as before.

Runtime: PyGObject with Gtk 4.0, WebKit 6.0 (all in the GNOME 51 Flatpak
runtime). No system-tray (GTK4 has no in-process tray; the GTK3
AppIndicator can't be mixed into a GTK4 process).
"""

import json
import os
import re as _re
import sys
import threading
import time
import urllib.request
import urllib.parse
import hashlib
import base64
import secrets
import shlex
import shutil
import subprocess
import pty
import select

# Software-composite WebKit (our sign-in webview) instead of the DMABUF/GBM
# GPU path. That path fails to allocate a GBM buffer on some GPUs/drivers
# (notably under XWayland / in the Flatpak sandbox, and after repeated webviews
# exhaust buffers), leaving the sign-in window BLANK. Must be set before WebKit
# initializes its renderer. setdefault so a user can still force the GPU path.
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, GLib, Gdk, Gio  # noqa: E402
from gi.repository import WebKit  # noqa: E402

# shared feed/auth logic lives next to this file (installed together)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avdfeed as af  # noqa: E402

APP_ID = "io.github.shakeelosmani.avd_feed_connect"
APP_NAME = "AVD Feed + Connect Linux"
APP_VERSION = "0.3.8"

# Persist the last discovered workspaces so reopening shows them instantly
# (like the Windows App), instead of bouncing to sign-in on every launch.
WS_CACHE = os.path.join(os.path.dirname(af.CACHE), "workspaces.json")
# Per-resource icon PNGs, so the saved workspace view looks exactly like the
# live one even before (or without) a fresh token — like the Windows App.
ICON_DIR = os.path.join(os.path.dirname(af.CACHE), "icons")


def _icon_path(res_id):
    return os.path.join(ICON_DIR, _re.sub(r"[^A-Za-z0-9_.-]", "_", res_id) + ".png")

# In-app display/connection preferences (⋯ → Settings…). Environment variables,
# if set, still override these for power users.
SETTINGS_FILE = os.path.join(os.path.dirname(af.CACHE), "settings.json")
SCALE_LABELS = ["Automatic (match display)", "100%", "125%", "150%",
                "175%", "200%", "250%", "300%"]
SCALE_VALUES = ["auto", "100", "125", "150", "175", "200", "250", "300"]
MULTIMON_LABELS = ["Automatic (match monitors)", "Single monitor", "All monitors"]
MULTIMON_VALUES = ["auto", "off", "on"]


def _load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_settings(d):
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(d, f)
    except OSError:
        pass


def _save_ws_cache(resources):
    try:
        os.makedirs(os.path.dirname(WS_CACHE), exist_ok=True)
        with open(WS_CACHE, "w") as f:
            json.dump({"upn": af.UPN, "resources": resources}, f)
    except OSError:
        pass


def _load_ws_cache():
    try:
        with open(WS_CACHE) as f:
            d = json.load(f)
        if d.get("upn") and not af.UPN:
            af.UPN = d["upn"]
        return d.get("resources") or []
    except (OSError, ValueError):
        return []


def _bearer_bytes(url, token):
    """Binary GET with the approved UA headers (for icons; af._get decodes text)."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "*/*")
    req.add_header("User-Agent", af.MS_USER_AGENT)
    req.add_header("X-MS-User-Agent", af.MS_USER_AGENT)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# ---- theme / styling -------------------------------------------------------
# GTK CSS is not web CSS: no var(), no transform, no ::before. We define the
# palette with @define-color (a light set and a dark set) and pick which to load
# based on the desktop's dark preference, so the look is consistent across
# distros/themes instead of inheriting whatever GTK theme is active.
PALETTE_LIGHT = """
@define-color avd_bg #F4F6F9;
@define-color avd_surface #FFFFFF;
@define-color avd_surface2 #F1F4F8;
@define-color avd_border #E3E8EF;
@define-color avd_border_strong #D3DAE3;
@define-color avd_ink #1A2230;
@define-color avd_muted #66707E;
@define-color avd_faint #98A2B3;
@define-color avd_accent #0E7CF4;
@define-color avd_accent_ink #0B63C4;
@define-color avd_ok #2FB86B;
@define-color avd_warn #B87E00;
"""
PALETTE_DARK = """
@define-color avd_bg #161A21;
@define-color avd_surface #212630;
@define-color avd_surface2 #2A303B;
@define-color avd_border #353D49;
@define-color avd_border_strong #48525F;
@define-color avd_ink #F2F5FA;
@define-color avd_muted #AEB8C6;
@define-color avd_faint #7B8593;
@define-color avd_accent #57A0FF;
@define-color avd_accent_ink #8ABAFF;
@define-color avd_ok #4FD08D;
@define-color avd_warn #F2B838;
"""
CSS_BASE = """
window.avd, .avd-page { background:@avd_bg; }
.avd-page { background:@avd_bg; }

headerbar.avd-header {
  background:linear-gradient(to bottom, @avd_surface, @avd_surface2);
  border-bottom:1px solid @avd_border; box-shadow:none; min-height:52px;
  padding:6px 8px;
}
/* window controls (min/max/close) — keep them clearly visible in dark mode */
headerbar.avd-header windowcontrols button,
headerbar.avd-header .titlebutton { color:@avd_muted; background:none;
  box-shadow:none; min-width:26px; min-height:26px; }
headerbar.avd-header windowcontrols button:hover,
headerbar.avd-header .titlebutton:hover { color:@avd_ink; background:alpha(@avd_ink,0.08); }
.mark { min-width:34px; min-height:34px; border-radius:10px; color:#ffffff;
  background:linear-gradient(145deg,#2D8BFF,#0E63D6);
  box-shadow:0 3px 8px -2px alpha(#0E7CF4,0.55); }
.brand-title { font-weight:800; font-size:15px; color:@avd_ink; }
.brand-sub { font-size:11px; color:@avd_faint; }
button.iconbtn { border-radius:9px; color:@avd_muted; background:none;
  border:1px solid transparent; min-width:34px; min-height:34px; box-shadow:none; padding:0; }
button.iconbtn:hover { background:@avd_bg; color:@avd_ink; border-color:@avd_border; }
/* the class sits on the menubutton; style its inner button (else it keeps the
   theme's default light background and the email text vanishes in dark mode) */
menubutton.acct-btn { background:none; box-shadow:none; }
menubutton.acct-btn > button { border-radius:20px; border:1px solid @avd_border;
  background:@avd_bg; color:@avd_ink; padding:2px 10px 2px 3px; box-shadow:none; min-height:32px; }
menubutton.acct-btn > button:hover { border-color:@avd_border_strong; background:@avd_bg; }
.avatar { min-width:26px; min-height:26px; border-radius:20px;
  background:linear-gradient(145deg,#5B6BFF,#7A3BE0); color:#ffffff;
  font-weight:800; font-size:11px; }
.acct-who { font-weight:600; font-size:12px; color:@avd_ink; }
.caret { color:@avd_faint; }

.section-h { font-weight:800; font-size:11px; letter-spacing:1px; color:@avd_muted; }
.count { font-size:12px; color:@avd_faint; }

flowbox, flowboxchild { background:none; padding:0; border:none; box-shadow:none; }
flowboxchild:selected, flowboxchild:focus, flowboxchild:active { background:none; }

.tile { background:@avd_surface; border:1px solid @avd_border; border-radius:15px;
  padding:15px; box-shadow:0 1px 2px alpha(#121C2E,0.05);
  transition:border-color 150ms, box-shadow 150ms, background 150ms; }
.tile:hover { border-color:@avd_accent;
  box-shadow:0 10px 24px -14px alpha(@avd_accent,0.55), 0 2px 6px -2px alpha(#121C2E,0.12); }
.tile.tile-connected { border-color:alpha(@avd_ok,0.55); }
flowboxchild:focus-visible .tile { outline:2px solid @avd_accent; outline-offset:2px; }

.ic { min-width:50px; min-height:50px; border-radius:13px; background:@avd_surface2; }
.ic.desktop { background:alpha(@avd_accent,0.13); }
.ic.desktop image { color:@avd_accent; }
.ic.app { background:alpha(@avd_warn,0.15); }
.ic.app image { color:@avd_warn; }

.tname { font-weight:800; font-size:14px; color:@avd_ink; }
.chiplabel { font-size:10px; font-weight:700; letter-spacing:0.7px; color:@avd_muted; }
.chipdot { min-width:6px; min-height:6px; border-radius:6px; background:@avd_accent; }
.chip-app .chipdot { background:@avd_warn; }

.status-pill { font-size:9px; font-weight:800; letter-spacing:0.5px; padding:3px 9px;
  border-radius:20px; background:alpha(@avd_ok,0.15); color:@avd_ok; }
.status-pill.pill-connecting { background:alpha(@avd_warn,0.18); color:@avd_warn; }

.foot { background:@avd_surface; border-top:1px solid @avd_border; padding:8px 16px; }
.status { color:@avd_muted; font-size:12px; }
.ver { color:@avd_faint; font-size:11px; }
.livedot { min-width:7px; min-height:7px; border-radius:7px; background:@avd_ok; }

.connect-card { background:@avd_surface; border:1px solid @avd_border; border-radius:18px;
  padding:26px 34px; box-shadow:0 20px 48px -14px alpha(#121C2E,0.4); }
.connect-label { font-weight:700; font-size:14px; color:@avd_ink; }
.connect-sub { font-size:12px; color:@avd_muted; }

.bigmark { min-width:66px; min-height:66px; border-radius:19px; color:#ffffff;
  background:linear-gradient(145deg,#2D8BFF,#0E63D6);
  box-shadow:0 12px 30px -10px alpha(#0E7CF4,0.65); }
.hero-title { font-weight:800; font-size:22px; color:@avd_ink; }
.hero-sub { font-size:14px; color:@avd_muted; }
.hero-note { font-size:11px; color:@avd_faint; }
button.msbtn { background:@avd_accent; color:#ffffff; font-weight:700; font-size:14px;
  border-radius:11px; padding:11px 20px; border:none;
  box-shadow:0 8px 18px -6px alpha(@avd_accent,0.6); }
button.msbtn:hover { background:@avd_accent_ink; }

.settings-title { font-weight:800; font-size:17px; color:@avd_ink; }
.settings-sub { font-size:12px; color:@avd_muted; }
.field-label { font-weight:800; font-size:11px; letter-spacing:0.6px; color:@avd_muted; }
.menu-head { font-weight:800; font-size:9px; letter-spacing:0.8px; color:@avd_faint; }
box.linked > button { min-height:26px; font-size:12px; font-weight:700;
  color:@avd_muted; background:@avd_bg; box-shadow:none; }
box.linked > button:hover { color:@avd_ink; }
box.linked > button:checked { background:@avd_accent; color:#ffffff; }
"""


class AvdApp(Gtk.Application):
    def __init__(self):
        # Demo/screenshot runs skip D-Bus single-instance registration entirely
        # (NON_UNIQUE), so they always run standalone and never hand off to (or
        # collide with) the installed app. Under Flatpak the sandbox only lets
        # the app own its exact app id, so a custom demo id can't be registered.
        if os.environ.get("AVD_DEMO"):
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.NON_UNIQUE)
        else:
            super().__init__(application_id=APP_ID)
        self.token = None
        self._deadline = 0.0        # monotonic time the access token expires
        self._refresh_source = 0    # GLib timeout id for the scheduled refresh
        self._token_lock = threading.Lock()
        self.win = None
        self.stack = None
        self.grid = None
        self.status = None
        self._tiles = []            # GTK4 FlowBox has no get_children(); track ours
        self._signout_item = None
        self._scale = 1             # client display scale factor (1 or 2 = HiDPI)
        self._n_monitors = 1        # how many monitors the compositor reports
        self._settings = _load_settings()   # {"_default": {...}, "<res id>": {...}}
        self._pending_launch = None  # resource id to connect to once sign-in completes
        self._signin_dlg = None      # the open sign-in window, if any
        self._web_session_obj = None  # shared persistent WebKit session (SSO cookies)
        self._connecting = set()      # resource ids currently establishing a session

    # ---- app lifecycle ----------------------------------------------------
    def do_activate(self):
        if self.win:
            self.win.present()
            return
        self._build_ui()
        self.win.present()
        self._detect_displays()
        # Dev/screenshot mode: show a fixed, anonymous sample feed (no network,
        # no sign-in). Used for documentation screenshots so they carry no real
        # account or org details. Never triggered in normal use.
        if os.environ.get("AVD_DEMO"):
            af.UPN = "alex@contoso.com"
            demo = [
                {"id": "d1", "title": "Finance Desktop", "type": "Desktop",
                 "tenant": "Contoso", "icon32": None},
                {"id": "d2", "title": "Design Studio", "type": "Desktop",
                 "tenant": "Contoso", "icon32": None},
                {"id": "d3", "title": "Ops Console", "type": "RemoteApp",
                 "tenant": "Contoso", "icon32": None},
                {"id": "d4", "title": "Dev Sandbox", "type": "Desktop",
                 "tenant": "Contoso", "icon32": None},
                {"id": "d5", "title": "DR Failover", "type": "Desktop",
                 "tenant": "Contoso", "icon32": None},
            ]
            self._populate(demo)
            self._set_status("Signed in as alex@contoso.com")
            self._set_tile_state("d2", "", "state-connected")
            return
        # If we have previously discovered workspaces, show them immediately and
        # refresh the token + feed silently in the background (Windows-App-style
        # persistent session). Only a first run with no cache shows sign-in.
        cached = _load_ws_cache()
        if cached:
            self._populate(cached)
            self._set_status(f"{len(cached)} workspaces · refreshing…")
            threading.Thread(target=self._background_refresh, daemon=True).start()
        else:
            self._set_status("Signing in…")
            threading.Thread(target=self._silent_signin, daemon=True).start()

    def _detect_displays(self):
        """Read the client's real display layout from the compositor so the
        remote session matches it automatically (like the Windows App does):
        number of monitors → single vs multi-monitor, and the HiDPI scale."""
        try:
            self._scale = self.win.get_scale_factor() or 1
        except Exception:
            self._scale = 1
        try:
            mons = Gdk.Display.get_default().get_monitors()
            self._n_monitors = max(1, mons.get_n_items())
        except Exception:
            self._n_monitors = 1

    def _background_refresh(self):
        """Silently refresh the token and re-fetch the feed, keeping the cached
        workspaces on screen if it fails (never auto-bounces to sign-in)."""
        if not self._do_refresh(silent=True):
            self._set_status(self._refresh_failed_status())
            return
        try:
            resources = af.enumerate_feed(self.token)
        except Exception:
            self._set_status("Showing saved workspaces (couldn't refresh)")
            return
        _save_ws_cache(resources)
        GLib.idle_add(self._populate, resources)

    def _is_dark(self):
        ov = os.environ.get("AVD_THEME", "").strip().lower()
        if ov in ("dark", "light"):
            return ov == "dark"
        pref = (self._settings.get("_theme") or "system")   # in-app choice
        if pref == "light":
            return False
        if pref == "dark":
            return True
        s = Gtk.Settings.get_default()
        try:
            if s.get_property("gtk-application-prefer-dark-theme"):
                return True
        except Exception:
            pass
        try:
            return "dark" in (s.get_property("gtk-theme-name") or "").lower()
        except Exception:
            return False

    def _apply_theme(self, *_):
        css = (PALETTE_DARK if self._is_dark() else PALETTE_LIGHT) + CSS_BASE
        self._css_provider.load_from_string(css)

    def _set_theme(self, key):
        """Persist the appearance choice (system/light/dark) and apply it live."""
        self._settings["_theme"] = key
        _save_settings(self._settings)
        self._apply_theme()

    def _build_ui(self):
        self._css_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._apply_theme()
        st = Gtk.Settings.get_default()
        for prop in ("notify::gtk-application-prefer-dark-theme", "notify::gtk-theme-name"):
            st.connect(prop, self._apply_theme)

        self.win = Gtk.ApplicationWindow(application=self, title=APP_NAME)
        self.win.add_css_class("avd")
        self.win.set_default_size(820, 600)

        # ---- header bar: brand (left) · refresh + account (right) -----------
        hb = Gtk.HeaderBar()
        hb.add_css_class("avd-header")
        hb.set_title_widget(Gtk.Label())     # blank the centered window title
        self.win.set_titlebar(hb)

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
        # The mark IS the image (a Gtk.Image always draws its icon centered in
        # its allocation), sized/tinted by the .mark CSS — no box wrapper to
        # mis-align or expand.
        mark = Gtk.Image.new_from_icon_name("computer-symbolic")
        mark.set_pixel_size(18); mark.add_css_class("mark"); mark.set_valign(Gtk.Align.CENTER)
        tbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tbox.set_valign(Gtk.Align.CENTER)      # center the title block next to the logo
        t1 = Gtk.Label(label="AVD Feed + Connect", xalign=0); t1.add_css_class("brand-title")
        t2 = Gtk.Label(label="Azure Virtual Desktop · Windows 365", xalign=0)
        t2.add_css_class("brand-sub")
        tbox.append(t1); tbox.append(t2)
        brand.append(mark); brand.append(tbox)
        brand.set_valign(Gtk.Align.CENTER)
        hb.pack_start(brand)

        # account menu button (avatar + who + caret), opens the settings/sign-out popover
        menu_btn = Gtk.MenuButton(); menu_btn.add_css_class("acct-btn")
        acctbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        # The avatar IS the label (a Gtk.Label centers its own text), sized/
        # tinted by the .avatar CSS — no box wrapper.
        self._avatar_lbl = Gtk.Label(label="?"); self._avatar_lbl.add_css_class("avatar")
        self._avatar_lbl.set_valign(Gtk.Align.CENTER)
        self._acct_who = Gtk.Label(label="Sign in"); self._acct_who.add_css_class("acct-who")
        self._acct_who.set_ellipsize(3); self._acct_who.set_max_width_chars(24)
        caret = Gtk.Image.new_from_icon_name("pan-down-symbolic"); caret.add_css_class("caret")
        acctbox.append(self._avatar_lbl); acctbox.append(self._acct_who); acctbox.append(caret)
        menu_btn.set_child(acctbox)

        pop = Gtk.Popover()
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for m in ("top", "bottom", "start", "end"):
            getattr(pbox, f"set_margin_{m}")(6)

        # Appearance: System / Light / Dark (remembered), applied live.
        appr = Gtk.Label(label="APPEARANCE", xalign=0); appr.add_css_class("menu-head")
        appr.set_margin_start(4); appr.set_margin_top(2)
        pbox.append(appr)
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        seg.add_css_class("linked"); seg.set_margin_bottom(4)
        cur_theme = self._settings.get("_theme") or "system"
        first = None
        for key, label in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            b = Gtk.ToggleButton(label=label)
            if first is None:
                first = b
            else:
                b.set_group(first)
            b.set_active(key == cur_theme)
            b.connect("toggled", lambda btn, k=key: btn.get_active() and self._set_theme(k))
            seg.append(b)
        pbox.append(seg)
        pbox.append(Gtk.Separator())

        setbtn = Gtk.Button(label="Default settings…"); setbtn.add_css_class("flat")
        setbtn.connect("clicked", lambda *_: (pop.popdown(), self._open_settings(None)))
        pbox.append(setbtn)
        pbox.append(Gtk.Separator())
        signout = Gtk.Button(label="Sign out"); signout.add_css_class("flat")
        signout.set_sensitive(False)
        signout.connect("clicked", lambda *_: (pop.popdown(), self._sign_out()))
        self._signout_item = signout
        pbox.append(signout)
        pop.set_child(pbox)
        menu_btn.set_popover(pop)

        self.refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_btn.add_css_class("iconbtn")
        self.refresh_btn.set_tooltip_text("Refresh workspaces")
        self.refresh_btn.connect("clicked", lambda *_: self._reload_feed())

        hb.pack_end(menu_btn)
        hb.pack_end(self.refresh_btn)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_css_class("avd-page")
        self.win.set_child(self.stack)

        # ---- sign-in hero ---------------------------------------------------
        signin = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        signin.add_css_class("avd-page")
        signin.set_valign(Gtk.Align.CENTER); signin.set_halign(Gtk.Align.CENTER)
        bm = Gtk.Image.new_from_icon_name("computer-symbolic"); bm.set_pixel_size(32)
        bm.add_css_class("bigmark"); bm.set_margin_bottom(14); bm.set_halign(Gtk.Align.CENTER)
        h1 = Gtk.Label(label="Sign in to your workspaces"); h1.add_css_class("hero-title")
        h2 = Gtk.Label(label="Connect your Microsoft work account to see the desktops "
                             "and apps you're entitled to.")
        h2.add_css_class("hero-sub"); h2.set_wrap(True); h2.set_justify(Gtk.Justification.CENTER)
        h2.set_max_width_chars(38); h2.set_margin_top(4)
        btn = Gtk.Button(label="Sign in with Microsoft"); btn.add_css_class("msbtn")
        btn.set_halign(Gtk.Align.CENTER); btn.set_margin_top(20)
        btn.connect("clicked", lambda *_: self._interactive_signin())
        note = Gtk.Label(label="Unofficial client · not affiliated with or endorsed by Microsoft")
        note.add_css_class("hero-note"); note.set_margin_top(24)
        for w in (bm, h1, h2, btn, note):
            signin.append(w)
        self.stack.add_named(signin, "signin")

        # ---- workspaces page ------------------------------------------------
        wp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wp.add_css_class("avd-page")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        for m, v in (("top", 18), ("start", 22), ("end", 22), ("bottom", 4)):
            getattr(head, f"set_margin_{m}")(v)
        sh = Gtk.Label(label="YOUR WORKSPACES", xalign=0); sh.add_css_class("section-h")
        sh.set_hexpand(True)
        self._count_lbl = Gtk.Label(label="", xalign=1); self._count_lbl.add_css_class("count")
        head.append(sh); head.append(self._count_lbl)
        wp.append(head)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        self.grid = Gtk.FlowBox(valign=Gtk.Align.START, max_children_per_line=5,
                                min_children_per_line=2, row_spacing=14,
                                column_spacing=14, homogeneous=True,
                                selection_mode=Gtk.SelectionMode.NONE)
        self.grid.set_margin_top(8); self.grid.set_margin_bottom(18)
        self.grid.set_margin_start(22); self.grid.set_margin_end(22)
        self.grid.connect("child-activated", self._on_tile_activated)
        sw.set_child(self.grid)

        # Connecting overlay: centered spinner card shown while a session is
        # establishing (silent AAD + RDP handshake), until the desktop logs on.
        overlay = Gtk.Overlay()
        overlay.set_vexpand(True)
        overlay.set_child(sw)
        cbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        cbox.set_halign(Gtk.Align.CENTER); cbox.set_valign(Gtk.Align.CENTER)
        cbox.add_css_class("connect-card")
        self._connect_spinner = Gtk.Spinner(); self._connect_spinner.set_size_request(42, 42)
        self._connect_label = Gtk.Label(label="Connecting…"); self._connect_label.add_css_class("connect-label")
        csub = Gtk.Label(label="Securing your session"); csub.add_css_class("connect-sub")
        cbox.append(self._connect_spinner); cbox.append(self._connect_label); cbox.append(csub)
        cbox.set_visible(False)
        cbox.set_can_target(False)
        self._connect_overlay = cbox
        overlay.add_overlay(cbox)
        wp.append(overlay)

        # footer: live dot · status · version
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        foot.add_css_class("foot")
        self._livedot = Gtk.Box(); self._livedot.add_css_class("livedot")
        self._livedot.set_valign(Gtk.Align.CENTER)
        self.status = Gtk.Label(label="", xalign=0); self.status.add_css_class("status")
        self.status.set_hexpand(True)
        ver = Gtk.Label(label="v" + APP_VERSION, xalign=1); ver.add_css_class("ver")
        foot.append(self._livedot); foot.append(self.status); foot.append(ver)
        wp.append(foot)
        self.stack.add_named(wp, "workspaces")

        # busy page
        busy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        busy.add_css_class("avd-page")
        busy.set_valign(Gtk.Align.CENTER)
        sp = Gtk.Spinner(); sp.start()
        busy.append(sp)
        busy.append(Gtk.Label(label="Loading…"))
        self.stack.add_named(busy, "busy")
        self.stack.set_visible_child_name("busy")

    # ---- helpers (main thread) --------------------------------------------
    def _set_status(self, text):
        GLib.idle_add(lambda: self.status.set_text(text) if self.status else None)

    def _update_account(self, who):
        """Fill the header account chip: email + a 2-letter avatar."""
        if not who or "@" not in who:
            self._acct_who.set_text("Sign in")
            self._avatar_lbl.set_text("?")
            return
        self._acct_who.set_text(who)
        user = who.split("@", 1)[0]
        parts = _re.split(r"[.\-_]+", user)
        initials = (parts[0][:1] + (parts[1][:1] if len(parts) > 1 else user[1:2]))
        self._avatar_lbl.set_text(initials.upper() or "?")

    def _set_connecting(self, res_id, title, on):
        """Show/hide the centered 'Connecting…' spinner overlay. Safe to call
        from any thread. The overlay stays up while any session is connecting."""
        def apply():
            if on:
                self._connecting.add(res_id)
            else:
                self._connecting.discard(res_id)
            if self._connecting:
                n = len(self._connecting)
                self._connect_label.set_text(
                    f"Connecting to {title}…" if n == 1 else f"Connecting… ({n})")
                self._connect_spinner.start()
                self._connect_overlay.set_visible(True)
            else:
                self._connect_overlay.set_visible(False)
                self._connect_spinner.stop()
            return False
        GLib.idle_add(apply)

    def _show(self, name):
        def apply():
            self.stack.set_visible_child_name(name)
            # "Sign out" only makes sense once we're signed in (workspaces view)
            if self._signout_item is not None:
                self._signout_item.set_sensitive(name == "workspaces")
        GLib.idle_add(apply)

    # ---- auth / token lifecycle ------------------------------------------
    def _apply_token(self, tok):
        """Store a freshly minted token, persist the (rotated) refresh token,
        and schedule a silent refresh before it expires."""
        with self._token_lock:
            self.token = tok["access_token"]
            self._deadline = time.monotonic() + int(tok.get("expires_in", 3600))
        af.set_upn_from_token(tok)  # learn the account for /u: and the status bar
        rt = tok.get("refresh_token")
        if rt:
            af.save_refresh_token(rt)
        # refresh 5 min before expiry (min 60s out)
        secs = max(60, int(tok.get("expires_in", 3600)) - 300)
        GLib.idle_add(self._schedule_refresh, secs)

    def _schedule_refresh(self, secs):
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
        self._refresh_source = GLib.timeout_add_seconds(secs, self._auto_refresh)
        return False

    def _auto_refresh(self):
        self._refresh_source = 0
        threading.Thread(target=self._do_refresh, args=(True,), daemon=True).start()
        return False  # one-shot; _apply_token reschedules the next one

    def _do_refresh(self, silent):
        """Run the refresh_token grant; returns True on success."""
        rec = af.load_token_record()
        rt = rec.get("refresh_token") if rec else None
        if not rt:
            if not silent:
                self._show("signin")
            return False
        tok = af._refresh(rt)
        if not tok:
            if not silent:
                self._error("Session expired — please sign in again.")
                self._show("signin")
            return False
        self._apply_token(tok)
        return True

    def _ensure_token(self):
        """Called from worker threads before a feed/rdp request; refreshes
        synchronously if the token is within 2 min of expiry."""
        with self._token_lock:
            fresh = self.token and time.monotonic() < self._deadline - 120
        if fresh:
            return True
        return self._do_refresh(silent=True)

    def _silent_signin(self):
        if af.has_token() and self._do_refresh(silent=True):
            self._load_feed_bg()
            return
        self._show("signin")

    def _refresh_failed_status(self):
        """Status-bar text for a failed silent refresh, naming the real cause
        when it's the tenant's Conditional Access sign-in-frequency policy
        (AADSTS70043) rather than anything the app can fix."""
        err = af.LAST_REFRESH_ERROR
        if "AADSTS70043" in err or "sign-in frequency" in err:
            return ("Showing saved workspaces · your organization requires signing "
                    "in again (Conditional Access sign-in frequency)")
        return "Showing saved workspaces · sign in again to refresh"

    def _web_session(self):
        """Shared, PERSISTENT WebKit network session used by every Microsoft
        sign-in webview — the launcher's sign-in dialog and the connection-time
        AAD resolver. It only ever visits Entra login domains, so persisting its
        cookies (a) makes a re-auth after a token refresh is refused a
        click-through (SSO) instead of full password+MFA, and (b) lets the
        connection-time gateway token be fetched silently from the same SSO
        session. Stored in our app data dir (user-private); the refresh token
        itself lives in the keyring, not here."""
        if self._web_session_obj is None:
            d = os.path.join(os.path.dirname(af.CACHE), "webview")
            os.makedirs(d, exist_ok=True)
            self._web_session_obj = WebKit.NetworkSession.new(
                d, os.path.join(d, "cache"))
            try:
                self._web_session_obj.get_cookie_manager().set_persistent_storage(
                    os.path.join(d, "cookies.sqlite"),
                    WebKit.CookiePersistentStorage.SQLITE)
            except Exception:
                pass
        return self._web_session_obj

    def _interactive_signin(self):
        # Never reuse or resume a previous sign-in window: tear any existing one
        # down and start fresh, so closing and reopening is always a clean slate
        # (the user's rule: close must close, reopen must resume from clean).
        if self._signin_dlg is not None:
            old, self._signin_dlg = self._signin_dlg, None
            old.destroy()
        verifier = af._b64url(secrets.token_bytes(64))
        challenge = af._b64url(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(16)
        # Always show the account picker (prompt=select_account). It is the
        # known-good flow: it bypasses Azure AD Seamless SSO — which needs a
        # Windows Kerberos ticket and, on Linux, hangs the webview forever on
        # "Trying to sign you in" — and it does NOT force the full interactive
        # re-login that makes tenants inject a security-info registration
        # interrupt ("Keep your account secure"). Trying to save the one picker
        # click (login_hint without a prompt, or prompt=login) reintroduced
        # both of those bugs, so we don't.
        params = {
            "client_id": af.CLIENT_ID, "response_type": "code",
            "redirect_uri": af.REDIRECT, "scope": af.SCOPE,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "state": state, "prompt": "select_account",
        }
        if af.UPN:
            params["login_hint"] = af.UPN   # preselect the known account in the picker
        url = af.LOGIN + "/authorize?" + urllib.parse.urlencode(params)

        dlg = Gtk.Window(title="Sign in", transient_for=self.win, modal=True)
        dlg.set_default_size(520, 640)
        # Shared PERSISTENT session (SSO cookies kept on disk) so a re-auth is a
        # click-through and the connection-time token can be fetched silently.
        # The half-finished-flow / ghost-window problems that made us try an
        # ephemeral session are fixed independently (popup blocking + explicit
        # close-request + always destroying/rebuilding the dialog + DMABUF off),
        # so persistence is safe and is what actually reduces sign-in prompts.
        wv = WebKit.WebView(network_session=self._web_session())
        dlg.set_child(wv)
        self._signin_dlg = dlg
        got_code = [False]

        # Block popups. A page (e.g. the personal-account "approve on your phone"
        # step) can call window.open(); WebKit would spawn a separate, orphan
        # WebView/window with no controls — the "ghost window" that can't be
        # closed. Returning None here declines the popup instead.
        wv.connect("create", lambda *_a: None)

        def on_closed(*_):
            # Clicking the window's × always closes it and fully resets state,
            # so the next sign-in is a clean start (no resumed half-finished flow).
            if self._signin_dlg is dlg:
                self._signin_dlg = None
            if not got_code[0]:
                self._pending_launch = None   # user gave up; don't auto-connect later
        dlg.connect("destroy", on_closed)
        # Default close-request already destroys the window; make it explicit and
        # unconditional so nothing can swallow the close.
        dlg.connect("close-request", lambda w: (w.destroy(), True)[1])

        def on_decide(view, decision, dtype):
            if dtype != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
                return False
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(af.REDIRECT) and "code=" in uri:
                decision.ignore()
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
                got_code[0] = True
                dlg.destroy()
                if qs.get("state", [state])[0] != state:
                    self._pending_launch = None
                    self._error("Sign-in state mismatch, please retry.")
                    return True
                code = qs.get("code", [""])[0]
                self._show("busy")
                threading.Thread(target=self._exchange, args=(code, verifier),
                                 daemon=True).start()
                return True
            return False

        wv.connect("decide-policy", on_decide)
        wv.load_uri(url)
        dlg.present()

    def _exchange(self, code, verifier):
        st, tok = af._post(af.LOGIN + "/token", {
            "grant_type": "authorization_code", "client_id": af.CLIENT_ID,
            "code": code, "redirect_uri": af.REDIRECT, "scope": af.SCOPE,
            "code_verifier": verifier})
        if st != 200:
            self._pending_launch = None
            self._error("Sign-in failed: " + tok.get("error_description", tok.get("error", "?")))
            self._show("signin")
            return
        self._apply_token(tok)
        self._load_feed_bg()   # _populate then auto-connects any pending workspace

    def _sign_out(self):
        # Manual sign-out is the ONLY thing that clears the session + workspaces.
        af.clear_token_cache()            # keyring entry + any fallback file
        try:
            if os.path.exists(WS_CACHE):
                os.remove(WS_CACHE)
        except OSError:
            pass
        shutil.rmtree(ICON_DIR, ignore_errors=True)
        # Forget the persisted Microsoft SSO cookies too, so sign-out is total
        # (next sign-in is a fresh password+MFA, not a cookie click-through).
        self._web_session_obj = None
        shutil.rmtree(os.path.join(os.path.dirname(af.CACHE), "webview"),
                      ignore_errors=True)
        self._pending_launch = None
        af.UPN = "" if not os.environ.get("AVD_UPN") else af.UPN
        with self._token_lock:
            self.token = None
            self._deadline = 0.0
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0
        self._clear_tiles()
        self._show("signin")

    # ---- feed -------------------------------------------------------------
    def _reload_feed(self):
        if not self.token:
            self._interactive_signin()   # e.g. saved view whose refresh was refused
            return
        self._show("busy")
        self._load_feed_bg()

    def _load_feed_bg(self):
        self._show("busy")
        threading.Thread(target=self._load_feed, daemon=True).start()

    def _load_feed(self):
        have_cache = bool(_load_ws_cache())
        if not self._ensure_token():
            if not have_cache:
                self._show("signin")
            return
        try:
            resources = af.enumerate_feed(self.token)
        except Exception as e:
            # Keep whatever is on screen if we have a cached list; only a first
            # run with nothing to show falls back to the sign-in page.
            self._pending_launch = None
            if have_cache:
                self._set_status("Couldn't refresh workspaces — showing saved list")
            else:
                self._error("Feed error: " + str(e)); self._show("signin")
            return
        _save_ws_cache(resources)
        GLib.idle_add(self._populate, resources)

    def _clear_tiles(self):
        for ch in self._tiles:
            self.grid.remove(ch)
        self._tiles = []

    def _populate(self, resources):
        self._clear_tiles()
        for res in resources:
            ch = self._make_tile(res)
            self.grid.append(ch)
            self._tiles.append(ch)
        who = af.UPN or "your account"
        self.status.set_text(f"Signed in as {who}")
        n = len(resources)
        self._count_lbl.set_text(f"{n} resource" + ("" if n == 1 else "s"))
        self._update_account(who)
        self.stack.set_visible_child_name("workspaces")
        if self._signout_item is not None:
            self._signout_item.set_sensitive(True)
        # Icons: cached PNGs show immediately; with a token they're re-fetched
        # (and re-cached) in the background so the grid never looks "reset".
        threading.Thread(target=self._load_icons, args=(resources,),
                         daemon=True).start()
        # A connect that had to wait for sign-in resumes now, on the fresh tiles
        pending, self._pending_launch = self._pending_launch, None
        if pending:
            ch = self._child_for(pending)
            if ch:
                self._on_tile_activated(self.grid, ch)

    def _make_tile(self, res):
        is_desktop = res["type"] == "Desktop"
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=13)
        tile.add_css_class("tile")

        # top row: tinted icon plate (left) + status pill (top-right)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        ic = Gtk.Box(); ic.add_css_class("ic")
        ic.add_css_class("desktop" if is_desktop else "app")
        ic.set_halign(Gtk.Align.START); ic.set_valign(Gtk.Align.CENTER)
        ic.set_size_request(52, 52)          # fixed square, so the glyph centers
        img = Gtk.Image.new_from_icon_name(
            "computer-symbolic" if is_desktop else "view-grid-symbolic")
        img.set_pixel_size(26)
        img.set_hexpand(True); img.set_vexpand(True)
        img.set_halign(Gtk.Align.CENTER); img.set_valign(Gtk.Align.CENTER)
        ic.append(img)
        pill = Gtk.Label(label="")
        pill.add_css_class("status-pill")
        pill.set_valign(Gtk.Align.START)
        pill.set_visible(False)
        spacer = Gtk.Box(); spacer.set_hexpand(True)
        top.append(ic); top.append(spacer); top.append(pill)

        # meta: title + type chip
        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        title = Gtk.Label(label=res["title"], xalign=0); title.add_css_class("tname")
        title.set_wrap(True); title.set_max_width_chars(18); title.set_lines(2)
        title.set_ellipsize(3)
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        if not is_desktop:
            chip.add_css_class("chip-app")
        chip.set_halign(Gtk.Align.START)
        dot = Gtk.Box(); dot.add_css_class("chipdot"); dot.set_valign(Gtk.Align.CENTER)
        clabel = Gtk.Label(label="DESKTOP" if is_desktop else "REMOTE APP")
        clabel.add_css_class("chiplabel")
        chip.append(dot); chip.append(clabel)
        meta.append(title); meta.append(chip)

        tile.append(top); tile.append(meta)

        child = Gtk.FlowBoxChild()
        child.set_child(tile)
        child._res = res
        child._img = img
        child._tile = tile
        child._pill = pill
        child._proc = None
        child._launching = False
        child.set_tooltip_text(
            f"{res['title']} — {res['tenant']}\nDouble-click to connect · "
            f"right-click for this workspace's settings")
        rclick = Gtk.GestureClick()
        rclick.set_button(3)
        rclick.connect("pressed", lambda g, n, x, y, r=res: self._open_settings(r))
        child.add_controller(rclick)
        return child

    def _child_for(self, res_id):
        for c in self._tiles:
            if getattr(c, "_res", {}).get("id") == res_id:
                return c
        return None

    def _set_tile_state(self, res_id, text, css):
        # Render session state as a corner pill: amber "Connecting", green
        # "Connected", hidden when idle. (css: state-connecting/connected/ended)
        def apply():
            ch = self._child_for(res_id)
            if not ch:
                return
            pill, tile = ch._pill, ch._tile
            pill.remove_css_class("pill-connecting")
            tile.remove_css_class("tile-connected")
            if css == "state-connected":
                pill.set_text("CONNECTED")
                pill.set_visible(True)
                tile.add_css_class("tile-connected")
            elif css == "state-connecting":
                pill.set_text("CONNECTING")
                pill.add_css_class("pill-connecting")
                pill.set_visible(True)
            else:
                pill.set_visible(False)
        GLib.idle_add(apply)

    def _load_icons(self, resources):
        children = {c._res["id"]: c for c in self._tiles}
        token = self.token
        for res in resources:
            url = res.get("icon32")
            if not url:
                continue
            path = _icon_path(res["id"])
            data = None
            if token:
                try:
                    data = _bearer_bytes(url, token)
                    os.makedirs(ICON_DIR, exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(data)
                except Exception:
                    data = None
            if data is None:            # no token, or the fetch failed → cache
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                except OSError:
                    continue
            try:
                # Load the PNG straight into a GdkTexture (the Image scales it to
                # its pixel size); avoids the deprecated new_for_pixbuf path.
                tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except Exception:
                continue
            ch = children.get(res["id"])
            if ch:
                GLib.idle_add(ch._img.set_from_paintable, tex)

    # ---- launch + live connection state -----------------------------------
    def _on_tile_activated(self, flowbox, child):
        res = child._res
        # Guard synchronously on the main thread: the second click of a
        # double-click arrives before the launch thread has set child._proc, so
        # a flag set here (not the proc handle) is what prevents a second launch.
        if getattr(child, "_launching", False) or (child._proc and child._proc.poll() is None):
            self.status.set_text(f"{res['title']} is already open")
            return
        child._launching = True
        self._set_tile_state(res["id"], "● Connecting…", "state-connecting")
        self.status.set_text(f"Connecting to {res['title']}…")
        self._set_connecting(res["id"], res["title"], True)
        threading.Thread(target=self._launch, args=(child, res), daemon=True).start()

    def _launch(self, child, res):
        if not self._ensure_token():
            # Session lapsed and can't refresh silently — re-auth on demand
            # (like the Windows App when you click a workspace after a while),
            # keeping the workspace list intact.
            child._launching = False
            self._set_connecting(res["id"], res["title"], False)
            self._set_tile_state(res["id"], "", None)
            self._set_status("Sign in to connect")
            self._pending_launch = res["id"]   # connect automatically afterwards
            GLib.idle_add(self._interactive_signin)
            return
        try:
            path = af.download_rdp(self.token, res)
        except (SystemExit, Exception) as e:  # never let a write/HTTP error hang the tile
            child._launching = False
            self._set_connecting(res["id"], res["title"], False)
            self._error(str(e))
            self._set_tile_state(res["id"], "● Failed", "state-ended")
            return
        env = dict(os.environ)
        # sdl-freerdp (SDL3) and FreeRDP's own AAD webview are unstable on native
        # Wayland (#2: "Error 71 dispatching to Wayland display"). Force X11 /
        # XWayland for the child, which is stable — and drop WAYLAND_DISPLAY so
        # nothing in the subprocess re-selects Wayland.
        env["GDK_BACKEND"] = "x11"
        env["SDL_VIDEODRIVER"] = "x11"
        env.pop("WAYLAND_DISPLAY", None)
        # FreeRDP no longer has its own webview (we resolve AAD in-process), but
        # keep this set for the child in case any bundled component initializes
        # WebKitGTK — the DMABUF/GBM path blanks on some GPUs. Harmless otherwise.
        env["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"
        # Reduce tearing in the SDL3 client (vsync + double buffer). SDL hints,
        # ignored where unsupported, so always safe.
        env.setdefault("SDL_RENDER_VSYNC", "1")
        env.setdefault("SDL_VIDEO_DOUBLE_BUFFER", "1")
        if af.SDL_LIBS and os.path.isdir(af.SDL_LIBS):
            env["LD_LIBRARY_PATH"] = af.SDL_LIBS + (
                os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        os.makedirs(af.OUT, exist_ok=True)
        safe = _re.sub(r"[^A-Za-z0-9]+", "_", res["title"])[:40]
        logpath = os.path.join(af.OUT, f"session_{safe}.log")
        argv = [af.SDL, path, "/gateway:type:arm", "/sec:aad"]
        if af.UPN:
            argv.append(f"/u:{af.UPN}")
        # Remote scale follows the client's display scale (HiDPI → 200%, standard/
        # ultrawide → 100%); AVD_SCALE overrides. Multi-monitor and any other flag
        # are opt-in via AVD_EXTRA_ARGS (e.g. "/multimon /gfx"), until a settings UI.
        # --- display config: env var > per-resource/default setting > auto ---
        extra = self._eff_extra(res)
        scale = os.environ.get("AVD_SCALE") or self._eff("scale", res) \
            or str(100 * max(1, self._scale))
        argv += ["/sound:sys:pulse", "/microphone", "/cert:ignore",
                 "/f", f"/scale-desktop:{scale}", "/log-level:info",
                 # bandwidth/quality + resilience + keepalive:
                 "+compression", "+fonts",
                 "+auto-reconnect", "/auto-reconnect-max-retries:10",
                 # inject fake input so the Azure gateway doesn't idle-drop the
                 # session (which would otherwise force a reconnect + token re-mint)
                 "/prevent-session-lock:120"]
        mm = os.environ.get("AVD_MULTIMON", "").strip().lower()
        if mm in ("1", "on", "true", "yes"):
            want_multimon = True
        elif mm in ("0", "off", "false", "no"):
            want_multimon = False
        else:
            setting = self._eff("multimon", res)      # "on"/"off"/None
            want_multimon = (setting == "on") if setting else (self._n_monitors > 1)
        # Don't fight an explicit choice already present in the extra flags.
        if "multimon" not in extra:
            argv.append("/multimon" if want_multimon else "-multimon")
        if extra:
            try:
                argv += shlex.split(extra)
            except ValueError:
                pass
        # Launch under a PTY. With FreeRDP built WITHOUT its own AAD webview,
        # /sec:aad prints "Browse to: <url>" and reads the redirect URL back
        # from stdin — but only when it thinks it's attached to a terminal, so
        # we give it a real pty. We service that prompt ourselves in a hidden
        # webview that shares the launcher's SSO cookies, making the
        # connection-time token silent. A new process group lets us tear down
        # the whole FreeRDP tree cleanly.
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(argv, env=env, stdin=slave, stdout=slave,
                                    stderr=slave, start_new_session=True,
                                    close_fds=True)
        finally:
            os.close(slave)
        child._proc = proc
        child._ptym = master
        self._set_status(f"Launched {res['title']}")
        self._watch_session(child, res, proc, master, logpath)

    _ANSI = _re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")

    def _watch_session(self, child, res, proc, master, logpath):
        """Read sdl-freerdp's PTY output: flip the tile to 'Connected' on logon,
        service each 'Browse to:' AAD prompt silently, and tidy up on exit."""
        state = {"connected": False}
        buf = b""
        log = open(logpath, "wb")
        try:
            while True:
                try:
                    r, _, _ = select.select([master], [], [], 1.0)
                except (OSError, ValueError):
                    break
                if r:
                    try:
                        data = os.read(master, 4096)
                    except OSError:
                        break
                    if not data:            # EOF: child closed the pty (exited)
                        break
                    log.write(data)
                    log.flush()
                    buf += data
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = self._ANSI.sub(b"", raw).decode("utf-8", "replace").strip()
                        if line:
                            self._on_freerdp_line(child, res, master, line, state)
                elif proc.poll() is not None:
                    break
        finally:
            try:
                log.close()
            except Exception:
                pass
            try:
                os.close(master)
            except OSError:
                pass
        child._launching = False
        self._set_connecting(res["id"], res["title"], False)  # ensure overlay clears
        self._set_tile_state(res["id"], "○ Disconnected", "state-ended")
        self._set_status(f"{res['title']} session ended")
        GLib.timeout_add_seconds(
            6, lambda: (self._set_tile_state(res["id"], "", None), False)[1])

    def _on_freerdp_line(self, child, res, master, line, state):
        """Handle one line of FreeRDP output (called from the reader thread)."""
        if not state["connected"] and "Logon Info" in line:
            state["connected"] = True
            self._set_tile_state(res["id"], "● Connected", "state-connected")
            self._set_status(f"Connected to {res['title']}")
            self._set_connecting(res["id"], res["title"], False)  # desktop is up
        if line.startswith("Browse to: "):
            url = line[len("Browse to: "):].strip()
            if url.startswith("http"):
                # Resolve on the main thread (WebKit must run there).
                GLib.idle_add(self._resolve_aad, master, url, res)

    def _resolve_aad(self, master, url, res):
        """Service FreeRDP's connection-time AAD prompt: load its authorize URL
        in a hidden webview sharing our SSO cookies, and write the resulting
        redirect URL back to FreeRDP's stdin. The window is shown only if it
        doesn't resolve silently within 3s (i.e. MFA/consent is really needed)."""
        wv = WebKit.WebView(network_session=self._web_session())
        # Unpresented window: a WebView loads/navigates while its window is
        # hidden (verified), so the silent path never flashes a window.
        win = Gtk.Window(title=f"Sign in — {res['title']}", transient_for=self.win)
        win.set_default_size(520, 640)
        win.set_child(wv)
        holder = {"done": False, "shown": False}

        def finish(redirect_uri):
            if holder["done"]:
                return
            holder["done"] = True
            try:
                os.write(master, (redirect_uri + "\n").encode())
            except OSError:
                pass
            win.destroy()

        def on_decide(view, decision, dtype):
            if dtype == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
                uri = decision.get_navigation_action().get_request().get_uri()
                if "nativeclient" in uri and "code=" in uri:
                    decision.ignore()
                    finish(uri)
                    return True
            return False

        def on_destroy(*_):
            # Window closed before it resolved (user cancelled): send a blank
            # line so FreeRDP aborts this auth instead of blocking on stdin.
            if not holder["done"]:
                holder["done"] = True
                try:
                    os.write(master, b"\n")
                except OSError:
                    pass

        wv.connect("create", lambda *_a: None)     # block popups (no ghost windows)
        wv.connect("decide-policy", on_decide)
        win.connect("close-request", lambda w: (w.destroy(), True)[1])
        win.connect("destroy", on_destroy)
        wv.load_uri(url)

        def show_if_pending():
            if not holder["done"] and not holder["shown"]:
                holder["shown"] = True
                self._set_status(f"Signing in to {res['title']}…")
                win.present()
            return False
        GLib.timeout_add(3000, show_if_pending)
        return False

    # ---- per-resource settings -------------------------------------------
    def _eff(self, key, res):
        """Resolve a setting: per-resource value → global default → None(=auto)."""
        rv = self._settings.get(res["id"], {}).get(key, "auto")
        if rv and rv != "auto":
            return rv
        dv = self._settings.get("_default", {}).get(key, "auto")
        if dv and dv != "auto":
            return dv
        return None

    def _eff_extra(self, res):
        parts = [self._settings.get("_default", {}).get("extra_args", ""),
                 self._settings.get(res["id"], {}).get("extra_args", ""),
                 os.environ.get("AVD_EXTRA_ARGS", "")]
        return " ".join(p.strip() for p in parts if p and p.strip()).strip()

    def _form_row(self, label, widget):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.set_hexpand(True)
        widget.set_halign(Gtk.Align.END)
        row.append(lbl)
        row.append(widget)
        return row

    def _open_settings(self, res):
        key = res["id"] if res else "_default"
        cur = self._settings.get(key, {})
        title = f"Settings — {res['title']}" if res else "Default connection settings"
        auto_note = ("Automatic uses your Default settings, then the display."
                     if res else "Automatic matches your display and monitors.")

        dlg = Gtk.Window(title=title, transient_for=self.win, modal=True)
        dlg.set_default_size(440, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(16)

        scale_dd = Gtk.DropDown(model=Gtk.StringList.new(SCALE_LABELS))
        sv = cur.get("scale", "auto")
        scale_dd.set_selected(SCALE_VALUES.index(sv) if sv in SCALE_VALUES else 0)
        box.append(self._form_row("Display scale", scale_dd))

        mm_dd = Gtk.DropDown(model=Gtk.StringList.new(MULTIMON_LABELS))
        mv = cur.get("multimon", "auto")
        mm_dd.set_selected(MULTIMON_VALUES.index(mv) if mv in MULTIMON_VALUES else 0)
        box.append(self._form_row("Monitors", mm_dd))

        extra_entry = Gtk.Entry()
        extra_entry.set_text(cur.get("extra_args", ""))
        extra_entry.set_placeholder_text("advanced: e.g. /gfx /network:auto")
        extra_entry.set_hexpand(True)
        box.append(self._form_row("Advanced flags", extra_entry))

        note = Gtk.Label(label=auto_note + " Applies to your next connection.",
                         xalign=0, wrap=True)
        note.add_css_class("tile-sub")
        box.append(note)

        btnbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btnbox.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: dlg.destroy())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")

        def do_save(*_):
            entry = {
                "scale": SCALE_VALUES[scale_dd.get_selected()],
                "multimon": MULTIMON_VALUES[mm_dd.get_selected()],
                "extra_args": extra_entry.get_text().strip(),
            }
            # drop an all-default entry so the file stays tidy
            if entry["scale"] == "auto" and entry["multimon"] == "auto" \
                    and not entry["extra_args"]:
                self._settings.pop(key, None)
            else:
                self._settings[key] = entry
            _save_settings(self._settings)
            dlg.destroy()
            self._set_status("Settings saved — applies to your next connection")
        save.connect("clicked", do_save)
        btnbox.append(cancel)
        btnbox.append(save)
        box.append(btnbox)
        dlg.set_child(box)
        dlg.present()

    def _error(self, msg):
        def show():
            d = Gtk.AlertDialog()
            d.set_modal(True)
            d.set_message(msg)
            d.show(self.win)
        GLib.idle_add(show)


if __name__ == "__main__":
    app = AvdApp()
    sys.exit(app.run(None))
