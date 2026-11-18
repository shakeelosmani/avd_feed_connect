"""AVD feed discovery: hit the ARM feeddiscovery endpoint, then each tenant
feed, and flatten the entitled resources.

  GET feeddiscovery -> <TenantFeedURLs><TenantFeedURL FeedURL=… TenantId=…>
  GET each FeedURL   -> <ResourceCollection><Publisher><Resources><Resource …>
                          <HostingTerminalServers>…<ResourceFile URL=…>

Each resource is returned as a plain dict (the GUI and CLI index it by key).
Raw discovery and per-tenant feed XML are saved under the feed data dir for
reference/debugging.
"""

import os
import re
import sys
import xml.etree.ElementTree as ET

from .. import config, http
from ..i18n import _ as tr


def _tag(el):
    return el.tag.split('}')[-1]


def _find(el, name):
    for c in el.iter():
        if _tag(c) == name:
            return c
    return None


def _save_raw(name, body):
    os.makedirs(config.OUT, exist_ok=True)
    with open(os.path.join(config.OUT, name), "w") as f:
        f.write(body)


class FeedClient:
    """Enumerates the workspaces/resources an access token is entitled to."""

    def enumerate(self, token):
        """Return a list of resource dicts across all tenant feeds."""
        st, _, body = http.get(config.DISCOVERY, token, config.ACCEPT_DISCOVERY)
        _save_raw("00_feeddiscovery.xml", body)
        if st != 200:
            sys.exit(tr("Feed discovery failed ({status}): {details}").format(status=st, details=body[:400]))
        disc = ET.fromstring(body)
        feeds = [dict(el.attrib) for el in disc.iter() if _tag(el) == "TenantFeedURL"]
        if not feeds:
            sys.exit(tr("No TenantFeedURL entries — account may have no AVD assignments.\nRaw discovery response:\n{details}").format(details=body[:800]))
        resources = []
        for fi, feed in enumerate(feeds):
            url = feed.get("FeedURL")
            if not url:
                continue
            try:
                st, _, body = http.get(url, token, config.ACCEPT_FEED)
            except ValueError as e:
                print(f"  ! skipping tenant feed: {e}", file=sys.stderr)
                continue
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
                    # ARM feeddiscovery puts the AVD workspace on TenantFeedURL
                    # (WorkspaceName); TenantDisplayName is the same value today
                    # but is also used as a Cloud PC / host-pool label.
                    "workspace": feed.get("WorkspaceName") or feed.get("TenantDisplayName", ""),
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
              f"{r.get('workspace') or r['tenant'] or r['publisher']}")
    print("\nconnect with:  avd-feed.py connect <N>")
