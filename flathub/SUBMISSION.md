# Flathub submission — AVD Feed + Connect

Everything here is prepared for **you** to open the Flathub PR. Nothing in this
folder affects the app's own repo or the self-hosted (GitHub Pages) build.

App ID: `io.github.shakeelosmani.avd_feed_connect`
Manifest: [`io.github.shakeelosmani.avd_feed_connect.yml`](./io.github.shakeelosmani.avd_feed_connect.yml)

## Status vs the Flathub linter
- ✅ Manifest passes `flatpak-builder-lint manifest` (no errors).
- ✅ MetaInfo passes `flatpak-builder-lint appstream` (validation successful).
- ⚠️ Only note left: linter suggests a newer runtime (GNOME 49 → 51). Non-fatal;
  GNOME 49 is supported. Optional to bump before submitting (needs a rebuild +
  quick test against the newer runtime).
- ℹ️ Minor, non-blocking: the metainfo still has a deprecated `<developer_name>`
  tag — worth removing in the next app release, but it does not block Flathub.

## How this manifest differs from the app-repo manifest
Only what Flathub requires:
1. **Single graphics socket** — `--socket=x11` only (dropped `--socket=wayland`).
   Flathub forbids shipping both. The app already forces its RDP client onto
   X11/XWayland for stability, and the launcher renders via XWayland on Wayland.
2. **App module pinned to the released tag** `v0.3.7` (+ its commit), instead of
   a floating commit, and `x-checker-data` added so Flathub's bot can propose
   dependency/version updates.

## Steps to submit (you do these)
1. Fork **https://github.com/flathub/flathub** to your account.
2. Clone your fork; create a branch named **exactly** the app id:
   ```bash
   git clone https://github.com/<you>/flathub.git
   cd flathub
   git checkout -b io.github.shakeelosmani.avd_feed_connect
   ```
3. Copy the manifest to the repo root (filename = app id):
   ```bash
   cp <this-folder>/io.github.shakeelosmani.avd_feed_connect.yml .
   git add io.github.shakeelosmani.avd_feed_connect.yml
   git commit -m "Add io.github.shakeelosmani.avd_feed_connect"
   git push -u origin io.github.shakeelosmani.avd_feed_connect
   ```
4. Open a PR from that branch to **flathub/flathub `master`**. Use the PR text
   below. `flathubbot` will build it; a reviewer will follow up in the PR.
5. On merge, Flathub creates `flathub/io.github.shakeelosmani.avd_feed_connect`,
   which becomes the canonical repo you maintain (add committers, push updates).

Docs: https://docs.flathub.org/docs/for-app-authors/submission

---

## PR description (paste into the Flathub PR)

**io.github.shakeelosmani.avd_feed_connect — AVD Feed + Connect (Linux)**

A native GTK4 client for **Azure Virtual Desktop** and **Windows 365**: sign in
with a Microsoft work account, see the desktops and remote apps you're entitled
to (feed discovery), and connect over RDP via a bundled, hardened FreeRDP 3
(SDL3). Unofficial — not affiliated with or endorsed by Microsoft.

Upstream: https://github.com/shakeelosmani/avd_feed_connect

**Notes for reviewers**
- **Unofficial third-party client.** No Microsoft trademarks or logos are
  shipped; the app clearly states it is unofficial. Authentication uses the
  standard Entra ID public auth-code + PKCE flow (the same interactive flow the
  official clients use), so Conditional Access is honored. Happy to adjust
  wording/branding if anything is a concern.
- **Permissions and why they're needed:**
  - `--share=network` — feed discovery, Entra sign-in, and the RDP gateway.
  - `--socket=x11` (not `fallback-x11`) — the bundled FreeRDP SDL3 client is
    unstable on native Wayland, so connections are forced onto X11/XWayland;
    the real X11 socket must be present even on Wayland hosts. The launcher GUI
    renders via XWayland there too. (Only one graphics socket is shipped.)
  - `--socket=pulseaudio` — RDP audio playback and microphone redirection.
  - `--device=dri` — GPU for the RDP graphics pipeline.
  - `--device=all` — webcam access for camera redirection into the session
    (rdpecam / UVC). Happy to narrow this if there's a preferred approach.
  - `--talk-name=org.freedesktop.secrets` — stores only the OAuth refresh token
    in the login keyring (libsecret), encrypted at rest, instead of a file.
  - `--talk-name=org.freedesktop.Notifications`,
    `--talk-name=org.kde.StatusNotifierWatcher` — desktop notifications / tray.
- **Bundled FreeRDP** is pinned to an upstream master commit that includes the
  merged PulseAudio hot-unplug fixes (FreeRDP PR #13334); it can move to the
  next tagged FreeRDP 3.x release once one is cut past that merge.
- **Reproducible/offline:** every source is pinned (git commits / sha256); no
  network access at build time.

---

## Likely reviewer questions → prepared answers
- **"Why `--socket=x11` instead of `fallback-x11`?"** FreeRDP's SDL client
  crashes on native Wayland; the client is forced to X11/XWayland, which needs
  the real socket on Wayland hosts (fallback-x11 only exposes X11 when there is
  no Wayland).
- **"`--device=all`?"** Needed for webcam redirection into the remote session.
  Can be dropped if camera support is cut, or revisited if a portal-based camera
  path becomes viable for FreeRDP.
- **"Unofficial Microsoft client / uses the Remote Desktop public client id?"**
  It's the standard public interactive flow; no MS branding is shipped; clearly
  labeled unofficial. Open to any changes requested.
