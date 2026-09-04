# AVD Feed + Connect (Linux)

A native Linux client for **Azure Virtual Desktop** and **Windows 365**. Sign in
with your work account, see the desktops and remote apps you're entitled to —
just like Microsoft's Windows App on Windows/macOS — and connect over RDP.

> Unofficial. Not affiliated with or endorsed by Microsoft.

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
   the bundled `sdl-freerdp` with `/gateway:type:arm /sec:aad`.

Token refresh is the standard OAuth2 `refresh_token` grant (offline_access);
the app renews the access token silently before it expires.

## The bundled FreeRDP matters

The Flatpak bundles a FreeRDP build with two things stock upstream lacks:

- **Pulse hot-unplug + rdpsnd busy-loop fixes** ([FreeRDP#13334](https://github.com/FreeRDP/FreeRDP/pull/13334)) —
  without these, changing the audio device mid-call (e.g. plugging headphones
  during a Teams call) **freezes the whole session**.
- **Camera redirection** (`CHANNEL_RDPECAM_CLIENT`), plus microphone and
  multi-monitor.

## Install (Flatpak)

```bash
flatpak install flathub io.github.shakeelosmani.avd_feed_connect   # once on Flathub
```

### Build locally

```bash
flatpak install flathub org.gnome.Platform//48 org.gnome.Sdk//48 \
  org.freedesktop.Platform.ffmpeg-full//24.08
flatpak-builder --user --install --force-clean build-dir \
  io.github.shakeelosmani.avd_feed_connect.yml
flatpak run io.github.shakeelosmani.avd_feed_connect
```

## Run unpackaged (development)

The same code runs without Flatpak if you have PyGObject (Gtk 3.0, WebKit2 4.1)
and an SDL3 `sdl-freerdp` on `PATH` (or at `~/opt/freerdp-sdl3-cam/bin/`):

```bash
python3 src/avd_feed_gui.py           # GUI
python3 src/avdfeed.py list           # CLI: list workspaces
python3 src/avdfeed.py connect 0      # CLI: connect to resource 0
```

Override the tenant/account with `AVD_TENANT` / `AVD_UPN`, and the client binary
with `AVD_SDL_FREERDP`.

## Status

Early. Feed discovery, sign-in, the workspace grid, and connect are working. The
Flatpak manifest is complete but still needs a first clean `flatpak-builder` run
(the FreeRDP module has the most external deps). Contributions welcome.

## License

MIT for this app's code. Bundled components keep their own licenses (FreeRDP:
Apache-2.0, SDL: Zlib).
