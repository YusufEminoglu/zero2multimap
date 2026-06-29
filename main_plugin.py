# -*- coding: utf-8 -*-
"""02-Multimap — main plugin class.

Integrates the multi-panel workspace into QGIS 4 menus and provides
toolbar launch shortcuts.
"""
from __future__ import annotations

import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu


class O2MultiMapPlugin:
    MENU_NAME = "&02-Multimap"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.icon_path = os.path.join(self.plugin_dir, "icons", "icon.png")
        self.action: QAction | None = None
        self.dialog = None
        self.menu: QMenu | None = None

    def initGui(self) -> None:
        self.action = QAction(
            QIcon(self.icon_path), 
            "02-Multimap Workspace", 
            self.iface.mainWindow()
        )
        self.action.setStatusTip("Open multi-panel synchronized map viewer workspace.")
        self.action.triggered.connect(self.show_dialog)
        
        # Add a custom top-level menu
        self.menu = QMenu(self.MENU_NAME, self.iface.mainWindow().menuBar())
        self.iface.mainWindow().menuBar().addMenu(self.menu)
        self.menu.addAction(self.action)
        
        # Add to standard toolbar
        self.iface.addToolBarIcon(self.action)

    def unload(self) -> None:
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.menu:
            self.menu.deleteLater()
            self.menu = None
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
