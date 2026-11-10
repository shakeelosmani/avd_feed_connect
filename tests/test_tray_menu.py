"""build_menu: the tray's right-click menu layout."""

from avd_feed_connect.gui.tray_menu import SEPARATOR, build_menu


def _res(rid, title, tenant="Contoso"):
    return {"id": rid, "title": title, "tenant": tenant}


def _labels(items):
    return [i.get("label") for i in items]


def test_workspaces_are_disabled_headers_in_feed_order():
    items = build_menu([_res("a", "Dev"), _res("b", "Ops", "Fabrikam"),
                        _res("c", "Win11")], {})
    assert _labels(items[:5]) == ["CONTOSO", "Dev", "Win11", "FABRIKAM", "Ops"]
    assert not items[0]["enabled"] and not items[3]["enabled"]
    assert items[0]["action"] is None


def test_idle_resource_offers_connect_and_windowed():
    vm = build_menu([_res("a", "Dev")], {})[1]
    assert vm["label"] == "Dev"
    assert [(c["label"], c["action"]) for c in vm["children"]] == [
        ("Connect", ("connect", "a")), ("Connect (windowed)", ("connect-windowed", "a"))]


def test_connected_resource_offers_focus_and_disconnect():
    vm = build_menu([_res("a", "Dev")], {"a": {"state": "connected", "running": True}})[1]
    assert vm["label"] == "● Dev"
    assert [c["action"] for c in vm["children"]] == [("focus", "a"), ("disconnect", "a")]


def test_connecting_without_process_is_not_actionable():
    vm = build_menu([_res("a", "Dev")], {"a": {"state": "connecting", "running": False}})[1]
    assert vm["label"] == "○ Dev"
    assert [(c["label"], c["enabled"]) for c in vm["children"]] == [("Connecting…", False)]


def test_connecting_with_process_can_be_focused_or_cancelled():
    vm = build_menu([_res("a", "Dev")], {"a": {"state": "connecting", "running": True}})[1]
    assert [c["label"] for c in vm["children"]] == ["Connecting…", "Focus window", "Disconnect"]


def test_footer_and_empty_feed():
    items = build_menu([], {})
    assert items[0]["label"] == "No workspaces yet" and not items[0]["enabled"]
    assert items[1] == SEPARATOR
    assert [i["action"] for i in items[2:]] == [("refresh",), ("show",), ("quit",)]


def test_workspace_falls_back_to_publisher():
    items = build_menu([{"id": "a", "title": "Dev", "tenant": "", "publisher": "Pub"}], {})
    assert items[0]["label"] == "PUB"


def test_workspace_prefers_workspace_name_over_tenant():
    items = build_menu([{"id": "a", "title": "Dev", "workspace": "AVD-RemoteApps",
                         "tenant": "Contoso", "publisher": "Pub"}], {})
    assert items[0]["label"] == "AVD-REMOTEAPPS"


def test_account_actions_keep_owner_with_identical_resource_ids():
    from avd_feed_connect.gui.tray_menu import build_accounts_menu
    accounts = [{'id': 'a', 'upn': 'a@example.com', 'resources': [_res('same', 'Desktop')]},
                {'id': 'b', 'upn': 'b@example.com', 'resources': [_res('same', 'Desktop')]}]
    menu = build_accounts_menu(accounts, {('a', 'same'): {'running': True, 'state': 'connected'}})
    assert menu[0]['label'] == 'a@example.com'
    assert menu[0]['children'][1]['children'][0]['action'] == ('focus', 'a', 'same')
    assert menu[1]['children'][1]['children'][0]['action'] == ('connect', 'b', 'same')


def test_removed_account_only_offers_running_session_actions():
    from avd_feed_connect.gui.tray_menu import build_accounts_menu
    accounts = [{'id': 'a', 'upn': 'old@example.com', 'removed': True,
                 'resources': [_res('running', 'Running'), _res('idle', 'Idle')]}]
    menu = build_accounts_menu(accounts, {('a', 'running'): {'running': True, 'state': 'connected'}})
    assert [r['label'] for r in menu[0]['children']] == ['CONTOSO', '● Running']
    assert menu[0]['children'][1]['children'][1]['action'] == ('disconnect', 'a', 'running')


def test_menu_can_omit_footer_without_truncating_resources():
    items = build_menu([_res('a', 'Dev'), _res('b', 'Ops')], {}, include_footer=False)
    assert _labels(items) == ['CONTOSO', 'Dev', 'Ops']
    assert items[-1]['children'][0]['action'] == ('connect', 'b')


def test_empty_menu_without_footer_keeps_placeholder():
    items = build_menu([], {}, include_footer=False)
    assert len(items) == 1
    assert items[0]['label'] == 'No workspaces yet'
    assert not items[0]['enabled']
