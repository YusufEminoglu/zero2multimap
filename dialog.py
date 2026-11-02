# -*- coding: utf-8 -*-
"""02Multimap — main workspace dialog.

A grid of 2 to 8 synchronized map panels, or one swipe map that shows a bottom
layer set everywhere and a top layer set on one side of a draggable divider.
Also: laser crosshair, legend cards, time-series auto-fill, cross-panel
inspection, the change overlay, true-area chips and the print/HTML export
entry point. Runs on QGIS 3.40 LTR through QGIS 4 (PyQt5 / PyQt6).

Navigation sync copies centre, scale and rotation, never a raw extent
rectangle: a rectangle copied into a canvas of another shape is fitted with
margins, so every round trip between two canvases used to zoom out.
"""
from __future__ import annotations

import contextlib
import html
import json
import re

from qgis.PyQt.QtCore import Qt, QObject, QEvent, QPoint, pyqtSignal, QSettings, QTimer
from qgis.PyQt.QtGui import QColor, QGuiApplication
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
    QToolButton,
)
from qgis.core import (
    Qgis,
    QgsProject,
    QgsMapLayer,
    QgsMapSettings,
    QgsCoordinateReferenceSystem,
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
from .swipe_view import SwipePanel


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

# Qgis.GeometryType exists from QGIS 3.30; the QgsWkbTypes names are the fallback.
_GEOMETRY_TYPE = getattr(Qgis, "GeometryType", None)
POLYGON = _GEOMETRY_TYPE.Polygon if _GEOMETRY_TYPE is not None else QgsWkbTypes.PolygonGeometry
LINE = _GEOMETRY_TYPE.Line if _GEOMETRY_TYPE is not None else QgsWkbTypes.LineGeometry
POINT = _GEOMETRY_TYPE.Point if _GEOMETRY_TYPE is not None else QgsWkbTypes.PointGeometry

GRID_LAYOUTS = {
    "2 Panels (1x2)": (1, 2),
    "2 Panels (2x1)": (2, 1),
    "3 Panels (1x3)": (1, 3),
    "4 Panels (2x2)": (2, 2),
    "6 Panels (2x3)": (2, 3),
    "8 Panels (2x4)": (2, 4),
}
SWIPE_LAYOUT = "Swipe (bottom / top)"
DEFAULT_LAYOUT = "4 Panels (2x2)"
MODE_TEXT = {
    "sync": "Follow Main Map",
    "layer": "Compare One Layer",
    "theme": "Use Map Theme",
}
LAYER_PLACEHOLDER = "Choose a layer…"
OVERLAY_DELAY_MS = 250
LAYER_REBUILD_DELAY_MS = 150
PROJECT_SCOPE = "zero2multimap"
PROJECT_KEY = "/panel_setup"
PRESET_GROUP = "zero2multimap/presets"
# A four-digit year between 1900 and 2099 that is not part of a longer number.
# "\b" is wrong here: "_" is a word character, so "landuse_2015" has no
# boundary before the year and was never matched.
YEAR_PATTERN = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


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


def extract_year(name: str) -> int | None:
    """The year written in a layer name (``landuse_2015`` -> 2015), or None."""
    match = YEAR_PATTERN.search(name or "")
    return int(match.group(1)) if match else None


def name_stem(name: str) -> str:
    """A layer name without its year: ``landuse_2015`` and ``Landuse 2023`` share ``landuse``."""
    stem = YEAR_PATTERN.sub(" ", name or "")
    return re.sub(r"[\s_\-.]+", " ", stem).strip().casefold()


def geometry_kind(layer: QgsMapLayer) -> str:
    """A short, hashable description of what a layer holds."""
    if isinstance(layer, QgsVectorLayer):
        geometry_type = layer.geometryType()
        if geometry_type == POLYGON:
            return "polygon"
        if geometry_type == LINE:
            return "line"
        if geometry_type == POINT:
            return "point"
        return "table"
    if isinstance(layer, QgsRasterLayer):
        return "raster"
    return "other"


def _rect_around(center: QgsPointXY, width: float, height: float) -> QgsRectangle:
    return QgsRectangle(
        center.x() - width / 2.0,
        center.y() - height / 2.0,
        center.x() + width / 2.0,
        center.y() + height / 2.0,
    )


def transform_point(point: QgsPointXY, source_crs, target_crs) -> QgsPointXY | None:
    """``point`` moved from ``source_crs`` into ``target_crs``; None if it cannot be."""
    if not source_crs.isValid() or not target_crs.isValid() or source_crs == target_crs:
        return QgsPointXY(point)
    try:
        transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        return QgsPointXY(transform.transform(point))
    except (QgsCsException, RuntimeError):
        return None


def view_of(canvas: QgsMapCanvas):
    """``(centre, crs, scale, rotation)`` of a canvas."""
    return (
        QgsPointXY(canvas.center()),
        canvas.mapSettings().destinationCrs(),
        canvas.scale(),
        canvas.rotation(),
    )


def apply_view(canvas: QgsMapCanvas, center: QgsPointXY | None, scale: float, rotation: float) -> bool:
    """Show ``center`` (in the canvas CRS) at 1:``scale`` with ``rotation``.

    The extent is built from the canvas's own pixel size and applied with one
    ``setExtent`` call, so the canvas emits exactly one ``scaleChanged`` and a
    canvas of another shape keeps the scale instead of fitting a foreign
    rectangle. The scale is refined on a copy of the map settings because in a
    geographic CRS it depends on the latitude of the centre.
    """
    if center is None or not scale or scale <= 0:
        return False
    if abs(canvas.rotation() - rotation) > 1e-9:
        canvas.setRotation(rotation)
    settings = QgsMapSettings(canvas.mapSettings())
    size = settings.outputSize()
    units_per_pixel = settings.mapUnitsPerPixel()
    current_scale = settings.scale()
    if size.width() <= 0 or size.height() <= 0 or units_per_pixel <= 0 or current_scale <= 0:
        return False
    factor = scale / current_scale
    width = size.width() * units_per_pixel * factor
    height = size.height() * units_per_pixel * factor
    rect = _rect_around(center, width, height)
    for _attempt in range(3):
        settings.setExtent(rect, True)
        achieved = settings.scale()
        if achieved <= 0:
            break
        correction = scale / achieved
        if abs(correction - 1.0) < 1e-9:
            break
        width *= correction
        height *= correction
        rect = _rect_around(center, width, height)
    if rect.isEmpty() or not rect.isFinite():
        return False
    canvas.setExtent(rect, True)
    return True


def copy_view(source: QgsMapCanvas, target: QgsMapCanvas) -> bool:
    """Give ``target`` the centre, scale and rotation of ``source``, across CRSs."""
    center, source_crs, scale, rotation = view_of(source)
    if source.mapSettings().extent().isEmpty() or scale <= 0:
        return False
    target_crs = target.mapSettings().destinationCrs()
    moved = transform_point(center, source_crs, target_crs)
    if moved is None:
        return False
    if target.mapSettings().extent().isEmpty():
        # A canvas that never had an extent has no scale to adjust yet.
        target.setExtent(_rect_around(moved, 1.0, 1.0))
    return apply_view(target, moved, scale, rotation)


class CanvasEventFilter(QObject):
    """Filters mouse events on the canvas viewport to track coordinates for the crosshair cursor."""

    def __init__(self, panel, on_mouse_move, on_leave, on_click=None, canvas=None):
        self.canvas = canvas if canvas is not None else panel.canvas
        super().__init__(self.canvas.viewport())
        self.panel = panel
        self.on_mouse_move = on_mouse_move
        self.on_leave = on_leave
        self.on_click = on_click

        viewport = self.canvas.viewport()
        viewport.installEventFilter(self)
        viewport.setMouseTracking(True)
        self.canvas.setMouseTracking(True)

    def eventFilter(self, obj, event) -> bool:
        event_type = event.type()
        if event_type == QEvent.Type.MouseMove and self.on_mouse_move:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self.on_mouse_move(self.panel, pos)
        elif event_type == QEvent.Type.MouseButtonPress and self.on_click:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self.on_click(self.panel, pos)
        elif event_type == QEvent.Type.Leave and self.on_leave:
            self.on_leave(self.panel)
        return super().eventFilter(obj, event)


class CanvasResizeWatcher(QObject):
    """Reports a canvas resize (and its first show) before the canvas reacts to it.

    ``QgsMapCanvas.resizeEvent`` refits the extent and emits ``extentsChanged``
    synchronously, which looks exactly like a zoom. ``QgsMapCanvas.showEvent``
    may switch to the screen's physical DPI (QGIS 4 does), which silently
    changes the scale of the same extent by up to 12 %. The dialog uses these
    hooks to keep that canvas's centre and scale instead of pushing a new view
    to every other map.
    """

    def __init__(self, canvas: QgsMapCanvas, on_resize, on_show=None):
        super().__init__(canvas)
        self.canvas = canvas
        self._on_resize = on_resize
        self._on_show = on_show
        canvas.installEventFilter(self)

    def detach(self) -> None:
        with contextlib.suppress(RuntimeError):
            self.canvas.removeEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        event_type = event.type()
        if event_type == QEvent.Type.Resize:
            self._on_resize(self.canvas)
        elif event_type == QEvent.Type.Show and self._on_show is not None:
            self._on_show(self.canvas)
        return False


class MapPanelWidget(QFrame):
    """Encapsulates a single QgsMapCanvas with a styled header control bar and legend overlay.

    With ``picker_only`` the panel keeps its header (what to show) but hides
    its canvas: the swipe layout uses two of them to choose the bottom and the
    top layer set.
    """

    active_changed = pyqtSignal(object)  # Emits self when clicked/activated
    layers_changed = pyqtSignal(object)  # Emits self after the canvas layers were set

    def __init__(self, index: int, iface, parent=None, picker_only: bool = False):
        super().__init__(parent)
        self.index = index
        self.iface = iface
        self.mode = "sync"  # "sync", "layer", "theme"
        self.picker_only = picker_only
        self.compact = False
        self.marker: QgsVertexMarker | None = None
        self.legend_card: QFrame | None = None
        self.hud_label: QLabel | None = None
        self.background_layer_id: str | None = None
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

        compact_policy = QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon

        # Mode selector
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([MODE_TEXT["sync"], MODE_TEXT["layer"], MODE_TEXT["theme"]])
        self.mode_combo.setSizeAdjustPolicy(compact_policy)
        self.mode_combo.setMinimumContentsLength(9)
        self.mode_combo.setToolTip(
            "Choose what this panel shows. Use 'Compare One Layer' to give "
            "every panel a different layer."
        )
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        header_layout.addWidget(self.mode_combo)

        # Layer Selector (visible in layer mode)
        self.layer_combo = QComboBox()
        self.layer_combo.setVisible(False)
        self.layer_combo.setSizeAdjustPolicy(compact_policy)
        self.layer_combo.setMinimumContentsLength(10)
        self.layer_combo.setToolTip(
            "The selected layer is isolated in this panel. Repeat with a "
            "different layer in each panel, or use Choose for Me."
        )
        self.layer_combo.currentIndexChanged.connect(self.update_canvas_layers)
        header_layout.addWidget(self.layer_combo)

        # Theme Selector (visible in theme mode)
        self.theme_combo = QComboBox()
        self.theme_combo.setVisible(False)
        self.theme_combo.setSizeAdjustPolicy(compact_policy)
        self.theme_combo.setMinimumContentsLength(10)
        self.theme_combo.currentTextChanged.connect(self.update_canvas_layers)
        header_layout.addWidget(self.theme_combo)

        header_layout.addStretch(1)

        # Layer Type Badge
        self.badge_label = QLabel()
        self.badge_label.setObjectName("TypeBadge")
        self.badge_label.setVisible(False)
        header_layout.addWidget(self.badge_label)

        # Quick Panel Actions
        self.zoom_layer_btn = self._icon_button("🔍", "Zoom to selected layer extent", self.zoom_to_selected_layer)
        header_layout.addWidget(self.zoom_layer_btn)
        self.legend_btn = self._icon_button("🎨", "Toggle layer legend overlay", self.toggle_legend_overlay)
        header_layout.addWidget(self.legend_btn)
        self.opacity_btn = self._icon_button("🌓", "Adjust focus layer opacity", self._show_opacity_menu)
        header_layout.addWidget(self.opacity_btn)
        self.snapshot_btn = self._icon_button("📷", "Save image snapshot of this panel", self.save_panel_snapshot)
        header_layout.addWidget(self.snapshot_btn)

        # Narrow panels (eight at a time) fold the last three actions into one menu.
        self.more_btn = QToolButton()
        self.more_btn.setObjectName("PanelHeaderIconBtn")
        self.more_btn.setText("⋯")
        self.more_btn.setToolTip("Legend, opacity and snapshot")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(self.more_btn)
        more_menu.addAction("Show or hide legend", self.toggle_legend_overlay)
        more_menu.addAction("Layer opacity…", self._show_opacity_menu)
        more_menu.addAction("Save snapshot…", self.save_panel_snapshot)
        self.more_btn.setMenu(more_menu)
        self.more_btn.setVisible(False)
        header_layout.addWidget(self.more_btn)

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
                color: #1d6b63;
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

        if self.picker_only:
            # A layer-set picker never renders: its canvas only holds the layer list.
            self.canvas.freeze(True)
            self.canvas.setVisible(False)
            self.legend_btn.setVisible(False)
            self.snapshot_btn.setVisible(False)

    def _icon_button(self, text: str, tooltip: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setObjectName("PanelHeaderIconBtn")
        button.setFixedWidth(24)
        button.clicked.connect(slot)
        return button

    def set_compact(self, compact: bool) -> None:
        """Tighter header for narrow panels: badge hidden, three actions in one menu."""
        self.compact = compact
        for button in (self.legend_btn, self.opacity_btn, self.snapshot_btn):
            button.setVisible(not compact and not (self.picker_only and button is not self.opacity_btn))
        self.more_btn.setVisible(compact)
        if compact:
            self.badge_label.setVisible(False)
        else:
            self.badge_label.setVisible(bool(self.badge_label.text()))

    def mousePressEvent(self, event) -> None:
        self.active_changed.emit(self)
        super().mousePressEvent(event)

    def _on_mode_changed(self, mode_text: str) -> None:
        if mode_text in (MODE_TEXT["sync"], "Sync Main Map"):
            self.mode = "sync"
            self.layer_combo.setVisible(False)
            self.theme_combo.setVisible(False)
        elif mode_text in (MODE_TEXT["layer"], "Focus Layer"):
            self.mode = "layer"
            self.layer_combo.setVisible(True)
            self.theme_combo.setVisible(False)
        elif mode_text in (MODE_TEXT["theme"], "Map Theme"):
            self.mode = "theme"
            self.layer_combo.setVisible(False)
            self.theme_combo.setVisible(True)
        self.update_canvas_layers()

    def set_mode(self, mode: str) -> None:
        """Switch to ``"sync"``, ``"layer"`` or ``"theme"`` and update the canvas."""
        text = MODE_TEXT.get(mode, MODE_TEXT["sync"])
        if self.mode_combo.currentText() == text:
            self.update_canvas_layers()
        else:
            self.mode_combo.setCurrentText(text)

    def selected_layer(self) -> QgsMapLayer | None:
        """Return the focus layer selected by stable project layer ID."""
        layer_id = self.layer_combo.currentData()
        if not layer_id:
            return None
        return QgsProject.instance().mapLayer(layer_id)

    def set_comparison_layer(self, layer: QgsMapLayer) -> bool:
        """Switch this panel to one-layer comparison mode for ``layer``."""
        layer_index = self.layer_combo.findData(layer.id())
        if layer_index < 0:
            window = self.window()
            if isinstance(window, MultiMapDialog):
                window.populate_layer_combos()
                layer_index = self.layer_combo.findData(layer.id())
        if layer_index < 0:
            return False
        self.layer_combo.blockSignals(True)
        self.layer_combo.setCurrentIndex(layer_index)
        self.layer_combo.blockSignals(False)
        if self.mode != "layer":
            self.mode_combo.setCurrentText(MODE_TEXT["layer"])
        else:
            self.update_canvas_layers()
        return True

    def _shared_background(self) -> QgsMapLayer | None:
        window = self.window()
        if isinstance(window, MultiMapDialog):
            return window.get_global_base_layer()
        return None

    def layer_set(self) -> tuple[list, str | None]:
        """The layers this panel shows in its current mode, and the background's id."""
        if self.mode == "sync":
            return list(self.iface.mapCanvas().layers()), None
        if self.mode == "theme":
            theme_name = self.theme_combo.currentText()
            if not theme_name:
                return [], None
            collection = QgsProject.instance().mapThemeCollection()
            return list(collection.mapThemeVisibleLayers(theme_name)), None
        layers = []
        selected = self.selected_layer()
        if selected is not None:
            layers.append(selected)
        background = self._shared_background()
        if background is not None and (selected is None or background.id() != selected.id()):
            layers.append(background)
            return layers, background.id()
        return layers, None

    def update_canvas_layers(self) -> None:
        """Applies layers to the map canvas based on selected mode."""
        layers, self.background_layer_id = self.layer_set()
        self.canvas.setLayers(layers)

        badge = ""
        if self.mode == "sync":
            badge = "Sync"
        elif self.mode == "theme":
            badge = "Theme"
        else:
            selected = self.selected_layer()
            if isinstance(selected, QgsVectorLayer):
                badge = "Vector"
            elif isinstance(selected, QgsRasterLayer):
                badge = "Raster"
            elif selected is not None:
                badge = "Layer"
        self.badge_label.setText(badge)
        self.badge_label.setVisible(bool(badge) and not self.compact)

        if self.legend_card and self.legend_card.isVisible():
            self._rebuild_legend_overlay()

        self.canvas.refresh()
        self.layers_changed.emit(self)

    def zoom_to_selected_layer(self) -> None:
        """Zoom this panel canvas to the bounding box of its focus layer."""
        layer = self.selected_layer()
        if not layer:
            return
        extent = layer.extent()
        if extent.isEmpty():
            return
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if layer.crs().isValid() and canvas_crs.isValid() and layer.crs() != canvas_crs:
            with contextlib.suppress(QgsCsException, RuntimeError):
                transform = QgsCoordinateTransform(layer.crs(), canvas_crs, QgsProject.instance())
                extent = transform.transformBoundingBox(extent)
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
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet("font-weight: bold; font-size: 10px; color: #1d6b63;")
        card_layout.addWidget(title)

        for entry in items:
            row = QHBoxLayout()
            row.setSpacing(4)
            box = QLabel()
            box.setFixedSize(10, 10)
            col = entry.get("color", "#1d6b63")
            box.setStyleSheet(f"background-color: {col}; border: 1px solid #777; border-radius: 2px;")
            lbl = QLabel(entry.get("label", ""))
            lbl.setTextFormat(Qt.TextFormat.PlainText)
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
            layer.triggerRepaint()

        slider.valueChanged.connect(_on_opacity_changed)

        action = QWidgetAction(menu)
        action.setDefaultWidget(slider)
        menu.addAction(action)
        anchor = self.opacity_btn if self.opacity_btn.isVisible() else self.more_btn
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

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
        if not show_hud or self.picker_only:
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
        scale_val = round(self.canvas.scale())
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
        """Measure what this panel shows inside its visible extent.

        Polygons report their ellipsoidal area, so the chip reads the same for
        a layer in EPSG:4326, EPSG:3857 or a local projected CRS; points and
        lines are counted, never reported as "0 ha". The shared background is
        left out: it is the same in every panel and is not what is compared.
        When nothing can be measured the chip says why.
        """
        if not visible or self.picker_only:
            self.kpi_bar.setVisible(False)
            return

        canvas_crs = self.canvas.mapSettings().destinationCrs()
        layers = [layer for layer in self.canvas.layers() if layer is not None and layer.isValid()]
        background_name = ""
        if self.background_layer_id:
            for layer in layers:
                if layer.id() == self.background_layer_id:
                    background_name = layer.name()
            layers = [layer for layer in layers if layer.id() != self.background_layer_id]

        vectors = [layer for layer in layers if isinstance(layer, QgsVectorLayer)]
        if not layers:
            if background_name:
                text = "In view: nothing to measure, only the shared background is shown"
            else:
                text = "In view: no layers in this panel"
        elif not vectors:
            text = "In view: nothing to measure, raster layers have no features"
        else:
            area_m2, polygons, lines, points = self._measure_in_view(vectors, canvas_crs)
            parts = []
            if polygons:
                parts.append(f"{format_area(area_m2)} in {polygons:,} polygon{'' if polygons == 1 else 's'}")
            if lines:
                parts.append(f"{lines:,} line{'' if lines == 1 else 's'}")
            if points:
                parts.append(f"{points:,} point{'' if points == 1 else 's'}")
            text = "In view: " + (" · ".join(parts) if parts else "no features")

        tooltip = (
            "Ellipsoidal area of polygon features clipped to the visible extent "
            f"({resolve_ellipsoid(QgsProject.instance().ellipsoid())} ellipsoid), "
            "independent of the layer CRS; points and lines are counted. Canvas CRS: "
            f"{canvas_crs.authid() or 'not set'}."
        )
        if background_name:
            tooltip += f" The shared background ({background_name}) is not counted."
        self.kpi_bar.setText(text)
        self.kpi_bar.setToolTip(tooltip)
        self.kpi_bar.setVisible(True)

    def _measure_in_view(self, layers: list, canvas_crs) -> tuple[float, int, int, int]:
        """Ellipsoidal polygon area and polygon/line/point counts inside the extent."""
        extent_geom = QgsGeometry.fromRect(self.canvas.extent())
        total_area_m2 = 0.0
        polygons = lines = points = 0
        for layer in layers:
            layer_crs = layer.crs()
            if layer_crs.isValid() and canvas_crs.isValid() and layer_crs != canvas_crs:
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
            request = QgsFeatureRequest().setFilterRect(clip_geom.boundingBox())
            for feature in layer.getFeatures(request):
                geom = feature.geometry()
                if geom is None or geom.isEmpty() or not geom.intersects(clip_geom):
                    continue
                geometry_type = geom.type()
                if geometry_type == POLYGON:
                    polygons += 1
                    inter = geom.intersection(clip_geom)
                    if inter and not inter.isEmpty():
                        total_area_m2 += calculator.measureArea(inter)
                elif geometry_type == LINE:
                    lines += 1
                elif geometry_type == POINT:
                    points += 1
        return total_area_m2, polygons, lines, points

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "legend_card") and self.legend_card and self.legend_card.isVisible():
            card_h = self.legend_card.sizeHint().height() or 50
            self.legend_card.move(6, max(6, self.canvas.height() - card_h - 6))


class _SwipeCanvasView:
    """The swipe map's view (extent, scale, CRS, pixel size) carrying one layer set.

    Read by the export code exactly like ``panel.canvas``.
    """

    def __init__(self, canvas: QgsMapCanvas, layers: list):
        self._canvas = canvas
        self._layers = [layer for layer in layers if layer is not None]

    def layers(self) -> list:
        return list(self._layers)

    def extent(self) -> QgsRectangle:
        return self._canvas.extent()

    def scale(self) -> float:
        return self._canvas.scale()

    def rotation(self) -> float:
        return self._canvas.rotation()

    def canvasColor(self) -> QColor:  # noqa: N802 (QgsMapCanvas API)
        return self._canvas.canvasColor()

    def width(self) -> int:
        return self._canvas.width()

    def height(self) -> int:
        return self._canvas.height()

    def mapSettings(self) -> QgsMapSettings:  # noqa: N802 (QgsMapCanvas API)
        settings = QgsMapSettings(self._canvas.mapSettings())
        settings.setLayers(self._layers)
        return settings


class SwipeExportPanel:
    """One side of the swipe comparison, shaped like a panel for the export code.

    The layer set comes from the picker (Panel 1 = bottom, Panel 2 = top); the
    extent, scale, CRS and shape come from the swipe map, so both exported maps
    show exactly what the swipe map shows.
    """

    export_keep_aspect = True

    def __init__(self, picker: MapPanelWidget, canvas: QgsMapCanvas):
        self._picker = picker
        self.index = picker.index
        self.mode = picker.mode
        self.layer_combo = picker.layer_combo
        self.theme_combo = picker.theme_combo
        layers, _background_id = picker.layer_set()
        self.canvas = _SwipeCanvasView(canvas, layers)

    def export_title(self) -> str:
        return self._picker.title_label.text()

    def selected_layer(self) -> QgsMapLayer | None:
        return self._picker.selected_layer()


class MultiMapDialog(QDialog):

    """The main floating window that manages the grid of map panels and coordinate syncing."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.panels: list[MapPanelWidget] = []
        self.event_filters: list[CanvasEventFilter] = []
        self.diff_rubber_bands: dict[str, QgsRubberBand] = {}
        self._diff_band_pool: dict[str, tuple[QgsRubberBand, QgsMapCanvas]] = {}
        self.rows = 2
        self.cols = 2
        self._is_syncing = False
        self.active_panel: MapPanelWidget | None = None
        self.swipe_panel = None
        self._swipe_canvas: QgsMapCanvas | None = None
        self._swipe_slot = None
        self._swipe_watcher: CanvasResizeWatcher | None = None
        self._swipe_click_filter: CanvasEventFilter | None = None
        self._swipe_vertical = True
        self._swipe_ratio = 0.5
        self._swipe_needs_rebuild = False
        self.main_canvas_marker: QgsVertexMarker | None = None
        self._connections: list = []
        self._theme_collection = None
        self._panel_watchers: list[CanvasResizeWatcher] = []
        self._pending_view: dict[int, tuple] = {}
        self._shown_views: dict[int, tuple] = {}
        self._main_resizing = False
        self._combos_dirty = True
        self._layers_dirty = False
        self._themes_dirty = False
        self._view_dirty = False
        self._cleaned = False
        self.timeline_index = 0

        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(OVERLAY_DELAY_MS)
        self._overlay_timer.timeout.connect(self._run_live_overlays)
        self._layer_timer = QTimer(self)
        self._layer_timer.setSingleShot(True)
        self._layer_timer.setInterval(LAYER_REBUILD_DELAY_MS)
        self._layer_timer.timeout.connect(self._rebuild_layer_state)
        self._resize_end_timer = QTimer(self)
        self._resize_end_timer.setSingleShot(True)
        self._resize_end_timer.setInterval(0)
        self._resize_end_timer.timeout.connect(self._end_resizes)
        self._show_restore_timer = QTimer(self)
        self._show_restore_timer.setSingleShot(True)
        self._show_restore_timer.setInterval(0)
        self._show_restore_timer.timeout.connect(self._restore_after_show)
        self._project_loading = False
        self._views_before_read: list = []
        self._project_read_timer = QTimer(self)
        self._project_read_timer.setSingleShot(True)
        self._project_read_timer.setInterval(0)
        self._project_read_timer.timeout.connect(self._finish_project_read)
        self.timeline_timer = QTimer(self)
        self.timeline_timer.timeout.connect(self._on_timeline_timer_tick)

        self.setWindowTitle("02Multimap: Sync-up Map Layers")
        self.setWindowFlags(WINDOW_FLAGS)
        self._build_ui()
        self._apply_qss()
        self._resize_to_screen()

        project = QgsProject.instance()
        main_canvas = self.iface.mapCanvas()
        self._connect(project.layersAdded, self._on_project_layers_changed)
        self._connect(project.layersRemoved, self._on_project_layers_removed)
        self._connect(project.mapThemeCollectionChanged, self._on_theme_collection_replaced)
        self._connect(project.writeProject, self._write_project_setup)
        self._connect(project.readProject, self._on_project_read)
        self._connect(main_canvas.layersChanged, self._schedule_layer_rebuild)
        self._connect(main_canvas.extentsChanged, self.on_main_canvas_extent_changed)
        self._connect(main_canvas.destinationCrsChanged, self._on_main_crs_changed)
        self._connect_theme_collection()
        self._main_watcher = CanvasResizeWatcher(main_canvas, self._on_canvas_resizing)

        self.grid_combo.blockSignals(True)
        self.grid_combo.setCurrentText(DEFAULT_LAYOUT)
        self.grid_combo.blockSignals(False)
        self.set_grid_layout(DEFAULT_LAYOUT)
        self._restore_project_setup()

    # ───────────────────────── Construction ─────────────────────────

    def _connect(self, signal, slot) -> None:
        """Connect an outside signal and remember it, so cleanup() can undo it."""
        signal.connect(slot)
        self._connections.append((signal, slot))

    def _resize_to_screen(self) -> None:
        """Default size that fits a 1,280 x 720 laptop (680 px of usable height)."""
        width, height = 1180, 640
        screen = None
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "screen"):
            with contextlib.suppress(RuntimeError):
                screen = parent.screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, max(640, available.width() - 40))
            height = min(height, max(420, available.height() - 60))
        self.resize(width, height)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(4)

        # Toolbar Frame: three short rows, so the window fits a laptop screen.
        self.toolbar_frame = QFrame()
        self.toolbar_frame.setObjectName("ToolbarFrame")
        toolbar_layout = QVBoxLayout(self.toolbar_frame)
        toolbar_layout.setContentsMargins(8, 6, 8, 6)
        toolbar_layout.setSpacing(5)

        setup_row = QHBoxLayout()
        setup_row.setSpacing(6)

        setup_row.addWidget(QLabel("Panels:"))
        self.grid_combo = QComboBox()
        self.grid_combo.addItems(list(GRID_LAYOUTS) + [SWIPE_LAYOUT])
        self.grid_combo.currentTextChanged.connect(self.set_grid_layout)
        self.grid_combo.setToolTip(
            "Choose how many comparison panels to create, or Swipe: one map with a "
            "bottom and a top layer set split by a draggable divider."
        )
        setup_row.addWidget(self.grid_combo)

        self.auto_fill_btn = QPushButton("Choose for Me")
        self.auto_fill_btn.setObjectName("PrimaryAction")
        self.auto_fill_btn.setToolTip(
            "Assign a different project layer to every panel. Layers that "
            "intersect the current QGIS map extent are chosen first."
        )
        self.auto_fill_btn.clicked.connect(self.auto_fill_from_current_extent)
        setup_row.addWidget(self.auto_fill_btn)

        self.time_fill_btn = QPushButton("Time-Series Auto-Fill")
        self.time_fill_btn.setToolTip(
            "Find the year in layer names (for example landuse_2015) and assign the "
            "layers to the panels from oldest to newest."
        )
        self.time_fill_btn.clicked.connect(self.auto_fill_time_series)
        setup_row.addWidget(self.time_fill_btn)

        setup_row.addWidget(QLabel("Background:"))
        self.base_layer_combo = QComboBox()
        self.base_layer_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.base_layer_combo.setMinimumContentsLength(14)
        self.base_layer_combo.setToolTip(
            "Optional shared background shown beneath each one-layer comparison. "
            "It is never measured or compared."
        )
        self.base_layer_combo.currentIndexChanged.connect(self.refresh_layer_panels)
        setup_row.addWidget(self.base_layer_combo)

        self.preset_combo = QComboBox()
        self.preset_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.preset_combo.setMinimumContentsLength(16)
        self._refresh_preset_combo()
        self.preset_combo.setToolTip("Save or load workspace comparison templates and presets.")
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)
        setup_row.addWidget(self.preset_combo)

        setup_row.addStretch(1)

        self.print_btn = QPushButton("Export / Print…")
        self.print_btn.setObjectName("PrimaryAction")
        self.print_btn.clicked.connect(self.show_print_layout)
        self.print_btn.setToolTip(
            "Export a print layout or a self-contained interactive HTML dashboard."
        )
        setup_row.addWidget(self.print_btn)

        self.guide_btn = QPushButton("Quick Guide")
        self.guide_btn.clicked.connect(self.show_quick_guide)
        self.guide_btn.setToolTip("Show a short guide to the panels, swipe and synchronized navigation.")
        setup_row.addWidget(self.guide_btn)
        toolbar_layout.addLayout(setup_row)

        navigation_row = QHBoxLayout()
        navigation_row.setSpacing(6)

        self.sync_extents_chk = QCheckBox("Sync Navigation")
        self.sync_extents_chk.setChecked(True)
        self.sync_extents_chk.setToolTip("Pan, zoom and rotate all panels together (centre, scale and rotation).")
        navigation_row.addWidget(self.sync_extents_chk)

        self.sync_main_chk = QCheckBox("Sync Canvas")
        self.sync_main_chk.setChecked(True)
        self.sync_main_chk.setToolTip("Keep the QGIS main map in step with the panels.")
        navigation_row.addWidget(self.sync_main_chk)

        self.laser_chk = QCheckBox("Laser Crosshair")
        self.laser_chk.setChecked(True)
        self.laser_chk.toggled.connect(self._on_laser_toggled)
        navigation_row.addWidget(self.laser_chk)

        self.kpi_chk = QCheckBox("Viewport KPIs")
        self.kpi_chk.setToolTip(
            "Show the polygon area (ha/km²) and the point and line counts in view under each panel.\n"
            "Areas are measured on the ellipsoid, so they are the true ground area in any layer CRS.\n"
            "The shared background is not counted."
        )
        self.kpi_chk.toggled.connect(self._on_kpi_toggled)
        navigation_row.addWidget(self.kpi_chk)

        self.hud_chk = QCheckBox("Canvas HUD")
        self.hud_chk.setToolTip("Overlay North arrow and scale indicator on map viewport corners.")
        self.hud_chk.toggled.connect(self._on_hud_toggled)
        navigation_row.addWidget(self.hud_chk)

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

        tools_row = QHBoxLayout()
        tools_row.setSpacing(6)

        self.inspect_btn = QPushButton("🎯 Multi-Inspect")
        self.inspect_btn.setToolTip(
            "Click any point on a panel to list the feature under it and raster "
            "values in every panel."
        )
        self.inspect_btn.setCheckable(True)
        self.inspect_btn.toggled.connect(self._on_inspect_toggled)
        tools_row.addWidget(self.inspect_btn)

        self.diff_btn = QPushButton("⚡ Show Diff")
        self.diff_btn.setToolTip(
            "Outline what changed between the polygon layers chosen in Panels 1 and 2 "
            "(green added, red removed)."
        )
        self.diff_btn.setCheckable(True)
        self.diff_btn.toggled.connect(self._on_diff_toggled)
        tools_row.addWidget(self.diff_btn)

        self.drawer_btn = QPushButton("📋 Legend Drawer")
        self.drawer_btn.setToolTip("Toggle right-side drawer stacking all panel micro-legends in one column.")
        self.drawer_btn.clicked.connect(self.toggle_legend_drawer)
        tools_row.addWidget(self.drawer_btn)

        self.player_btn = QPushButton("▶ Play Timeline")
        self.player_btn.setToolTip("Highlight the panels one after another, every 1.5 seconds.")
        self.player_btn.clicked.connect(self.toggle_timeline_player)
        tools_row.addWidget(self.player_btn)

        tools_row.addStretch(1)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Redraw every panel.")
        self.refresh_btn.clicked.connect(self.refresh_all_canvases)
        tools_row.addWidget(self.refresh_btn)

        toolbar_layout.addLayout(tools_row)

        root_layout.addWidget(self.toolbar_frame)

        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)

        # Central grid container. It scrolls instead of widening the window
        # when eight panels need more room than the screen has.
        self.grid_container = QFrame()
        self.grid_container.setObjectName("GridContent")
        setup_frame_style(self.grid_container, "StyledPanel", "Sunken")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(2, 2, 2, 2)
        self.grid_layout.setSpacing(3)
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setObjectName("GridScroll")
        self.grid_scroll.setWidgetResizable(True)
        setup_frame_style(self.grid_scroll, "NoFrame", "Plain")
        self.grid_scroll.setWidget(self.grid_container)
        self.grid_scroll.viewport().setObjectName("GridViewport")
        body_layout.addWidget(self.grid_scroll, 1)

        # Right-side Legend Drawer
        self.drawer_frame = QFrame()
        self.drawer_frame.setObjectName("LegendDrawer")
        self.drawer_frame.setVisible(False)
        self.drawer_frame.setFixedWidth(250)
        drawer_layout = QVBoxLayout(self.drawer_frame)
        drawer_layout.setContentsMargins(8, 8, 8, 8)
        drawer_title = QLabel("Unified Panel Legends")
        drawer_title.setObjectName("DrawerTitle")
        drawer_layout.addWidget(drawer_title)

        self.drawer_scroll = QScrollArea()
        self.drawer_scroll.setObjectName("DrawerScroll")
        self.drawer_scroll.setWidgetResizable(True)
        setup_frame_style(self.drawer_scroll, "NoFrame", "Plain")
        self.drawer_content = QWidget()
        self.drawer_content.setObjectName("DrawerContent")
        self.drawer_scroll_layout = QVBoxLayout(self.drawer_content)
        self.drawer_scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.drawer_scroll_layout.setSpacing(6)
        self.drawer_scroll.setWidget(self.drawer_content)
        self.drawer_scroll.viewport().setObjectName("DrawerViewport")
        drawer_layout.addWidget(self.drawer_scroll, 1)

        body_layout.addWidget(self.drawer_frame)
        root_layout.addLayout(body_layout, 1)

        # Status Bar
        self.status_frame = QFrame()
        self.status_frame.setObjectName("StatusFrame")
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(6, 3, 6, 3)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
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
        status_layout.addWidget(self.overlap_label)

        status_layout.addSpacing(12)

        self.coords_label = QLabel("Cursor Coord: -")
        self.coords_label.setObjectName("CoordsLabel")
        status_layout.addWidget(self.coords_label)

        root_layout.addWidget(self.status_frame)

    def _apply_qss(self) -> None:
        """Light stylesheet matching the 02viz family.

        Every text colour reaches at least 4.5:1 against its background, so the
        dialog stays readable whatever QGIS theme (default, Blend of Gray,
        Night Mapping) or operating-system dark mode is active.
        """
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
            QFrame#GridContent, QWidget#GridViewport, QWidget#SwipeHolder {
                background-color: #fbfbfd;
            }
            QFrame#LegendDrawer {
                background: #ffffff;
                border: 1px solid #cbd3da;
                border-radius: 6px;
            }
            QWidget#DrawerContent, QWidget#DrawerViewport {
                background-color: #ffffff;
            }
            QLabel#DrawerTitle {
                color: #1d6b63;
                font-size: 12px;
                font-weight: bold;
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
                color: #1d6b63;
                font-family: "Consolas", monospace;
                font-weight: 700;
            }
            QLabel#OverlapLabel {
                color: #9a4a00;
                font-family: "Consolas", monospace;
                font-weight: bold;
            }
            QLabel#CoordsLabel {
                color: #1d6b63;
                font-family: "Consolas", monospace;
                font-weight: 600;
            }
            QLabel#WorkflowHint, QLabel#SwipeHint {
                color: #52636c;
                padding-left: 6px;
                font-size: 11px;
            }
            QLabel#TypeBadge {
                background-color: #e4f2f0;
                color: #17635b;
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
                border: 1px solid #8a99a3;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background-color: #1d6b63;
                border: 1px solid #17635b;
            }
            QComboBox {
                background-color: #ffffff;
                color: #16323f;
                border: 1px solid #cbd3da;
                border-radius: 6px;
                padding: 3px 6px;
                min-width: 90px;
                font-size: 11px;
            }
            QWidget#PanelHeader QComboBox {
                min-width: 40px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 16px;
                border-left: 1px solid #cbd3da;
            }
            QComboBox:hover {
                background-color: #ffffff;
                border-color: #1d6b63;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #16323f;
                border: 1px solid #cbd3da;
                selection-background-color: #1d6b63;
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
                border-color: #1d6b63;
            }
            QPushButton:pressed, QPushButton:checked {
                background-color: #dbe1e6;
                border-color: #1d6b63;
            }
            QPushButton#PrimaryAction {
                background-color: #1d6b63;
                color: #ffffff;
                border-color: #17635b;
                padding-left: 14px;
                padding-right: 14px;
            }
            QPushButton#PrimaryAction:hover {
                background-color: #17635b;
            }
            QPushButton#PanelHeaderIconBtn, QToolButton#PanelHeaderIconBtn {
                background-color: transparent;
                color: #16323f;
                border: 1px solid transparent;
                padding: 1px;
                font-size: 12px;
            }
            QPushButton#PanelHeaderIconBtn:hover, QToolButton#PanelHeaderIconBtn:hover {
                background-color: #cbd3da;
                border-radius: 3px;
            }
            QToolButton#PanelHeaderIconBtn::menu-indicator {
                image: none;
            }
            QFrame#MapPanel {
                background-color: #ffffff;
                border: 2px solid #cbd3da;
                border-radius: 8px;
            }
            QFrame#MapPanel[active="true"] {
                border: 2px solid #1d6b63;
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

    # ───────────────────────── Grid & swipe layouts ─────────────────────────

    def set_grid_layout(self, layout_name: str) -> None:
        """Replace the panels with ``layout_name``; keep each panel's choice, the main map and the scale."""
        if self._cleaned:
            return
        reference = self._capture_reference_view()
        kept = [self._panel_snapshot(panel) for panel in self.panels]
        with self._syncing():
            self._teardown_grid()
            if layout_name == SWIPE_LAYOUT:
                if not self._build_swipe_layout():
                    self._teardown_grid()
                    QMessageBox.information(
                        self,
                        "Swipe",
                        "Swipe could not be started in this version of the plugin. "
                        "Showing two panels side by side instead.",
                    )
                    layout_name = "2 Panels (1x2)"
                    self.grid_combo.blockSignals(True)
                    self.grid_combo.setCurrentText(layout_name)
                    self.grid_combo.blockSignals(False)
                    self._build_grid(*GRID_LAYOUTS[layout_name])
            else:
                self._build_grid(*GRID_LAYOUTS.get(layout_name, GRID_LAYOUTS[DEFAULT_LAYOUT]))

            self.populate_layer_combos()
            self.populate_theme_combos()
            for panel, snapshot in zip(self.panels, kept):
                self._apply_panel_snapshot(panel, snapshot)
            self.sync_all_panel_layers()
            self._update_swipe_layers()
            for canvas in self._synced_canvases():
                self._seed_view(canvas, reference)
            if self.panels:
                self.set_active_panel(self.panels[0])

        if self.swipe_panel is not None:
            self.workflow_hint.setText(
                "<b>Swipe:</b> choose the bottom and the top layers above the map, "
                "then drag the divider."
            )
        else:
            self.workflow_hint.setText(
                "<b>Fast comparison:</b> click <b>Choose for Me</b> to fill "
                f"all {len(self.panels)} panels with different layers."
            )
        self.refresh_all_canvases()
        self._schedule_overlays()

    def _build_grid(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        count = rows * cols
        for index in range(count):
            panel = self._make_panel(index)
            panel.set_compact(cols >= 4)

        if rows == 1:
            h_splitter = QSplitter(get_orientation("Horizontal"))
            for panel in self.panels:
                h_splitter.addWidget(panel)
            h_splitter.setSizes([max(1, self.width() // cols)] * cols)
            self.grid_layout.addWidget(h_splitter, 0, 0)
            return

        v_splitter = QSplitter(get_orientation("Vertical"))
        row1_splitter = QSplitter(get_orientation("Horizontal"))
        for idx in range(cols):
            row1_splitter.addWidget(self.panels[idx])
        v_splitter.addWidget(row1_splitter)
        row2_splitter = QSplitter(get_orientation("Horizontal"))
        for idx in range(cols, count):
            row2_splitter.addWidget(self.panels[idx])
        v_splitter.addWidget(row2_splitter)
        h_sizes = [max(1, self.width() // cols)] * cols
        row1_splitter.setSizes(h_sizes)
        row2_splitter.setSizes(h_sizes)
        v_splitter.setSizes([max(1, self.height() // 2)] * 2)
        self.grid_layout.addWidget(v_splitter, 0, 0)

    def _make_panel(self, index: int, picker: bool = False) -> MapPanelWidget:
        panel = MapPanelWidget(index, self.iface, self, picker_only=picker)
        canvas = panel.canvas
        # QgsMapCanvas writes and reads a <mapcanvas> node named after the object;
        # a name keeps the panels' nodes apart from the main map's.
        canvas.setObjectName(f"zero2multimap_{'picker' if picker else 'panel'}_{index + 1}")
        if not self.isVisible():
            canvas.freeze(True)
        canvas.setDestinationCrs(self.iface.mapCanvas().mapSettings().destinationCrs())
        canvas.extentsChanged.connect(lambda c=canvas: self._on_canvas_extent_changed(c))
        panel.active_changed.connect(self.set_active_panel)
        panel.layers_changed.connect(self._on_panel_layers_changed)
        if not picker:
            self.event_filters.append(CanvasEventFilter(
                panel,
                self.on_mouse_moved_in_panel,
                self.on_mouse_left_panel,
                self._on_canvas_clicked,
            ))
            self._panel_watchers.append(
                CanvasResizeWatcher(canvas, self._on_canvas_resizing, self._on_canvas_showing)
            )
        self.panels.append(panel)
        return panel

    def _build_swipe_layout(self) -> bool:
        """Two layer-set pickers (Panel 1 = bottom, Panel 2 = top) above one swipe map."""
        self.rows, self.cols = 1, 1
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        pickers = QHBoxLayout()
        pickers.setSpacing(3)
        titles = (
            ("Bottom layers", "Shown everywhere on the swipe map (Panel 1)."),
            ("Top layers", "Shown on one side of the divider (Panel 2)."),
        )
        for index, (title, tooltip) in enumerate(titles):
            panel = self._make_panel(index, picker=True)
            panel.title_label.setText(title)
            panel.title_label.setToolTip(tooltip)
            pickers.addWidget(panel, 1)
        outer.addLayout(pickers)

        frame = QFrame()
        frame.setObjectName("MapPanel")
        setup_frame_style(frame, "StyledPanel", "Raised")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        frame_layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("PanelHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 4, 6, 4)
        header_layout.setSpacing(6)
        title = QLabel("Swipe")
        title.setObjectName("PanelTitle")
        header_layout.addWidget(title)
        header_layout.addWidget(QLabel("Divider:"))
        self.swipe_orientation_combo = QComboBox()
        self.swipe_orientation_combo.addItems([
            "Vertical (top layers on the left)",
            "Horizontal (top layers above)",
        ])
        self.swipe_orientation_combo.setCurrentIndex(0 if self._swipe_vertical else 1)
        self.swipe_orientation_combo.setToolTip("Turn the divider upright or lay it flat.")
        self.swipe_orientation_combo.currentIndexChanged.connect(self._on_swipe_orientation_changed)
        header_layout.addWidget(self.swipe_orientation_combo)
        hint = QLabel("Drag the divider across the map to compare the two layer sets.")
        hint.setObjectName("SwipeHint")
        header_layout.addWidget(hint)
        header_layout.addStretch(1)
        frame_layout.addWidget(header)

        self.swipe_holder = QWidget()
        self.swipe_holder.setObjectName("SwipeHolder")
        self.swipe_holder_layout = QVBoxLayout(self.swipe_holder)
        self.swipe_holder_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.addWidget(self.swipe_holder, 1)
        outer.addWidget(frame, 1)
        self.grid_layout.addWidget(container, 0, 0)
        return self._build_swipe_view()

    def _build_swipe_view(self) -> bool:
        """Create the SwipePanel and hook its canvas into the sync like any panel canvas."""
        try:
            panel = SwipePanel(self.swipe_holder)
        except NotImplementedError:
            return False
        canvas = panel.canvas()
        self.swipe_panel = panel
        self._swipe_canvas = canvas
        self._swipe_needs_rebuild = False
        self.swipe_holder_layout.addWidget(panel, 1)
        canvas.setObjectName("zero2multimap_swipe")
        if not self.isVisible():
            canvas.freeze(True)
        canvas.setDestinationCrs(self.iface.mapCanvas().mapSettings().destinationCrs())
        self._swipe_slot = lambda c=canvas: self._on_canvas_extent_changed(c)
        canvas.extentsChanged.connect(self._swipe_slot)
        self._swipe_watcher = CanvasResizeWatcher(canvas, self._on_canvas_resizing, self._on_canvas_showing)
        self._swipe_click_filter = CanvasEventFilter(
            None, None, None, lambda _panel, pos: self._on_swipe_clicked(pos), canvas=canvas
        )
        panel.set_orientation(self._swipe_vertical)
        panel.set_divider_ratio(self._swipe_ratio)
        self._update_swipe_layers()
        return True

    def _release_swipe_view(self) -> None:
        """Disconnect and clean up the swipe panel (the dialog never reuses one)."""
        panel = self.swipe_panel
        canvas = self._swipe_canvas
        self.swipe_panel = None
        self._swipe_canvas = None
        if panel is None:
            return
        with contextlib.suppress(RuntimeError):
            self._swipe_ratio = float(panel.divider_ratio())
        if canvas is not None and self._swipe_slot is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                canvas.extentsChanged.disconnect(self._swipe_slot)
        self._swipe_slot = None
        if self._swipe_watcher is not None:
            self._swipe_watcher.detach()
            with contextlib.suppress(RuntimeError):
                self._swipe_watcher.deleteLater()
            self._swipe_watcher = None
        if self._swipe_click_filter is not None:
            with contextlib.suppress(RuntimeError):
                canvas.viewport().removeEventFilter(self._swipe_click_filter)
                self._swipe_click_filter.deleteLater()
            self._swipe_click_filter = None
        self._pending_view.pop(id(canvas), None)
        with contextlib.suppress(RuntimeError):
            panel.cleanup()
        with contextlib.suppress(RuntimeError):
            panel.hide()
            panel.deleteLater()

    def _on_swipe_orientation_changed(self, index: int) -> None:
        self._swipe_vertical = index == 0
        if self.swipe_panel is not None:
            self.swipe_panel.set_orientation(self._swipe_vertical)

    def _update_swipe_layers(self) -> None:
        """Bottom set = what Panel 1 would show, top set = what Panel 2 would show."""
        if self.swipe_panel is None or len(self.panels) < 2:
            return
        bottom, _bottom_bg = self.panels[0].layer_set()
        top, _top_bg = self.panels[1].layer_set()
        self.swipe_panel.set_layers(bottom, top)

    def _on_swipe_clicked(self, pos: QPoint) -> None:
        if not self.inspect_btn.isChecked() or self._swipe_canvas is None:
            return
        map_point = self._swipe_canvas.getCoordinateTransform().toMapCoordinates(pos.x(), pos.y())
        self.perform_multi_inspect(map_point, source_canvas=self._swipe_canvas)

    def _teardown_grid(self) -> None:
        """Remove every panel (and the swipe map) together with everything drawn on them."""
        self._clear_diff_bands()
        self._remove_main_marker()
        for event_filter in self.event_filters:
            with contextlib.suppress(RuntimeError):
                event_filter.deleteLater()
        self.event_filters.clear()
        for watcher in self._panel_watchers:
            watcher.detach()
            with contextlib.suppress(RuntimeError):
                watcher.deleteLater()
        self._panel_watchers.clear()
        self._release_swipe_view()
        for panel in self.panels:
            with contextlib.suppress(TypeError, RuntimeError):
                panel.canvas.extentsChanged.disconnect()
            with contextlib.suppress(RuntimeError):
                panel.canvas.stopRendering()
        while self.grid_layout.count() > 0:
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.panels.clear()
        self.active_panel = None
        self._pending_view.clear()
        self._shown_views.clear()

    def _synced_canvases(self) -> list:
        canvases = [panel.canvas for panel in self.panels]
        if self._swipe_canvas is not None:
            canvases.append(self._swipe_canvas)
        return canvases

    def _reference_canvas(self) -> QgsMapCanvas | None:
        """The canvas the status bar describes: the swipe map, or the active panel."""
        if self._swipe_canvas is not None:
            return self._swipe_canvas
        if self.active_panel is not None:
            return self.active_panel.canvas
        return self.panels[0].canvas if self.panels else None

    def _panel_for_canvas(self, canvas) -> MapPanelWidget | None:
        for panel in self.panels:
            if panel.canvas is canvas:
                return panel
        return None

    def _capture_reference_view(self):
        """The view new panels should open with: the main map when synced, else the active panel."""
        main = self.iface.mapCanvas()
        candidates = [main]
        if not (self.sync_main_chk.isChecked() and self.sync_extents_chk.isChecked()):
            reference = self._reference_canvas()
            if reference is not None:
                candidates.insert(0, reference)
        for canvas in candidates:
            with contextlib.suppress(RuntimeError):
                if not canvas.mapSettings().extent().isEmpty() and canvas.scale() > 0:
                    return view_of(canvas)
        return None

    def _seed_view(self, canvas: QgsMapCanvas, reference) -> None:
        if reference is None:
            main_extent = self.iface.mapCanvas().extent()
            if not main_extent.isEmpty():
                canvas.setExtent(main_extent)
            return
        center, crs, scale, rotation = reference
        moved = transform_point(center, crs, canvas.mapSettings().destinationCrs())
        if moved is None:
            return
        if canvas.mapSettings().extent().isEmpty():
            canvas.setExtent(_rect_around(moved, 1.0, 1.0))
        apply_view(canvas, moved, scale, rotation)

    def get_global_base_layer(self) -> QgsMapLayer | None:
        """Returns the layer selected globally as base layer, if any."""
        layer_id = self.base_layer_combo.currentData()
        if not layer_id:
            return None
        return QgsProject.instance().mapLayer(layer_id)

    # ───────────────────────── Layer lists (debounced) ─────────────────────────

    def _on_project_layers_changed(self, *_args) -> None:
        self._combos_dirty = True
        self._schedule_layer_rebuild()

    def _on_project_layers_removed(self, layer_ids=()) -> None:
        """Panels showing a removed layer let go of it at once; the lists follow debounced."""
        removed = set(layer_ids or ())
        if self.base_layer_combo.currentData() in removed:
            self.base_layer_combo.setCurrentIndex(0)
        orphaned = []
        for panel in self.panels:
            if panel.layer_combo.currentData() in removed:
                panel.layer_combo.blockSignals(True)
                panel.layer_combo.setCurrentIndex(0)
                panel.layer_combo.blockSignals(False)
                if panel.mode == "layer":
                    panel.update_canvas_layers()
                    orphaned.append(panel)
        if orphaned:
            names = ", ".join(panel.title_label.text() for panel in orphaned)
            self.status_label.setText(f"{names}: the layer was removed from the project. Pick another one.")
        self._on_project_layers_changed()

    def _schedule_layer_rebuild(self, *_args) -> None:
        """Coalesce layer changes: one rebuild after a burst, and none while hidden."""
        if self._cleaned:
            return
        if not self.isVisible():
            self._layers_dirty = True
            return
        self._layer_timer.start()

    def _rebuild_layer_state(self) -> None:
        if self._cleaned:
            return
        if not self.isVisible():
            self._layers_dirty = True
            return
        self._layers_dirty = False
        if self._combos_dirty:
            self.populate_layer_combos()
        self.sync_all_panel_layers()

    def _flush_layer_rebuild(self) -> None:
        """Bring the layer lists up to date now (before a template or auto-fill reads them)."""
        self._layer_timer.stop()
        if self._combos_dirty:
            self.populate_layer_combos()

    def _connect_theme_collection(self) -> None:
        collection = QgsProject.instance().mapThemeCollection()
        if collection is self._theme_collection:
            return
        self._disconnect_theme_collection()
        collection.mapThemesChanged.connect(self._on_themes_changed)
        self._theme_collection = collection

    def _disconnect_theme_collection(self) -> None:
        if self._theme_collection is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                self._theme_collection.mapThemesChanged.disconnect(self._on_themes_changed)
        self._theme_collection = None

    def _on_theme_collection_replaced(self) -> None:
        self._theme_collection = None
        self._connect_theme_collection()
        self._on_themes_changed()

    def _on_themes_changed(self) -> None:
        if self._cleaned:
            return
        if not self.isVisible():
            self._themes_dirty = True
            return
        self.populate_theme_combos()

    def populate_layer_combos(self) -> None:
        """Fill every panel's layer list and the shared background list.

        A panel whose layer left the project falls back to "Choose a layer…"
        and an empty map, so the list never names a layer the map does not show.
        """
        self._combos_dirty = False
        layers = [layer for layer in QgsProject.instance().mapLayers().values() if layer.isSpatial()]
        layers.sort(key=lambda layer: layer.name().casefold())

        current_base_id = self.base_layer_combo.currentData()
        self.base_layer_combo.blockSignals(True)
        self.base_layer_combo.clear()
        self.base_layer_combo.addItem("No shared background", None)
        for layer in layers:
            self.base_layer_combo.addItem(layer.name(), layer.id())
        current_base_index = self.base_layer_combo.findData(current_base_id) if current_base_id else 0
        self.base_layer_combo.setCurrentIndex(max(0, current_base_index))
        self.base_layer_combo.blockSignals(False)
        base_lost = bool(current_base_id) and current_base_index < 0

        orphaned = []
        for panel in self.panels:
            current_layer_id = panel.layer_combo.currentData()
            panel.layer_combo.blockSignals(True)
            panel.layer_combo.clear()
            panel.layer_combo.addItem(LAYER_PLACEHOLDER, None)
            for layer in layers:
                panel.layer_combo.addItem(layer.name(), layer.id())
            index = panel.layer_combo.findData(current_layer_id) if current_layer_id else 0
            if index < 0:
                index = 0
                if panel.mode == "layer":
                    orphaned.append(panel)
            panel.layer_combo.setCurrentIndex(index)
            panel.layer_combo.blockSignals(False)

        for panel in orphaned:
            panel.update_canvas_layers()
        if base_lost:
            self.refresh_layer_panels()
        if orphaned:
            names = ", ".join(panel.title_label.text() for panel in orphaned)
            self.status_label.setText(f"{names}: the layer was removed from the project. Pick another one.")

    def populate_theme_combos(self) -> None:
        """Fills the Map Theme Selector in all panels."""
        self._themes_dirty = False
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
            if panel.mode == "theme" and panel.theme_combo.currentText() != current_theme:
                panel.update_canvas_layers()

    def sync_all_panel_layers(self) -> None:
        """Update every panel after the QGIS main-canvas layers change."""
        for panel in self.panels:
            panel.update_canvas_layers()

    def refresh_layer_panels(self, *_args) -> None:
        """Update one-layer comparison panels after the background changes."""
        for panel in self.panels:
            if panel.mode == "layer":
                panel.update_canvas_layers()

    def _on_panel_layers_changed(self, panel: MapPanelWidget) -> None:
        """A panel shows other layers: its chip, the Diff and the swipe map must follow."""
        if panel in self.panels[:2]:
            self._update_swipe_layers()
        if not self._is_syncing:
            self._schedule_overlays()

    # ───────────────────────── Auto-fill & templates ─────────────────────────

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

    def _layers_newest_first(self) -> list[QgsMapLayer]:
        """Spatial layers in Layers-panel order: QGIS puts a newly added layer on top."""
        base_layer = self.get_global_base_layer()
        base_id = base_layer.id() if base_layer else None
        layers = []
        for node in QgsProject.instance().layerTreeRoot().findLayers():
            layer = node.layer()
            if layer is not None and layer.id() != base_id and layer.isValid() and layer.isSpatial():
                layers.append(layer)
        return layers

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

    def auto_fill_from_current_extent(self) -> int:
        """Assign a different extent-relevant layer to each current panel; return how many."""
        if not self.panels:
            return 0
        self._flush_layer_rebuild()

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
            return 0

        for panel, layer in zip(self.panels, selected):
            panel.set_comparison_layer(layer)

        for panel in self.panels[len(selected):]:
            panel.set_mode("sync")

        self.status_label.setText(f"Ready: Assigned {len(selected)} layers to panels.")
        self.workflow_hint.setText(
            "<b>Comparison ready:</b> each panel is isolated to one layer."
        )
        return len(selected)

    @staticmethod
    def _pick_time_series(dated: list) -> list:
        """The series to show: the largest group of one theme over several years.

        ``landuse_2015``, ``landuse_2019`` and ``landuse_2023`` beat a lone
        ``schools_2019``. Without such a group, every dated layer is used.
        """
        groups: dict[tuple, list] = {}
        for year, layer in dated:
            groups.setdefault((geometry_kind(layer), name_stem(layer.name())), []).append((year, layer))
        best = None
        for members in groups.values():
            if len({year for year, _layer in members}) < 2:
                continue
            key = (len(members), max(year for year, _layer in members))
            if best is None or key > best[0]:
                best = (key, members)
        series = best[1] if best is not None else dated
        return sorted(series, key=lambda item: (item[0], item[1].name().casefold()))

    def auto_fill_time_series(self) -> list:
        """Assign layers whose names carry a year, oldest first; report what was assigned."""
        if not self.panels:
            return []
        self._flush_layer_rebuild()

        dated = []
        for layer in self._ordered_spatial_layers():
            year = extract_year(layer.name())
            if year is not None:
                dated.append((year, layer))
        if not dated:
            QMessageBox.information(
                self,
                "Time-Series Auto-Fill",
                "No layer name contains a year between 1900 and 2099. Name the layers "
                "like landuse_2015 and landuse_2023, or pick a layer in each panel.",
            )
            self.status_label.setText("Time series: no layer name contains a year.")
            return []

        series = self._pick_time_series(dated)
        selected = series[:len(self.panels)]
        for panel, (_year, layer) in zip(self.panels, selected):
            panel.set_comparison_layer(layer)
        for panel in self.panels[len(selected):]:
            panel.set_mode("sync")

        summary = ", ".join(layer.name() for _year, layer in selected)
        message = f"Time series: {summary}"
        if len(series) > len(selected):
            message += f" ({len(series) - len(selected)} later layer(s) did not fit)"
        if len(selected) < len(self.panels):
            message += f"; the other {len(self.panels) - len(selected)} panel(s) follow the main map"
        self.status_label.setText(message + ".")
        return [layer for _year, layer in selected]

    def _before_after_pair(self) -> tuple | None:
        """(before, after): two layers of one kind, preferring one theme over two years."""
        groups: dict[tuple, list] = {}
        for layer in self._ordered_spatial_layers():
            year = extract_year(layer.name())
            if year is not None:
                groups.setdefault((geometry_kind(layer), name_stem(layer.name())), []).append((year, layer))
        best = None
        for members in groups.values():
            members.sort(key=lambda item: (item[0], item[1].name().casefold()))
            latest = members[-1]
            earlier = [item for item in members if item[0] < latest[0]]
            if not earlier:
                continue
            if best is None or latest[0] > best[1][0]:
                best = (earlier[-1], latest)
        if best is not None:
            return best[0][1], best[1][1]

        by_kind: dict[str, list] = {}
        for layer in self._layers_newest_first():
            bucket = by_kind.setdefault(geometry_kind(layer), [])
            bucket.append(layer)
            if len(bucket) == 2:
                newer, older = bucket
                return older, newer
        return None

    def _set_grid(self, layout_name: str) -> None:
        if self.grid_combo.currentText() != layout_name:
            self.grid_combo.setCurrentText(layout_name)

    def load_built_in_preset(self, preset_type: str) -> None:
        """Load one of the built-in scenario templates."""
        self._flush_layer_rebuild()
        if preset_type == "before_after":
            self._set_grid("2 Panels (1x2)")
            pair = self._before_after_pair()
            if pair is None or len(self.panels) < 2:
                for panel in self.panels[:2]:
                    panel.layer_combo.setCurrentIndex(0)
                    panel.set_mode("layer")
                QMessageBox.information(
                    self,
                    "Before & After",
                    "Before & After needs two layers of the same kind, for example two "
                    "polygon layers or two rasters. Pick the earlier layer in Panel 1 "
                    "and the later one in Panel 2.",
                )
                self.status_label.setText("Before & After: pick a layer in each panel.")
                return
            before, after = pair
            self.panels[0].set_comparison_layer(before)
            self.panels[1].set_comparison_layer(after)
            self.status_label.setText(
                f"Before & After: {before.name()} (Panel 1) and {after.name()} (Panel 2)."
            )
        elif preset_type == "matrix_2x2":
            self._set_grid("4 Panels (2x2)")
            assigned = self.auto_fill_from_current_extent()
            if 0 < assigned < 4:
                QMessageBox.information(
                    self,
                    "4-Scenario Matrix",
                    f"The project has {assigned} spatial layer(s), so {4 - assigned} "
                    "panel(s) follow the main map. Add the missing scenario layers and "
                    "choose them in those panels.",
                )
        elif preset_type == "timeline_1x3":
            self._set_grid("3 Panels (1x3)")
            self.auto_fill_time_series()

    # ───────────────────────── Presets & project setup ─────────────────────────

    def _panel_snapshot(self, panel: MapPanelWidget) -> dict:
        layer = panel.selected_layer()
        return {
            "mode": panel.mode,
            "layer": layer.name() if layer is not None else "",
            "layer_id": layer.id() if layer is not None else "",
            "theme": panel.theme_combo.currentText() if panel.mode == "theme" else "",
            "scale": round(panel.canvas.scale(), 3),
        }

    def _setup_snapshot(self) -> dict:
        base = self.get_global_base_layer()
        reference = self._reference_canvas()
        data = {
            "version": 2,
            "grid": self.grid_combo.currentText(),
            "base_layer": base.name() if base is not None else "",
            "base_layer_id": base.id() if base is not None else "",
            "scale": round(reference.scale(), 3) if reference is not None else 0,
            "panels": [self._panel_snapshot(panel) for panel in self.panels],
        }
        if reference is not None:
            center = reference.center()
            data["center"] = [center.x(), center.y()]
            data["crs"] = reference.mapSettings().destinationCrs().authid()
        if self.swipe_panel is not None:
            with contextlib.suppress(RuntimeError):
                self._swipe_ratio = float(self.swipe_panel.divider_ratio())
        data["swipe"] = {"vertical": self._swipe_vertical, "ratio": self._swipe_ratio}
        return data

    @staticmethod
    def _find_layer(layer_id: str, name: str) -> QgsMapLayer | None:
        project = QgsProject.instance()
        layer = project.mapLayer(layer_id) if layer_id else None
        if layer is None and name:
            matches = project.mapLayersByName(name)
            layer = matches[0] if matches else None
        return layer

    def _apply_panel_snapshot(self, panel: MapPanelWidget, snapshot: dict) -> str:
        """Restore one panel's mode, layer and theme; return what could not be found."""
        mode = snapshot.get("mode", "sync")
        if mode == "layer":
            layer = self._find_layer(snapshot.get("layer_id", ""), snapshot.get("layer", ""))
            if layer is not None and panel.set_comparison_layer(layer):
                return ""
            panel.layer_combo.setCurrentIndex(0)
            panel.set_mode("layer")
            return snapshot.get("layer", "") or ""
        if mode == "theme":
            theme = snapshot.get("theme", "")
            panel.set_mode("theme")
            if theme and panel.theme_combo.findText(theme) >= 0:
                panel.theme_combo.setCurrentText(theme)
                return ""
            return theme
        panel.set_mode("sync")
        return ""

    def apply_setup(self, data: dict) -> list[str]:
        """Restore a saved setup (preset or project); return the names it could not find."""
        if not isinstance(data, dict):
            return []
        self._flush_layer_rebuild()
        grid = data.get("grid")
        if grid in GRID_LAYOUTS or grid == SWIPE_LAYOUT:
            swipe = data.get("swipe") or {}
            if isinstance(swipe, dict):
                self._swipe_vertical = bool(swipe.get("vertical", self._swipe_vertical))
                with contextlib.suppress(TypeError, ValueError):
                    self._swipe_ratio = min(1.0, max(0.0, float(swipe.get("ratio", self._swipe_ratio))))
            self._set_grid(grid)
            if grid == SWIPE_LAYOUT:
                self._push_swipe_settings()

        missing = []
        base = self._find_layer(data.get("base_layer_id", ""), data.get("base_layer", ""))
        index = self.base_layer_combo.findData(base.id()) if base is not None else 0
        if base is None and data.get("base_layer"):
            missing.append(data["base_layer"])
        self.base_layer_combo.setCurrentIndex(max(0, index))

        panel_data = data.get("panels") or []
        for panel, snapshot in zip(self.panels, panel_data):
            if isinstance(snapshot, dict):
                lost = self._apply_panel_snapshot(panel, snapshot)
                if lost:
                    missing.append(lost)

        scale = data.get("scale") or 0
        if not scale and panel_data and isinstance(panel_data[0], dict):
            scale = panel_data[0].get("scale") or 0
        with contextlib.suppress(TypeError, ValueError):
            scale = float(scale)
            if scale > 0:
                self._apply_stored_scale(scale, panel_data, data)
        return missing

    @staticmethod
    def _stored_center(data: dict):
        """The saved centre and its CRS, or ``(None, None)``."""
        center = data.get("center")
        crs = QgsCoordinateReferenceSystem(str(data.get("crs") or ""))
        if not isinstance(center, (list, tuple)) or len(center) != 2 or not crs.isValid():
            return None, None
        try:
            return QgsPointXY(float(center[0]), float(center[1])), crs
        except (TypeError, ValueError):
            return None, None

    def _apply_stored_scale(self, scale: float, panel_data: list, data: dict | None = None) -> None:
        """Give the panels a saved view without ever moving the user's main map.

        With Sync Navigation and Sync Canvas on, the main map leads and the
        panels keep following it, so the saved scale is not applied.
        """
        if self.sync_extents_chk.isChecked():
            if self.sync_main_chk.isChecked():
                return
            center, center_crs = self._stored_center(data or {})
            with self._syncing():
                for canvas in self._synced_canvases():
                    target = None
                    if center is not None:
                        target = transform_point(center, center_crs, canvas.mapSettings().destinationCrs())
                    apply_view(canvas, target or QgsPointXY(canvas.center()), scale, canvas.rotation())
                    canvas.refresh()
            self._update_scale_readout()
            return
        with self._syncing():
            for panel, snapshot in zip(self.panels, panel_data):
                panel_scale = 0.0
                if isinstance(snapshot, dict):
                    with contextlib.suppress(TypeError, ValueError):
                        panel_scale = float(snapshot.get("scale") or 0)
                if panel_scale > 0:
                    apply_view(panel.canvas, QgsPointXY(panel.canvas.center()), panel_scale, panel.canvas.rotation())
                    panel.canvas.refresh()
        self._update_scale_readout()

    def _push_swipe_settings(self) -> None:
        """Show the stored divider orientation and position on the live swipe map."""
        if self.swipe_panel is None:
            return
        combo = self.swipe_orientation_combo
        combo.blockSignals(True)
        combo.setCurrentIndex(0 if self._swipe_vertical else 1)
        combo.blockSignals(False)
        self.swipe_panel.set_orientation(self._swipe_vertical)
        self.swipe_panel.set_divider_ratio(self._swipe_ratio)

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
        settings.beginGroup(PRESET_GROUP)
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
        """Save the layout, every panel's choice (layers, map themes) and the scale."""
        preset_name, ok = QInputDialog.getText(self, "Save Workspace Preset", "Preset Name:")
        if not ok or not preset_name.strip():
            return
        clean_name = preset_name.strip()
        settings = QSettings()
        settings.setValue(f"{PRESET_GROUP}/{clean_name}", json.dumps(self._setup_snapshot()))
        self._refresh_preset_combo()
        QMessageBox.information(self, "Workspace Presets", f"Preset '{clean_name}' saved.")

    def load_saved_preset(self, preset_name: str) -> None:
        """Restore a user-saved workspace preset from QSettings."""
        raw = QSettings().value(f"{PRESET_GROUP}/{preset_name}", "")
        if not raw:
            return
        try:
            data = json.loads(str(raw))
        except ValueError:
            self.status_label.setText(f"Preset '{preset_name}' could not be read.")
            return
        missing = self.apply_setup(data)
        if missing:
            self.status_label.setText(
                f"Loaded preset {preset_name}; not found in this project: {', '.join(missing)}."
            )
        else:
            self.status_label.setText(f"Loaded workspace preset: {preset_name}")

    def _write_project_setup(self, _document=None) -> None:
        """Store the panel setup in the project file when the project is saved."""
        if self._cleaned:
            return
        QgsProject.instance().writeEntry(PROJECT_SCOPE, PROJECT_KEY, json.dumps(self._setup_snapshot()))

    def _on_project_read(self, _document=None) -> None:
        """Start restoring once the project read has finished.

        Every QgsMapCanvas, the panels' included, restores its own extent from
        the project file right after this slot runs. Until the read is over
        those moves are ignored, so they can never reach the main map.
        """
        if self._cleaned:
            return
        self._project_loading = True
        self._views_before_read = [(canvas, view_of(canvas)) for canvas in self._synced_canvases()]
        self._combos_dirty = True
        self._connect_theme_collection()
        self._themes_dirty = True
        self._project_read_timer.start()

    def _finish_project_read(self) -> None:
        self._project_loading = False
        views, self._views_before_read = self._views_before_read, []
        if self._cleaned:
            return
        # Undo what the canvases restored by themselves: the stored setup, not a
        # <mapcanvas> node that happens to share a canvas's name, decides the view.
        with self._syncing():
            for canvas, (center, crs, scale, rotation) in views:
                with contextlib.suppress(RuntimeError):
                    if canvas.mapSettings().destinationCrs() != crs:
                        canvas.setDestinationCrs(crs)
                    apply_view(canvas, center, scale, rotation)
        self.populate_theme_combos()
        self._restore_project_setup()
        if self.sync_main_chk.isChecked() and self.sync_extents_chk.isChecked():
            # The main map leads: the panels take its view, never the other way round.
            if self.isVisible():
                self._follow_main_crs()
                main_canvas = self.iface.mapCanvas()
                with self._syncing():
                    for canvas in self._synced_canvases():
                        self._copy_and_refresh(main_canvas, canvas)
                self._update_scale_readout()
            else:
                self._view_dirty = True
        self._schedule_overlays()

    def _restore_project_setup(self) -> None:
        raw, ok = QgsProject.instance().readEntry(PROJECT_SCOPE, PROJECT_KEY, "")
        if not ok or not raw:
            return
        try:
            data = json.loads(raw)
        except ValueError:
            return
        missing = self.apply_setup(data)
        message = "Panel setup restored from the project."
        if missing:
            message = f"Panel setup restored from the project; not found: {', '.join(missing)}."
        self.status_label.setText(message)

    # ───────────────────────── Panels & canvases ─────────────────────────

    def set_active_panel(self, active_panel: MapPanelWidget) -> None:
        """Visually marks a panel as active by updating styling dynamic property."""
        if active_panel is None:
            return
        self.active_panel = active_panel
        if not active_panel.picker_only:
            for panel in self.panels:
                panel.setProperty("active", panel is active_panel)
                panel.style().unpolish(panel)
                panel.style().polish(panel)
            self.status_label.setText(f"Active Canvas: Panel {active_panel.index + 1}")
        self._update_scale_readout()

    def _update_scale_readout(self) -> None:
        canvas = self._reference_canvas()
        if canvas is not None:
            self.scale_readout.setText(f"Scale: 1:{round(canvas.scale()):,}")

    def refresh_all_canvases(self) -> None:
        """Triggers a render refresh on all canvases."""
        for canvas in self._synced_canvases():
            canvas.refresh()

    def fit_all_panels(self) -> None:
        """Zoom each map to the full extent of its own layers."""
        if not self.panels:
            return
        with self._syncing():
            for canvas in self._synced_canvases():
                canvas.zoomToFullExtent()
                canvas.refresh()
            if self.sync_main_chk.isChecked():
                main_canvas = self.iface.mapCanvas()
                main_canvas.zoomToFullExtent()
                main_canvas.refresh()
        self._update_scale_readout()
        self._schedule_overlays()

    def export_panels(self) -> tuple[list, int, int]:
        """What an export shows: the panels, or in Swipe the two layer sets side by side (1 x 2)."""
        if self._swipe_canvas is not None and len(self.panels) >= 2:
            return [SwipeExportPanel(picker, self._swipe_canvas) for picker in self.panels[:2]], 1, 2
        return list(self.panels), self.rows, self.cols

    def show_print_layout(self) -> None:
        """Open the export dialog; it is deleted once closed."""
        dlg = PrintLayoutDialog(self, self.iface)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

    # ───────────────────────── Scale Presets ─────────────────────────

    def _on_scale_preset_selected(self, text: str) -> None:
        if not text or "Preset" in text:
            return
        with contextlib.suppress(ValueError):
            raw_denom = text.replace("1:", "").replace(",", "").replace(".", "").strip()
            scale_denom = float(raw_denom)
            self._apply_target_scale(scale_denom)

    def _apply_target_scale(self, target_scale: float, move_main: bool = True) -> None:
        """Every map to 1:``target_scale`` around its own centre (the main map only if asked and synced)."""
        with self._syncing():
            for canvas in self._synced_canvases():
                apply_view(canvas, QgsPointXY(canvas.center()), target_scale, canvas.rotation())
                canvas.refresh()
            if move_main and self.sync_main_chk.isChecked():
                main_canvas = self.iface.mapCanvas()
                apply_view(main_canvas, QgsPointXY(main_canvas.center()), target_scale, main_canvas.rotation())
                main_canvas.refresh()
        self._update_scale_readout()
        self._schedule_overlays()

    # ───────────────────────── Navigation Sync ─────────────────────────

    @contextlib.contextmanager
    def _syncing(self):
        previous = self._is_syncing
        self._is_syncing = True
        try:
            yield
        finally:
            self._is_syncing = previous

    def _on_canvas_resizing(self, canvas: QgsMapCanvas) -> None:
        """A canvas is about to be resized: remember its view so the resize cannot move a map."""
        if canvas is self.iface.mapCanvas():
            self._main_resizing = True
        else:
            key = id(canvas)
            if key not in self._pending_view and not canvas.mapSettings().extent().isEmpty() and canvas.scale() > 0:
                self._pending_view[key] = (QgsPointXY(canvas.center()), canvas.scale(), canvas.rotation())
        self._resize_end_timer.start()

    def _end_resizes(self) -> None:
        self._pending_view.clear()
        self._main_resizing = False

    def _on_canvas_showing(self, canvas: QgsMapCanvas) -> None:
        """Remember a canvas's view before it is shown (and may change its DPI)."""
        if canvas.mapSettings().extent().isEmpty() or canvas.scale() <= 0:
            return
        self._shown_views[id(canvas)] = (canvas, QgsPointXY(canvas.center()), canvas.scale(), canvas.rotation())
        self._show_restore_timer.start()

    def _restore_after_show(self) -> None:
        views, self._shown_views = self._shown_views, {}
        if self._cleaned:
            return
        with self._syncing():
            for canvas, center, scale, rotation in views.values():
                with contextlib.suppress(RuntimeError):
                    if abs(canvas.scale() / scale - 1.0) > 1e-9:
                        apply_view(canvas, center, scale, rotation)
                        canvas.refresh()
        self._update_scale_readout()

    def _copy_and_refresh(self, source: QgsMapCanvas, target: QgsMapCanvas) -> None:
        if copy_view(source, target):
            target.refresh()

    def _on_canvas_extent_changed(self, canvas: QgsMapCanvas) -> None:
        """A panel (or the swipe map) moved: give every other map its centre, scale and rotation."""
        if self._cleaned:
            return
        pending = self._pending_view.pop(id(canvas), None)
        if pending is not None:
            # Resize only: keep this canvas's centre and scale, move nothing else.
            center, scale, rotation = pending
            with self._syncing():
                apply_view(canvas, center, scale, rotation)
            self._schedule_overlays()
            return
        if self._is_syncing or self._project_loading or not any(other is canvas for other in self._synced_canvases()):
            return

        panel = self._panel_for_canvas(canvas)
        if panel is not None and panel is not self.active_panel and not panel.picker_only:
            self.set_active_panel(panel)
        else:
            self._update_scale_readout()

        if self.sync_extents_chk.isChecked():
            with self._syncing():
                for other in self._synced_canvases():
                    if other is not canvas:
                        self._copy_and_refresh(canvas, other)
                if self.sync_main_chk.isChecked():
                    self._copy_and_refresh(canvas, self.iface.mapCanvas())
        self._schedule_overlays()

    def on_panel_extent_changed(self, trigger_panel: MapPanelWidget) -> None:
        """Synchronizes other panels when one is panned or zoomed."""
        self._on_canvas_extent_changed(trigger_panel.canvas)

    def on_main_canvas_extent_changed(self) -> None:
        """Syncs all panels when main QGIS map canvas extent changes."""
        if self._is_syncing or self._cleaned or self._main_resizing:
            return
        if not self.sync_main_chk.isChecked() or not self.sync_extents_chk.isChecked():
            return
        if not self.isVisible():
            # A closed dialog does no work; it catches up when it is shown again.
            self._view_dirty = True
            return
        main_canvas = self.iface.mapCanvas()
        with self._syncing():
            for canvas in self._synced_canvases():
                self._copy_and_refresh(main_canvas, canvas)
        self._update_scale_readout()
        self._schedule_overlays()

    def _on_main_crs_changed(self) -> None:
        """Panels follow the main map's CRS; each keeps its centre and scale."""
        if self._cleaned:
            return
        if not self.isVisible():
            self._view_dirty = True
            return
        self._follow_main_crs()
        if self.sync_main_chk.isChecked() and self.sync_extents_chk.isChecked():
            self.on_main_canvas_extent_changed()

    def _follow_main_crs(self) -> None:
        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        with self._syncing():
            for canvas in self._synced_canvases():
                old_crs = canvas.mapSettings().destinationCrs()
                if old_crs == crs:
                    continue
                center, _crs, scale, rotation = view_of(canvas)
                canvas.setDestinationCrs(crs)
                apply_view(canvas, transform_point(center, old_crs, crs), scale, rotation)
                canvas.refresh()

    # ───────────────────────── Scale/Extent Alignment ─────────────────────────

    def match_scales_to_active(self) -> None:
        """Aligns zoom scale of all panels to match active panel scale."""
        reference = self._reference_canvas()
        if reference is None:
            return
        self._apply_target_scale(reference.scale())

    def match_extents_to_active(self) -> None:
        """Give every map the active panel's centre, scale and rotation."""
        reference = self._reference_canvas()
        if reference is None:
            return
        with self._syncing():
            for canvas in self._synced_canvases():
                if canvas is not reference:
                    self._copy_and_refresh(reference, canvas)
            if self.sync_main_chk.isChecked():
                self._copy_and_refresh(reference, self.iface.mapCanvas())
        self._schedule_overlays()

    # ───────────────────────── Show / hide / cleanup ─────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if event.spontaneous() or self._cleaned:
            return
        for panel in self.panels:
            if not panel.picker_only:
                panel.canvas.freeze(False)
        rebuild_swipe = (
            self._swipe_needs_rebuild
            and self.swipe_panel is None
            and self.grid_combo.currentText() == SWIPE_LAYOUT
            and len(self.panels) >= 2
        )
        if rebuild_swipe and self._build_swipe_view():
            self._seed_view(self._swipe_canvas, self._capture_reference_view())
        if self._swipe_canvas is not None:
            self._swipe_canvas.freeze(False)
        if self._combos_dirty or self._layers_dirty:
            self._rebuild_layer_state()
        if self._themes_dirty:
            self.populate_theme_combos()
        if self._view_dirty:
            self._view_dirty = False
            self._follow_main_crs()
            self.on_main_canvas_extent_changed()
        self.refresh_all_canvases()
        self._schedule_overlays()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if event.spontaneous():
            return
        self._overlay_timer.stop()
        self._layer_timer.stop()
        self._stop_timeline()
        self.on_mouse_left_panel(None)
        self._remove_main_marker()
        for canvas in self._synced_canvases():
            with contextlib.suppress(RuntimeError):
                canvas.stopRendering()
                canvas.freeze(True)
        if self.swipe_panel is not None:
            self._release_swipe_view()
            self._swipe_needs_rebuild = True

    def cleanup(self) -> None:
        """Undo every outside connection and release every canvas item. Safe to call twice."""
        if self._cleaned:
            return
        self._cleaned = True
        for timer in (self._overlay_timer, self._layer_timer, self._resize_end_timer, self._show_restore_timer,
                      self._project_read_timer):
            timer.stop()
        self._stop_timeline()
        for signal, slot in self._connections:
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)
        self._connections.clear()
        self._disconnect_theme_collection()
        if self._main_watcher is not None:
            self._main_watcher.detach()
            with contextlib.suppress(RuntimeError):
                self._main_watcher.deleteLater()
            self._main_watcher = None
        self._teardown_grid()

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

    def on_mouse_left_panel(self, source_panel: MapPanelWidget | None) -> None:
        """Hides laser cursors when mouse leaves a panel canvas."""
        for panel in self.panels:
            panel.marker.hide()
        if self.main_canvas_marker:
            self.main_canvas_marker.hide()

    def _remove_main_marker(self) -> None:
        if self.main_canvas_marker is None:
            return
        with contextlib.suppress(RuntimeError):
            self.main_canvas_marker.hide()
            self.iface.mapCanvas().scene().removeItem(self.main_canvas_marker)
        self.main_canvas_marker = None

    def _on_laser_toggled(self, checked: bool) -> None:
        """Unticking Laser Crosshair removes every marker it drew."""
        if not checked:
            for panel in self.panels:
                panel.marker.hide()
            self._remove_main_marker()
            self.coords_label.setText("Cursor Coord: -")

    def _on_canvas_clicked(self, source_panel: MapPanelWidget, pos: QPoint) -> None:
        """Triggered when mouse clicks on a panel canvas."""
        if not self.inspect_btn.isChecked():
            return
        map_point = source_panel.canvas.mapSettings().mapToPixel().toMapCoordinates(pos.x(), pos.y())
        self.perform_multi_inspect(map_point, source_panel=source_panel)

    # ───────────────────────── Legend drawer, KPI, HUD ─────────────────────────

    def toggle_legend_drawer(self) -> None:
        """Toggle right-side drawer stacking all panel micro-legends in one column."""
        self.drawer_frame.setVisible(not self.drawer_frame.isVisible())
        if self.drawer_frame.isVisible():
            self.update_legend_drawer()

    def update_legend_drawer(self) -> None:
        """Populate right-side drawer with all active panel legends (including graduated & rule-based)."""
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
            title = QLabel(f"<b>{html.escape(panel.title_label.text())} Legend</b>")
            title.setStyleSheet("font-size: 11px; color: #1d6b63;")
            card_l.addWidget(title)

            for entry in items:
                swatch = entry.get("color", "#1d6b63")
                row = QLabel(f"<span style='color: {swatch};'>■</span> {html.escape(entry.get('label', ''))}")
                row.setStyleSheet("font-size: 10px; color: #16323f; font-weight: bold;")
                card_l.addWidget(row)
            self.drawer_scroll_layout.addWidget(card)

    def _on_kpi_toggled(self, checked: bool) -> None:
        """Toggle the area and count chips under each panel."""
        for panel in self.panels:
            panel.update_kpi_bar(checked)

    def _on_hud_toggled(self, checked: bool) -> None:
        """Toggle North arrow and scale indicator on map viewport corners."""
        for panel in self.panels:
            panel.update_hud(checked)

    def _schedule_overlays(self) -> None:
        """Refresh chips, HUD, drawer and Diff once, 250 ms after the last change."""
        if self._cleaned or not self.isVisible():
            return
        self._overlay_timer.start()

    def _refresh_live_overlays(self) -> None:
        """Kept for callers of earlier versions: schedules the debounced refresh."""
        self._schedule_overlays()

    def _run_live_overlays(self) -> None:
        if self._cleaned or not self.isVisible():
            return
        if self.hud_chk.isChecked():
            for panel in self.panels:
                panel.update_hud(True)
        if self.kpi_chk.isChecked():
            for panel in self.panels:
                panel.update_kpi_bar(True)
        if self.drawer_frame.isVisible():
            self.update_legend_drawer()
        if self.diff_btn.isChecked():
            self.update_spatial_diff_highlights()

    # ───────────────────────── Multi-Inspect ─────────────────────────

    def _on_inspect_toggled(self, checked: bool) -> None:
        """Toggle multi-panel coordinated feature inspector mode."""
        msg = "Multi-Inspect Active: Click any point on a map panel to query all panels." if checked else "Multi-Inspect Off"
        self.status_label.setText(msg)

    @staticmethod
    def _feature_under_point(layer: QgsVectorLayer, point: QgsPointXY, tolerance: float):
        """The feature of ``layer`` under ``point`` (layer CRS) by an exact geometry test.

        Polygons must contain the point (a hole does not count); points and
        lines must lie within ``tolerance``. When several polygons contain it,
        the smallest wins; otherwise the nearest feature.
        """
        search = QgsRectangle(point.x() - tolerance, point.y() - tolerance,
                              point.x() + tolerance, point.y() + tolerance)
        probe = QgsGeometry.fromPointXY(point)
        best = None
        best_rank = None
        for feature in layer.getFeatures(QgsFeatureRequest().setFilterRect(search)):
            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue
            if geom.type() == POLYGON:
                if not geom.intersects(probe):
                    continue
                rank = (0, geom.area())
            else:
                distance = geom.distance(probe)
                if distance < 0 or distance > tolerance:
                    continue
                rank = (1, distance)
            if best_rank is None or rank < best_rank:
                best, best_rank = feature, rank
        return best

    @staticmethod
    def _describe_feature(feature) -> str:
        parts = []
        for field_name in feature.fields().names():
            value = feature[field_name]
            if value is None or (hasattr(value, "isNull") and value.isNull()):
                continue
            parts.append(f"{html.escape(str(field_name))}: {html.escape(str(value))}")
            if len(parts) == 3:
                break
        return ", ".join(parts) if parts else f"Feature ID {feature.id()}"

    def perform_multi_inspect(self, map_point: QgsPointXY, source_panel: MapPanelWidget | None = None,
                              source_canvas: QgsMapCanvas | None = None) -> str:
        """List the feature under ``map_point`` and raster values in every panel.

        Layer names, field names and values are escaped: the message is rich
        text, and a value such as ``</script>`` or ``<b>`` must show as typed.
        """
        if source_canvas is None:
            ref_panel = source_panel or self.active_panel or (self.panels[0] if self.panels else None)
            source_canvas = ref_panel.canvas if ref_panel else self.iface.mapCanvas()
        source_crs = source_canvas.mapSettings().destinationCrs()

        lines = []
        for panel in self.panels:
            p_name = html.escape(panel.title_label.text())
            layers = [layer for layer in panel.canvas.layers() if layer is not None and layer.isValid()]
            if not layers:
                lines.append(f"<b>{p_name}:</b> No active layers")
                continue

            panel_crs = panel.canvas.mapSettings().destinationCrs()
            tol_panel = max(1e-7, source_canvas.mapUnitsPerPixel() or 1.0) * 6.0
            pt_panel = transform_point(map_point, source_crs, panel_crs) or QgsPointXY(map_point)
            rect_panel = QgsRectangle(
                pt_panel.x() - tol_panel, pt_panel.y() - tol_panel,
                pt_panel.x() + tol_panel, pt_panel.y() + tol_panel,
            )

            p_info = []
            for lyr in layers:
                lyr_crs = lyr.crs()
                query_pt = pt_panel
                tolerance = tol_panel
                if panel_crs.isValid() and lyr_crs.isValid() and panel_crs != lyr_crs:
                    try:
                        ct_l = QgsCoordinateTransform(panel_crs, lyr_crs, QgsProject.instance())
                        query_rect = ct_l.transformBoundingBox(rect_panel)
                        query_pt = QgsPointXY(ct_l.transform(pt_panel))
                        tolerance = max(query_rect.width(), query_rect.height()) / 2.0
                    except (QgsCsException, RuntimeError):
                        continue

                name = html.escape(lyr.name())
                if isinstance(lyr, QgsVectorLayer):
                    feature = self._feature_under_point(lyr, query_pt, tolerance)
                    if feature is not None:
                        p_info.append(f"{name} ({self._describe_feature(feature)})")
                elif isinstance(lyr, QgsRasterLayer):
                    with contextlib.suppress(Exception):
                        val, ok = lyr.dataProvider().sample(query_pt, 1)
                        if ok:
                            p_info.append(f"{name} (Val: {val:.2f})")

            val_str = " | ".join(p_info) if p_info else "No features at point"
            lines.append(f"<b>{p_name}:</b> {val_str}")

        msg = "<br>".join(lines)
        text = f"<b>Coordinate:</b> {map_point.x():.4f}, {map_point.y():.4f}<br><br>{msg}"
        QMessageBox.information(self, "Multi-Panel Feature Inspector 🎯", text)
        return text

    # ───────────────────────── Show Diff ─────────────────────────

    def _diff_inputs(self) -> tuple:
        """``(layer_1, layer_2, "")`` or ``(None, None, reason)`` in plain words."""
        if self.swipe_panel is not None:
            return None, None, "Show Diff works with the panel grid. Choose a 2-panel layout to compare two layers."
        if len(self.panels) < 2:
            return None, None, "Show Diff needs two panels."
        chosen = []
        for panel in self.panels[:2]:
            name = f"Panel {panel.index + 1}"
            layer = panel.selected_layer() if panel.mode == "layer" else None
            if layer is None:
                return None, None, (
                    f"Show Diff compares the layers chosen in Panels 1 and 2. Set {name} "
                    "to 'Compare One Layer' and pick a polygon layer. The shared "
                    "background is never compared."
                )
            kind = geometry_kind(layer)
            if kind == "raster":
                return None, None, (
                    f"Show Diff compares polygon layers, but {name} shows a raster "
                    f"({layer.name()}). Pick a polygon layer in {name}; the shared "
                    "background is never compared."
                )
            if kind != "polygon":
                return None, None, (
                    f"Show Diff compares polygon layers, but {name} shows {kind}s "
                    f"({layer.name()})."
                )
            chosen.append(layer)
        return chosen[0], chosen[1], ""

    def _on_diff_toggled(self, checked: bool) -> None:
        """Toggle visual spatial difference engine highlights (green added, red removed)."""
        if not checked:
            self._clear_diff_bands()
            self.status_label.setText("Diff Highlights Off")
            self.overlap_label.setText("Overlap: —")
            self.overlap_label.setToolTip("")
            return

        _layer_1, _layer_2, reason = self._diff_inputs()
        if reason:
            self.diff_btn.blockSignals(True)
            self.diff_btn.setChecked(False)
            self.diff_btn.blockSignals(False)
            self._clear_diff_bands()
            self.overlap_label.setText("Overlap: —")
            self.overlap_label.setToolTip(reason)
            self.status_label.setText(reason)
            QMessageBox.information(self, "Show Diff", reason)
            return
        self.status_label.setText("Diff Highlights Active: Green (added), Red (removed)")
        self.update_spatial_diff_highlights()

    def _diff_band(self, key: str, canvas: QgsMapCanvas, color: QColor) -> QgsRubberBand:
        """One reusable band per role; a new one only when the canvas changed."""
        pooled = self._diff_band_pool.get(key)
        if pooled is not None and pooled[1] is canvas:
            return pooled[0]
        self._drop_diff_band(key)
        band = QgsRubberBand(canvas, POLYGON)
        fill = QColor(color)
        fill.setAlpha(80)
        stroke = QColor(color)
        stroke.setAlpha(240)
        band.setColor(fill)
        band.setStrokeColor(stroke)
        band.setWidth(2)
        self._diff_band_pool[key] = (band, canvas)
        return band

    def _drop_diff_band(self, key: str) -> None:
        pooled = self._diff_band_pool.pop(key, None)
        self.diff_rubber_bands.pop(key, None)
        if pooled is None:
            return
        band, canvas = pooled
        with contextlib.suppress(RuntimeError):
            band.reset(POLYGON)
            band.hide()
            canvas.scene().removeItem(band)

    def _clear_diff_bands(self) -> None:
        for key in list(self._diff_band_pool):
            self._drop_diff_band(key)
        self.diff_rubber_bands.clear()

    def _hide_diff_band(self, key: str) -> None:
        pooled = self._diff_band_pool.get(key)
        self.diff_rubber_bands.pop(key, None)
        if pooled is not None:
            with contextlib.suppress(RuntimeError):
                pooled[0].reset(POLYGON)
                pooled[0].hide()

    def update_spatial_diff_highlights(self) -> None:
        """Compute the geometric difference between the layers of Panel 1 and Panel 2.

        Only the layer chosen in each panel is compared: never the shared
        background, never a raster. Both layers are unioned in their own CRS
        and reprojected into Panel 1's canvas CRS before any geometry
        operation; two bands are reused instead of adding new ones per pan.
        """
        if not self.diff_btn.isChecked():
            return
        layer_1, layer_2, reason = self._diff_inputs()
        if reason:
            self._hide_diff_band("removed")
            self._hide_diff_band("added")
            self.overlap_label.setText("Overlap: —")
            self.overlap_label.setToolTip(reason)
            self.status_label.setText(reason)
            return

        canvas_1 = self.panels[0].canvas
        canvas_2 = self.panels[1].canvas
        canvas_crs = canvas_1.mapSettings().destinationCrs()
        extent_geom = QgsGeometry.fromRect(canvas_1.extent())
        result = compare_layers(layer_1, layer_2, extent_geom, canvas_crs)

        if result.removed is not None:
            band = self._diff_band("removed", canvas_1, QColor(231, 76, 60))
            band.setToGeometry(result.removed, canvas_crs)
            band.show()
            self.diff_rubber_bands["removed"] = band
        else:
            self._hide_diff_band("removed")

        added_on_2 = None
        if result.added is not None:
            # The green band lives on Panel 2's canvas, which may sit in a
            # different CRS than the working CRS the difference was computed in.
            panel2_crs = canvas_2.mapSettings().destinationCrs()
            added_on_2 = transform_geometry(result.added, canvas_crs, panel2_crs)
        if added_on_2 is not None:
            band = self._diff_band("added", canvas_2, QColor(46, 204, 113))
            band.setToGeometry(added_on_2, canvas_2.mapSettings().destinationCrs())
            band.show()
            self.diff_rubber_bands["added"] = band
        else:
            self._hide_diff_band("added")

        if result.area_a_m2 <= 0 and result.area_b_m2 <= 0:
            self.overlap_label.setText("Overlap: —")
            self.overlap_label.setToolTip("Neither layer has polygons in view.")
            return
        if result.area_a_m2 > 0:
            self.overlap_label.setText(f"Overlap: {result.overlap_percent:.1f}%")
        else:
            self.overlap_label.setText("Overlap: —")
        self.overlap_label.setToolTip(
            f"Panel 1: {format_area(result.area_a_m2)} · "
            f"Panel 2: {format_area(result.area_b_m2)} · "
            f"removed {format_area(result.removed_area_m2)} · "
            f"added {format_area(result.added_area_m2)} (ellipsoidal area)"
        )

    # ───────────────────────── Timeline player ─────────────────────────

    def toggle_timeline_player(self) -> None:
        """Start or stop highlighting the panels one after another."""
        if self.timeline_timer.isActive():
            self._stop_timeline()
            self.status_label.setText("Timeline Player Stopped")
            return
        if self.swipe_panel is not None or len(self.panels) < 2:
            self.status_label.setText("Play Timeline steps through the panels of a grid.")
            return
        self.timeline_index = self.panels.index(self.active_panel) if self.active_panel in self.panels else 0
        self.timeline_timer.start(1500)
        self.player_btn.setText("⏸ Pause Timeline")
        self.status_label.setText("Timeline Player Active")

    def _stop_timeline(self) -> None:
        if self.timeline_timer.isActive():
            self.timeline_timer.stop()
        self.player_btn.setText("▶ Play Timeline")

    def _on_timeline_timer_tick(self) -> None:
        """Cycle active panel on timer tick."""
        if not self.panels:
            return
        self.timeline_index = (self.timeline_index + 1) % len(self.panels)
        self.set_active_panel(self.panels[self.timeline_index])

    def show_quick_guide(self) -> None:
        """Open the guide; it is deleted once closed."""
        dialog = QuickGuideDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()


QUICK_GUIDE_HTML = """
<div style="font-family: 'Segoe UI', Roboto, sans-serif; line-height: 1.5;">
    <h2 style="color: #16323f; font-weight: 700; border-bottom: 2px solid #1d6b63;
               padding-bottom: 6px; margin-top: 0;">
        02Multimap Workspace Guide
    </h2>
    <p><b>02Multimap</b> shows the same place in several synchronized maps, so you can
    compare layers, years or scenarios side by side, or in one map with a swipe divider.</p>

    <div style="background-color: #eef1f4; border: 1px solid #cbd3da; border-radius: 6px; padding: 10px; margin-bottom: 12px;">
        <b>Online manual:</b>
        <a href="https://geophilo.com/zero2multimap/" style="color: #1d6b63; font-weight: bold; text-decoration: underline;">
            https://geophilo.com/zero2multimap/
        </a>
    </div>

    <h3 style="color: #1d6b63; font-size: 14px; margin-top: 14px; margin-bottom: 4px;">
        1. Panels and Swipe
    </h3>
    <ul>
        <li><b>Panels:</b> 2, 3, 4, 6 or 8 maps in a row, a column or a grid. Each panel follows
        the main map, shows one layer (<i>Compare One Layer</i>) or a map theme.</li>
        <li><b>Swipe:</b> one map. Choose the bottom layers and the top layers above it; the top
        layers show on one side of the divider. Drag the divider, and turn it upright or flat
        in the Swipe header.</li>
        <li><b>Choose for Me:</b> gives every panel a different layer, preferring layers in the
        current map extent.</li>
        <li><b>Time-Series Auto-Fill:</b> finds the year in layer names (for example
        <i>landuse_2015</i>, <i>landuse_2023</i>) and fills the panels from oldest to newest.</li>
        <li><b>Templates and presets:</b> Before &amp; After picks two layers of the same kind,
        4-Scenario Matrix fills four panels. Saved presets keep the layout, layers, map themes
        and scale. Saving the QGIS project also saves the panel setup.</li>
    </ul>

    <h3 style="color: #1d6b63; font-size: 14px; margin-top: 14px; margin-bottom: 4px;">
        2. Navigation
    </h3>
    <ul>
        <li><b>Sync Navigation:</b> panning, zooming or rotating one map moves the others to the
        same centre, scale and rotation, also when they use different coordinate systems.</li>
        <li><b>Sync Canvas:</b> the QGIS main map moves with the panels, and the panels with it.</li>
        <li><b>Laser Crosshair:</b> shows the mouse position in the other panels, and on the QGIS
        main map when Sync Canvas is on. It is not shown in the Swipe layout, which is one map.</li>
        <li><b>Match Scale, Match Extent, Fit All and Scale Preset:</b> align the maps once.</li>
    </ul>

    <h3 style="color: #1d6b63; font-size: 14px; margin-top: 14px; margin-bottom: 4px;">
        3. Reading the maps
    </h3>
    <ul>
        <li><b>🎯 Multi-Inspect:</b> click a point to list the feature under it and raster values
        in every panel.</li>
        <li><b>⚡ Show Diff:</b> outlines what changed between the polygon layers chosen in
        Panels 1 and 2 (green added, red removed) and how much of Panel 1's area Panel 2 also
        covers (<b>Overlap</b>); hover it for the areas. Rasters and the shared background are
        not compared.</li>
        <li><b>Viewport KPIs:</b> ellipsoidal polygon area (ha/km²) and point and line counts in
        view under each panel; the shared background is not counted.</li>
        <li><b>📋 Legend Drawer and Canvas HUD:</b> all panel legends in one column; a north arrow,
        scale bar and scale on each map.</li>
        <li><b>▶ Play Timeline:</b> highlights the panels one after another, every 1.5 seconds.</li>
    </ul>

    <h3 style="color: #1d6b63; font-size: 14px; margin-top: 14px; margin-bottom: 4px;">
        4. Export
    </h3>
    <p><b>Export / Print…</b> builds a print layout (PDF or image) of the panels, opens it in the
    Layout Designer, or writes a self-contained HTML page with the panel maps, legends and a
    scale bar that works offline. In the Swipe layout the export shows the bottom and the top
    layers side by side, at the swipe map's extent and scale.</p>
</div>
"""


class QuickGuideDialog(QDialog):
    """A short guide to 02Multimap."""

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
                background-color: #1d6b63;
                color: #ffffff;
                border: 1px solid #17635b;
                border-radius: 4px;
                padding: 6px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #17635b;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setHtml(QUICK_GUIDE_HTML)

        layout.addWidget(self.browser, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
