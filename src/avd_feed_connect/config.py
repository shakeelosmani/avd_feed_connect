"""Static configuration: the OAuth/feed endpoints, on-disk locations, and the
path to the bundled sdl-freerdp.

These are process-wide constants (mostly derived from the environment once, at
import time). Anything that changes per signed-in session — the user's UPN, the
last refresh error — lives on :class:`~avd_feed_connect.auth.oauth.OAuthClient`
instead, never here.
"""

import os
import shutil
import sys
import urllib.parse

# Multi-tenant by default: "organizations" lets any work/school account sign in
# and the feed returns every workspace that account is entitled to. Override
# with AVD_TENANT to pin a single tenant. The signed-in user's UPN is normally
# learned from the token (OAuthClient.set_upn_from_token); AVD_UPN can pre-fill
# the login hint (see OAuthClient).
TENANT = os.environ.get("AVD_TENANT", "organizations")
CLIENT_ID = "a85cf173-4192-42f8-81fa-777a763e6e2c"  # Microsoft Remote Desktop (public)

# Azure "sovereign" clouds each have their own AVD feed host, Entra sign-in
# authority, and AVD resource URI — and their own set of hosts a bearer token /
# the sign-in WebView may talk to. Everything cloud-specific is grouped here and
# selected as one matched set via AVD_CLOUD (default "commercial"), so the feed,
# the token endpoints, and the host allowlists always agree. `trusted` lists the
# dot-suffixes a bearer token may be sent to for that cloud (see is_trusted_url).
CLOUDS = {
    "commercial": dict(
        feed="rdweb.wvd.microsoft.com",
        authority="login.microsoftonline.com",
        resource="https://www.wvd.microsoft.com/.default",
        trusted=(".microsoft.com", ".microsoftonline.com"),
    ),
    # Azure Government (US Gov — GCC High and DoD share these endpoints).
    "usgov": dict(
        feed="rdweb.wvd.azure.us",
        authority="login.microsoftonline.us",
        resource="https://www.wvd.azure.us/.default",
        trusted=(".azure.us", ".microsoftonline.us"),
    ),
    # Azure China (operated by 21Vianet).
    "china": dict(
        feed="rdweb.wvd.azure.cn",
        authority="login.partner.microsoftonline.cn",
        resource="https://www.wvd.azure.cn/.default",
        trusted=(".azure.cn", ".microsoftonline.cn", ".chinacloudapi.cn"),
    ),
}
CLOUD = os.environ.get("AVD_CLOUD", "commercial").strip().lower()
if CLOUD not in CLOUDS:
    print(f"config: unknown AVD_CLOUD={CLOUD!r}; using 'commercial'", file=sys.stderr)
    CLOUD = "commercial"
_C = CLOUDS[CLOUD]

SCOPE = f"{_C['resource']} offline_access openid profile"
DISCOVERY = f"https://{_C['feed']}/api/arm/feeddiscovery"
LOGIN = f"https://{_C['authority']}/{TENANT}/oauth2/v2.0"
# Registered redirect for the public MS Remote Desktop client; the browser
# lands here (a blank page) with ?code=… after an interactive sign-in.
REDIRECT = f"https://{_C['authority']}/common/oauth2/nativeclient"
# The feed service rejects unknown clients ("INCOMPATIBLE_CLIENT_VERSION /
# Client did not send any User Agent approved header"); this is exactly the
# X-MS-User-Agent the web client sends (clientType/clientVersion sdkType/sdk).
MS_USER_AGENT = "com.microsoft.rdc.html/2.0.79.2 rdhtml-sdk/2.0.4"

ACCEPT_DISCOVERY = "application/x-msts-radc-discovery+xml,text/xml"
ACCEPT_FEED = "application/x-msts-radc+xml;radc_schema_version=2.0,text/xml"


def gateway_arg():
    """The value for FreeRDP's ``/gateway:``. Commercial keeps the bare
    ``type:arm`` (unchanged behaviour); a sovereign cloud appends the AAD
    authority and resource scope so FreeRDP's connection-time token targets that
    cloud's Entra instead of the commercial default it ships with
    (``login.microsoftonline.com`` / ``www.wvd.microsoft.com``). The scope is
    URL-encoded as FreeRDP's ``avd-scope:`` sub-option expects."""
    arg = "type:arm"
    if CLOUD != "commercial":
        scope = urllib.parse.quote(SCOPE, safe="")
        arg += f",ad:{_C['authority']},avd-scope:{scope}"
    return arg

HOME = os.path.expanduser("~")

# Data dir: use XDG_DATA_HOME (set to the app's private dir inside Flatpak) so
# the token cache and generated .rdp files land in a sane, writable place both
# packaged and unpackaged.
_DATA = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
OUT = os.path.join(_DATA, "avd-feed-connect", "feed")
CACHE = os.path.join(_DATA, "avd-feed-connect", "token-cache.json")


def find_sdl_freerdp():
    """Locate the SDL3 FreeRDP client. In the Flatpak it is on PATH at
    /app/bin/sdl-freerdp; unpackaged, fall back to the local -cam build."""
    return (os.environ.get("AVD_SDL_FREERDP")
            or shutil.which("sdl-freerdp")
            or os.path.join(HOME, "opt", "freerdp-sdl3-cam", "bin", "sdl-freerdp"))


SDL = find_sdl_freerdp()
# Only needed unpackaged (Flatpak resolves libs via rpath); empty = don't touch.
SDL_LIBS = os.environ.get("AVD_SDL_LIBS", os.path.join(HOME, "opt", "sdl3", "lib"))

AAD_LOGIN_HOST = _C["authority"]


def is_aad_login_url(url):
    """True only for an https URL on the Entra login host. FreeRDP's "Browse to:"
    URL is loaded in a WebView that shares our SSO cookies, so nothing else
    may be loaded there."""
    try:
        p = urllib.parse.urlparse(url)
        return p.scheme == "https" and (p.hostname or "").lower() == AAD_LOGIN_HOST
    except ValueError:
        return False


# Hosts a bearer token may be sent to. Feed XML supplies the feed, .rdp and icon
# URLs, so they are checked before the Authorization header is attached. The set
# is per-cloud (see CLOUDS) so a sovereign cloud accepts only its own hosts.
TRUSTED_SUFFIXES = _C["trusted"]


def is_trusted_url(url):
    """True only for an https URL on a Microsoft host (dot-boundary match, so
    ``evilmicrosoft.com`` and ``microsoft.com.evil.io`` are rejected)."""
    try:
        p = urllib.parse.urlparse(url)
        host = (p.hostname or "").lower()
    except ValueError:
        return False
    return p.scheme == "https" and any(
        host == s[1:] or host.endswith(s) for s in TRUSTED_SUFFIXES)
