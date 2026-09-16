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
  <img src="screenshots/workspaces.png" width="720" alt="Workspace grid">
  <br>
  <em>Your Azure Virtual Desktop workspaces, discovered automatically after sign-in.</em>
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
`flatpak update`. The GNOME 49 runtime is pulled from Flathub automatically.

**Alternative — single-file bundle** from the
[latest release](https://github.com/shakeelosmani/avd_feed_connect/releases/latest):

```bash
flatpak install --user avd_feed_connect.flatpak
```

*(A Flathub listing is planned; until then, use either method above.)*

### Build it yourself

```bash
flatpak install flathub org.gnome.Platform//49 org.gnome.Sdk//49 org.flatpak.Builder
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
  **Monitors** (single / all / automatic), and any **Advanced flags**. These are
  remembered per resource, so a RemoteApp and a full Desktop can differ.
- **Defaults for everything:** the **⋯ menu → Default settings…** sets the
  fallback used by any workspace left on "Automatic".

Precedence is: a workspace's own setting → your Default settings → automatic
detection. (Environment variables, below, override even these — for scripting.)

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

## Status

Working: feed discovery, keyring-backed sign-in with click-through re-auth,
the workspace grid, silent connect, camera/mic/multi-monitor, and self-updating
Flatpak install. Contributions welcome.

## License

MIT for this app's code. Bundled components keep their own licenses (FreeRDP:
Apache-2.0, SDL: Zlib).
