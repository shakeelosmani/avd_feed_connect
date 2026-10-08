"""Tray right-click menu layout: workspaces as headers, one submenu per resource.

Pure data (no GTK/D-Bus), so it can be unit-tested. Each item is a dict:
  {"label": str, "enabled": bool, "action": tuple | None, "children": [items]}
or the separator {"type": "separator"}. ``action`` is handed back to the app
when the item is clicked, e.g. ("connect", "<resource id>").
"""

SEPARATOR = {"type": "separator"}

# Label prefix per session state (idle has none).
MARKERS = {"connected": "● ", "connecting": "○ "}


def item(label, action=None, enabled=True, children=None):
    return {"label": label, "enabled": enabled, "action": action,
            "children": children or []}


def workspace_name(res):
    return res.get("tenant") or res.get("publisher") or "Workspaces"


def _resource_item(res, session):
    state = session.get("state") or "idle"
    running = session.get("running", False)
    rid = res["id"]
    if running:
        children = [item("Connecting…", enabled=False)] if state == "connecting" else []
        children += [item("Focus window", ("focus", rid)),
                     item("Disconnect", ("disconnect", rid))]
    elif state == "connecting":
        children = [item("Connecting…", enabled=False)]
    else:
        children = [item("Connect", ("connect", rid)),
                    item("Connect (windowed)", ("connect-windowed", rid))]
    return item(MARKERS.get(state, "") + res["title"], children=children)


def build_menu(resources, sessions):
    """Menu for ``resources`` (feed dicts, in grid order). ``sessions`` maps a
    resource id to {"state": "idle"|"connecting"|"connected", "running": bool}."""
    groups = {}
    for res in resources:
        groups.setdefault(workspace_name(res), []).append(res)
    items = []
    if not resources:
        items.append(item("No workspaces yet", enabled=False))
    for name, members in groups.items():
        items.append(item(name.upper(), enabled=False))
        items += [_resource_item(r, sessions.get(r["id"], {})) for r in members]
    items += [SEPARATOR,
              item("Refresh", ("refresh",)),
              item("Show window", ("show",)),
              item("Quit", ("quit",))]
    return items
