# -*- coding: utf-8 -*-
"""02Multimap — main plugin class.

Toolbar-only integration: a single launch icon, no top-level QGIS menu.
"""
from __future__ import annotations

import os
from qgis.PyQt.QtGui import QIcon

try:
    from qgis.PyQt.QtWidgets import QAction
except ImportError:
    from qgis.PyQt.QtGui import QAction


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

        # Add to standard toolbar
        self.iface.addToolBarIcon(self.action)

    def unload(self) -> None:
        if self.action:
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
