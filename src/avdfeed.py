#!/usr/bin/env python3
"""Linux Azure Virtual Desktop feed client — enumerate your workspace and
launch a resource, the way the Windows App / web client does it, but driving
the existing custom sdl-freerdp for the actual connection.

This is the "feed discovery" piece that FreeRDP's SDL client doesn't have yet:
the GUI clients already do MFA and then hit the ARM feed to LIST what you're
entitled to; here we replicate that list step and hand the generated .rdp to
sdl-freerdp. Nothing here touches the existing hand-made .rdpw launchers.

Flow (mirrors client.wvd.microsoft.com's own JS bundle, chunk-845):
  1. Interactive OAuth2 authorization-code + PKCE sign-in in your browser,
     public client a85cf173-… (Microsoft Remote Desktop), resource
     https://www.wvd.microsoft.com. This is the SAME flow the native SDL/web
     client uses — importantly NOT the device-code flow, which Conditional
     Access commonly blocks ("You cannot access this right now"). refresh_token
     (offline_access) is cached so later runs are silent.
  2. GET https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery
        Accept: application/x-msts-radc-discovery+xml,text/xml
     -> <TenantFeedURLs><TenantFeedURL FeedURL=… TenantId=… …>
  3. GET each TenantFeedURL/FeedURL
        Accept: application/x-msts-radc+xml;radc_schema_version=2.0,text/xml
     -> <ResourceCollection><Publisher><Resources><Resource …>
          <HostingTerminalServers><HostingTerminalServer>
            <ResourceFile URL=…>   (the per-resource .rdp)
  4. GET a resource's ResourceFile URL -> ready-to-use .rdp text.
  5. Launch it: sdl-freerdp <file.rdp> /gateway:type:arm /sec:aad /u:<upn> …
     (sdl-freerdp then does its own AAD webview auth for the connection.)

How Microsoft keeps the feed "fresh" (the thing you asked about): there is no
push. The client holds an offline_access refresh token; the short-lived
(~60-90 min) access token is silently re-minted via the refresh_token grant
(MSAL "acquireTokenSilent") whenever it expires or the user re-opens the feed,
and the feed is simply re-fetched. That's all `refresh` below demonstrates.

Usage:
  avd-feed.py                 # sign in (once), list resources
  avd-feed.py list            # same
  avd-feed.py connect N       # download resource N's .rdp and launch it
  avd-feed.py rdp N           # just write resource N's .rdp, print the path
  avd-feed.py refresh         # prove silent token renewal, print expiry
  avd-feed.py logout          # forget the cached refresh token
Env overrides: AVD_TENANT (default from the Dev Entra .rdpw), AVD_UPN.
Add "devicecode" as a second arg to force the old device-code flow.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import subprocess
import time
import urllib.request
import urllib.parse
import urllib.error
import webbrowser
import xml.etree.ElementTree as ET

# Multi-tenant by default: "organizations" lets any work/school account sign in
# and the feed returns every workspace that account is entitled to. Override
# with AVD_TENANT to pin a single tenant. UPN is normally learned from the
# signed-in token (see set_upn_from_token); AVD_UPN can pre-fill the login hint.
TENANT = os.environ.get("AVD_TENANT", "organizations")
UPN = os.environ.get("AVD_UPN", "")
CLIENT_ID = "a85cf173-4192-42f8-81fa-777a763e6e2c"  # Microsoft Remote Desktop (public)
SCOPE = "https://www.wvd.microsoft.com/.default offline_access openid profile"
DISCOVERY = "https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery"
LOGIN = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
# Registered redirect for the public MS Remote Desktop client; the browser
# lands here (a blank page) with ?code=… after an interactive sign-in.
REDIRECT = "https://login.microsoftonline.com/common/oauth2/nativeclient"
# The feed service rejects unknown clients ("INCOMPATIBLE_CLIENT_VERSION /
# Client did not send any User Agent approved header"); this is exactly the
# X-MS-User-Agent the web client sends (clientType/clientVersion sdkType/sdk).
MS_USER_AGENT = "com.microsoft.rdc.html/2.0.79.2 rdhtml-sdk/2.0.4"

HOME = os.path.expanduser("~")

# Data dir: use XDG_DATA_HOME (set to the app's private dir inside Flatpak) so
# the token cache and generated .rdp files land in a sane, writable place both
# packaged and unpackaged.
_DATA = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
OUT = os.path.join(_DATA, "avd-feed-connect", "feed")
CACHE = os.path.join(_DATA, "avd-feed-connect", "token-cache.json")


def _find_sdl_freerdp():
    """Locate the SDL3 FreeRDP client. In the Flatpak it is on PATH at
    /app/bin/sdl-freerdp; unpackaged, fall back to the local -cam build."""
    return (os.environ.get("AVD_SDL_FREERDP")
            or shutil.which("sdl-freerdp")
            or os.path.join(HOME, "opt", "freerdp-sdl3-cam", "bin", "sdl-freerdp"))


SDL = _find_sdl_freerdp()
# Only needed unpackaged (Flatpak resolves libs via rpath); empty = don't touch.
SDL_LIBS = os.environ.get("AVD_SDL_LIBS", os.path.join(HOME, "opt", "sdl3", "lib"))

ACCEPT_DISCOVERY = "application/x-msts-radc-discovery+xml,text/xml"
ACCEPT_FEED = "application/x-msts-radc+xml;radc_schema_version=2.0,text/xml"


# ---- OAuth2 ---------------------------------------------------------------

def _post(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _get(url, token, accept="*/*"):
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", MS_USER_AGENT)
    req.add_header("X-MS-User-Agent", MS_USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors="replace")


def _save_cache(refresh_token):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)  # XDG data dir may not exist yet
    fd = os.open(CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"refresh_token": refresh_token, "obtained": int(time.time())}, f)


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _auth_code():
    """Interactive authorization-code + PKCE — the flow the native client uses.
    Conditional Access frequently blocks the device-code flow but allows this
    one, so this is what avoids the 'You cannot access this right now' wall."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": CLIENT_ID, "response_type": "code", "redirect_uri": REDIRECT,
        "scope": SCOPE, "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "prompt": "select_account",
    }
    if UPN:
        params["login_hint"] = UPN
    url = LOGIN + "/authorize?" + urllib.parse.urlencode(params)
    print("\nOpen this URL in your browser and sign in:\n\n  " + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("After sign-in the page goes blank at login.microsoftonline.com/…/"
          "nativeclient — copy that FINAL address bar URL and paste it here.\n")
    landed = input("Paste the redirected URL (or the code value): ").strip()
    # Accept: a full URL, a bare "?...=..." query, "code=..&state=..", or the
    # bare code possibly with a trailing "&state=..&session_state=.." appended.
    frag = landed
    if "://" in frag:
        p = urllib.parse.urlparse(frag)
        frag = p.query or p.fragment
    frag = frag.lstrip("?#")
    qs = urllib.parse.parse_qs(frag)
    if "error" in qs:
        sys.exit("auth error: " + (qs.get("error_description") or qs["error"])[0])
    if "code" in qs:
        code = qs["code"][0]
        returned_state = qs.get("state", [None])[0]
    else:
        # bare code; drop any trailing &state=…/&session_state=… the paste kept
        code = frag.split("&", 1)[0]
        returned_state = None
    if returned_state is not None and returned_state != state:
        sys.exit("state mismatch — aborting for safety, try again")
    if not code:
        sys.exit("no authorization code found in what you pasted")
    st, tok = _post(LOGIN + "/token", {
        "grant_type": "authorization_code", "client_id": CLIENT_ID,
        "code": code, "redirect_uri": REDIRECT, "scope": SCOPE,
        "code_verifier": verifier})
    if st != 200:
        sys.exit(f"token exchange failed ({st}): {tok.get('error','')}: "
                 f"{tok.get('error_description','')[:300]}")
    return tok


def _device_code():
    st, dc = _post(LOGIN + "/devicecode", {"client_id": CLIENT_ID, "scope": SCOPE})
    if st != 200:
        sys.exit(f"devicecode request failed ({st}): {dc}")
    print("\n>>> " + dc["message"] + "\n", flush=True)
    interval = dc.get("interval", 5)
    while True:
        time.sleep(interval)
        st, tok = _post(LOGIN + "/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT_ID, "device_code": dc["device_code"]})
        if st == 200:
            return tok
        err = tok.get("error", "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        sys.exit(f"device-code auth failed: {err}: "
                 f"{tok.get('error_description','')[:300]}")


def _refresh(rt):
    st, tok = _post(LOGIN + "/token", {
        "grant_type": "refresh_token", "client_id": CLIENT_ID,
        "scope": SCOPE, "refresh_token": rt})
    if st != 200:
        return None
    return tok


def set_upn_from_token(tok):
    """Learn the signed-in user's UPN from the id_token so /u: and the login
    hint work without hardcoding an account. Sets the module-level UPN when it
    isn't already pinned via AVD_UPN."""
    global UPN
    if UPN:
        return UPN
    idt = tok.get("id_token")
    if not idt or idt.count(".") < 2:
        return UPN
    try:
        payload = idt.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # pad base64url
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8", "replace"))
        UPN = claims.get("preferred_username") or claims.get("upn") \
            or claims.get("unique_name") or claims.get("email") or ""
    except Exception:
        pass
    return UPN


def get_token(verbose=True, use_device_code=False):
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            rt = json.load(f)["refresh_token"]
        tok = _refresh(rt)
        if tok:
            _save_cache(tok.get("refresh_token", rt))
            set_upn_from_token(tok)
            if verbose:
                print(f"token: silent refresh ok (expires_in={tok['expires_in']}s,"
                      f" rotated_refresh={'yes' if 'refresh_token' in tok else 'no'})")
            return tok["access_token"]
        if verbose:
            print("cached refresh token invalid, signing in again")
    tok = _device_code() if use_device_code else _auth_code()
    _save_cache(tok["refresh_token"])
    set_upn_from_token(tok)
    if verbose:
        how = "device-code" if use_device_code else "interactive auth-code"
        print(f"token: {how} sign-in ok (expires_in={tok['expires_in']}s)")
    return tok["access_token"]


# ---- feed -----------------------------------------------------------------

def _tag(el):
    return el.tag.split('}')[-1]


def _find(el, name):
    for c in el.iter():
        if _tag(c) == name:
            return c
    return None


def _save_raw(name, body):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w") as f:
        f.write(body)


def enumerate_feed(token):
    """Return list of resource dicts across all tenant feeds. Raw discovery and
    per-tenant feed XML are saved under ~/avd/feed/ for reference/debugging."""
    st, _, body = _get(DISCOVERY, token, ACCEPT_DISCOVERY)
    _save_raw("00_feeddiscovery.xml", body)
    if st != 200:
        sys.exit(f"feeddiscovery failed ({st}): {body[:400]}")
    disc = ET.fromstring(body)
    feeds = [dict(el.attrib) for el in disc.iter() if _tag(el) == "TenantFeedURL"]
    if not feeds:
        sys.exit("no TenantFeedURL entries — account may have no AVD assignments.\n"
                 "raw discovery response:\n" + body[:800])
    resources = []
    for fi, feed in enumerate(feeds):
        url = feed.get("FeedURL")
        if not url:
            continue
        st, _, body = _get(url, token, ACCEPT_FEED)
        _save_raw(f"01_feed{fi}_{re.sub(r'[^A-Za-z0-9]+','_',feed.get('TenantDisplayName','t'))[:40]}.xml", body)
        if st != 200:
            print(f"  ! tenant feed {feed.get('TenantDisplayName','?')} "
                  f"failed ({st})", file=sys.stderr)
            continue
        rc = ET.fromstring(body)
        pub = _find(rc, "Publisher")
        pubname = pub.attrib.get("Name", "") if pub is not None else ""
        for res in rc.iter():
            if _tag(res) != "Resource":
                continue
            rf = _find(res, "ResourceFile")
            icon = _find(res, "Icon32")
            resources.append({
                "tenant": feed.get("TenantDisplayName", ""),
                "tenant_id": feed.get("TenantId", ""),
                "publisher": pubname,
                "id": res.attrib.get("ID", ""),
                "title": res.attrib.get("Title", "?"),
                "type": res.attrib.get("Type", "?"),   # Desktop / RemoteApp
                "armpath": res.attrib.get("ArmPath", ""),
                "rdp_url": rf.attrib.get("URL") if rf is not None else None,
                "icon32": icon.attrib.get("FileURL") if icon is not None else None,
            })
    return resources


def print_list(resources):
    if not resources:
        print("no resources found.")
        return
    w = max(len(r["title"]) for r in resources)
    print(f"\n{len(resources)} resource(s):\n")
    for i, r in enumerate(resources):
        print(f"  [{i}] {r['title']:<{w}}  {r['type']:<10} "
              f"{r['tenant'] or r['publisher']}")
    print("\nconnect with:  avd-feed.py connect <N>")


def download_rdp(token, res):
    if not res["rdp_url"]:
        sys.exit(f"resource {res['title']!r} has no ResourceFile URL")
    st, _, rdp = _get(res["rdp_url"], token)
    if st != 200:
        sys.exit(f"rdp download failed ({st}): {rdp[:300]}")
    os.makedirs(OUT, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", res["title"])[:80]
    path = os.path.join(OUT, safe + ".rdp")
    with open(path, "w") as f:
        f.write(rdp)
    return path


def launch(path):
    env = dict(os.environ)
    # Only inject SDL_LIBS when it actually exists (unpackaged builds); inside
    # the Flatpak the loader finds the bundled libs via rpath.
    if SDL_LIBS and os.path.isdir(SDL_LIBS):
        env["LD_LIBRARY_PATH"] = SDL_LIBS + (
            os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    os.makedirs(OUT, exist_ok=True)
    log = os.path.join(OUT, "feed-last-run.log")
    argv = [SDL, path, "/gateway:type:arm", "/sec:aad"]
    if UPN:
        argv.append(f"/u:{UPN}")
    argv += ["/sound:sys:pulse", "/microphone", "/cert:ignore",
             "/f", "/scale-desktop:200", "-multimon", "/log-level:info"]
    print("launching:", " ".join(argv))
    print("log:", log)
    with open(log, "w") as lf:
        return subprocess.call(argv, env=env, stdout=lf, stderr=subprocess.STDOUT)


# ---- main -----------------------------------------------------------------

def _pick(resources, idx_arg):
    try:
        idx = int(idx_arg)
    except (TypeError, ValueError):
        sys.exit("give a resource index, e.g. avd-feed.py connect 0")
    if not 0 <= idx < len(resources):
        sys.exit(f"index {idx} out of range (0..{len(resources)-1})")
    return resources[idx]


def main():
    args = sys.argv[1:]
    dev = "devicecode" in args
    args = [a for a in args if a != "devicecode"]
    cmd = args[0] if args else "list"
    if cmd == "logout":
        if os.path.exists(CACHE):
            os.remove(CACHE)
        print("cached refresh token removed.")
        return
    if cmd == "refresh":
        get_token(verbose=True, use_device_code=dev)
        return

    token = get_token(use_device_code=dev)
    if cmd in ("list", "ls"):
        print_list(enumerate_feed(token))
    elif cmd in ("rdp", "connect", "conn"):
        res = _pick(enumerate_feed(token), args[1] if len(args) > 1 else None)
        path = download_rdp(token, res)
        print(f"wrote {path}")
        if cmd != "rdp":
            sys.exit(launch(path))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
