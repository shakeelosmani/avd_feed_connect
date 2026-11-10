"""FeedClient: discovery + tenant-feed XML is flattened into resource dicts."""

from avd_feed_connect import config, http
from avd_feed_connect.feed.discovery import FeedClient

_DISCOVERY_XML = """<?xml version="1.0"?>
<TenantFeedURLs>
  <TenantFeedURL FeedURL="https://rdweb.wvd.microsoft.com/f0" TenantId="t1"
                 TenantDisplayName="Contoso"/>
</TenantFeedURLs>"""

_FEED_XML = """<?xml version="1.0"?>
<ResourceCollection>
  <Publisher Name="Contoso IT">
    <Resources>
      <Resource ID="r1" Title="Finance Desktop" Type="Desktop" ArmPath="/arm/1">
        <HostingTerminalServers><HostingTerminalServer>
          <ResourceFile URL="https://rdweb.wvd.microsoft.com/r1.rdp"/>
        </HostingTerminalServer></HostingTerminalServers>
        <Icon32 FileURL="https://rdweb.wvd.microsoft.com/r1.png"/>
      </Resource>
      <Resource ID="r2" Title="Ops Console" Type="RemoteApp" ArmPath="/arm/2">
        <HostingTerminalServers><HostingTerminalServer>
          <ResourceFile URL="https://rdweb.wvd.microsoft.com/r2.rdp"/>
        </HostingTerminalServer></HostingTerminalServers>
      </Resource>
    </Resources>
  </Publisher>
</ResourceCollection>"""


def test_enumerate_flattens_resources(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OUT", str(tmp_path))  # raw XML dumps go to tmp

    def fake_get(url, token, accept="*/*"):
        body = _DISCOVERY_XML if url == config.DISCOVERY else _FEED_XML
        return 200, {}, body

    monkeypatch.setattr(http, "get", fake_get)

    resources = FeedClient().enumerate("token")
    assert len(resources) == 2

    desktop = resources[0]
    assert desktop["id"] == "r1"
    assert desktop["title"] == "Finance Desktop"
    assert desktop["type"] == "Desktop"
    assert desktop["publisher"] == "Contoso IT"
    assert desktop["tenant"] == "Contoso"
    assert desktop["workspace"] == "Contoso"  # WorkspaceName absent → TenantDisplayName
    assert desktop["rdp_url"] == "https://rdweb.wvd.microsoft.com/r1.rdp"
    assert desktop["icon32"] == "https://rdweb.wvd.microsoft.com/r1.png"

    remoteapp = resources[1]
    assert remoteapp["type"] == "RemoteApp"
    assert remoteapp["icon32"] is None  # no Icon32 element on this resource


def test_untrusted_feed_url_is_skipped_without_sending_token(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OUT", str(tmp_path))
    evil = _DISCOVERY_XML.replace("rdweb.wvd.microsoft.com", "evil.example")
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(req.full_url)
        raise AssertionError("request must not be sent")

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    # Discovery itself goes through the real http.get (trusted host) — stub just that hop.
    real_get = http.get

    def get(url, token, accept="*/*"):
        if url == config.DISCOVERY:
            return 200, {}, evil
        return real_get(url, token, accept)

    monkeypatch.setattr(http, "get", get)
    assert FeedClient().enumerate("token") == []
    assert sent == []


_DISCOVERY_TWO_XML = """<?xml version="1.0"?>
<TenantFeedURLs>
  <TenantFeedURL FeedURL="https://rdweb.wvd.microsoft.com/f0" TenantId="t1"
                 TenantDisplayName="Contoso" WorkspaceName="Admin Workspace T1"/>
  <TenantFeedURL FeedURL="https://rdweb.wvd.microsoft.com/f1" TenantId="t2"
                 TenantDisplayName="Contoso" WorkspaceName="AVD-RemoteApps"/>
</TenantFeedURLs>"""


def test_enumerate_keeps_workspace_name_from_each_feed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OUT", str(tmp_path))

    def fake_get(url, token, accept="*/*"):
        if url == config.DISCOVERY:
            return 200, {}, _DISCOVERY_TWO_XML
        return 200, {}, _FEED_XML

    monkeypatch.setattr(http, "get", fake_get)
    resources = FeedClient().enumerate("token")
    assert [r["workspace"] for r in resources] == (
        ["Admin Workspace T1", "Admin Workspace T1",
         "AVD-RemoteApps", "AVD-RemoteApps"])
    assert {r["tenant"] for r in resources} == {"Contoso"}
