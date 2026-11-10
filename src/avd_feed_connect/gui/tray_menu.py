"""Tray right-click menu layout: workspaces as headers, one submenu per resource.

Pure data (no GTK/D-Bus), so it can be unit-tested. Each item is a dict:
  {"label": str, "enabled": bool, "action": tuple | None, "children": [items]}
or the separator {"type": "separator"}. ``action`` is handed back to the app
when the item is clicked, e.g. ("connect", "<resource id>").
"""

from ..i18n import _

SEPARATOR = {"type": "separator"}

# Label prefix per session state (idle has none).
MARKERS = {"connected": "● ", "connecting": "○ "}


def item(label, action=None, enabled=True, children=None):
    return {"label": label, "enabled": enabled, "action": action,
            "children": children or []}


def workspace_name(res):
    return (res.get("workspace") or res.get("tenant") or res.get("publisher")
            or _("Workspaces"))


def group_resources(resources):
    """Preserve feed order while grouping resources under their AVD workspace."""
    groups = {}
    for res in resources:
        groups.setdefault(workspace_name(res), []).append(res)
    return groups


def _resource_item(res, session):
    state = session.get("state") or "idle"
    running = session.get("running", False)
    rid = res["id"]
    if running:
        children = [item(_("Connecting…"), enabled=False)] if state == "connecting" else []
        children += [item(_("Focus window"), ("focus", rid)),
                     item(_("Disconnect"), ("disconnect", rid))]
    elif state == "connecting":
        children = [item(_("Connecting…"), enabled=False)]
    else:
        children = [item(_("Connect"), ("connect", rid)),
                    item(_("Connect (windowed)"), ("connect-windowed", rid))]
    return item(MARKERS.get(state, "") + res["title"], children=children)


def build_menu(resources, sessions, *, include_footer=True):
    """Menu for ``resources`` (feed dicts, in grid order). ``sessions`` maps a
    resource id to {"state": "idle"|"connecting"|"connected", "running": bool}.
    Set ``include_footer=False`` when embedding workspace groups in a submenu."""
    groups = group_resources(resources)
    items = []
    if not resources:
        items.append(item(_("No workspaces yet"), enabled=False))
    for name, members in groups.items():
        items.append(item(name.upper(), enabled=False))
        items += [_resource_item(r, sessions.get(r["id"], {})) for r in members]
    if include_footer:
        items += [SEPARATOR,
                  item(_("Refresh"), ("refresh",)),
                  item(_("Show window"), ("show",)),
                  item(_("Quit"), ("quit",))]
    return items


def build_accounts_menu(accounts, sessions):
    """Group workspaces by account, preserving ownership in resource actions."""
    items = []
    for account in accounts:
        aid = account['id']
        resources = account['resources']
        if account.get('removed'):
            resources = [r for r in resources if sessions.get((aid, r['id']), {}).get('running')]
        if not resources and account.get('removed'):
            continue
        states = {r['id']: sessions.get((aid, r['id']), {}) for r in resources}
        children = build_menu(resources, states, include_footer=False)
        for group in children:
            for action in group.get('children', []):
                if action.get('action'):
                    kind, rid = action['action']
                    action['action'] = (kind, aid, rid)
        items.append(item(account['upn'] or _('Saved account'), children=children))
    if not items:
        items.append(item(_('No workspaces yet'), enabled=False))
    return items + [SEPARATOR, item(_('Refresh'), ('refresh',)),
                    item(_('Show window'), ('show',)), item(_('Quit'), ('quit',))]
