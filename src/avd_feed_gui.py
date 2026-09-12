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
  * persistent session (cached workspaces) + silent token refresh + sign out

The connection itself is still sdl-freerdp with /gateway:type:arm /sec:aad, so
camera/mic/gfx behave exactly as before.

Runtime: PyGObject with Gtk 4.0, WebKit 6.0, GdkPixbuf (all in the GNOME 49
Flatpak runtime). No system-tray (GTK4 has no in-process tray; the GTK3
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
import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk  # noqa: E402
from gi.repository import WebKit  # noqa: E402

# shared feed/auth logic lives next to this file (installed together)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avdfeed as af  # noqa: E402

APP_ID = "io.github.shakeelosmani.avd_feed_connect"
APP_NAME = "AVD Feed + Connect Linux"

# Persist the last discovered workspaces so reopening shows them instantly
# (like the Windows App), instead of bouncing to sign-in on every launch.
WS_CACHE = os.path.join(os.path.dirname(af.CACHE), "workspaces.json")


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


CSS = """
.tile { padding: 10px; border-radius: 10px; }
.tile:hover { background: rgba(128,128,128,0.15); }
.tile-title { font-weight: 600; margin-top: 6px; }
.tile-sub { font-size: 90%; opacity: 0.6; }
.status { padding: 6px 10px; opacity: 0.7; font-size: 90%; }
.tile-state { font-size: 85%; margin-top: 2px; }
.state-connecting { color: #e08a00; }
.state-connected { color: #2ea043; font-weight: 600; }
.state-ended { opacity: 0.5; }
"""


class AvdApp(Gtk.Application):
    def __init__(self):
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

    # ---- app lifecycle ----------------------------------------------------
    def do_activate(self):
        if self.win:
            self.win.present()
            return
        self._build_ui()
        self.win.present()
        # Client display scale (1 = standard, 2 = HiDPI) → drives the remote
        # desktop scale so text isn't tiny on HiDPI nor huge on standard/ultrawide.
        try:
            self._scale = self.win.get_scale_factor() or 1
        except Exception:
            self._scale = 1
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

    def _background_refresh(self):
        """Silently refresh the token and re-fetch the feed, keeping the cached
        workspaces on screen if it fails (never auto-bounces to sign-in)."""
        if not self._do_refresh(silent=True):
            self._set_status("Showing saved workspaces · sign in again to refresh")
            return
        try:
            resources = af.enumerate_feed(self.token)
        except Exception:
            self._set_status("Showing saved workspaces (couldn't refresh)")
            return
        _save_ws_cache(resources)
        GLib.idle_add(self._populate, resources)

    def _build_ui(self):
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win = Gtk.ApplicationWindow(application=self, title=APP_NAME)
        self.win.set_default_size(760, 560)

        hb = Gtk.HeaderBar()
        hb.set_title_widget(Gtk.Label(label=APP_NAME))
        self.win.set_titlebar(hb)

        self.refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_btn.set_tooltip_text("Refresh workspaces")
        self.refresh_btn.connect("clicked", lambda *_: self._reload_feed())
        hb.pack_start(self.refresh_btn)

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        pop = Gtk.Popover()
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pbox.set_margin_top(6); pbox.set_margin_bottom(6)
        pbox.set_margin_start(6); pbox.set_margin_end(6)
        signout = Gtk.Button(label="Sign out")
        signout.add_css_class("flat")
        signout.set_sensitive(False)  # nothing to sign out of until signed in
        signout.connect("clicked", lambda *_: (pop.popdown(), self._sign_out()))
        self._signout_item = signout
        pbox.append(signout)
        pop.set_child(pbox)
        menu_btn.set_popover(pop)
        hb.pack_end(menu_btn)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.win.set_child(self.stack)

        # sign-in page
        signin = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        signin.set_valign(Gtk.Align.CENTER)
        lbl = Gtk.Label(label="Sign in to see your workspaces")
        btn = Gtk.Button(label="Sign in")
        btn.add_css_class("suggested-action")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self._interactive_signin())
        signin.append(lbl)
        signin.append(btn)
        self.stack.add_named(signin, "signin")

        # workspaces page
        wp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        self.grid = Gtk.FlowBox(valign=Gtk.Align.START, max_children_per_line=5,
                                min_children_per_line=2, row_spacing=8,
                                column_spacing=8, homogeneous=True,
                                selection_mode=Gtk.SelectionMode.NONE)
        self.grid.set_margin_top(12); self.grid.set_margin_bottom(12)
        self.grid.set_margin_start(12); self.grid.set_margin_end(12)
        self.grid.connect("child-activated", self._on_tile_activated)
        sw.set_child(self.grid)
        wp.append(sw)
        self.status = Gtk.Label(label="", xalign=0)
        self.status.add_css_class("status")
        wp.append(self.status)
        self.stack.add_named(wp, "workspaces")

        # busy page
        busy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        busy.set_valign(Gtk.Align.CENTER)
        sp = Gtk.Spinner(); sp.start()
        busy.append(sp)
        busy.append(Gtk.Label(label="Loading…"))
        self.stack.add_named(busy, "busy")
        self.stack.set_visible_child_name("busy")

    # ---- helpers (main thread) --------------------------------------------
    def _set_status(self, text):
        GLib.idle_add(lambda: self.status.set_text(text) if self.status else None)

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
            af._save_cache(rt)
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
        try:
            rt = json.load(open(af.CACHE))["refresh_token"]
        except Exception:
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
        if os.path.exists(af.CACHE) and self._do_refresh(silent=True):
            self._load_feed_bg()
            return
        self._show("signin")

    def _interactive_signin(self):
        verifier = af._b64url(secrets.token_bytes(64))
        challenge = af._b64url(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(16)
        params = {
            "client_id": af.CLIENT_ID, "response_type": "code",
            "redirect_uri": af.REDIRECT, "scope": af.SCOPE,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "state": state, "prompt": "select_account",
        }
        if af.UPN:
            params["login_hint"] = af.UPN
        url = af.LOGIN + "/authorize?" + urllib.parse.urlencode(params)

        dlg = Gtk.Window(title="Sign in", transient_for=self.win, modal=True)
        dlg.set_default_size(520, 640)
        wv = WebKit.WebView()
        dlg.set_child(wv)

        def on_decide(view, decision, dtype):
            if dtype != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
                return False
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(af.REDIRECT) and "code=" in uri:
                decision.ignore()
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
                dlg.destroy()
                if qs.get("state", [state])[0] != state:
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
            self._error("Sign-in failed: " + tok.get("error_description", tok.get("error", "?")))
            self._show("signin")
            return
        self._apply_token(tok)
        self._load_feed_bg()

    def _sign_out(self):
        # Manual sign-out is the ONLY thing that clears the session + workspaces.
        for p in (af.CACHE, WS_CACHE):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
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
        self.status.set_text(f"{len(resources)} workspaces · signed in as {who}")
        self.stack.set_visible_child_name("workspaces")
        if self._signout_item is not None:
            self._signout_item.set_sensitive(True)
        # fetch icons in the background (needs a token; skipped for the cached
        # view before the silent refresh completes — icons fill in on refresh)
        if self.token:
            threading.Thread(target=self._load_icons, args=(resources,),
                             daemon=True).start()

    def _make_tile(self, res):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("tile")
        img = Gtk.Image.new_from_icon_name(
            "computer" if res["type"] == "Desktop" else "application-x-executable")
        img.set_pixel_size(64)
        title = Gtk.Label(label=res["title"])
        title.add_css_class("tile-title")
        title.set_wrap(True); title.set_justify(Gtk.Justification.CENTER)
        title.set_max_width_chars(16)
        sub = Gtk.Label(label=res["type"])
        sub.add_css_class("tile-sub")
        state = Gtk.Label(label="")
        state.add_css_class("tile-state")
        state.set_visible(False)  # shown only when there's a state
        box.append(img)
        box.append(title)
        box.append(sub)
        box.append(state)
        child = Gtk.FlowBoxChild()
        child.set_child(box)
        child._res = res
        child._img = img
        child._state = state
        child._proc = None
        child._launching = False
        child.set_tooltip_text(f"{res['title']} — {res['tenant']}")
        return child

    def _child_for(self, res_id):
        for c in self._tiles:
            if getattr(c, "_res", {}).get("id") == res_id:
                return c
        return None

    def _set_tile_state(self, res_id, text, css):
        def apply():
            ch = self._child_for(res_id)
            if not ch:
                return
            for cls in ("state-connecting", "state-connected", "state-ended"):
                ch._state.remove_css_class(cls)
            if text:
                if css:
                    ch._state.add_css_class(css)
                ch._state.set_text(text)
                ch._state.set_visible(True)
            else:
                ch._state.set_visible(False)
        GLib.idle_add(apply)

    def _load_icons(self, resources):
        children = {c._res["id"]: c for c in self._tiles}
        for res in resources:
            url = res.get("icon32")
            if not url:
                continue
            try:
                data = _bearer_bytes(url, self.token)
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(data); loader.close()
                pb = loader.get_pixbuf().scale_simple(64, 64,
                                                      GdkPixbuf.InterpType.BILINEAR)
                tex = Gdk.Texture.new_for_pixbuf(pb)
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
        threading.Thread(target=self._launch, args=(child, res), daemon=True).start()

    def _launch(self, child, res):
        if not self._ensure_token():
            # Session lapsed and can't refresh silently — re-auth on demand
            # (like the Windows App when you click a workspace after a while),
            # keeping the workspace list intact.
            child._launching = False
            self._set_tile_state(res["id"], "", None)
            self._set_status("Sign in to connect")
            GLib.idle_add(self._interactive_signin)
            return
        try:
            path = af.download_rdp(self.token, res)
        except (SystemExit, Exception) as e:  # never let a write/HTTP error hang the tile
            child._launching = False
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
        scale = os.environ.get("AVD_SCALE") or str(100 * max(1, self._scale))
        extra = os.environ.get("AVD_EXTRA_ARGS", "").strip()
        argv += ["/sound:sys:pulse", "/microphone", "/cert:ignore",
                 "/f", f"/scale-desktop:{scale}", "/log-level:info"]
        # Default to a single fullscreen monitor — otherwise a multi-head remote
        # renders as a doubled/stacked desktop crammed into one screen. Opt into
        # multi-monitor with AVD_EXTRA_ARGS="/multimon" (then we don't force it off).
        if "/multimon" not in extra:
            argv.append("-multimon")
        if extra:
            try:
                argv += shlex.split(extra)
            except ValueError:
                pass
        with open(logpath, "w") as log:
            proc = subprocess.Popen(argv, env=env, stdout=log,
                                    stderr=subprocess.STDOUT)
        child._proc = proc
        self._set_status(f"Launched {res['title']}")
        self._watch_session(child, res, proc, logpath)

    def _watch_session(self, child, res, proc, logpath):
        """Flip the tile to 'Connected' once the session logs on, then back to
        idle when sdl-freerdp exits."""
        connected = False
        while proc.poll() is None:
            if not connected:
                try:
                    with open(logpath, "r", errors="replace") as f:
                        blob = f.read()
                    # "Logon Info V2" is logged once the AVD session logs on
                    if "Logon Info" in blob:
                        connected = True
                        self._set_tile_state(res["id"], "● Connected",
                                             "state-connected")
                        self._set_status(f"Connected to {res['title']}")
                except OSError:
                    pass
            time.sleep(1.0)
        # process exited — allow relaunch
        child._launching = False
        self._set_tile_state(res["id"], "○ Disconnected", "state-ended")
        self._set_status(f"{res['title']} session ended")
        # clear the label after a short while
        GLib.timeout_add_seconds(
            6, lambda: (self._set_tile_state(res["id"], "", None), False)[1])

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
