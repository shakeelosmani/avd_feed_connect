"""Sample Contoso feed for demo / screenshot mode (AVD_DEMO=1).

Anonymous, fixed data so documentation screenshots and the showcase video carry
no real account or organization details. Never used in normal operation.
"""


def demo_resources():
    """The fixed sample workspace list shown in demo mode."""
    return [
        {"id": "d1", "title": "Finance Desktop", "type": "Desktop",
         "tenant": "Contoso Desktops", "workspace": "Contoso Desktops", "icon32": None},
        {"id": "d2", "title": "Design Studio", "type": "Desktop",
         "tenant": "Contoso Desktops", "workspace": "Contoso Desktops", "icon32": None},
        {"id": "d5", "title": "DR Failover", "type": "Desktop",
         "tenant": "Contoso Desktops", "workspace": "Contoso Desktops", "icon32": None},
        {"id": "d3", "title": "Ops Console", "type": "RemoteApp",
         "tenant": "Contoso Apps", "workspace": "Contoso Apps", "icon32": None},
        {"id": "d4", "title": "Dev Sandbox", "type": "Desktop",
         "tenant": "Contoso Dev", "workspace": "Contoso Dev", "icon32": None},
    ]
