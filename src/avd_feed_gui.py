#!/usr/bin/env python3
"""Windows-App-style desktop client for Azure Virtual Desktop on Linux.

A GTK3 + WebKit2 front-end over the feed-discovery logic in avd-feed.py:
  * in-app interactive sign-in (embedded WebKit view — the same auth-code+PKCE
    flow the native client uses, so Conditional Access lets it through; it
    catches the …/oauth2/nativeclient?code=… redirect automatically, no paste)
  * a tiled workspace grid with the real per-resource icons from the feed
  * double-click / Enter a tile to connect via the existing sdl-freerdp
  * refresh + sign-out, and a system-tray icon (show / quit)

The connection itself is still sdl-freerdp with /gateway:type:arm /sec:aad, so
camera/mic/gfx behave exactly as they do today. Nothing here touches the
hand-made .rdpw launchers.

Deps (already present here): PyGObject with Gtk 3.0, WebKit2 4.1, GdkPixbuf,
and AyatanaAppIndicator3 (tray, optional).
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
import subprocess

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk  # noqa: E402
from gi.repository import WebKit2  # noqa: E402

# shared feed/auth logic lives next to this file (installed together)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avdfeed as af  # noqa: E402

APP_ID = "io.github.shakeelosmani.avd_feed_connect"
APP_NAME = "AVD Feed + Connect Linux"
APP_ICON = APP_ID  # icon installed under the app-id name (Flatpak convention)


def _bearer_bytes(url, token):
    """Binary GET with the approved UA headers (for icons; af._get decodes text)."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "*/*")
    req.add_header("User-Agent", af.MS_USER_AGENT)
    req.add_header("X-MS-User-Agent", af.MS_USER_AGENT)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


CSS = b"""
.tile { padding: 10px; border-radius: 10px; }
.tile:hover { background: alpha(@theme_fg_color, 0.08); }
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
        self.indicator = None

    # ---- app lifecycle ----------------------------------------------------
    def do_activate(self):
        if self.win:
            self.win.present()
            return
        self._build_ui()
        self._install_tray()
        self.win.show_all()
        # try a silent sign-in first; fall back to the sign-in page
        self._set_status("Signing in…")
        threading.Thread(target=self._silent_signin, daemon=True).start()

    def _build_ui(self):
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        Gtk.Window.set_default_icon_name(APP_ICON)
        self.win = Gtk.ApplicationWindow(application=self, title=APP_NAME)
        self.win.set_default_size(760, 560)
        try:
            self.win.set_icon_name(APP_ICON)
        except Exception:
            pass

        hb = Gtk.HeaderBar(show_close_button=True, title=APP_NAME)
        self.win.set_titlebar(hb)
        self.refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic",
                                                         Gtk.IconSize.BUTTON)
        self.refresh_btn.set_tooltip_text("Refresh workspaces")
        self.refresh_btn.connect("clicked", lambda *_: self._reload_feed())
        hb.pack_start(self.refresh_btn)

        menu_btn = Gtk.MenuButton()
        menu_btn.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic",
                                                        Gtk.IconSize.BUTTON))
        menu = Gtk.Menu()
        mi_out = Gtk.MenuItem(label="Sign out")
        mi_out.connect("activate", lambda *_: self._sign_out())
        menu.append(mi_out)
        menu.show_all()
        menu_btn.set_popup(menu)
        hb.pack_end(menu_btn)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.win.add(self.stack)

        # sign-in page
        signin = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        signin.set_valign(Gtk.Align.CENTER)
        lbl = Gtk.Label(label="Sign in to see your workspaces")
        btn = Gtk.Button(label="Sign in")
        btn.get_style_context().add_class("suggested-action")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self._interactive_signin())
        signin.pack_start(lbl, False, False, 0)
        signin.pack_start(btn, False, False, 0)
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
        sw.add(self.grid)
        wp.pack_start(sw, True, True, 0)
        self.status = Gtk.Label(label="", xalign=0)
        self.status.get_style_context().add_class("status")
        wp.pack_start(self.status, False, False, 0)
        self.stack.add_named(wp, "workspaces")

        # busy page
        busy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        busy.set_valign(Gtk.Align.CENTER)
        sp = Gtk.Spinner(); sp.start()
        busy.pack_start(sp, False, False, 0)
        busy.pack_start(Gtk.Label(label="Loading…"), False, False, 0)
        self.stack.add_named(busy, "busy")
        self.stack.set_visible_child_name("busy")

    def _install_tray(self):
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except Exception:
            return
        ind = AppIndicator.Indicator.new(
            APP_ID, APP_ICON,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        theme_dir = os.path.expanduser("~/.local/share/icons/hicolor")
        if os.path.isdir(theme_dir):
            try:
                ind.set_icon_theme_path(theme_dir)
            except Exception:
                pass
        ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        ind.set_title(APP_NAME)
        m = Gtk.Menu()
        mi_show = Gtk.MenuItem(label="Show workspaces")
        mi_show.connect("activate", lambda *_: self.win.present())
        mi_quit = Gtk.MenuItem(label="Quit")
        mi_quit.connect("activate", lambda *_: self.quit())
        m.append(mi_show); m.append(mi_quit); m.show_all()
        ind.set_menu(m)
        self.indicator = ind

    # ---- helpers (main thread) --------------------------------------------
    def _set_status(self, text):
        GLib.idle_add(lambda: self.status.set_text(text) if self.status else None)

    def _show(self, name):
        GLib.idle_add(lambda: self.stack.set_visible_child_name(name))

    # ---- auth / token lifecycle ------------------------------------------
    def _apply_token(self, tok):
        """Store a freshly minted token, persist the (rotated) refresh token,
        and schedule a silent refresh before it expires."""
        with self._token_lock:
            self.token = tok["access_token"]
            self._deadline = time.monotonic() + int(tok.get("expires_in", 3600))
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
        wv = WebKit2.WebView()
        dlg.add(wv)

        def on_decide(view, decision, dtype):
            if dtype != WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
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
        dlg.show_all()

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
        try:
            if os.path.exists(af.CACHE):
                os.remove(af.CACHE)
        except Exception:
            pass
        with self._token_lock:
            self.token = None
            self._deadline = 0.0
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0
        for c in self.grid.get_children():
            self.grid.remove(c)
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
        if not self._ensure_token():
            return
        try:
            resources = af.enumerate_feed(self.token)
        except SystemExit as e:
            self._error(str(e)); self._show("signin"); return
        except Exception as e:
            self._error("Feed error: " + str(e)); self._show("signin"); return
        GLib.idle_add(self._populate, resources)

    def _populate(self, resources):
        for c in self.grid.get_children():
            self.grid.remove(c)
        for res in resources:
            self.grid.add(self._make_tile(res))
        self.grid.show_all()
        self.status.set_text(
            f"{len(resources)} workspaces · signed in as {af.UPN}")
        self.stack.set_visible_child_name("workspaces")
        # fetch icons in the background
        threading.Thread(target=self._load_icons, args=(resources,),
                         daemon=True).start()

    def _make_tile(self, res):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.get_style_context().add_class("tile")
        img = Gtk.Image.new_from_icon_name(
            "computer" if res["type"] == "Desktop" else "application-x-executable",
            Gtk.IconSize.DIALOG)
        img.set_pixel_size(64)
        title = Gtk.Label(label=res["title"])
        title.get_style_context().add_class("tile-title")
        title.set_line_wrap(True); title.set_justify(Gtk.Justification.CENTER)
        title.set_max_width_chars(16)
        sub = Gtk.Label(label=res["type"])
        sub.get_style_context().add_class("tile-sub")
        state = Gtk.Label(label="")
        state.get_style_context().add_class("tile-state")
        state.set_no_show_all(True)  # stays hidden until there's a state
        box.pack_start(img, False, False, 0)
        box.pack_start(title, False, False, 0)
        box.pack_start(sub, False, False, 0)
        box.pack_start(state, False, False, 0)
        child = Gtk.FlowBoxChild()
        child.add(box)
        child._res = res
        child._img = img
        child._state = state
        child._proc = None
        child.set_tooltip_text(f"{res['title']} — {res['tenant']}")
        return child

    def _child_for(self, res_id):
        for c in self.grid.get_children():
            if getattr(c, "_res", {}).get("id") == res_id:
                return c
        return None

    def _set_tile_state(self, res_id, text, css):
        def apply():
            ch = self._child_for(res_id)
            if not ch:
                return
            ctx = ch._state.get_style_context()
            for cls in ("state-connecting", "state-connected", "state-ended"):
                ctx.remove_class(cls)
            if text:
                if css:
                    ctx.add_class(css)
                ch._state.set_text(text)
                ch._state.show()
            else:
                ch._state.hide()
        GLib.idle_add(apply)

    def _load_icons(self, resources):
        # map id -> child for updating on the main thread
        children = {c._res["id"]: c for c in self.grid.get_children()}
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
            except Exception:
                continue
            ch = children.get(res["id"])
            if ch:
                GLib.idle_add(ch._img.set_from_pixbuf, pb)

    # ---- launch + live connection state -----------------------------------
    def _on_tile_activated(self, flowbox, child):
        res = child._res
        if child._proc and child._proc.poll() is None:
            # already connected/connecting — just note it, don't double-launch
            self.status.set_text(f"{res['title']} is already open")
            return
        self._set_tile_state(res["id"], "● Connecting…", "state-connecting")
        self.status.set_text(f"Connecting to {res['title']}…")
        threading.Thread(target=self._launch, args=(child, res), daemon=True).start()

    def _launch(self, child, res):
        if not self._ensure_token():
            self._set_tile_state(res["id"], "", None)
            return
        try:
            path = af.download_rdp(self.token, res)
        except SystemExit as e:
            self._error(str(e))
            self._set_tile_state(res["id"], "● Failed", "state-ended")
            return
        env = dict(os.environ)
        if af.SDL_LIBS and os.path.isdir(af.SDL_LIBS):
            env["LD_LIBRARY_PATH"] = af.SDL_LIBS + (
                os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        os.makedirs(af.OUT, exist_ok=True)
        safe = _re.sub(r"[^A-Za-z0-9]+", "_", res["title"])[:40]
        logpath = os.path.join(af.OUT, f"session_{safe}.log")
        argv = [af.SDL, path, "/gateway:type:arm", "/sec:aad", f"/u:{af.UPN}",
                "/sound:sys:pulse", "/microphone", "/cert:ignore",
                "/f", "/scale-desktop:200", "-multimon", "/log-level:info"]
        with open(logpath, "w") as log:
            proc = subprocess.Popen(argv, env=env, stdout=log,
                                    stderr=subprocess.STDOUT)
        child._proc = proc
        self._set_status(f"Launched {res['title']}")
        self._watch_session(res, proc, logpath)

    def _watch_session(self, res, proc, logpath):
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
        # process exited
        self._set_tile_state(res["id"], "○ Disconnected", "state-ended")
        self._set_status(f"{res['title']} session ended")
        # clear the label after a short while
        GLib.timeout_add_seconds(
            6, lambda: (self._set_tile_state(res["id"], "", None), False)[1])

    def _error(self, msg):
        def show():
            d = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                  message_type=Gtk.MessageType.ERROR,
                                  buttons=Gtk.ButtonsType.OK, text=msg)
            d.run(); d.destroy()
        GLib.idle_add(show)


if __name__ == "__main__":
    app = AvdApp()
    sys.exit(app.run(None))
