# -*- coding: utf-8 -*-
"""PlanX MultiMap — main workspace dialog.

This module provides the multi-panel grid container, navigation synchronization,
laser crosshair tracking, extent bounding box overlays, legend micro-cards,
time-series auto-fill, and print/HTML export triggers.
Designed to run on QGIS 3.40 LTR and QGIS 4 (PyQt5 / PyQt6).
"""
from __future__ import annotations

import contextlib
import re
from qgis.PyQt.QtCore import Qt, QObject, QEvent, QPoint, pyqtSignal
from qgis.PyQt.QtGui import QColor
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
    QSplitter,
    QTextBrowser,
    QMessageBox,
    QSlider,
    QMenu,
    QWidgetAction,
    QFileDialog,
)
from qgis.core import (
    QgsProject,
    QgsMapLayer,
    QgsCoordinateTransform,
    QgsCsException,
    QgsWkbTypes,
    QgsGeometry,
    QgsVectorLayer,
    QgsRasterLayer,
)
from qgis.gui import QgsMapCanvas, QgsVertexMarker, QgsMapToolPan, QgsRubberBand
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
            mouse_move = QEvent.Type.MouseMove
            leave = QEvent.Type.Leave
        else:
            leave = QEvent.Leave

        if event_type == mouse_move:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self.on_mouse_move(self.panel, pos)
        elif event_type == leave:
            self.on_leave(self.panel)
        return super().eventFilter(obj, event)


class MapPanelWidget(QFrame):
    """Encapsulates a single QgsMapCanvas with a styled header control bar and legend overlay."""

    active_changed = pyqtSignal(object)  # Emits self when clicked/activated

    def __init__(self, index: int, iface, parent=None):
        super().__init__(parent)
        self.index = index
        self.iface = iface
        self.mode = "sync"  # "sync", "layer", "theme"
        self.marker: QgsVertexMarker | None = None
        self.legend_card: QFrame | None = None
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
        header_layout.setSpacing(4)

        # Panel title badge
        self.title_label = QLabel(f"Panel {self.index + 1}")
        self.title_label.setObjectName("PanelTitle")
        header_layout.addWidget(self.title_label)

        # Mode selector
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "Follow Main Map",
            "Compare One Layer",
            "Use Map Theme",
        ])
        self.mode_combo.setToolTip(
            "Choose what this panel shows. Use 'Compare One Layer' to give "
            "every panel a different layer."
        )
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        header_layout.addWidget(self.mode_combo)

        # Layer Selector (visible in layer mode)
        self.layer_combo = QComboBox()
        self.layer_combo.setVisible(False)
        self.layer_combo.setToolTip(
            "The selected layer is isolated in this panel. Repeat with a "
            "different layer in each panel, or use Auto-fill."
        )
        self.layer_combo.currentTextChanged.connect(self.update_canvas_layers)
        header_layout.addWidget(self.layer_combo)

        # Theme Selector (visible in theme mode)
        self.theme_combo = QComboBox()
        self.theme_combo.setVisible(False)
        self.theme_combo.currentTextChanged.connect(self.update_canvas_layers)
        header_layout.addWidget(self.theme_combo)

        header_layout.addStretch(1)

        # Layer Type Badge
        self.badge_label = QLabel()
        self.badge_label.setObjectName("TypeBadge")
        self.badge_label.setVisible(False)
        header_layout.addWidget(self.badge_label)

        # Quick Panel Actions
        self.zoom_layer_btn = QPushButton("🔍")
        self.zoom_layer_btn.setToolTip("Zoom to selected layer extent")
        self.zoom_layer_btn.setObjectName("PanelHeaderIconBtn")
        self.zoom_layer_btn.setFixedWidth(26)
        self.zoom_layer_btn.clicked.connect(self.zoom_to_selected_layer)
        header_layout.addWidget(self.zoom_layer_btn)

        self.legend_btn = QPushButton("🎨")
        self.legend_btn.setToolTip("Toggle layer legend overlay")
        self.legend_btn.setObjectName("PanelHeaderIconBtn")
        self.legend_btn.setFixedWidth(26)
        self.legend_btn.clicked.connect(self.toggle_legend_overlay)
        header_layout.addWidget(self.legend_btn)

        self.opacity_btn = QPushButton("🌓")
        self.opacity_btn.setToolTip("Adjust focus layer opacity")
        self.opacity_btn.setObjectName("PanelHeaderIconBtn")
        self.opacity_btn.setFixedWidth(26)
        self.opacity_btn.clicked.connect(self._show_opacity_menu)
        header_layout.addWidget(self.opacity_btn)

        self.snapshot_btn = QPushButton("📷")
        self.snapshot_btn.setToolTip("Save image snapshot of this panel")
        self.snapshot_btn.setObjectName("PanelHeaderIconBtn")
        self.snapshot_btn.setFixedWidth(26)
        self.snapshot_btn.clicked.connect(self.save_panel_snapshot)
        header_layout.addWidget(self.snapshot_btn)

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
        self.marker.setColor(QColor(46, 204, 113))
        self.marker.setPenWidth(2)
        self.marker.setIconSize(24)
        self.marker.hide()

    def mousePressEvent(self, event) -> None:
        self.active_changed.emit(self)
        super().mousePressEvent(event)

    def _on_mode_changed(self, mode_text: str) -> None:
        if mode_text in ("Follow Main Map", "Sync Main Map"):
            self.mode = "sync"
            self.layer_combo.setVisible(False)
            self.theme_combo.setVisible(False)
        elif mode_text in ("Compare One Layer", "Focus Layer"):
            self.mode = "layer"
            self.layer_combo.setVisible(True)
            self.theme_combo.setVisible(False)
        elif mode_text in ("Use Map Theme", "Map Theme"):
            self.mode = "theme"
            self.layer_combo.setVisible(False)
            self.theme_combo.setVisible(True)
        self.update_canvas_layers()

    def selected_layer(self) -> QgsMapLayer | None:
        """Return the focus layer selected by stable project layer ID."""
        layer_id = self.layer_combo.currentData()
        if not layer_id:
            return None
        return QgsProject.instance().mapLayer(layer_id)

    def set_comparison_layer(self, layer: QgsMapLayer) -> None:
        """Switch this panel to one-layer comparison mode for ``layer``."""
        layer_index = self.layer_combo.findData(layer.id())
        if layer_index < 0:
            return
        self.layer_combo.blockSignals(True)
        self.layer_combo.setCurrentIndex(layer_index)
        self.layer_combo.blockSignals(False)
        self.mode_combo.setCurrentText("Compare One Layer")
        self.update_canvas_layers()

    def update_canvas_layers(self) -> None:
        """Applies layers to the map canvas based on selected mode."""
        project = QgsProject.instance()

        if self.mode == "sync":
            self.canvas.setLayers(self.iface.mapCanvas().layers())
            self.badge_label.setText("Sync")
            self.badge_label.setVisible(True)
        elif self.mode == "layer":
            canvas_layers = []
            selected_layer = self.selected_layer()
            if selected_layer:
                canvas_layers.append(selected_layer)
                if isinstance(selected_layer, QgsVectorLayer):
                    self.badge_label.setText("Vector")
                elif isinstance(selected_layer, QgsRasterLayer):
                    self.badge_label.setText("Raster")
                else:
                    self.badge_label.setText("Layer")
                self.badge_label.setVisible(True)
            else:
                self.badge_label.setVisible(False)

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
            self.badge_label.setText("Theme")
            self.badge_label.setVisible(True)

        if self.legend_card and self.legend_card.isVisible():
            self._rebuild_legend_overlay()

        self.canvas.refresh()

    def zoom_to_selected_layer(self) -> None:
        """Zoom this panel canvas to the bounding box of its focus layer."""
        layer = self.selected_layer()
        if layer:
            extent = layer.extent()
            if not extent.isEmpty():
                self.canvas.setExtent(extent)
                self.canvas.refresh()

    def toggle_legend_overlay(self) -> None:
        """Toggle floating micro-legend overlay card on this panel."""
        if self.legend_card and self.legend_card.isVisible():
            self.legend_card.hide()
        else:
            self._rebuild_legend_overlay()

    def _rebuild_legend_overlay(self) -> None:
        if self.legend_card:
            self.legend_card.deleteLater()
            self.legend_card = None

        layer = self.selected_layer()
        if not layer or not layer.isValid():
            return

        self.legend_card = QFrame(self.canvas)
        self.legend_card.setObjectName("LegendCard")
        setup_frame_style(self.legend_card, "StyledPanel", "Plain")

        card_layout = QVBoxLayout(self.legend_card)
        card_layout.setContentsMargins(6, 4, 6, 4)
        card_layout.setSpacing(3)

        title = QLabel(layer.name())
        title.setStyleSheet("font-weight: bold; font-size: 10px; color: #2a8f85;")
        card_layout.addWidget(title)

        if hasattr(layer, "renderer") and layer.renderer():
            renderer = layer.renderer()
            if hasattr(renderer, "categories"):
                for cat in renderer.categories()[:5]:
                    row = QHBoxLayout()
                    row.setSpacing(4)
                    box = QLabel()
                    box.setFixedSize(10, 10)
                    col = cat.symbol().color().name() if cat.symbol() else "#2a8f85"
                    box.setStyleSheet(f"background-color: {col}; border: 1px solid #777; border-radius: 2px;")
                    lbl = QLabel(cat.label() or str(cat.value()))
                    lbl.setStyleSheet("font-size: 10px; color: #16323f;")
                    row.addWidget(box)
                    row.addWidget(lbl)
                    card_layout.addLayout(row)
            elif hasattr(renderer, "symbol") and renderer.symbol():
                row = QHBoxLayout()
                row.setSpacing(4)
                box = QLabel()
                box.setFixedSize(10, 10)
                col = renderer.symbol().color().name()
                box.setStyleSheet(f"background-color: {col}; border: 1px solid #777; border-radius: 2px;")
                lbl = QLabel("Single Symbol")
                lbl.setStyleSheet("font-size: 10px; color: #16323f;")
                row.addWidget(box)
                row.addWidget(lbl)
                card_layout.addLayout(row)

        self.legend_card.setStyleSheet("""
            QFrame#LegendCard {
                background-color: rgba(255, 255, 255, 0.94);
                border: 1px solid #cbd3da;
                border-radius: 5px;
            }
        """)
        self.legend_card.move(10, max(10, self.canvas.height() - 110))
        self.legend_card.show()

    def _show_opacity_menu(self) -> None:
        """Open a small popup menu with a layer opacity slider."""
        layer = self.selected_layer()
        if not layer:
            return

        menu = QMenu(self)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(10, 100)
        slider.setValue(int(layer.opacity() * 100))
        slider.setFixedWidth(120)

        def _on_opacity_changed(val: int):
            layer.setOpacity(val / 100.0)
            self.canvas.refresh()

        slider.valueChanged.connect(_on_opacity_changed)

        action = QWidgetAction(menu)
        action.setDefaultWidget(slider)
        menu.addAction(action)
        menu.exec(self.opacity_btn.mapToGlobal(QPoint(0, self.opacity_btn.height())))

    def save_panel_snapshot(self) -> None:
        """Save a quick PNG image snapshot of this map panel canvas."""
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            f"Save Panel {self.index + 1} Snapshot",
            "",
            "PNG Image (*.png);;JPEG Image (*.jpg)",
        )
        if filepath:
            pixmap = self.canvas.grab()
            pixmap.save(filepath)


class MultiMapDialog(QDialog):
    """The main floating window that manages the grid of map panels and coordinate syncing."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.panels: list[MapPanelWidget] = []
        self.event_filters: list[CanvasEventFilter] = []
        self.rubber_bands: dict[MapPanelWidget, QgsRubberBand] = {}
        self.rows = 2
        self.cols = 2
        self._is_syncing = False
        self.active_panel: MapPanelWidget | None = None

        self.main_canvas_marker: QgsVertexMarker | None = None

        self.setWindowTitle("02Multimap: Sync-up Map Layers")
        self.setWindowFlags(WINDOW_FLAGS)
        self.resize(1180, 790)
        self._build_ui()
        self._apply_qss()

        project = QgsProject.instance()
        project.layerWasAdded.connect(self.populate_layer_combos)
        project.layerRemoved.connect(self.populate_layer_combos)
        project.mapThemeCollection().mapThemesChanged.connect(self.populate_theme_combos)

        self.iface.mapCanvas().layersChanged.connect(self.sync_all_panel_layers)
        self.iface.mapCanvas().extentsChanged.connect(self.on_main_canvas_extent_changed)

        self.grid_combo.blockSignals(True)
        self.grid_combo.setCurrentText("4 Panels (2x2)")
        self.grid_combo.blockSignals(False)
        self.set_grid_layout("4 Panels (2x2)")

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(4)

        # Toolbar Frame
        self.toolbar_frame = QFrame()
        self.toolbar_frame.setObjectName("ToolbarFrame")
        toolbar_layout = QVBoxLayout(self.toolbar_frame)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(6)

        setup_row = QHBoxLayout()
        setup_row.setSpacing(8)

        setup_row.addWidget(QLabel("Panels:"))
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
        self.grid_combo.setToolTip("Choose how many comparison panels to create.")
        setup_row.addWidget(self.grid_combo)

        self.auto_fill_btn = QPushButton("Choose for Me · Current Extent")
        self.auto_fill_btn.setObjectName("PrimaryAction")
        self.auto_fill_btn.setToolTip(
            "Assign a different project layer to every panel. Layers that "
            "intersect the current QGIS map extent are chosen first."
        )
        self.auto_fill_btn.clicked.connect(self.auto_fill_from_current_extent)
        setup_row.addWidget(self.auto_fill_btn)

        self.time_fill_btn = QPushButton("Time-Series Auto-Fill")
        self.time_fill_btn.setToolTip("Sort layers chronologically by date/year in name and assign across panels.")
        self.time_fill_btn.clicked.connect(self.auto_fill_time_series)
        setup_row.addWidget(self.time_fill_btn)

        setup_row.addWidget(QLabel("Background:"))
        self.base_layer_combo = QComboBox()
        self.base_layer_combo.setToolTip(
            "Optional shared background shown beneath each one-layer comparison."
        )
        self.base_layer_combo.currentTextChanged.connect(self.refresh_layer_panels)
        setup_row.addWidget(self.base_layer_combo)

        setup_row.addStretch(1)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_all_canvases)
        setup_row.addWidget(self.refresh_btn)

        self.print_btn = QPushButton("Export / Print…")
        self.print_btn.clicked.connect(self.show_print_layout)
        self.print_btn.setToolTip(
            "Export a print layout or a self-contained interactive HTML dashboard."
        )
        setup_row.addWidget(self.print_btn)

        self.guide_btn = QPushButton("Quick Guide")
        self.guide_btn.clicked.connect(self.show_quick_guide)
        self.guide_btn.setToolTip(
            "Show a short guide to panel comparison and synchronized navigation."
        )
        setup_row.addWidget(self.guide_btn)
        toolbar_layout.addLayout(setup_row)

        navigation_row = QHBoxLayout()
        navigation_row.setSpacing(8)

        self.sync_extents_chk = QCheckBox("Sync Navigation")
        self.sync_extents_chk.setChecked(True)
        navigation_row.addWidget(self.sync_extents_chk)

        self.sync_main_chk = QCheckBox("Sync with QGIS Canvas")
        self.sync_main_chk.setChecked(True)
        navigation_row.addWidget(self.sync_main_chk)

        self.laser_chk = QCheckBox("Laser Crosshair")
        self.laser_chk.setChecked(True)
        self.laser_chk.toggled.connect(self._on_laser_toggled)
        navigation_row.addWidget(self.laser_chk)

        self.extent_box_chk = QCheckBox("Extent Box")
        self.extent_box_chk.setChecked(True)
        self.extent_box_chk.setToolTip("Overlay active panel's extent box across all other viewports")
        self.extent_box_chk.toggled.connect(self._on_extent_box_toggled)
        navigation_row.addWidget(self.extent_box_chk)

        navigation_row.addSpacing(6)

        self.match_scale_btn = QPushButton("Match Scale")
        self.match_scale_btn.clicked.connect(self.match_scales_to_active)
        self.match_scale_btn.setToolTip(
            "Sync zoom scales of all panels to match active panel scale."
        )
        navigation_row.addWidget(self.match_scale_btn)

        self.match_extent_btn = QPushButton("Match Extent")
        self.match_extent_btn.clicked.connect(self.match_extents_to_active)
        self.match_extent_btn.setToolTip(
            "Sync full extent (center and zoom scale) of all panels to match active panel."
        )
        navigation_row.addWidget(self.match_extent_btn)

        self.fit_all_btn = QPushButton("Fit All")
        self.fit_all_btn.setToolTip("Zoom every panel to full extent of visible layers.")
        self.fit_all_btn.clicked.connect(self.fit_all_panels)
        navigation_row.addWidget(self.fit_all_btn)

        self.scale_preset_combo = QComboBox()
        self.scale_preset_combo.addItems([
            "Scale Preset...",
            "1:1,000",
            "1:2,500",
            "1:5,000",
            "1:10,000",
            "1:25,000",
            "1:50,000",
            "1:100,000",
            "1:250,000",
        ])
        self.scale_preset_combo.setToolTip("Set standard cartographic zoom scale for all panels.")
        self.scale_preset_combo.currentTextChanged.connect(self._on_scale_preset_selected)
        navigation_row.addWidget(self.scale_preset_combo)

        navigation_row.addStretch(1)

        self.workflow_hint = QLabel(
            "<b>Fast comparison:</b> choose a panel count, then click "
            "<b>Choose for Me</b>."
        )
        self.workflow_hint.setObjectName("WorkflowHint")
        self.workflow_hint.setWordWrap(True)
        navigation_row.addWidget(self.workflow_hint, 1)
        toolbar_layout.addLayout(navigation_row)

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

        self.scale_readout = QLabel("Scale: 1:—")
        self.scale_readout.setObjectName("ScaleReadout")
        status_layout.addWidget(self.scale_readout)

        status_layout.addSpacing(12)

        self.coords_label = QLabel("Cursor Coord: -")
        self.coords_label.setObjectName("CoordsLabel")
        status_layout.addWidget(self.coords_label)

        root_layout.addWidget(self.status_frame)

    def _apply_qss(self) -> None:
        """Applies a premium light theme stylesheet matching the 02viz family."""
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
            QLabel#ScaleReadout {
                color: #2a8f85;
                font-family: "Consolas", monospace;
                font-weight: 700;
            }
            QLabel#CoordsLabel {
                color: #2a8f85;
                font-family: "Consolas", monospace;
                font-weight: 600;
            }
            QLabel#WorkflowHint {
                color: #52636c;
                padding-left: 6px;
                font-size: 11px;
            }
            QLabel#TypeBadge {
                background-color: #e4f2f0;
                color: #237a72;
                border: 1px solid #b5d8d3;
                border-radius: 9px;
                padding: 1px 6px;
                font-size: 10px;
                font-weight: bold;
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
                min-width: 110px;
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
                padding: 4px 10px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #e4e9ed;
                border-color: #2a8f85;
            }
            QPushButton:pressed {
                background-color: #dbe1e6;
            }
            QPushButton#PrimaryAction {
                background-color: #2a8f85;
                color: #ffffff;
                border-color: #237a72;
                padding-left: 14px;
                padding-right: 14px;
            }
            QPushButton#PrimaryAction:hover {
                background-color: #319c91;
            }
            QPushButton#PanelHeaderIconBtn {
                background-color: transparent;
                border: 1px solid transparent;
                padding: 1px;
                font-size: 12px;
            }
            QPushButton#PanelHeaderIconBtn:hover {
                background-color: #cbd3da;
                border-radius: 3px;
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
                width: 4px;
            }
            QSplitter::handle:vertical {
                height: 4px;
            }
        """)

    def set_grid_layout(self, layout_name: str) -> None:
        """Destroys current grid canvas widgets and recreates layout with specified size."""
        self._is_syncing = True

        for ef in self.event_filters:
            ef.deleteLater()
        self.event_filters.clear()

        self.clear_extent_boxes()

        if self.main_canvas_marker:
            self.iface.mapCanvas().scene().removeItem(self.main_canvas_marker)
            self.main_canvas_marker = None

        while self.grid_layout.count() > 0:
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.panels.clear()

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

        self.rows = rows
        self.cols = cols
        count = rows * cols
        main_extent = self.iface.mapCanvas().extent()
        main_crs = self.iface.mapCanvas().mapSettings().destinationCrs()

        for i in range(count):
            panel = MapPanelWidget(i, self.iface, self)
            panel.canvas.setDestinationCrs(main_crs)
            panel.canvas.setExtent(main_extent)

            panel.canvas.extentsChanged.connect(
                lambda p=panel: self.on_panel_extent_changed(p)
            )
            panel.active_changed.connect(self.set_active_panel)

            ef = CanvasEventFilter(panel, self.on_mouse_moved_in_panel, self.on_mouse_left_panel)
            self.event_filters.append(ef)
            self.panels.append(panel)

        if rows == 1:
            h_splitter = QSplitter(get_orientation("Horizontal"))
            for panel in self.panels:
                h_splitter.addWidget(panel)

            sizes = [self.width() // cols] * cols
            h_splitter.setSizes(sizes)
            self.grid_layout.addWidget(h_splitter, 0, 0)
        else:
            v_splitter = QSplitter(get_orientation("Vertical"))

            row1_splitter = QSplitter(get_orientation("Horizontal"))
            for idx in range(cols):
                row1_splitter.addWidget(self.panels[idx])
            v_splitter.addWidget(row1_splitter)

            row2_splitter = QSplitter(get_orientation("Horizontal"))
            for idx in range(cols, count):
                row2_splitter.addWidget(self.panels[idx])
            v_splitter.addWidget(row2_splitter)

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
        self.workflow_hint.setText(
            "<b>Fast comparison:</b> click <b>Choose for Me</b> to fill "
            f"all {count} panels with different layers."
        )
        self.refresh_all_canvases()

    def get_global_base_layer(self) -> QgsMapLayer | None:
        """Returns the layer selected globally as base layer, if any."""
        layer_id = self.base_layer_combo.currentData()
        if not layer_id:
            return None
        return QgsProject.instance().mapLayer(layer_id)

    # ───────────────────────── Sync & Fill Combos ─────────────────────────

    def populate_layer_combos(self) -> None:
        """Fills the Layer Selector in all panels and the global base layer combobox."""
        layers = list(QgsProject.instance().mapLayers().values())
        layers.sort(key=lambda layer: layer.name().casefold())

        current_base_id = self.base_layer_combo.currentData()
        self.base_layer_combo.blockSignals(True)
        self.base_layer_combo.clear()
        self.base_layer_combo.addItem("No shared background", None)
        for layer in layers:
            self.base_layer_combo.addItem(layer.name(), layer.id())
        current_base_index = self.base_layer_combo.findData(current_base_id)
        if current_base_index >= 0:
            self.base_layer_combo.setCurrentIndex(current_base_index)
        self.base_layer_combo.blockSignals(False)

        for panel in self.panels:
            current_layer_id = panel.layer_combo.currentData()
            panel.layer_combo.blockSignals(True)
            panel.layer_combo.clear()
            for layer in layers:
                panel.layer_combo.addItem(layer.name(), layer.id())
            current_layer_index = panel.layer_combo.findData(current_layer_id)
            if current_layer_index >= 0:
                panel.layer_combo.setCurrentIndex(current_layer_index)
            panel.layer_combo.blockSignals(False)

    def _ordered_spatial_layers(self) -> list[QgsMapLayer]:
        """Return unique spatial layers, prioritizing the main canvas order."""
        project = QgsProject.instance()
        ordered = list(self.iface.mapCanvas().layers())
        ordered.extend(project.layerTreeRoot().layerOrder())
        ordered.extend(project.mapLayers().values())

        base_layer = self.get_global_base_layer()
        base_id = base_layer.id() if base_layer else None
        unique_layers = []
        seen_ids = set()
        for layer in ordered:
            if not layer or layer.id() in seen_ids or layer.id() == base_id:
                continue
            seen_ids.add(layer.id())
            if layer.isValid() and layer.isSpatial():
                unique_layers.append(layer)
        return unique_layers

    def _layer_intersects_main_extent(self, layer: QgsMapLayer) -> bool:
        """Return whether a layer's declared extent overlaps the QGIS canvas."""
        main_canvas = self.iface.mapCanvas()
        canvas_extent = main_canvas.extent()
        canvas_crs = main_canvas.mapSettings().destinationCrs()
        layer_extent = layer.extent()
        layer_crs = layer.crs()

        try:
            if layer_crs.isValid() and canvas_crs.isValid() and layer_crs != canvas_crs:
                transform = QgsCoordinateTransform(layer_crs, canvas_crs, QgsProject.instance())
                layer_extent = transform.transformBoundingBox(layer_extent)
        except (QgsCsException, RuntimeError):
            return False
        return layer_extent.intersects(canvas_extent)

    def auto_fill_from_current_extent(self) -> None:
        """Assign a different extent-relevant layer to each current panel."""
        if not self.panels:
            return

        all_layers = self._ordered_spatial_layers()
        intersecting = [
            layer for layer in all_layers
            if self._layer_intersects_main_extent(layer)
        ]
        intersecting_ids = {layer.id() for layer in intersecting}
        remaining = [layer for layer in all_layers if layer.id() not in intersecting_ids]
        candidates = intersecting + remaining
        selected = candidates[:len(self.panels)]

        if not selected:
            QMessageBox.information(
                self,
                "Choose Layers for Me",
                "No spatial project layers are available. Add map layers, then try again.",
            )
            return

        for panel, layer in zip(self.panels, selected):
            panel.set_comparison_layer(layer)

        for panel in self.panels[len(selected):]:
            panel.mode_combo.setCurrentText("Follow Main Map")

        self.status_label.setText(f"Ready: Assigned {len(selected)} layers to panels.")
        self.workflow_hint.setText(
            "<b>Comparison ready:</b> each panel is isolated to one layer."
        )

    def auto_fill_time_series(self) -> None:
        """Assign spatial layers sorted chronologically by date/year in layer names."""
        if not self.panels:
            return

        all_layers = self._ordered_spatial_layers()
        if not all_layers:
            QMessageBox.information(self, "Time-Series Auto-Fill", "No spatial layers found in project.")
            return

        def _extract_year(layer: QgsMapLayer) -> int:
            match = re.search(r"\b(19\d\d|20\d\d)\b", layer.name())
            return int(match.group(1)) if match else 9999


        sorted_layers = sorted(all_layers, key=_extract_year)
        selected = sorted_layers[:len(self.panels)]

        for panel, layer in zip(self.panels, selected):
            panel.set_comparison_layer(layer)

        self.status_label.setText(f"Time-Series Ready: Assigned {len(selected)} chronological layers.")

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
        """Update every panel after the QGIS main-canvas layers change."""
        for panel in self.panels:
            panel.update_canvas_layers()

    def refresh_layer_panels(self) -> None:
        """Update one-layer comparison panels after the background changes."""
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

        scale_val = int(active_panel.canvas.scale())
        self.scale_readout.setText(f"Scale: 1:{scale_val:,}")
        self.status_label.setText(f"Active Canvas: Panel {active_panel.index + 1}")
        self.update_extent_boxes()

    def refresh_all_canvases(self) -> None:
        """Triggers a render refresh on all canvases."""
        for panel in self.panels:
            panel.canvas.refresh()

    def fit_all_panels(self) -> None:
        """Zoom each map panel to the full extent of its visible layers."""
        if not self.panels:
            return
        self._is_syncing = True
        try:
            for panel in self.panels:
                panel.canvas.blockSignals(True)
                panel.canvas.zoomToFullExtent()
                panel.canvas.refresh()
                panel.canvas.blockSignals(False)
            if self.sync_main_chk.isChecked():
                main_canvas = self.iface.mapCanvas()
                main_canvas.blockSignals(True)
                main_canvas.zoomToFullExtent()
                main_canvas.refresh()
                main_canvas.blockSignals(False)
        finally:
            self._is_syncing = False

    def show_print_layout(self) -> None:
        """Instantiates and displays the print layout exporter dialog."""
        dlg = PrintLayoutDialog(self, self.iface)
        dlg.exec()

    # ───────────────────────── Scale Presets ─────────────────────────

    def _on_scale_preset_selected(self, text: str) -> None:
        if not text or "Preset" in text:
            return
        with contextlib.suppress(ValueError):
            raw_denom = text.replace("1:", "").replace(",", "").replace(".", "").strip()
            scale_denom = float(raw_denom)
            self._apply_target_scale(scale_denom)

    def _apply_target_scale(self, target_scale: float) -> None:
        self._is_syncing = True
        try:
            for panel in self.panels:
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
            if self.active_panel:
                self.scale_readout.setText(f"Scale: 1:{int(target_scale):,}")

    # ───────────────────────── Extent Bounding Box ─────────────────────────

    def clear_extent_boxes(self) -> None:
        """Hide and delete all rubberband extent boxes."""
        for rb in list(self.rubber_bands.values()):
            if rb:
                with contextlib.suppress(Exception):
                    rb.hide()
        self.rubber_bands.clear()

    def update_extent_boxes(self) -> None:
        """Draw active panel's extent box overlay across all other viewports."""
        if not self.extent_box_chk.isChecked() or not self.active_panel:
            self.clear_extent_boxes()
            return

        source_extent = self.active_panel.canvas.extent()
        source_crs = self.active_panel.canvas.mapSettings().destinationCrs()

        polygon_geom_type = getattr(QgsWkbTypes, "PolygonGeometry", QgsWkbTypes.PolygonGeometry)

        for panel in self.panels:
            if panel == self.active_panel:
                if panel in self.rubber_bands and self.rubber_bands[panel]:
                    self.rubber_bands[panel].hide()
                continue

            if panel not in self.rubber_bands or self.rubber_bands[panel] is None:
                rb = QgsRubberBand(panel.canvas, polygon_geom_type)
                rb.setColor(QColor(42, 143, 133, 40))
                rb.setStrokeColor(QColor(42, 143, 133, 220))
                rb.setWidth(2)
                self.rubber_bands[panel] = rb

            rb = self.rubber_bands[panel]
            dest_crs = panel.canvas.mapSettings().destinationCrs()
            transformed_extent = source_extent
            if source_crs.isValid() and dest_crs.isValid() and source_crs != dest_crs:
                with contextlib.suppress(QgsCsException, RuntimeError):
                    ct = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())
                    transformed_extent = ct.transformBoundingBox(source_extent)

            geom = QgsGeometry.fromRect(transformed_extent)
            rb.setToGeometry(geom, None)
            rb.show()

    def _on_extent_box_toggled(self, checked: bool) -> None:
        if checked:
            self.update_extent_boxes()
        else:
            self.clear_extent_boxes()

    # ───────────────────────── Navigation Sync ─────────────────────────

    def on_panel_extent_changed(self, trigger_panel: MapPanelWidget) -> None:
        """Synchronizes other panels when one is panned or zoomed."""
        if trigger_panel == self.active_panel:
            self.scale_readout.setText(f"Scale: 1:{int(trigger_panel.canvas.scale()):,}")
            self.update_extent_boxes()

        if self._is_syncing or not self.sync_extents_chk.isChecked():
            return

        self._is_syncing = True
        try:
            extent = trigger_panel.canvas.extent()

            for panel in self.panels:
                if panel == trigger_panel:
                    continue
                panel.canvas.blockSignals(True)
                panel.canvas.setExtent(extent)
                panel.canvas.refresh()
                panel.canvas.blockSignals(False)

            if self.sync_main_chk.isChecked():
                main_canvas = self.iface.mapCanvas()
                main_canvas.blockSignals(True)
                main_canvas.setExtent(extent)
                main_canvas.refresh()
                main_canvas.blockSignals(False)
        finally:
            self._is_syncing = False

    def on_main_canvas_extent_changed(self) -> None:
        """Syncs all panels when main QGIS map canvas extent changes."""
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
            self.update_extent_boxes()
        finally:
            self._is_syncing = False

    # ───────────────────────── Scale/Extent Alignment ─────────────────────────

    def match_scales_to_active(self) -> None:
        """Aligns zoom scale of all panels to match active panel scale."""
        if not hasattr(self, "active_panel") or not self.active_panel:
            if self.panels:
                self.active_panel = self.panels[0]
            else:
                return

        target_scale = self.active_panel.canvas.scale()
        self._apply_target_scale(target_scale)

    def match_extents_to_active(self) -> None:
        """Aligns full extent of all panels to match active panel extent."""
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
            self.update_extent_boxes()
        finally:
            self._is_syncing = False

    # ───────────────────────── Laser Crosshair ─────────────────────────

    def on_mouse_moved_in_panel(self, source_panel: MapPanelWidget, pos: QPoint) -> None:
        """Triggered when mouse moves over a panel map canvas viewport."""
        if not self.laser_chk.isChecked():
            return

        map_point = source_panel.canvas.mapSettings().mapToPixel().toMapCoordinates(pos.x(), pos.y())
        crs_auth = source_panel.canvas.mapSettings().destinationCrs().authid()
        self.coords_label.setText(f"X: {map_point.x():.2f}, Y: {map_point.y():.2f} ({crs_auth})")

        for panel in self.panels:
            if panel == source_panel:
                panel.marker.hide()
                continue

            panel.marker.setCenter(map_point)
            panel.marker.show()

        main_canvas = self.iface.mapCanvas()
        if self.sync_main_chk.isChecked():
            if not self.main_canvas_marker:
                self.main_canvas_marker = QgsVertexMarker(main_canvas)
                self.main_canvas_marker.setIconType(QgsVertexMarker.ICON_CROSS)
                self.main_canvas_marker.setColor(QColor(231, 76, 60))
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
        self.clear_extent_boxes()
        for panel in self.panels:
            panel.marker.hide()
        if self.main_canvas_marker:
            self.iface.mapCanvas().scene().removeItem(self.main_canvas_marker)
            self.main_canvas_marker = None
        super().closeEvent(event)

    def show_quick_guide(self) -> None:
        """Opens a help guide dialog explaining plugin usage."""
        dialog = QuickGuideDialog(self)
        dialog.exec()


class QuickGuideDialog(QDialog):
    """A stylish dialog displaying the quick help guide for 02Multimap."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("02Multimap Workspace Guide")
        self.resize(620, 540)

        self.setStyleSheet("""
            QDialog {
                background-color: #fbfbfd;
            }
            QTextBrowser {
                background-color: #ffffff;
                border: 1px solid #cbd3da;
                border-radius: 6px;
                padding: 14px;
                color: #2c3e46;
                font-size: 13px;
                line-height: 1.45;
            }
            QPushButton {
                background-color: #2a8f85;
                color: #ffffff;
                border: 1px solid #237a72;
                border-radius: 4px;
                padding: 6px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #319c91;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)

        html_help = """
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <h2 style="color: #16323f; font-weight: 700; border-bottom: 2px solid #2a8f85;
                       padding-bottom: 6px; margin-top: 0;">
                02Multimap Workspace Guide
            </h2>
            <p>Welcome to <b>02Multimap</b>, a multi-panel synchronized comparative map visualization workspace.</p>

            <h3 style="color: #2a8f85; font-size: 14px; margin-top: 16px; margin-bottom: 6px;">
                1. Fast Layer & Time-Series Auto-Fill
            </h3>
            <p>Select 2 to 8 panels, then click <b>Choose for Me · Current Extent</b> or <b>Time-Series Auto-Fill</b> to assign layers automatically.</p>

            <h3 style="color: #2a8f85; font-size: 14px; margin-top: 16px; margin-bottom: 6px;">
                2. Extent Box & Legend Overlay
            </h3>
            <ul>
                <li><b>Extent Box:</b> Displays the bounding box of the active viewport over all other viewports.</li>
                <li><b>Micro-Legend (🎨):</b> Click the palette button on any panel header card to toggle a floating legend overlay.</li>
            </ul>

            <h3 style="color: #2a8f85; font-size: 14px; margin-top: 16px; margin-bottom: 6px;">
                3. Quick Panel Tools & Scale Presets
            </h3>
            <ul>
                <li><b>Zoom to Layer (🔍):</b> Instantly zooms that panel to focus layer extent.</li>
                <li><b>Opacity (🌓):</b> Slider popup to adjust layer transparency.</li>
                <li><b>Snapshot (📷):</b> Save PNG image of individual viewports.</li>
                <li><b>Scale Presets:</b> Jump all viewports to standard cartographic scales (e.g., 1:10,000, 1:25,000).</li>
            </ul>

            <h3 style="color: #2a8f85; font-size: 14px; margin-top: 16px; margin-bottom: 6px;">
                4. Multi-Theme HTML Dashboard Exporter
            </h3>
            <p>Export offline HTML dashboards featuring Slate Light, Dark Midnight, or Emerald Clean themes, complete with micro-legends and live theme toggles.</p>
        </div>
        """
        self.browser.setHtml(html_help)
        layout.addWidget(self.browser, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
