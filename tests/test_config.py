"""Only the Entra login host may be loaded for FreeRDP's "Browse to:" prompt."""

import importlib

import pytest

from avd_feed_connect import config


def _config_for(monkeypatch, cloud):
    """Re-import config with AVD_CLOUD set (endpoints/allowlists derive at import)."""
    if cloud is None:
        monkeypatch.delenv("AVD_CLOUD", raising=False)
    else:
        monkeypatch.setenv("AVD_CLOUD", cloud)
    return importlib.reload(config)


def test_accepts_entra_login():
    assert config.is_aad_login_url(
        "https://login.microsoftonline.com/common/oauth2/authorize?client_id=x")


@pytest.mark.parametrize("url", [
    "http://login.microsoftonline.com/x",
    "https://evil.example/x",
    "https://login.microsoftonline.com.evil.io/x",
    "https://evil.io/login.microsoftonline.com",
    "https://login.microsoftonline.com@evil.io/x",
    "https://microsoftonline.com/x",
    "file:///etc/passwd", "javascript:alert(1)", "",
])
def test_rejects_others(url):
    assert not config.is_aad_login_url(url)


# --- sovereign cloud selection (AVD_CLOUD) --------------------------------

def test_commercial_is_the_default_and_unchanged(monkeypatch):
    for cloud in (None, "", "commercial", "nonsense"):
        c = _config_for(monkeypatch, cloud)
        assert c.CLOUD == "commercial"
        assert c.DISCOVERY == "https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery"
        assert c.LOGIN.startswith("https://login.microsoftonline.com/")
        assert c.REDIRECT == \
            "https://login.microsoftonline.com/common/oauth2/nativeclient"
        assert c.SCOPE == \
            "https://www.wvd.microsoft.com/.default offline_access openid profile"
        assert c.AAD_LOGIN_HOST == "login.microsoftonline.com"
        assert c.TRUSTED_SUFFIXES == (".microsoft.com", ".microsoftonline.com")


def test_usgov_endpoints_and_allowlist(monkeypatch):
    c = _config_for(monkeypatch, "usgov")
    assert c.DISCOVERY == "https://rdweb.wvd.azure.us/api/arm/feeddiscovery"
    assert c.LOGIN.startswith("https://login.microsoftonline.us/")
    assert c.SCOPE.startswith("https://www.wvd.azure.us/.default")
    assert c.AAD_LOGIN_HOST == "login.microsoftonline.us"
    # its own login host passes; commercial is rejected under this cloud
    assert c.is_aad_login_url("https://login.microsoftonline.us/common/authorize")
    assert not c.is_aad_login_url("https://login.microsoftonline.com/common/authorize")
    assert c.is_trusted_url("https://rdweb.wvd.azure.us/api/arm/feeddiscovery")
    assert not c.is_trusted_url("https://rdweb.wvd.microsoft.com/x")


def test_china_endpoints_and_allowlist(monkeypatch):
    c = _config_for(monkeypatch, "china")
    assert c.DISCOVERY == "https://rdweb.wvd.azure.cn/api/arm/feeddiscovery"
    assert c.AAD_LOGIN_HOST == "login.partner.microsoftonline.cn"
    assert c.is_trusted_url("https://rdweb.wvd.azure.cn/x")
    assert not c.is_trusted_url("https://rdweb.wvd.microsoft.com/x")


def test_gateway_arg_is_always_arm(monkeypatch):
    # AAD sub-options live on /azure, not /gateway (FreeRDP only parses
    # g:/u:/d:/p:/type:/access-token:/bearer: on /gateway).
    assert _config_for(monkeypatch, "commercial").gateway_arg() == "type:arm"
    assert _config_for(monkeypatch, "usgov").gateway_arg() == "type:arm"


def test_azure_arg_commercial_empty(monkeypatch):
    # Commercial needs no /azure — FreeRDP defaults already target commercial Entra.
    assert _config_for(monkeypatch, "commercial").azure_arg() == ""


def test_azure_arg_sovereign_authority_with_commercial_redirect(monkeypatch):
    # Sovereign overrides the authority (ad:) but keeps the public client's
    # registered redirect on the commercial host (avd-access:) — the combination
    # verified working for a US Gov AVD + PIV smartcard from Linux (#17).
    arg = _config_for(monkeypatch, "usgov").azure_arg()
    assert "ad:login.microsoftonline.us" in arg
    assert "tenantid:common" in arg
    assert ("avd-access:https%3A%2F%2Flogin.microsoftonline.com"
            "%2Fcommon%2Foauth2%2Fnativeclient") in arg


def test_reset_config_to_commercial(monkeypatch):
    # leave the module back on its default so later tests see commercial
    _config_for(monkeypatch, None)
