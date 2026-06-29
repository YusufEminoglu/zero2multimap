# -*- coding: utf-8 -*-
"""PlanX MultiMap — main plugin class.

Integrates the multi-panel workspace into QGIS 4 menus under the 'PlanX' menu group
and provides toolbar launch shortcuts.
"""
from __future__ import annotations

import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox


class PlanXMultiMapPlugin:
    MENU_NAME = "PlanX"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.icon_path = os.path.join(self.plugin_dir, "icons", "icon.png")
        self.action: QAction | None = None
        self.dialog = None

    def initGui(self) -> None:
        self.action = QAction(
            QIcon(self.icon_path), 
            "PlanX MultiMap Workspace", 
            self.iface.mainWindow()
        )
        self.action.setStatusTip("Open multi-panel synchronized map viewer workspace.")
        self.action.triggered.connect(self.show_dialog)
        
        # Add to the unified PlanX toolbar & menu
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.MENU_NAME, self.action)

    def unload(self) -> None:
        if self.action:
            self.iface.removePluginMenu(self.MENU_NAME, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.dialog:
            self.dialog.close()
            self.dialog = None

    # ───────────────────────── Dialog lifecycle ─────────────────────────

    def show_dialog(self) -> None:
        if self.dialog is None:
            from .dialog import MultiMapDialog

            self.dialog = MultiMapDialog(self.iface, self.iface.mainWindow())
            
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
