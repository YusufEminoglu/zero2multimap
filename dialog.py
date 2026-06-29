# -*- coding: utf-8 -*-
"""PlanX MultiMap — main workspace dialog.

This module provides the multi-panel grid container, synchronization logic,
and cursor tracking mechanisms. It is designed to run in QGIS 4 (PyQt6).
"""
from __future__ import annotations

import os
from qgis.PyQt.QtCore import Qt, QObject, QEvent, QPoint, pyqtSignal
from qgis.PyQt.QtGui import QColor, QMouseEvent
from qgis.PyQt.QtWidgets import (
    QDialog,
    QComboBox,
    QCheckBox,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QFrame,
    QWidget,
    QMessageBox,
    QSplitter,
)
from qgis.core import QgsProject, QgsMapLayer, QgsPointXY, QgsCoordinateReferenceSystem
from qgis.gui import QgsMapCanvas, QgsVertexMarker, QgsMapToolPan
from .print_layout import PrintLayoutDialog

# Resolved dynamically to bypass static checks and work on both PyQt5 & PyQt6
WINDOW_FLAGS = Qt.WindowFlags()
if hasattr(Qt, "WindowType"):
    WINDOW_FLAGS |= Qt.WindowType.Window
    WINDOW_FLAGS |= Qt.WindowType.WindowMinMaxButtonsHint
    WINDOW_FLAGS |= Qt.WindowType.WindowCloseButtonHint
else:
    WINDOW_FLAGS |= getattr(Qt, "Window")
    WINDOW_FLAGS |= getattr(Qt, "WindowMinMaxButtonsHint")
    WINDOW_FLAGS |= getattr(Qt, "WindowCloseButtonHint")


def setup_frame_style(frame: QFrame, shape_name: str, shadow_name: str) -> None:
    """Helper to apply QFrame shape and shadow compatibly under PyQt5/PyQt6."""
    shape_cls = getattr(QFrame, "Shape", QFrame)
    shadow_cls = getattr(QFrame, "Shadow", QFrame)
    frame.setFrameShape(getattr(shape_cls, shape_name))
    frame.setFrameShadow(getattr(shadow_cls, shadow_name))


def get_orientation(name: str):
    """Helper to retrieve Qt.Orientation compatibly under PyQt5/PyQt6."""
    orient_cls = getattr(Qt, "Orientation", Qt)
    return getattr(orient_cls, name)



class CanvasEventFilter(QObject):
    """Filters mouse events on the canvas viewport to track coordinates for the crosshair cursor."""

    def __init__(self, panel: MapPanelWidget, on_mouse_move, on_leave):
        super().__init__(panel.canvas.viewport())
        self.panel = panel
        self.on_mouse_move = on_mouse_move
        self.on_leave = on_leave
        
        viewport = panel.canvas.viewport()
        viewport.installEventFilter(self)
        viewport.setMouseTracking(True)
        panel.canvas.setMouseTracking(True)

    def eventFilter(self, obj, event) -> bool:
        event_type = event.type()
        mouse_move = getattr(QEvent, "MouseMove", None)
        if mouse_move is None:
            # PyQt6
            mouse_move = QEvent.Type.MouseMove
            leave = QEvent.Type.Leave
        else:
            # PyQt5
            leave = QEvent.Leave

        if event_type == mouse_move:
            # Extract point position safely for PyQt5/PyQt6
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self.on_mouse_move(self.panel, pos)
        elif event_type == leave:
            self.on_leave(self.panel)
        return super().eventFilter(obj, event)


class MapPanelWidget(QFrame):
    """Encapsulates a single QgsMapCanvas with a styled header control bar."""

    active_changed = pyqtSignal(object)  # Emits self when clicked/activated

    def __init__(self, index: int, iface, parent=None):
        super().__init__(parent)
        self.index = index
        self.iface = iface
        self.mode = "sync"  # "sync", "layer", "theme"
        self.marker: QgsVertexMarker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setObjectName("MapPanel")
        setup_frame_style(self, "StyledPanel", "Raised")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        # Header widget
        self.header = QWidget()
        self.header.setObjectName("PanelHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(6, 4, 6, 4)
        header_layout.setSpacing(6)

        # Panel title
        self.title_label = QLabel(f"Panel {self.index + 1}")
        self.title_label.setObjectName("PanelTitle")
        header_layout.addWidget(self.title_label)

        # Mode Selector
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Sync Main Map", "Focus Layer", "Map Theme"])
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        header_layout.addWidget(self.mode_combo)

        # Layer Selector (visible in layer mode)
        self.layer_combo = QComboBox()
        self.layer_combo.setVisible(False)
        self.layer_combo.currentTextChanged.connect(self.update_canvas_layers)
        header_layout.addWidget(self.layer_combo)

        # Theme Selector (visible in theme mode)
        self.theme_combo = QComboBox()
        self.theme_combo.setVisible(False)
        self.theme_combo.currentTextChanged.connect(self.update_canvas_layers)
        header_layout.addWidget(self.theme_combo)

        header_layout.addStretch(1)
        layout.addWidget(self.header)

        # Map Canvas
        self.canvas = QgsMapCanvas(self)
        self.canvas.setCanvasColor(QColor(240, 242, 245))
        layout.addWidget(self.canvas, 1)

        # Standard pan tool
        self.pan_tool = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.pan_tool)

        # Custom mouse tracking marker
        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.marker.setColor(QColor(46, 204, 113))  # Neon green crosshair
        self.marker.setPenWidth(2)
        self.marker.setIconSize(24)
        self.marker.hide()

    def mousePressEvent(self, event) -> None:
        self.active_changed.emit(self)
        super().mousePressEvent(event)

    def _on_mode_changed(self, mode_text: str) -> None:
        if mode_text == "Sync Main Map":
            self.mode = "sync"
            self.layer_combo.setVisible(False)
            self.theme_combo.setVisible(False)
        elif mode_text == "Focus Layer":
            self.mode = "layer"
            self.layer_combo.setVisible(True)
            self.theme_combo.setVisible(False)
        elif mode_text == "Map Theme":
            self.mode = "theme"
            self.layer_combo.setVisible(False)
            self.theme_combo.setVisible(True)
        self.update_canvas_layers()

    def update_canvas_layers(self) -> None:
        """Applies layers to the map canvas based on selected mode."""
        project = QgsProject.instance()
        
        if self.mode == "sync":
            self.canvas.setLayers(self.iface.mapCanvas().layers())
        elif self.mode == "layer":
            selected_layer_name = self.layer_combo.currentText()
            layers = project.mapLayersByName(selected_layer_name)
            canvas_layers = []
            if layers:
                canvas_layers.append(layers[0])
            
            # Fetch global base layer if selected in main window
            window = self.window()
            if isinstance(window, MultiMapDialog):
                base_layer = window.get_global_base_layer()
                if base_layer and base_layer not in canvas_layers:
                    canvas_layers.append(base_layer)
            self.canvas.setLayers(canvas_layers)
        elif self.mode == "theme":
            theme_name = self.theme_combo.currentText()
            theme_layers = project.mapThemeCollection().mapThemeVisibleLayers(theme_name)
            self.canvas.setLayers(theme_layers)
        
        self.canvas.refresh()


class MultiMapDialog(QDialog):
    """The main floating window that manages the grid of map panels and coordinate syncing."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.panels: list[MapPanelWidget] = []
        self.event_filters: list[CanvasEventFilter] = []
        self._is_syncing = False
        
        # Cursor tracking markers
        self.main_canvas_marker: QgsVertexMarker | None = None

        self.setWindowTitle("02-Multimap: Sync-up Multimaps")
        self.setWindowFlags(WINDOW_FLAGS)
        self.resize(1100, 750)
        self._build_ui()
        self._apply_qss()

        # Listen to project layer/theme events to keep combos updated
        project = QgsProject.instance()
        project.layerWasAdded.connect(self.populate_layer_combos)
        project.layerRemoved.connect(self.populate_layer_combos)
        project.mapThemeCollection().mapThemesChanged.connect(self.populate_theme_combos)
        
        # Sync main canvas changes to sync-mode panels
        self.iface.mapCanvas().layersChanged.connect(self.sync_all_panel_layers)
        self.iface.mapCanvas().extentsChanged.connect(self.on_main_canvas_extent_changed)

        # Build initial 4 panels
        self.set_grid_layout("4 Panels (2x2)")

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(4)

        # Toolbar Frame
        self.toolbar_frame = QFrame()
        self.toolbar_frame.setObjectName("ToolbarFrame")
        toolbar_layout = QHBoxLayout(self.toolbar_frame)
        toolbar_layout.setContentsMargins(8, 6, 8, 6)
        toolbar_layout.setSpacing(12)

        # Grid layout selector
        toolbar_layout.addWidget(QLabel("Grid Layout:"))
        self.grid_combo = QComboBox()
        self.grid_combo.addItems([
            "2 Panels (1x2)",
            "2 Panels (2x1)",
            "3 Panels (1x3)",
            "4 Panels (2x2)",
            "6 Panels (2x3)",
            "8 Panels (2x4)"
        ])
        self.grid_combo.currentTextChanged.connect(self.set_grid_layout)
        toolbar_layout.addWidget(self.grid_combo)

        # Synchronization options
        self.sync_extents_chk = QCheckBox("Sync Navigation")
        self.sync_extents_chk.setChecked(True)
        toolbar_layout.addWidget(self.sync_extents_chk)

        self.sync_main_chk = QCheckBox("Sync with QGIS Canvas")
        self.sync_main_chk.setChecked(True)
        toolbar_layout.addWidget(self.sync_main_chk)

        self.laser_chk = QCheckBox("Laser Crosshair")
        self.laser_chk.setChecked(True)
        self.laser_chk.toggled.connect(self._on_laser_toggled)
        toolbar_layout.addWidget(self.laser_chk)

        # Scale and Extent Align Buttons
        self.match_scale_btn = QPushButton("Match Scale")
        self.match_scale_btn.clicked.connect(self.match_scales_to_active)
        self.match_scale_btn.setToolTip("Sync zoom scales of all panels to match the active panel's scale, preserving centers.")
        toolbar_layout.addWidget(self.match_scale_btn)

        self.match_extent_btn = QPushButton("Match Extent")
        self.match_extent_btn.clicked.connect(self.match_extents_to_active)
        self.match_extent_btn.setToolTip("Sync full extent (center and zoom scale) of all panels to match the active panel.")
        toolbar_layout.addWidget(self.match_extent_btn)

        # Global Base Layer Selector
        toolbar_layout.addWidget(QLabel("Global Base Layer:"))
        self.base_layer_combo = QComboBox()
        self.base_layer_combo.currentTextChanged.connect(self.refresh_layer_panels)
        toolbar_layout.addWidget(self.base_layer_combo)

        toolbar_layout.addStretch(1)

        # Refresh all button
        self.refresh_btn = QPushButton("Refresh All")
        self.refresh_btn.clicked.connect(self.refresh_all_canvases)
        toolbar_layout.addWidget(self.refresh_btn)

        # Print Layout button
        self.print_btn = QPushButton("Print Layout…")
        self.print_btn.clicked.connect(self.show_print_layout)
        self.print_btn.setToolTip("Open print layout dialog to customize and export comparative maps.")
        self.print_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a8f85;
                color: #ffffff;
                border: 1px solid #237a72;
            }
            QPushButton:hover {
                background-color: #319c91;
            }
        """)
        toolbar_layout.addWidget(self.print_btn)

        root_layout.addWidget(self.toolbar_frame)

        # Central grid container
        self.grid_container = QFrame()
        setup_frame_style(self.grid_container, "StyledPanel", "Sunken")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(2, 2, 2, 2)
        self.grid_layout.setSpacing(3)
        root_layout.addWidget(self.grid_container, 1)

        # Status Bar
        self.status_frame = QFrame()
        self.status_frame.setObjectName("StatusFrame")
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(6, 4, 6, 4)
        
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusLabel")
        status_layout.addWidget(self.status_label)
        
        status_layout.addStretch(1)
        
        self.coords_label = QLabel("Cursor Coord: -")
        self.coords_label.setObjectName("CoordsLabel")
        status_layout.addWidget(self.coords_label)
        
        root_layout.addWidget(self.status_frame)

    def _apply_qss(self) -> None:
        """Applies a premium, highly responsive light theme stylesheet matching the 02viz family."""
        self.setStyleSheet("""
            QDialog {
                background-color: #fbfbfd;
                color: #2c3e46;
                font-family: "Segoe UI", Inter, Helvetica, Arial, sans-serif;
            }
            QFrame#ToolbarFrame {
                background-color: #ffffff;
                border: 1px solid #cbd3da;
                border-radius: 6px;
            }
            QFrame#StatusFrame {
                background-color: #eef1f4;
                border-top: 1px solid #cbd3da;
            }
            QLabel {
                color: #2c3e46;
                font-size: 12px;
            }
            QLabel#StatusLabel {
                color: #16323f;
                font-weight: 600;
            }
            QLabel#CoordsLabel {
                color: #2a8f85;
                font-family: "Consolas", monospace;
                font-weight: 600;
            }
            QCheckBox {
                color: #2c3e46;
                spacing: 5px;
                font-size: 12px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                background-color: #ffffff;
                border: 1px solid #cbd3da;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background-color: #2a8f85;
                border: 1px solid #237a72;
            }
            QComboBox {
                background-color: #ffffff;
                color: #16323f;
                border: 1px solid #cbd3da;
                border-radius: 6px;
                padding: 3px 6px;
                min-width: 120px;
                font-size: 11px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 16px;
                border-left: 1px solid #cbd3da;
            }
            QComboBox:hover {
                background-color: #ffffff;
                border-color: #2a8f85;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #16323f;
                border: 1px solid #cbd3da;
                selection-background-color: #2a8f85;
                selection-color: #ffffff;
                outline: 0;
            }
            QPushButton {
                background-color: #eef1f4;
                color: #1f333d;
                border: 1px solid #cbd3da;
                border-radius: 6px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #e4e9ed;
            }
            QPushButton:pressed {
                background-color: #dbe1e6;
            }
            QFrame#MapPanel {
                background-color: #ffffff;
                border: 2px solid #cbd3da;
                border-radius: 8px;
            }
            QFrame#MapPanel[active="true"] {
                border: 2px solid #2a8f85;
            }
            QWidget#PanelHeader {
                background-color: #eef1f4;
                border-bottom: 1px solid #cbd3da;
            }
            QLabel#PanelTitle {
                color: #16323f;
                font-weight: bold;
                font-size: 12px;
            }
            QSplitter::handle {
                background-color: #cbd3da;
            }
            QSplitter::handle:horizontal {
                width: 3px;
            }
            QSplitter::handle:vertical {
                height: 3px;
            }
        """)

    def set_grid_layout(self, layout_name: str) -> None:
        """Destroys current grid canvas widgets and recreates layout with specified size."""
        self._is_syncing = True
        
        # Clean up existing panel filters, markers, and canvases
        for ef in self.event_filters:
            ef.deleteLater()
        self.event_filters.clear()

        # Clean main canvas marker if any
        if self.main_canvas_marker:
            self.iface.mapCanvas().scene().removeItem(self.main_canvas_marker)
            self.main_canvas_marker = None

        # Clean grid layout children
        while self.grid_layout.count() > 0:
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.panels.clear()

        # Parse layout configurations
        rows, cols = 2, 2
        if layout_name == "2 Panels (1x2)":
            rows, cols = 1, 2
        elif layout_name == "2 Panels (2x1)":
            rows, cols = 2, 1
        elif layout_name == "3 Panels (1x3)":
            rows, cols = 1, 3
        elif layout_name == "4 Panels (2x2)":
            rows, cols = 2, 2
        elif layout_name == "6 Panels (2x3)":
            rows, cols = 2, 3
        elif layout_name == "8 Panels (2x4)":
            rows, cols = 2, 4

        count = rows * cols
        main_extent = self.iface.mapCanvas().extent()
        main_crs = self.iface.mapCanvas().mapSettings().destinationCrs()

        for i in range(count):
            panel = MapPanelWidget(i, self.iface, self)
            panel.canvas.setDestinationCrs(main_crs)
            panel.canvas.setExtent(main_extent)
            
            # Listen to panel's extent changes
            panel.canvas.extentsChanged.connect(
                lambda p=panel: self.on_panel_extent_changed(p)
            )
            # Listen to active panel shifts
            panel.active_changed.connect(self.set_active_panel)
            
            # Install mouse event tracking
            ef = CanvasEventFilter(panel, self.on_mouse_moved_in_panel, self.on_mouse_left_panel)
            self.event_filters.append(ef)
            self.panels.append(panel)

        # Build dynamic QSplitter containers based on layout dimensions
        if rows == 1:
            # Single row of panels (e.g. 1x2, 1x3)
            h_splitter = QSplitter(get_orientation("Horizontal"))
            for panel in self.panels:
                h_splitter.addWidget(panel)
            
            # Equal spacing
            sizes = [self.width() // cols] * cols
            h_splitter.setSizes(sizes)
            self.grid_layout.addWidget(h_splitter, 0, 0)
        else:
            # Multi-row stacking (e.g. 2x1, 2x2, 2x3, 2x4)
            v_splitter = QSplitter(get_orientation("Vertical"))
            
            # Split panels into Row 1 and Row 2
            row1_splitter = QSplitter(get_orientation("Horizontal"))
            for idx in range(cols):
                row1_splitter.addWidget(self.panels[idx])
            v_splitter.addWidget(row1_splitter)
            
            row2_splitter = QSplitter(get_orientation("Horizontal"))
            for idx in range(cols, count):
                row2_splitter.addWidget(self.panels[idx])
            v_splitter.addWidget(row2_splitter)
            
            # Set initial equal sizes
            h_sizes = [self.width() // cols] * cols
            row1_splitter.setSizes(h_sizes)
            row2_splitter.setSizes(h_sizes)
            v_splitter.setSizes([self.height() // 2, self.height() // 2])
            
            self.grid_layout.addWidget(v_splitter, 0, 0)

        self.populate_layer_combos()
        self.populate_theme_combos()
        self.sync_all_panel_layers()
        
        self.set_active_panel(self.panels[0])
        self._is_syncing = False
        self.refresh_all_canvases()

    def get_global_base_layer(self) -> QgsMapLayer | None:
        """Returns the layer selected globally as base layer, if any."""
        text = self.base_layer_combo.currentText()
        if not text or text == "None":
            return None
        layers = QgsProject.instance().mapLayersByName(text)
        return layers[0] if layers else None

    # ───────────────────────── Sync & Fill Combos ─────────────────────────

    def populate_layer_combos(self) -> None:
        """Fills the Layer Selector in all panels and the global base layer combobox."""
        layers = [layer.name() for layer in QgsProject.instance().mapLayers().values()]
        layers.sort()

        # Update global base layer
        current_base = self.base_layer_combo.currentText()
        self.base_layer_combo.blockSignals(True)
        self.base_layer_combo.clear()
        self.base_layer_combo.addItem("None")
        self.base_layer_combo.addItems(layers)
        if current_base in layers or current_base == "None":
            self.base_layer_combo.setCurrentText(current_base)
        self.base_layer_combo.blockSignals(False)

        # Update panel combos
        for panel in self.panels:
            current_layer = panel.layer_combo.currentText()
            panel.layer_combo.blockSignals(True)
            panel.layer_combo.clear()
            panel.layer_combo.addItems(layers)
            if current_layer in layers:
                panel.layer_combo.setCurrentText(current_layer)
            panel.layer_combo.blockSignals(False)

    def populate_theme_combos(self) -> None:
        """Fills the Map Theme Selector in all panels."""
        themes = QgsProject.instance().mapThemeCollection().mapThemes()
        themes.sort()

        for panel in self.panels:
            current_theme = panel.theme_combo.currentText()
            panel.theme_combo.blockSignals(True)
            panel.theme_combo.clear()
            panel.theme_combo.addItems(themes)
            if current_theme in themes:
                panel.theme_combo.setCurrentText(current_theme)
            panel.theme_combo.blockSignals(False)

    def sync_all_panel_layers(self) -> None:
        """Force updates map canvases that are in 'Sync Main Map' mode."""
        for panel in self.panels:
            panel.update_canvas_layers()

    def refresh_layer_panels(self) -> None:
        """Triggered when global base layer changes. Updates panels in focus layer mode."""
        for panel in self.panels:
            if panel.mode == "layer":
                panel.update_canvas_layers()

    def set_active_panel(self, active_panel: MapPanelWidget) -> None:
        """Visually marks a panel as active by updating styling dynamic property."""
        self.active_panel = active_panel
        for panel in self.panels:
            panel.setProperty("active", panel == active_panel)
            panel.style().unpolish(panel)
            panel.style().polish(panel)
        self.status_label.setText(f"Active Canvas: Panel {active_panel.index + 1}")

    def refresh_all_canvases(self) -> None:
        """Triggers a render refresh on all canvases."""
        for panel in self.panels:
            panel.canvas.refresh()

    def show_print_layout(self) -> None:
        """Instantiates and displays the print layout exporter dialog."""
        dlg = PrintLayoutDialog(self, self.iface)
        dlg.exec()

    # ───────────────────────── Navigation Sync ─────────────────────────

    def on_panel_extent_changed(self, trigger_panel: MapPanelWidget) -> None:
        """Synchronizes other panels when one is panned or zoomed."""
        if self._is_syncing or not self.sync_extents_chk.isChecked():
            return
        
        self._is_syncing = True
        try:
            extent = trigger_panel.canvas.extent()
            
            # Sync to all other panels
            for panel in self.panels:
                if panel == trigger_panel:
                    continue
                panel.canvas.blockSignals(True)
                panel.canvas.setExtent(extent)
                panel.canvas.refresh()
                panel.canvas.blockSignals(False)

            # Sync to main QGIS canvas if option is active
            if self.sync_main_chk.isChecked():
                main_canvas = self.iface.mapCanvas()
                main_canvas.blockSignals(True)
                main_canvas.setExtent(extent)
                main_canvas.refresh()
                main_canvas.blockSignals(False)
        finally:
            self._is_syncing = False

    def on_main_canvas_extent_changed(self) -> None:
        """Syncs all panels when the main QGIS map canvas extent changes."""
        if self._is_syncing or not self.sync_main_chk.isChecked() or not self.sync_extents_chk.isChecked():
            return
        
        self._is_syncing = True
        try:
            extent = self.iface.mapCanvas().extent()
            for panel in self.panels:
                panel.canvas.blockSignals(True)
                panel.canvas.setExtent(extent)
                panel.canvas.refresh()
                panel.canvas.blockSignals(False)
        finally:
            self._is_syncing = False

    # ───────────────────────── Scale/Extent Alignment ─────────────────────────

    def match_scales_to_active(self) -> None:
        """Aligns the zoom scale of all panels to match the active panel's scale, preserving their centers."""
        if not hasattr(self, "active_panel") or not self.active_panel:
            if self.panels:
                self.active_panel = self.panels[0]
            else:
                return

        target_scale = self.active_panel.canvas.scale()

        self._is_syncing = True
        try:
            for panel in self.panels:
                if panel == self.active_panel:
                    continue
                panel.canvas.blockSignals(True)
                panel.canvas.zoomScale(target_scale)
                panel.canvas.refresh()
                panel.canvas.blockSignals(False)

            if self.sync_main_chk.isChecked():
                main_canvas = self.iface.mapCanvas()
                main_canvas.blockSignals(True)
                main_canvas.zoomScale(target_scale)
                main_canvas.refresh()
                main_canvas.blockSignals(False)
        finally:
            self._is_syncing = False

    def match_extents_to_active(self) -> None:
        """Aligns the full extent of all panels to match the active panel's extent."""
        if not hasattr(self, "active_panel") or not self.active_panel:
            if self.panels:
                self.active_panel = self.panels[0]
            else:
                return

        target_extent = self.active_panel.canvas.extent()

        self._is_syncing = True
        try:
            for panel in self.panels:
                if panel == self.active_panel:
                    continue
                panel.canvas.blockSignals(True)
                panel.canvas.setExtent(target_extent)
                panel.canvas.refresh()
                panel.canvas.blockSignals(False)

            if self.sync_main_chk.isChecked():
                main_canvas = self.iface.mapCanvas()
                main_canvas.blockSignals(True)
                main_canvas.setExtent(target_extent)
                main_canvas.refresh()
                main_canvas.blockSignals(False)
        finally:
            self._is_syncing = False

    # ───────────────────────── Laser Crosshair ─────────────────────────

    def on_mouse_moved_in_panel(self, source_panel: MapPanelWidget, pos: QPoint) -> None:
        """Triggered when mouse moves over a panel map canvas viewport."""
        if not self.laser_chk.isChecked():
            return
        
        # Convert local pixel coordinate to Map Coordinates (QgsPointXY)
        map_point = source_panel.canvas.mapSettings().mapToPixel().toMapCoordinates(pos.x(), pos.y())
        
        self.coords_label.setText(f"X: {map_point.x():.2f}, Y: {map_point.y():.2f}")

        # Update markers on all other panel canvases
        for panel in self.panels:
            if panel == source_panel:
                panel.marker.hide()
                continue
            
            panel.marker.setCenter(map_point)
            panel.marker.show()

        # Render crosshair on main QGIS canvas
        main_canvas = self.iface.mapCanvas()
        if self.sync_main_chk.isChecked():
            if not self.main_canvas_marker:
                self.main_canvas_marker = QgsVertexMarker(main_canvas)
                self.main_canvas_marker.setIconType(QgsVertexMarker.ICON_CROSS)
                self.main_canvas_marker.setColor(QColor(231, 76, 60))  # Sleek neon red
                self.main_canvas_marker.setPenWidth(2)
                self.main_canvas_marker.setIconSize(24)
            self.main_canvas_marker.setCenter(map_point)
            self.main_canvas_marker.show()

    def on_mouse_left_panel(self, source_panel: MapPanelWidget) -> None:
        """Hides laser cursors when mouse leaves a panel canvas."""
        for panel in self.panels:
            panel.marker.hide()
        if self.main_canvas_marker:
            self.main_canvas_marker.hide()

    def _on_laser_toggled(self, checked: bool) -> None:
        if not checked:
            for panel in self.panels:
                panel.marker.hide()
            if self.main_canvas_marker:
                self.main_canvas_marker.hide()

    # ───────────────────────── Window Close ─────────────────────────

    def closeEvent(self, event) -> None:
        # Hide markers before closing
        for panel in self.panels:
            panel.marker.hide()
        if self.main_canvas_marker:
            self.iface.mapCanvas().scene().removeItem(self.main_canvas_marker)
            self.main_canvas_marker = None
        super().closeEvent(event)
