<p align="center">
  <img src="assets/logo.png" width="120" alt="AVD Feed + Connect logo">
</p>

<h1 align="center">AVD Feed + Connect (Linux)</h1>

<p align="center">
A native Linux client for <b>Azure Virtual Desktop</b> and <b>Windows 365</b> —
sign in, see the desktops and remote apps you're entitled to (just like
Microsoft's Windows App), and connect over RDP.
</p>

> Unofficial. Not affiliated with or endorsed by Microsoft.

## Screenshots

<p align="center">
  <img src="screenshots/workspaces.png" width="49%" alt="Workspace grid — light">
  <img src="screenshots/workspaces-dark.png" width="49%" alt="Workspace grid — dark">
  <br>
  <em>Your Azure Virtual Desktop workspaces after sign-in — light and dark
  (switchable in-app, or follow the system). Example data shown.</em>
</p>

## Why

Linux has FreeRDP, but no client that does **feed discovery** — the step where
you sign in and the client lists your AVD workspaces instead of you
hand-authoring an `.rdp`/`.rdpw` file per host pool. This app adds that, and
drives a bundled, hardened FreeRDP for the actual connection.

## How it works

1. **Interactive Entra ID sign-in** (auth-code + PKCE, in an embedded WebKit
   view). This is the same flow the official clients use, so Conditional Access
   policies that block the device-code flow are honored.
2. **Feed discovery** against `rdweb.wvd.microsoft.com/api/arm/feeddiscovery`
   (sending the approved `X-MS-User-Agent` the service requires).
3. **Workspace grid** with the real per-resource icons; double-click to connect.
4. **Connect** by downloading the resource's `.rdp` from the feed and launching
   the bundled `sdl-freerdp` with `/gateway:type:arm /sec:aad`. The
   connection's Entra token is obtained **silently** in the background (see
   below) — no second sign-in window unless your org requires MFA at connect.

Token refresh is the standard OAuth2 `refresh_token` grant (offline_access);
the app renews the access token silently before it expires. The refresh token
is stored in your **login keyring** (via libsecret), encrypted at rest — not in
a plaintext file. The embedded sign-in keeps your Microsoft SSO session between
launches, so re-authenticating (for example when a Conditional Access policy
expires your token) is usually a single click rather than a full password+MFA.

## Silent connection sign-in

FreeRDP's `/sec:aad` needs an Entra token at connect time. Rather than let
FreeRDP open its **own** second browser window for that, this app builds
FreeRDP without a webview and drives it over a pseudo-terminal: it intercepts
FreeRDP's `Browse to:` prompt, resolves the URL in a hidden WebKit view that
**shares the sign-in SSO session**, and hands the result back. So the
connection token is acquired with no visible prompt; a window only appears if
MFA/consent is genuinely required (after a short delay). This also removes a
whole class of blank/second-window bugs the bundled FreeRDP webview could hit.

## Tray icon

The app puts an icon in the system tray. Right-click it for every resource,
grouped by workspace (`●` = connected), each with a submenu:
**Connect**, **Connect (windowed)** (a resizable window instead of full
screen), or — for a running session — **Focus window** and **Disconnect**.
Left-click shows the main window. **Quit** from the tray menu exits.

When you connect from the tray with the app hidden, there's a short quiet gap:
after the Microsoft sign-in window (if one is needed) closes, the connection
token is fetched in the background and the session starts with nothing on
screen yet — the app stays in the tray and no window is shown until the remote
desktop itself appears. That pause is normal; it isn't stuck.

Two options in the account menu, under **System tray** (both off by default,
so closing the window still quits the app):

- **Keep running in the tray when closed** — the window's close button hides it
  to the tray; sessions and the sign-in refresh keep running.
- **Start in the tray** — with a saved session, the app starts with only the
  tray icon (handy for autostart). A first run still shows sign-in.

The tray works wherever the desktop hosts StatusNotifierItem icons: KDE
Plasma, Xfce, Cinnamon, MATE, LXQt, Budgie, and wlroots/Hyprland bars with a
tray module (e.g. Waybar's `tray`). Stock GNOME needs the *AppIndicator and
KStatusNotifierItem Support* extension (Ubuntu ships it). Without a tray host
the options are greyed out, closing the window quits, and the app always starts
with its window. **Focus window** is a request to the window
manager; on Hyprland it needs `misc:focus_on_activate = true`. Set
`AVD_TRAY=0` to turn the tray off.

## The bundled FreeRDP

The Flatpak builds FreeRDP from upstream `master` with **camera redirection**
(`CHANNEL_RDPECAM_CLIENT`) enabled, plus microphone and multi-monitor.
Automatic reconnect and a gateway keepalive are on by default so brief network
blips and idle timeouts don't drop the session.

It also includes the **PulseAudio hot-unplug + rdpsnd busy-loop fixes**
([FreeRDP#13334](https://github.com/FreeRDP/FreeRDP/pull/13334), now merged
upstream) — without them, changing the audio device mid-call (e.g. plugging
headphones during a Teams call) **freezes the whole session**.

## Install

[![Latest release](https://img.shields.io/github/v/release/shakeelosmani/avd_feed_connect)](https://github.com/shakeelosmani/avd_feed_connect/releases/latest)

**Easiest — one click / one command** (from the signed repo on GitHub Pages):

👉 **[Install (avd_feed_connect.flatpakref)](https://shakeelosmani.github.io/avd_feed_connect/avd_feed_connect.flatpakref)**

Opening that file installs the app through GNOME Software / your Flatpak handler.
Or from a terminal:

```bash
flatpak install --user https://shakeelosmani.github.io/avd_feed_connect/avd_feed_connect.flatpakref
flatpak run io.github.shakeelosmani.avd_feed_connect
```

This adds a small signed remote so the app also **updates** with
`flatpak update`. The GNOME 51 runtime is pulled from Flathub automatically.

**Alternative — single-file bundle** from the
[latest release](https://github.com/shakeelosmani/avd_feed_connect/releases/latest):

```bash
flatpak install --user avd_feed_connect.flatpak
```

*(A Flathub listing is planned; until then, use either method above.)*

### Build it yourself

```bash
flatpak install flathub org.gnome.Platform//51 org.gnome.Sdk//51 org.flatpak.Builder
flatpak run org.flatpak.Builder --user --install --force-clean build-dir \
  io.github.shakeelosmani.avd_feed_connect.yml
flatpak run io.github.shakeelosmani.avd_feed_connect
```

## Run unpackaged (development)

The same code runs without Flatpak if you have PyGObject (Gtk 4.0, WebKit 6.0)
and an SDL3 `sdl-freerdp` on `PATH` (or at `~/opt/freerdp-sdl3-cam/bin/`):

```bash
python3 src/avd_feed_gui.py           # GUI
python3 src/avdfeed.py list           # CLI: list workspaces
python3 src/avdfeed.py connect 0      # CLI: connect to resource 0
```

Override the tenant/account with `AVD_TENANT` / `AVD_UPN`, and the client binary
with `AVD_SDL_FREERDP`.

> **Use SDL 3.2.x, not a newer system SDL3.** The bundled FreeRDP is built
> against SDL 3.2.30. Running a locally built `sdl-freerdp` against a newer
> distro SDL3 (e.g. Arch's 3.4.x) can make every fullscreen (`/f`) launch fail
> with `Monitor configuration virtual desktop width must be 200 <= 0 <= 32766`
> — SDL reports a 0×0 display. Build SDL 3.2.30 and install it where the app
> looks (`~/opt/sdl3`, or point `AVD_SDL_LIBS` at its `lib` dir):
>
> ```bash
> git clone --depth 1 --branch release-3.2.30 https://github.com/libsdl-org/SDL.git
> cmake -S SDL -B SDL/build -G Ninja -DCMAKE_BUILD_TYPE=Release \
>   -DSDL_STATIC=OFF -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF \
>   -DCMAKE_INSTALL_PREFIX=$HOME/opt/sdl3
> cmake --build SDL/build && cmake --install SDL/build
> ```
>
> The app adds `AVD_SDL_LIBS` (default `~/opt/sdl3/lib`) to `LD_LIBRARY_PATH` for
> the client when that directory exists, so it is picked up automatically.

### Connection tuning

The session **auto-adapts to your machine** — it reads your display layout from
the compositor and sets the remote accordingly:

- **Scale** follows your display's HiDPI factor — HiDPI (2×) → `200%`,
  standard/ultrawide (1×) → `100%` — so text isn't tiny or huge.
- **Multi-monitor** follows your actual monitor count — one monitor → single
  fullscreen; two or more → the remote spans them (`/multimon`).

**Most people never need to touch this** — it auto-adapts. If you do want to
override it, the easiest way is right inside the app.

#### In-app settings (no terminal, remembered per workspace)

- **Per workspace:** right-click a workspace tile → set its **Display scale**,
  **Monitors** (single / all / automatic), **Server certificate**, **Client shortcuts**, and any
  **Advanced flags**. These are remembered per resource, so a RemoteApp and a
  full Desktop can differ.
- **Defaults for everything:** the **⋯ menu → Default settings…** sets the
  fallback used by any workspace left on "Automatic".

Precedence is: a workspace's own setting → your Default settings → automatic
detection. (Environment variables, below, override even these — for scripting.)

**Server certificate (verify / don't verify).** By default the app **verifies
the session host's certificate**, pinning it on first connect (FreeRDP's
trust-on-first-use) and refusing the session if that certificate later changes —
protection against a man-in-the-middle. **If you can't connect** — a
"certificate changed" / verification error, common on **pooled** host pools
whose session hosts present different certificates each time, or after a host is
rebuilt — set **Server certificate → Don't verify** for that workspace (or in
Default settings). That restores the old behaviour of accepting any certificate.
Leave it on **Verify** whenever you can; only turn it off if it's actually
blocking you. (Scripting override: `AVD_CERT=ignore` or `AVD_CERT=verify`.)

**Client shortcuts (Right Shift + key).** The bundled SDL client reserves
Right Shift + key for its own shortcuts (D disconnects, Enter toggles
fullscreen, R resizable, G keyboard grab, M minimize), so those keys never reach
the remote desktop — and if Right Shift is seen as held (e.g. a stuck modifier
under XWayland), an ordinary Ctrl+D can end the session. Set **Client
shortcuts → Disabled** to pass every key through. The choice is written to
FreeRDP's own `~/.config/freerdp/sdl-freerdp.json` (`SDL_KeyModMask`) on each
connect; **Automatic** leaves that file untouched.

**Resizable window (dynamic resolution):** by default the session opens
fullscreen. If you'd rather have a resizable window whose remote resolution
follows the window as you resize it, add `/dynamic-resolution` to that
workspace's **Advanced flags**. The session then starts windowed (toggle
fullscreen any time with Ctrl+Shift+Enter). Fullscreen and `/dynamic-resolution`
can't be combined, so the app only forces fullscreen when this flag isn't set;
it also clears the feed's smart-sizing property, which would otherwise conflict.
Dynamic resolution runs on a **single monitor** — when it's set, the app ignores
multi-monitor selection for that session (leave it off to span monitors).

**Advanced flags reference.** The **Advanced flags** field takes raw
`sdl-freerdp` options, appended verbatim to the connection. Anything FreeRDP
accepts works; these are the ones people reach for most:

| Flag | What it does |
|---|---|
| `/dynamic-resolution` | Open a resizable window; the remote resolution follows the window instead of a fixed fullscreen size (see above). |
| `/d:AzureAD` | Override the NLA logon domain (the app sends an empty domain by default; a few host pools want `AzureAD` or an on-prem domain instead). |
| `/multimon` / `-multimon` | Force multi-monitor on / off, overriding the automatic monitor-count choice. Same as the **Monitors** setting. |
| `/gfx` | Force the GFX (RemoteFX/H.264) graphics pipeline — smoother video on capable host pools. |
| `/network:auto` | Let FreeRDP auto-detect the link and tune codecs/latency for it. |
| `-themes` / `-wallpaper` | Drop remote desktop themes / wallpaper to save bandwidth on slow links. |

Flags set here take precedence over the app's automatic choices, and
`AVD_EXTRA_ARGS` (below) is appended after them for scripting.

**How to enter them.** Type the flags into the **Advanced flags** field exactly
as you would on a command line — **separated by spaces**, and you can combine as
many as you like. Flags that take a value use a colon, with no space around it.
Examples (each line is what you'd put in the field):

```
/dynamic-resolution
```
Resizable window, remote follows the window size.

```
/dynamic-resolution /gfx
```
Resizable window **and** the H.264 graphics pipeline — two flags, space-separated.

```
/d:AzureAD
```
Force the NLA logon domain to `AzureAD` (for a host pool that rejects the empty
default).

```
/dynamic-resolution /gfx -wallpaper -themes
```
A slow-link profile: resizable window, GFX codec, and strip the remote wallpaper
and themes to save bandwidth — four flags at once.

If a value ever contains a space, quote it like on a shell (e.g.
`/drive:home,"/my folder"`); the field is parsed with the same rules.

#### Advanced: environment-variable overrides

These are for power users / scripting and win over the in-app settings. Set them
graphically (no terminal) with [**Flatseal**](https://flathub.org/apps/com.github.tchx84.Flatseal):
install it from your software center (search "Flatseal"), pick **AVD Feed +
Connect Linux**, open the **Environment** section, and add a line like
`AVD_SCALE=150`. Or from a terminal:

```bash
flatpak override --user --env=AVD_SCALE=150 io.github.shakeelosmani.avd_feed_connect
```

Both methods write the same setting.

#### The variables

| Variable | Effect |
|---|---|
| `AVD_SCALE` | Force the remote scale percentage (`100`, `125`, `150`, `200`, …). Overrides the auto HiDPI detection. |
| `AVD_MULTIMON` | Force multi-monitor on (`1`/`on`) or off (`0`/`off`). Overrides the auto monitor-count detection. |
| `AVD_EXTRA_ARGS` | Extra `sdl-freerdp` flags appended verbatim, e.g. `"/gfx"`, `"/network:auto"`, or your own `/multimon` / `-multimon` (which then wins over the auto choice). |
| `AVD_SDL_FREERDP` | Path to the `sdl-freerdp` binary (defaults to the bundled one). |
| `AVD_TRAY` | `0`/`off` disables the tray icon (closing the window then always quits). |

Connections are launched over X11/XWayland (`GDK_BACKEND=x11`) because FreeRDP's
SDL client is unstable on native Wayland; this is automatic.

## Signing in — and why you might be asked again

The app keeps you signed in the way the Windows App does: your workspaces (and
their icons) are saved and shown instantly on launch, and the access token is
renewed silently in the background with the refresh token. Normally you only
see the sign-in page on first run or after **⋯ → Sign out**.

If you are instead asked to sign in **every time** you open the app or connect,
that is your organization's **Conditional Access sign-in frequency** policy,
not the app. The tell-tales are the status bar ("your organization requires
signing in again (Conditional Access sign-in frequency)") and, in a terminal,
`token refresh failed … AADSTS70043 … maximum allowed lifetime for this request
is 300` — Entra refuses to renew the Azure Virtual Desktop token unless you
signed in within the last *N* minutes (300 s is the "Every time" setting). No
client can renew silently under that rule; the official clients usually avoid
the prompt only because the policy excludes managed/compliant devices, which a
Linux machine typically isn't. Your admin can see which policy fired under
Entra → Sign-in logs.

The app makes that as painless as it can: the saved workspaces stay on screen,
and because your Microsoft SSO session is remembered between launches, the
re-auth is usually a **single click** on your account (no password re-entry)
rather than a full password+MFA — unless the policy is strict enough to demand
fresh credentials. The workspace you double-clicked then connects on its own
once you're back in.

## Host pools that use NLA (username + password)

Some AVD host pools aren't set up for **Microsoft Entra ID RDP authentication**
(their feed `.rdp` has no `enablerdsaadauth`). The app detects this and connects
with **NLA** instead of the token flow: it passes your account as the username
and an **empty domain**, and FreeRDP prompts you for your password each time you
connect (it isn't stored). Your Entra sign-in is still used for the feed and the
gateway — the password is only for the session-host logon.

- **Hybrid (Active Directory) joined** hosts work this way with your normal
  domain password.
- A **pure Entra-ID-joined** host that requires NLA **can't be reached from
  Linux** — that path (PKU2U) needs Windows-only components. The fix is to have
  your admin enable *Microsoft Entra ID authentication* on the host pool, after
  which sign-in is token-based with no password prompt.
- If your host pool needs a specific domain instead of the empty default, set it
  in a workspace's **Advanced flags** (e.g. `/d:AzureAD`).

## Microsoft Teams calls (no media optimization)

Teams **chat and file sharing work**, but Teams **calls and meetings are not
media-optimized** with this client. Microsoft's Teams optimization — the
*WebRTC Redirector* that hands call audio/video to the client over a dedicated
virtual channel — is only implemented in the official Windows App and Microsoft
Remote Desktop clients. FreeRDP has no such channel, so inside the session Teams
falls back to running call media **on the session host itself** (you won't see
the "AVD Media Optimized" banner).

That fallback only works if the **session host** can reach Teams' media relays
directly — in particular outbound **UDP 3478–3481** to the Teams/Microsoft 365
media IP ranges. On host pools where that egress is locked down, calls started
inside the session fail to connect (Teams logs a `MediaWhitelistingIssue` /
`no relays` ICE error) even though the same account places calls fine from the
Windows App, where media is redirected to the local device instead.

Your microphone and camera themselves still reach the session via normal device
redirection; this is specifically about Teams' optimized call path. Options if
you need calls: ask your admins to permit the session host's outbound UDP to the
Teams relays, or place the call from a Teams client outside the VM. Implementing
the redirector itself is a large, Microsoft-specific protocol effort and is out
of scope for now.

## Status

Working: feed discovery, keyring-backed sign-in with click-through re-auth,
the workspace grid, silent connect, camera/mic/multi-monitor, and self-updating
Flatpak install. Contributions welcome.

## License

MIT for this app's code. Bundled components keep their own licenses (FreeRDP:
Apache-2.0, SDL: Zlib).
