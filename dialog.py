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
import json
from qgis.PyQt.QtCore import Qt, QObject, QEvent, QPoint, pyqtSignal, QSettings, QTimer

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
    QInputDialog,
    QScrollArea,
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
    QgsRectangle,
    QgsFeatureRequest,
    QgsPointXY,
)
from qgis.gui import QgsMapCanvas, QgsVertexMarker, QgsMapToolPan, QgsRubberBand
from .print_layout import (
    PrintLayoutDialog,
    extract_panel_legend_items,
    _measure_extent_ground_width_m,
    _nice_scale_segment,
)
from .spatial_metrics import (
    compare_layers,
    format_area,
    make_area_calculator,
    resolve_ellipsoid,
    transform_geometry,
)


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

    def __init__(self, panel: MapPanelWidget, on_mouse_move, on_leave, on_click=None):
        super().__init__(panel.canvas.viewport())
        self.panel = panel
        self.on_mouse_move = on_mouse_move
        self.on_leave = on_leave
        self.on_click = on_click

        viewport = panel.canvas.viewport()
        viewport.installEventFilter(self)
        viewport.setMouseTracking(True)
        panel.canvas.setMouseTracking(True)

    def eventFilter(self, obj, event) -> bool:
        event_type = event.type()
        mouse_move = getattr(QEvent, "MouseMove", None)
        mouse_press = getattr(QEvent, "MouseButtonPress", None)
        if mouse_move is None:
            mouse_move = QEvent.Type.MouseMove
            leave = QEvent.Type.Leave
            mouse_press = QEvent.Type.MouseButtonPress
        else:
            leave = QEvent.Leave

        if event_type == mouse_move:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self.on_mouse_move(self.panel, pos)
        elif event_type == mouse_press and self.on_click:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self.on_click(self.panel, pos)
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

        # KPI Chip Bar (bottom of panel)
        self.kpi_bar = QLabel()
        self.kpi_bar.setObjectName("KpiBar")
        self.kpi_bar.setStyleSheet("""
            QLabel#KpiBar {
                background-color: rgba(238, 241, 244, 0.95);
                color: #2a8f85;
                font-family: Consolas, monospace;
                font-size: 10px;
                font-weight: bold;
                padding: 3px 7px;
                border-top: 1px solid #cbd3da;
            }
        """)
        self.kpi_bar.setVisible(False)
        layout.addWidget(self.kpi_bar)

        # Custom mouse tracking marker
        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.marker.setColor(QColor(46, 204, 113))
        self.marker.setPenWidth(2)
        self.marker.setIconSize(24)
        self.marker.hide()

        self.hud_label: QLabel | None = None


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

        items = extract_panel_legend_items(self, max_items=8)
        if not items:
            return

        self.legend_card = QFrame(self.canvas)
        self.legend_card.setObjectName("LegendCard")
        setup_frame_style(self.legend_card, "StyledPanel", "Plain")

        card_layout = QVBoxLayout(self.legend_card)
        card_layout.setContentsMargins(6, 4, 6, 4)
        card_layout.setSpacing(3)

        layer = self.selected_layer()
        header_txt = layer.name() if layer and layer.isValid() else f"Panel {self.index + 1} Legend"
        title = QLabel(header_txt)
        title.setStyleSheet("font-weight: bold; font-size: 10px; color: #2a8f85;")
        card_layout.addWidget(title)

        for entry in items:
            row = QHBoxLayout()
            row.setSpacing(4)
            box = QLabel()
            box.setFixedSize(10, 10)
            col = entry.get("color", "#2a8f85")
            box.setStyleSheet(f"background-color: {col}; border: 1px solid #777; border-radius: 2px;")
            lbl = QLabel(entry.get("label", ""))
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
        self.legend_card.adjustSize()
        card_h = self.legend_card.sizeHint().height() or 50
        self.legend_card.move(6, max(6, self.canvas.height() - card_h - 6))
        self.legend_card.show()


    def _show_opacity_menu(self) -> None:
        """Open a small popup menu with a layer opacity slider."""
        layer = self.selected_layer()
        if not layer:
            layers = self.canvas.layers()
            layer = layers[0] if layers else None
        if not layer:
            return

        menu = QMenu(self)
        slider = QSlider(get_orientation("Horizontal"))
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

    def update_hud(self, show_hud: bool = False) -> None:
        """Update or render canvas HUD (North arrow, dynamic ground scale bar, and scale ratio)."""
        if not show_hud:
            if self.hud_label:
                self.hud_label.hide()
            return
        if not self.hud_label:
            self.hud_label = QLabel(self.canvas)
            self.hud_label.setStyleSheet("""
                background-color: rgba(22, 50, 63, 0.90);
                color: #ffffff;
                font-weight: bold;
                font-size: 11px;
                padding: 3px 8px;
                border-radius: 4px;
            """)
        scale_val = int(self.canvas.scale())
        ground_w = _measure_extent_ground_width_m(
            self.canvas.extent(), self.canvas.mapSettings().destinationCrs()
        )
        seg_m = _nice_scale_segment(ground_w * 0.2)
        dist_str = f"{seg_m / 1000.0:g} km" if seg_m >= 1000.0 else f"{int(round(seg_m))} m"
        self.hud_label.setText(f"▲ N  |  ━━ {dist_str}  |  1:{scale_val:,}")
        self.hud_label.adjustSize()
        self.hud_label.move(10, 10)
        self.hud_label.show()

    def update_kpi_bar(self, visible: bool = True) -> None:
        """Calculate spatial extent KPI metrics (polygon area in ha/km², feature count).

        The area is measured on the ellipsoid, so the chip reads the same for a
        layer in EPSG:4326, in EPSG:3857 and in a local projected CRS. The canvas
        extent is reprojected into each layer's CRS before clipping; skipping
        that step does not merely skew the number, it makes the intersection
        empty and reports ``0 ha`` beside a perfectly good feature count.
        """
        if not visible:
            self.kpi_bar.setVisible(False)
            return
        extent = self.canvas.extent()
        layers = self.canvas.layers()
        if not layers:
            self.kpi_bar.setText("KPIs: No visible layers")
            self.kpi_bar.setVisible(True)
            return

        canvas_crs = self.canvas.mapSettings().destinationCrs()
        extent_geom = QgsGeometry.fromRect(extent)
        total_area_m2 = 0.0
        total_features = 0

        for layer in layers:
            if not layer or not layer.isValid():
                continue
            if not isinstance(layer, QgsVectorLayer):
                continue

            layer_crs = layer.crs()
            needs_reprojection = (
                layer_crs.isValid() and canvas_crs.isValid() and layer_crs != canvas_crs
            )
            if needs_reprojection:
                clip_geom = transform_geometry(extent_geom, canvas_crs, layer_crs)
                if clip_geom is None:
                    # The extent is outside this CRS's valid domain. Reporting a
                    # number measured in the wrong CRS is worse than reporting
                    # nothing, so leave this layer out of the totals.
                    continue
                measure_crs = layer_crs
            else:
                clip_geom = extent_geom
                measure_crs = canvas_crs

            calculator = make_area_calculator(measure_crs)
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(clip_geom.boundingBox())):
                total_features += 1
                geom = feat.geometry()
                if geom and not geom.isEmpty() and geom.type() == QgsWkbTypes.PolygonGeometry:
                    inter = geom.intersection(clip_geom)
                    if inter and not inter.isEmpty():
                        total_area_m2 += calculator.measureArea(inter)

        self.kpi_bar.setText(
            f"KPIs: {format_area(total_area_m2)} · {total_features:,} features in extent"
        )
        self.kpi_bar.setToolTip(
            "Ellipsoidal area of polygon features clipped to the visible extent "
            f"({resolve_ellipsoid(QgsProject.instance().ellipsoid())} ellipsoid), "
            "independent of the layer CRS. Canvas CRS: "
            f"{canvas_crs.authid() or 'not set'}."
        )
        self.kpi_bar.setVisible(True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "legend_card") and self.legend_card and self.legend_card.isVisible():
            card_h = self.legend_card.sizeHint().height() or 50
            self.legend_card.move(6, max(6, self.canvas.height() - card_h - 6))


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

        self.preset_combo = QComboBox()
        self._refresh_preset_combo()
        self.preset_combo.setToolTip("Save or load workspace comparison templates and presets.")
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)
        setup_row.addWidget(self.preset_combo)

        setup_row.addStretch(1)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_all_canvases)
        setup_row.addWidget(self.refresh_btn)

        self.print_btn = QPushButton("Export / Print…")
        self.print_btn.setObjectName("PrimaryAction")
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
        navigation_row.setSpacing(6)

        self.sync_extents_chk = QCheckBox("Sync Navigation")
        self.sync_extents_chk.setChecked(True)
        navigation_row.addWidget(self.sync_extents_chk)

        self.sync_main_chk = QCheckBox("Sync Canvas")
        self.sync_main_chk.setChecked(True)
        navigation_row.addWidget(self.sync_main_chk)

        self.laser_chk = QCheckBox("Laser Crosshair")
        self.laser_chk.setChecked(True)
        navigation_row.addWidget(self.laser_chk)

        self.kpi_chk = QCheckBox("Viewport KPIs")
        self.kpi_chk.setToolTip(
            "Show real-time polygon area (ha/km²) and feature count chips under each panel.\n"
            "Areas are measured on the ellipsoid, so they are the true ground area in any layer CRS."
        )
        self.kpi_chk.toggled.connect(self._on_kpi_toggled)
        navigation_row.addWidget(self.kpi_chk)

        self.hud_chk = QCheckBox("Canvas HUD")
        self.hud_chk.setToolTip("Overlay North arrow and scale indicator on map viewport corners.")
        self.hud_chk.toggled.connect(self._on_hud_toggled)
        navigation_row.addWidget(self.hud_chk)

        self.inspect_btn = QPushButton("🎯 Multi-Inspect")
        self.inspect_btn.setToolTip("Click any point on a panel to query vector attributes and raster values across all active panels.")
        self.inspect_btn.setCheckable(True)
        self.inspect_btn.toggled.connect(self._on_inspect_toggled)
        navigation_row.addWidget(self.inspect_btn)

        self.diff_btn = QPushButton("⚡ Show Diff")
        self.diff_btn.setToolTip("Highlight geometric differences (green added, red removed) between comparison layers.")
        self.diff_btn.setCheckable(True)
        self.diff_btn.toggled.connect(self._on_diff_toggled)
        navigation_row.addWidget(self.diff_btn)

        self.drawer_btn = QPushButton("📋 Legend Drawer")
        self.drawer_btn.setToolTip("Toggle right-side drawer stacking all panel micro-legends in one column.")
        self.drawer_btn.clicked.connect(self.toggle_legend_drawer)
        navigation_row.addWidget(self.drawer_btn)

        self.player_btn = QPushButton("▶ Play Timeline")
        self.player_btn.setToolTip("Auto-cycle active panel focus or cross-fade timeline layers.")
        self.player_btn.clicked.connect(self.toggle_timeline_player)
        navigation_row.addWidget(self.player_btn)

        navigation_row.addStretch(1)

        self.match_scale_btn = QPushButton("Match Scale")
        self.match_scale_btn.clicked.connect(self.match_scales_to_active)
        navigation_row.addWidget(self.match_scale_btn)

        self.match_extent_btn = QPushButton("Match Extent")
        self.match_extent_btn.clicked.connect(self.match_extents_to_active)
        navigation_row.addWidget(self.match_extent_btn)

        self.fit_all_btn = QPushButton("Fit All")
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
        self.scale_preset_combo.currentTextChanged.connect(self._on_scale_preset_selected)
        navigation_row.addWidget(self.scale_preset_combo)

        toolbar_layout.addLayout(navigation_row)

        root_layout.addWidget(self.toolbar_frame)


        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)

        # Central grid container
        self.grid_container = QFrame()
        setup_frame_style(self.grid_container, "StyledPanel", "Sunken")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(2, 2, 2, 2)
        self.grid_layout.setSpacing(3)
        body_layout.addWidget(self.grid_container, 1)

        # Right-side Legend Drawer
        self.drawer_frame = QFrame()
        self.drawer_frame.setObjectName("LegendDrawer")
        self.drawer_frame.setVisible(False)
        self.drawer_frame.setFixedWidth(250)
        self.drawer_frame.setStyleSheet("QFrame#LegendDrawer { background: #ffffff; border: 1px solid #cbd3da; border-radius: 6px; }")
        drawer_layout = QVBoxLayout(self.drawer_frame)
        drawer_layout.setContentsMargins(8, 8, 8, 8)
        drawer_title = QLabel("Unified Panel Legends")
        drawer_title.setStyleSheet("color: #2a8f85; font-size: 12px; font-weight: bold;")
        drawer_layout.addWidget(drawer_title)

        self.drawer_scroll = QScrollArea()
        self.drawer_scroll.setWidgetResizable(True)
        self.drawer_scroll.setStyleSheet("border: none; background: transparent;")
        self.drawer_content = QWidget()
        self.drawer_scroll_layout = QVBoxLayout(self.drawer_content)
        self.drawer_scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.drawer_scroll_layout.setSpacing(6)
        self.drawer_scroll.setWidget(self.drawer_content)
        drawer_layout.addWidget(self.drawer_scroll, 1)

        body_layout.addWidget(self.drawer_frame)
        root_layout.addLayout(body_layout, 1)


        # Status Bar
        self.status_frame = QFrame()
        self.status_frame.setObjectName("StatusFrame")
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(6, 4, 6, 4)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusLabel")
        status_layout.addWidget(self.status_label)

        self.workflow_hint = QLabel()
        self.workflow_hint.setObjectName("WorkflowHint")
        status_layout.addWidget(self.workflow_hint)

        status_layout.addStretch(1)


        self.scale_readout = QLabel("Scale: 1:—")
        self.scale_readout.setObjectName("ScaleReadout")
        status_layout.addWidget(self.scale_readout)

        status_layout.addSpacing(12)

        self.overlap_label = QLabel("Overlap: —")
        self.overlap_label.setObjectName("OverlapLabel")
        self.overlap_label.setStyleSheet("color: #e67e22; font-weight: bold; font-family: Consolas, monospace;")
        status_layout.addWidget(self.overlap_label)


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

            ef = CanvasEventFilter(
                panel,
                self.on_mouse_moved_in_panel,
                self.on_mouse_left_panel,
                self._on_canvas_clicked,
            )

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
        """Hide and delete any rubberband extent boxes."""
        for rb in list(self.rubber_bands.values()):
            if rb:
                with contextlib.suppress(Exception):
                    rb.hide()
        self.rubber_bands.clear()

    def update_extent_boxes(self) -> None:
        """Keep non-active viewports free of rubberband tint overlays; active panel frame border indicates selection."""
        self.clear_extent_boxes()

    def _on_extent_box_toggled(self, checked: bool) -> None:
        self.clear_extent_boxes()

    # ───────────────────────── Navigation Sync ─────────────────────────

    def _refresh_live_overlays(self) -> None:
        """Refresh HUD badges, KPI bars, legend drawer, and diff overlays if currently enabled."""
        if hasattr(self, "hud_chk") and self.hud_chk.isChecked():
            for p in self.panels:
                p.update_hud(True)
        if hasattr(self, "kpi_chk") and self.kpi_chk.isChecked():
            for p in self.panels:
                p.update_kpi_bar(True)
        if hasattr(self, "drawer_frame") and self.drawer_frame.isVisible():
            self.update_legend_drawer()
        if hasattr(self, "diff_btn") and self.diff_btn.isChecked():
            self.update_spatial_diff_highlights()

    def on_panel_extent_changed(self, trigger_panel: MapPanelWidget) -> None:
        """Synchronizes other panels when one is panned or zoomed."""
        if hasattr(self, "active_panel") and self.active_panel != trigger_panel:
            self.set_active_panel(trigger_panel)
        else:
            self.scale_readout.setText(f"Scale: 1:{int(trigger_panel.canvas.scale()):,}")
            self.update_extent_boxes()

        if self._is_syncing or not self.sync_extents_chk.isChecked():
            self._refresh_live_overlays()
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
            self._refresh_live_overlays()

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
            self._refresh_live_overlays()

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
            self._refresh_live_overlays()

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

    def _on_canvas_clicked(self, source_panel: MapPanelWidget, pos: QPoint) -> None:
        """Triggered when mouse clicks on a panel canvas."""
        if not hasattr(self, "inspect_btn") or not self.inspect_btn.isChecked():
            return
        map_point = source_panel.canvas.mapSettings().mapToPixel().toMapCoordinates(pos.x(), pos.y())
        self.perform_multi_inspect(map_point, source_panel=source_panel)

    def _refresh_preset_combo(self) -> None:
        """Populate built-in templates and user-saved presets from QSettings."""
        if not hasattr(self, "preset_combo"):
            return
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItems([
            "Presets / Templates...",
            "Save Current Setup...",
            "Template: Before & After (1x2)",
            "Template: 4-Scenario Matrix (2x2)",
            "Template: Temporal Timeline (1x3)",
        ])
        settings = QSettings()
        settings.beginGroup("zero2multimap/presets")
        for name in sorted(settings.childKeys()):
            if name.strip():
                self.preset_combo.addItem(f"Preset: {name.strip()}")
        settings.endGroup()
        self.preset_combo.setCurrentIndex(0)
        self.preset_combo.blockSignals(False)

    def _on_preset_selected(self, preset_text: str) -> None:
        """Handle Preset selection from toolbar dropdown."""
        if not preset_text or preset_text == "Presets / Templates...":
            return
        if preset_text == "Save Current Setup...":
            self.save_workspace_preset()
        elif preset_text.startswith("Template: Before & After"):
            self.load_built_in_preset("before_after")
        elif preset_text.startswith("Template: 4-Scenario"):
            self.load_built_in_preset("matrix_2x2")
        elif preset_text.startswith("Template: Temporal"):
            self.load_built_in_preset("timeline_1x3")
        elif preset_text.startswith("Preset: "):
            self.load_saved_preset(preset_text[len("Preset: "):].strip())
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(0)
        self.preset_combo.blockSignals(False)

    def save_workspace_preset(self) -> None:
        """Save current workspace layout and panel assignments to QSettings."""
        preset_name, ok = QInputDialog.getText(self, "Save Workspace Preset", "Preset Name:")
        if not ok or not preset_name.strip():
            return
        clean_name = preset_name.strip()
        settings = QSettings()
        key = f"zero2multimap/presets/{clean_name}"
        data = {
            "grid": self.grid_combo.currentText(),
            "base_layer": self.base_layer_combo.currentText(),
            "panels": [
                {
                    "mode": panel.mode,
                    "layer": panel.layer_combo.currentText(),
                    "theme": panel.theme_combo.currentText(),
                    "scale": int(panel.canvas.scale()),
                }
                for panel in self.panels
            ],
        }
        settings.setValue(key, json.dumps(data))
        self._refresh_preset_combo()
        QMessageBox.information(self, "Workspace Presets", f"Preset '{clean_name}' saved successfully!")

    def load_saved_preset(self, preset_name: str) -> None:
        """Restore a user-saved workspace preset from QSettings."""
        raw = QSettings().value(f"zero2multimap/presets/{preset_name}", "")
        if not raw:
            return
        with contextlib.suppress(Exception):
            data = json.loads(str(raw))
            grid_txt = data.get("grid")
            if grid_txt and self.grid_combo.findText(grid_txt) >= 0:
                self.grid_combo.setCurrentText(grid_txt)
            base_txt = data.get("base_layer")
            if base_txt and self.base_layer_combo.findText(base_txt) >= 0:
                self.base_layer_combo.setCurrentText(base_txt)
            for panel, p_cfg in zip(self.panels, data.get("panels", [])):
                mode = p_cfg.get("mode", "sync")
                if mode == "layer":
                    panel.mode_combo.setCurrentText("Compare One Layer")
                    lyr_txt = p_cfg.get("layer", "")
                    if lyr_txt and panel.layer_combo.findText(lyr_txt) >= 0:
                        panel.layer_combo.setCurrentText(lyr_txt)
                elif mode == "theme":
                    panel.mode_combo.setCurrentText("Map Theme")
                    thm_txt = p_cfg.get("theme", "")
                    if thm_txt and panel.theme_combo.findText(thm_txt) >= 0:
                        panel.theme_combo.setCurrentText(thm_txt)
                else:
                    panel.mode_combo.setCurrentText("Follow Main Map")
            self.status_label.setText(f"Loaded workspace preset: {preset_name}")

    def load_built_in_preset(self, preset_type: str) -> None:
        """Load one of the built-in scenario templates."""
        if preset_type == "before_after":
            self.grid_combo.setCurrentText("2 Panels (1x2)")
            if len(self.panels) >= 2:
                self.panels[0].mode_combo.setCurrentText("Compare One Layer")
                self.panels[1].mode_combo.setCurrentText("Compare One Layer")
        elif preset_type == "matrix_2x2":
            self.grid_combo.setCurrentText("4 Panels (2x2)")
        elif preset_type == "timeline_1x3":
            self.grid_combo.setCurrentText("3 Panels (1x3)")
            self.auto_fill_time_series()

    def toggle_legend_drawer(self) -> None:
        """Toggle right-side drawer stacking all panel micro-legends in one column."""
        if not hasattr(self, "drawer_frame"):
            return
        self.drawer_frame.setVisible(not self.drawer_frame.isVisible())
        if self.drawer_frame.isVisible():
            self.update_legend_drawer()

    def update_legend_drawer(self) -> None:
        """Populate right-side drawer with all active panel legends (including graduated & rule-based)."""
        if not hasattr(self, "drawer_scroll_layout"):
            return
        for i in reversed(range(self.drawer_scroll_layout.count())):
            item = self.drawer_scroll_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)

        for panel in self.panels:
            items = extract_panel_legend_items(panel, max_items=8)
            if not items:
                continue
            card = QFrame()
            card.setStyleSheet("background: #f8fafc; border: 1px solid #cbd3da; border-radius: 5px; padding: 4px;")
            card_l = QVBoxLayout(card)
            card_l.setContentsMargins(4, 4, 4, 4)
            card_l.setSpacing(2)
            title = QLabel(f"<b>Panel {panel.index + 1} Legend</b>")
            title.setStyleSheet("font-size: 11px; color: #2a8f85;")
            card_l.addWidget(title)

            for entry in items:
                lbl = entry.get("label", "")
                col = entry.get("color", "#2a8f85")
                row = QLabel(f"▪ {lbl}")
                row.setStyleSheet(f"font-size: 10px; color: {col}; font-weight: bold;")
                card_l.addWidget(row)
            self.drawer_scroll_layout.addWidget(card)

    def _on_kpi_toggled(self, checked: bool) -> None:
        """Toggle real-time bounding box area and feature count KPI chips under each panel."""
        for panel in self.panels:
            panel.update_kpi_bar(checked)

    def _on_hud_toggled(self, checked: bool) -> None:
        """Toggle North arrow and scale indicator on map viewport corners."""
        for panel in self.panels:
            panel.update_hud(checked)

    def _on_inspect_toggled(self, checked: bool) -> None:
        """Toggle multi-panel coordinated feature inspector mode."""
        msg = "Multi-Inspect Active: Click any point on a map panel to query all panels." if checked else "Multi-Inspect Off"
        self.status_label.setText(msg)

    def perform_multi_inspect(self, map_point: QgsPointXY, source_panel: MapPanelWidget | None = None) -> None:
        """Query vector attributes and raster cell values at map_point across all active panels."""
        ref_panel = source_panel or self.active_panel or (self.panels[0] if self.panels else None)
        source_crs = ref_panel.canvas.mapSettings().destinationCrs() if ref_panel else self.iface.mapCanvas().mapSettings().destinationCrs()

        lines = []
        for index, panel in enumerate(self.panels):
            p_name = f"Panel {index + 1}"
            layers = panel.canvas.layers()
            if not layers:
                lines.append(f"<b>{p_name}:</b> No active layers")
                continue

            panel_crs = panel.canvas.mapSettings().destinationCrs()
            mupp = max(1e-7, panel.canvas.mapUnitsPerPixel() or 1.0)
            tol_panel = mupp * 6.0
            pt_panel = map_point
            if source_crs.isValid() and panel_crs.isValid() and source_crs != panel_crs:
                with contextlib.suppress(QgsCsException, RuntimeError):
                    ct_p = QgsCoordinateTransform(source_crs, panel_crs, QgsProject.instance())
                    pt_panel = ct_p.transform(map_point)

            rect_panel = QgsRectangle(
                pt_panel.x() - tol_panel,
                pt_panel.y() - tol_panel,
                pt_panel.x() + tol_panel,
                pt_panel.y() + tol_panel,
            )

            p_info = []
            for lyr in layers:
                if not lyr or not lyr.isValid():
                    continue
                lyr_crs = lyr.crs()
                query_rect = rect_panel
                query_pt = pt_panel
                if panel_crs.isValid() and lyr_crs.isValid() and panel_crs != lyr_crs:
                    with contextlib.suppress(QgsCsException, RuntimeError):
                        ct_l = QgsCoordinateTransform(panel_crs, lyr_crs, QgsProject.instance())
                        query_rect = ct_l.transformBoundingBox(rect_panel)
                        query_pt = ct_l.transform(pt_panel)

                if isinstance(lyr, QgsVectorLayer):
                    req = QgsFeatureRequest().setFilterRect(query_rect)
                    feats = list(lyr.getFeatures(req))
                    if feats:
                        f = feats[0]
                        fields = [f"{k}: {f[k]}" for k in f.fields().names()[:3] if f[k] is not None]
                        f_info = ", ".join(fields) if fields else f"Feature ID {f.id()}"
                        p_info.append(f"{lyr.name()} ({f_info})")
                elif isinstance(lyr, QgsRasterLayer):
                    with contextlib.suppress(Exception):
                        val, ok = lyr.dataProvider().sample(query_pt, 1)
                        if ok:
                            p_info.append(f"{lyr.name()} (Val: {val:.2f})")

            val_str = " | ".join(p_info) if p_info else "No features at point"
            lines.append(f"<b>{p_name}:</b> {val_str}")

        msg = "<br>".join(lines)
        QMessageBox.information(
            self,
            "Multi-Panel Feature Inspector 🎯",
            f"<b>Coordinate:</b> {map_point.x():.4f}, {map_point.y():.4f}<br><br>{msg}"
        )

    def _on_diff_toggled(self, checked: bool) -> None:
        """Toggle visual spatial difference engine highlights (green added, red removed)."""
        if not checked:
            if hasattr(self, "diff_rubber_bands"):
                for rb in list(self.diff_rubber_bands.values()):
                    if rb:
                        with contextlib.suppress(Exception):
                            rb.hide()
                self.diff_rubber_bands.clear()
            self.status_label.setText("Diff Highlights Off")
            self.overlap_label.setText("Overlap: —")
            self.overlap_label.setToolTip("")
            return

        self.status_label.setText("Diff Highlights Active: Green (added), Red (removed)")
        self.update_spatial_diff_highlights()

    def update_spatial_diff_highlights(self) -> None:
        """Compute the geometric difference between Panel 1 and Panel 2.

        Both layers are unioned in their own CRS and reprojected into Panel 1's
        canvas CRS *before* any geometry operation. Feeding two layers that
        disagree on CRS straight into ``difference()`` raises nothing — it
        returns a confident wrong answer (typically "everything changed"), which
        is how this went unnoticed.
        """
        if not hasattr(self, "diff_btn") or not self.diff_btn.isChecked() or len(self.panels) < 2:
            return

        if not hasattr(self, "diff_rubber_bands"):
            self.diff_rubber_bands = {}

        for rb in list(self.diff_rubber_bands.values()):
            if rb:
                with contextlib.suppress(Exception):
                    rb.hide()
        self.diff_rubber_bands.clear()

        p1_layers = [lyr for lyr in self.panels[0].canvas.layers() if isinstance(lyr, QgsVectorLayer)]
        p2_layers = [lyr for lyr in self.panels[1].canvas.layers() if isinstance(lyr, QgsVectorLayer)]

        if not p1_layers or not p2_layers:
            return

        canvas_crs = self.panels[0].canvas.mapSettings().destinationCrs()
        extent_geom = QgsGeometry.fromRect(self.panels[0].canvas.extent())
        polygon_geom_type = getattr(QgsWkbTypes, "PolygonGeometry", QgsWkbTypes.PolygonGeometry)

        result = compare_layers(p1_layers[0], p2_layers[0], extent_geom, canvas_crs)
        if result.area_a_m2 <= 0 and result.area_b_m2 <= 0:
            # Neither panel holds a measurable polygon inside the extent; the
            # previous readout would be a leftover from another zoom level.
            self.overlap_label.setText("Overlap: —")
            self.overlap_label.setToolTip("")
            return

        if result.removed is not None:
            rb_red = QgsRubberBand(self.panels[0].canvas, polygon_geom_type)
            rb_red.setColor(QColor(231, 76, 60, 80))
            rb_red.setStrokeColor(QColor(231, 76, 60, 240))
            rb_red.setWidth(2)
            rb_red.setToGeometry(result.removed, canvas_crs)
            rb_red.show()
            self.diff_rubber_bands["removed"] = rb_red

        if result.added is not None:
            # The green band lives on Panel 2's canvas, which may sit in a
            # different CRS than the working CRS the difference was computed in.
            panel2_crs = self.panels[1].canvas.mapSettings().destinationCrs()
            added_on_p2 = transform_geometry(result.added, canvas_crs, panel2_crs)
            if added_on_p2 is not None:
                rb_green = QgsRubberBand(self.panels[1].canvas, polygon_geom_type)
                rb_green.setColor(QColor(46, 204, 113, 80))
                rb_green.setStrokeColor(QColor(46, 204, 113, 240))
                rb_green.setWidth(2)
                rb_green.setToGeometry(added_on_p2, panel2_crs)
                rb_green.show()
                self.diff_rubber_bands["added"] = rb_green

        if result.area_a_m2 > 0:
            self.overlap_label.setText(f"Overlap: {result.overlap_percent:.1f}%")
            self.overlap_label.setToolTip(
                f"Panel 1: {format_area(result.area_a_m2)} · "
                f"Panel 2: {format_area(result.area_b_m2)} · "
                f"removed {format_area(result.removed_area_m2)} · "
                f"added {format_area(result.added_area_m2)} (ellipsoidal area)"
            )

    def toggle_timeline_player(self) -> None:
        """Start or stop timeline player auto-cycling panel highlights."""
        if not hasattr(self, "timeline_timer"):
            self.timeline_timer = QTimer(self)
            self.timeline_timer.timeout.connect(self._on_timeline_timer_tick)
            self.timeline_index = 0

        if self.timeline_timer.isActive():
            self.timeline_timer.stop()
            self.player_btn.setText("▶ Play Timeline")
            self.status_label.setText("Timeline Player Stopped")
        else:
            self.timeline_timer.start(1500)
            self.player_btn.setText("⏸ Pause Timeline")
            self.status_label.setText("Timeline Player Active")

    def _on_timeline_timer_tick(self) -> None:
        """Cycle active panel on timer tick."""
        if not self.panels:
            return
        self.timeline_index = (self.timeline_index + 1) % len(self.panels)
        self.set_active_panel(self.panels[self.timeline_index])


    def _on_laser_toggled(self, checked: bool) -> None:

        if not checked:
            for panel in self.panels:
                panel.marker.hide()
            if self.main_canvas_marker:
                self.main_canvas_marker.hide()

    # ───────────────────────── Window Close ─────────────────────────

    def closeEvent(self, event) -> None:
        if hasattr(self, "timeline_timer") and self.timeline_timer.isActive():
            self.timeline_timer.stop()
            if hasattr(self, "player_btn"):
                self.player_btn.setText("▶ Play Timeline")
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
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.5;">
            <h2 style="color: #16323f; font-weight: 700; border-bottom: 2px solid #2a8f85;
                       padding-bottom: 6px; margin-top: 0;">
                02Multimap Workspace Guide (v0.6.0)
            </h2>
            <p>Welcome to <b>02Multimap</b>, the advanced multi-panel synchronized spatial comparison workspace for QGIS.</p>
            
            <div style="background-color: #eef1f4; border: 1px solid #cbd3da; border-radius: 6px; padding: 10px; margin-bottom: 12px;">
                <b>🌐 Online User Manual & Detailed Documentation:</b><br>
                <a href="https://geophilo.com/zero2multimap/" style="color: #2a8f85; font-weight: bold; text-decoration: underline;">
                    https://geophilo.com/zero2multimap/
                </a>
            </div>

            <h3 style="color: #2a8f85; font-size: 14px; margin-top: 14px; margin-bottom: 4px;">
                1. Synchronized Viewport Grids & Fast Auto-Fill
            </h3>
            <ul>
                <li><b>Grid Topologies:</b> Select 2, 3, 4, 6, or 8 synchronized map viewports in horizontal, vertical, or grid matrices.</li>
                <li><b>Choose for Me:</b> Automatically assigns a different spatial layer to every panel, prioritizing layers intersecting the current map extent.</li>
                <li><b>Time-Series Auto-Fill:</b> Sorts layers chronologically by date/year in their names and populates viewports in timeline order.</li>
            </ul>

            <h3 style="color: #2a8f85; font-size: 14px; margin-top: 14px; margin-bottom: 4px;">
                2. Spatial Analysis & Inspection Suite (v0.6.0)
            </h3>
            <ul>
                <li><b>🎯 Multi-Inspect:</b> Click any coordinate on a panel to query vector attributes and raster cell values across all active panels simultaneously.</li>
                <li><b>⚡ Show Diff:</b> Compute geometric differences between Panel 1 & 2 layers. Renders green (added) and red (removed) overlays with a live <b>Overlap %</b> readout and a ground-area breakdown on hover. Panels in different coordinate systems are reprojected before comparison.</li>
                <li><b>📏 Measurement Tool:</b> Measure distance and area interactively across synchronized map viewports.</li>
                <li><b>📊 Viewport KPIs:</b> Displays real-time visible polygon areas (ha/km²) and feature counts beneath each panel. Areas are ellipsoidal (true ground area) and identical whether the layer is stored in EPSG:4326, EPSG:3857 or a projected CRS — hover a chip to see the ellipsoid and canvas CRS.</li>
            </ul>

            <h3 style="color: #2a8f85; font-size: 14px; margin-top: 14px; margin-bottom: 4px;">
                3. Cartographic & Viewport Controls
            </h3>
            <ul>
                <li><b>Extent Box & Laser Crosshair:</b> Overlay active panel's extent box across all viewports; track mouse coordinates across canvases simultaneously.</li>
                <li><b>Scale Presets & Match Extent:</b> Jump all panels to standard cartographic scales (1:1,000 to 1:250,000) or sync zoom/center.</li>
                <li><b>📋 Legend Drawer & 🧭 Canvas HUD:</b> Access a right-side drawer stacking all panel legends; overlay North arrow and scale graphics on canvas corners.</li>
                <li><b>▶ Play Timeline:</b> Auto-cycle focus panel highlights or cross-fade timeline layers at customizable playback speeds.</li>
            </ul>

            <h3 style="color: #2a8f85; font-size: 14px; margin-top: 14px; margin-bottom: 4px;">
                4. Print Layouts & Multi-Theme HTML Dashboards
            </h3>
            <p>Export publication-ready print layouts or self-contained interactive HTML dashboards featuring <b>Slate Light</b>, <b>Dark Midnight</b>, and <b>Emerald Clean</b> themes, <b>📺 Presentation Slide Mode</b>, opacity sliders, and measurement tools.</p>
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
