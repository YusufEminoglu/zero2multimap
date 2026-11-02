# -*- coding: utf-8 -*-
"""02Multimap — main plugin class.

One launch action, shown as a toolbar icon and as an entry in the Plugins
menu. Unloading removes both and tears the workspace dialog down completely:
signals disconnected, canvases released, widgets deleted.
"""
from __future__ import annotations

import os
from qgis.PyQt.QtGui import QIcon

try:
    from qgis.PyQt.QtWidgets import QAction
except ImportError:
    from qgis.PyQt.QtGui import QAction

MENU_NAME = "&02Multimap"


class O2MultiMapPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.icon_path = os.path.join(self.plugin_dir, "icons", "icon.png")
        self.action: QAction | None = None
        self.dialog = None

    def initGui(self) -> None:
        self.action = QAction(
            QIcon(self.icon_path),
            "02Multimap Workspace",
            self.iface.mainWindow()
        )
        self.action.setStatusTip("Open multi-panel synchronized map viewer workspace.")
        self.action.triggered.connect(self.show_dialog)

        self.iface.addToolBarIcon(self.action)
        add_to_menu = getattr(self.iface, "addPluginToMenu", None)
        if callable(add_to_menu):
            add_to_menu(MENU_NAME, self.action)

    def unload(self) -> None:
        if self.dialog is not None:
            dialog, self.dialog = self.dialog, None
            dialog.cleanup()
            dialog.close()
            dialog.setParent(None)
            dialog.deleteLater()
        if self.action is not None:
            action, self.action = self.action, None
            self.iface.removeToolBarIcon(action)
            remove_from_menu = getattr(self.iface, "removePluginMenu", None)
            if callable(remove_from_menu):
                remove_from_menu(MENU_NAME, action)
            action.triggered.disconnect(self.show_dialog)
            action.setParent(None)
            action.deleteLater()

    # ───────────────────────── Dialog lifecycle ─────────────────────────

    def show_dialog(self) -> None:
        if self.dialog is None:
            from .dialog import MultiMapDialog

            self.dialog = MultiMapDialog(self.iface, self.iface.mainWindow())

        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
