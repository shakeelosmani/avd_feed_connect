"""System-tray icon over D-Bus: StatusNotifierItem + com.canonical.dbusmenu.

GTK4 has no tray API and the GTK3 AppIndicator library can't be loaded into a
GTK4 process, but a modern tray icon is just two D-Bus objects, so they're
exported here with plain Gio:
  /StatusNotifierItem  the icon (org.kde.StatusNotifierItem)
  /MenuBar             its right-click menu (com.canonical.dbusmenu)
and registered with the session's org.kde.StatusNotifierWatcher (KDE, Waybar,
the GNOME AppIndicator extension, ...). The item registers under the
connection's unique name, so no extra bus name is owned (the Flatpak only needs
--talk-name=org.kde.StatusNotifierWatcher).

``available`` is True only while a watcher has accepted the item and reports a
tray host, so the app can fall back to a normal window where nothing would show
the icon (e.g. GNOME without the extension).
"""

from gi.repository import Gio, GLib

WATCHER = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

_ITEM_XML = """
<node><interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="i" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <signal name="NewTitle"/>
  <signal name="NewIcon"/>
  <signal name="NewToolTip"/>
  <signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"""

_MENU_XML = """
<node><interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
  <method name="GetLayout">
    <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
    <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
  </method>
  <method name="GetGroupProperties">
    <arg type="ai" direction="in"/><arg type="as" direction="in"/>
    <arg type="a(ia{sv})" direction="out"/>
  </method>
  <method name="GetProperty">
    <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
  </method>
  <method name="Event">
    <arg type="i" direction="in"/><arg type="s" direction="in"/>
    <arg type="v" direction="in"/><arg type="u" direction="in"/>
  </method>
  <method name="EventGroup">
    <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
  </method>
  <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
  <method name="AboutToShowGroup">
    <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
  </method>
  <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
  <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
  <signal name="ItemActivationRequested"><arg type="i"/><arg type="u"/></signal>
</interface></node>"""


def _props(node):
    """dbusmenu properties of one menu item (see gui.tray_menu for the shape)."""
    if node.get("type") == "separator":
        return {"type": GLib.Variant("s", "separator")}
    # "_" marks a mnemonic in dbusmenu labels; double it to show it literally.
    props = {"label": GLib.Variant("s", node.get("label", "").replace("_", "__"))}
    if not node.get("enabled", True):
        props["enabled"] = GLib.Variant("b", False)
    if node.get("children"):
        props["children-display"] = GLib.Variant("s", "submenu")
    return props


class TrayIcon:
    """A tray icon whose right-click menu is ``set_menu``'s item list.

    on_activate()          left click on the icon
    on_action(action)      a menu item with an ``action`` was clicked
    on_availability(bool)  a tray host appeared / went away
    All callbacks run on the GLib main loop.
    """

    def __init__(self, conn, app_id, title, icon_name, icon_theme_path,
                 on_activate, on_action, on_availability):
        self._conn = conn
        self._app_id, self._title = app_id, title
        self._icon_name, self._icon_path = icon_name, icon_theme_path
        self._on_activate = on_activate
        self._on_action = on_action
        self._on_availability = on_availability
        self._tooltip = ""
        self._revision = 1
        self._nodes = {0: {"label": "", "children": []}}  # id → item; 0 is the root
        self._children = {0: []}                          # id → child ids
        self._reg_ids = []
        self._watch_id = 0
        self.available = False

    # ---- lifecycle ---------------------------------------------------------
    def start(self):
        for path, xml, call, get in (
                (ITEM_PATH, _ITEM_XML, self._item_call, self._item_get),
                (MENU_PATH, _MENU_XML, self._menu_call, self._menu_get)):
            iface = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
            self._reg_ids.append(self._conn.register_object(path, iface, call, get, None))
        # Waybar/plasmashell own the watcher; re-register each time it (re)appears.
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._conn, WATCHER, Gio.BusNameWatcherFlags.NONE,
            self._watcher_appeared, self._watcher_vanished)

    def stop(self):
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        for rid in self._reg_ids:
            self._conn.unregister_object(rid)
        self._reg_ids = []
        self._set_available(False)

    def _set_available(self, value):
        if value != self.available:
            self.available = value
            self._on_availability(value)

    def _watcher_appeared(self, conn, name, owner):
        def registered(c, res):
            try:
                c.call_finish(res)
            except GLib.Error:
                self._set_available(False)
                return
            c.call(WATCHER, "/StatusNotifierWatcher", "org.freedesktop.DBus.Properties",
                   "Get", GLib.Variant("(ss)", (WATCHER, "IsStatusNotifierHostRegistered")),
                   GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, -1, None, host_known)

        def host_known(c, res):
            try:
                host = c.call_finish(res).unpack()[0]
            except GLib.Error:
                host = True     # watcher without the property: trust the registration
            self._set_available(bool(host))

        conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (conn.get_unique_name(),)), None,
                  Gio.DBusCallFlags.NONE, -1, None, registered)

    def _watcher_vanished(self, conn, name):
        self._set_available(False)

    # ---- public updates ----------------------------------------------------
    def set_tooltip(self, text):
        if text != self._tooltip:
            self._tooltip = text
            self._emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)

    def set_menu(self, items):
        """Replace the whole menu and tell the host to re-read it."""
        self._nodes = {0: {"label": "", "children": items}}
        self._children = {0: []}
        next_id = [1]

        def add(parent, node):
            nid = next_id[0]
            next_id[0] += 1
            self._nodes[nid] = node
            self._children[nid] = []
            self._children[parent].append(nid)
            for child in node.get("children", []):
                add(nid, child)

        for node in items:
            add(0, node)
        self._revision += 1
        self._emit(MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                   GLib.Variant("(ui)", (self._revision, 0)))

    def _emit(self, path, iface, signal, params):
        try:
            self._conn.emit_signal(None, path, iface, signal, params)
        except GLib.Error:
            pass

    # ---- org.kde.StatusNotifierItem ---------------------------------------
    def _item_get(self, conn, sender, path, iface, prop):
        no_pixmap = GLib.Variant("a(iiay)", [])
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", self._app_id),
            "Title": GLib.Variant("s", self._title),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", self._icon_name),
            "IconThemePath": GLib.Variant("s", self._icon_path or ""),
            "IconPixmap": no_pixmap,
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": no_pixmap,
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": no_pixmap,
            "ToolTip": GLib.Variant("(sa(iiay)ss)",
                                    (self._icon_name, [], self._title, self._tooltip)),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(prop)

    def _item_call(self, conn, sender, path, iface, method, params, invocation):
        invocation.return_value(None)
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(lambda: (self._on_activate(), False)[1])

    # ---- com.canonical.dbusmenu -------------------------------------------
    def _menu_get(self, conn, sender, path, iface, prop):
        return {"Version": GLib.Variant("u", 3),
                "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"),
                "IconThemePath": GLib.Variant("as", [])}.get(prop)

    def _filtered(self, nid, names):
        props = _props(self._nodes[nid]) if nid else {"children-display": GLib.Variant("s", "submenu")}
        return {k: v for k, v in props.items() if not names or k in names}

    def _layout(self, nid, depth, names):
        kids = [] if depth == 0 else [
            GLib.Variant("(ia{sv}av)", self._layout(c, depth - 1, names))
            for c in self._children.get(nid, [])]
        return (nid, self._filtered(nid, names), kids)

    def _menu_call(self, conn, sender, path, iface, method, params, invocation):
        args = params.unpack()
        if method == "GetLayout":
            parent, depth, names = args
            if parent not in self._nodes:
                parent = 0
            invocation.return_value(GLib.Variant(
                "(u(ia{sv}av))", (self._revision, self._layout(parent, depth, names))))
        elif method == "GetGroupProperties":
            ids, names = args
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (
                [(i, self._filtered(i, names)) for i in ids if i in self._nodes],)))
        elif method == "GetProperty":
            nid, name = args
            value = self._filtered(nid, [name]).get(name) if nid in self._nodes else None
            if value is None:
                invocation.return_dbus_error("com.canonical.dbusmenu.Error.UnknownProperty",
                                             f"no property {name} on item {nid}")
            else:
                invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            nid, event = args[0], args[1]
            invocation.return_value(None)
            self._event(nid, event)
        elif method == "EventGroup":
            events = args[0]
            invocation.return_value(GLib.Variant("(ai)", (
                [e[0] for e in events if e[0] not in self._nodes],)))
            for e in events:
                self._event(e[0], e[1])
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _event(self, nid, event):
        action = self._nodes.get(nid, {}).get("action")
        if event == "clicked" and action:
            GLib.idle_add(lambda: (self._on_action(action), False)[1])
