# -*- coding: utf-8 -*-
"""02Multimap — print layout and HTML dashboard exporter.

Provides a dedicated export studio dialog allowing users to customize title, page size,
orientation, scalebar style, north arrow graphic, master & per-panel legends, and export
to PNG, JPEG, SVG, PDF, or self-contained interactive HTML dashboards.
"""
from __future__ import annotations

import math
import os
from contextlib import suppress

from qgis.PyQt.QtCore import (
    Qt,
    QSettings,
    QByteArray,
    QBuffer,
    QIODevice,
    QSize,
    QSizeF,
    QSaveFile,
    QUrl,
)
from qgis.PyQt.QtGui import QColor, QFont, QDesktopServices

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QComboBox,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QFormLayout,
    QLabel,
    QTabWidget,
    QWidget,
)

from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutExporter,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLegendStyle,
    QgsPointXY,
    QgsTextFormat,
    QgsUnitTypes,
    QgsMapSettings,
    QgsMapRendererParallelJob,
    QgsMapRendererSequentialJob,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsWkbTypes,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
)

from .html_export import build_dashboard_html
from .spatial_metrics import make_area_calculator


def resolve_north_arrow_svg(rel_or_abs_path: str | None) -> str:
    """Resolve a QGIS or bundled North Arrow SVG path to an existing file on disk.

    Passing a bare relative string such as ``"arrows/NorthArrow_01.svg"`` straight to
    ``QgsLayoutItemPicture.setPicturePath`` leaves ``isMissingImage() == True`` in
    print exports because ``QgsLayoutItemPicture`` does not search ``svgPaths()``
    automatically on all runtimes.
    """
    if rel_or_abs_path is None:
        rel_or_abs_path = ""
    raw = str(rel_or_abs_path).strip()
    if raw.lower() == "none":
        return ""

    bundled_svg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "north_arrow.svg")
    if raw in ("", "builtin", "icons/north_arrow.svg") and os.path.isfile(bundled_svg):
        return bundled_svg

    if os.path.isabs(raw) and os.path.isfile(raw):
        return raw

    search_roots: list[str] = []
    with suppress(Exception):
        search_roots.extend(QgsApplication.svgPaths() or [])
    with suppress(Exception):
        pkg_svg = os.path.join(QgsApplication.pkgDataPath(), "svg")
        if pkg_svg not in search_roots:
            search_roots.append(pkg_svg)

    for root in search_roots:
        if not root:
            continue
        candidate = os.path.normpath(os.path.join(root, raw))
        if os.path.isfile(candidate):
            return candidate

    if os.path.isfile(bundled_svg):
        return bundled_svg
    return raw


def extract_panel_legend_items(panel, max_items: int = 10, fallback_layers=None) -> list[dict[str, str]]:
    """Extract category/range/rule/symbol labels and colors from a panel's visible layers."""
    layers = list(panel.canvas.layers()) if hasattr(panel, "canvas") and panel.canvas else []
    if not layers and hasattr(panel, "selected_layer"):
        sel = panel.selected_layer()
        if sel is not None:
            layers = [sel]
    if not layers and fallback_layers:
        layers = list(fallback_layers)

    items: list[dict[str, str]] = []
    for lyr in layers:
        if not lyr or not lyr.isValid():
            continue
        if isinstance(lyr, QgsVectorLayer) and lyr.renderer():
            renderer = lyr.renderer()
            added_for_layer = False
            if hasattr(renderer, "categories"):
                for cat in renderer.categories()[:6]:
                    lbl = cat.label() or str(cat.value())
                    sym = cat.symbol()
                    col = sym.color().name() if sym and sym.color().isValid() else "#2a8f85"
                    items.append({"label": f"{lyr.name()}: {lbl}", "color": col, "layer": lyr.name()})
                    added_for_layer = True
            elif hasattr(renderer, "ranges"):
                for rng in renderer.ranges()[:6]:
                    lbl = rng.label() or f"{rng.lowerValue():.2g} – {rng.upperValue():.2g}"
                    sym = rng.symbol()
                    col = sym.color().name() if sym and sym.color().isValid() else "#2a8f85"
                    items.append({"label": f"{lyr.name()}: {lbl}", "color": col, "layer": lyr.name()})
                    added_for_layer = True
            elif hasattr(renderer, "rootRule") and renderer.rootRule():
                for rule in renderer.rootRule().children()[:6]:
                    lbl = rule.label() or rule.filterExpression() or lyr.name()
                    sym = rule.symbol()
                    col = sym.color().name() if sym and sym.color().isValid() else "#2a8f85"
                    items.append({"label": f"{lyr.name()}: {lbl}", "color": col, "layer": lyr.name()})
                    added_for_layer = True
            elif hasattr(renderer, "symbol") and renderer.symbol():
                sym = renderer.symbol()
                col = sym.color().name() if sym and sym.color().isValid() else "#2a8f85"
                items.append({"label": lyr.name(), "color": col, "layer": lyr.name()})
                added_for_layer = True

            if not added_for_layer:
                items.append({"label": lyr.name(), "color": "#2a8f85", "layer": lyr.name()})
        elif isinstance(lyr, QgsRasterLayer):
            items.append({"label": f"Raster: {lyr.name()}", "color": "#3498db", "layer": lyr.name()})
        else:
            items.append({"label": lyr.name(), "color": "#2a8f85", "layer": lyr.name()})

    return items[:max_items]


def _nice_scale_segment(value: float) -> float:
    """Round a positive distance to a clean 1 / 2 / 2.5 / 5 × 10^n cartographic step."""
    if not math.isfinite(value) or value <= 0:
        return 100.0
    exponent = math.floor(math.log10(value))
    base = 10.0 ** exponent
    fraction = value / base
    if fraction >= 5.0:
        nice = 5.0
    elif fraction >= 2.5:
        nice = 2.5
    elif fraction >= 2.0:
        nice = 2.0
    else:
        nice = 1.0
    return nice * base


def _measure_extent_ground_width_m(extent, crs: QgsCoordinateReferenceSystem) -> float:
    """Return true ellipsoidal ground width in metres across the vertical midpoint of ``extent``."""
    if extent is None or extent.isEmpty():
        return 1000.0
    with suppress(Exception):
        calc = make_area_calculator(crs)
        mid_y = 0.5 * (extent.yMinimum() + extent.yMaximum())
        p1 = QgsPointXY(extent.xMinimum(), mid_y)
        p2 = QgsPointXY(extent.xMaximum(), mid_y)
        dist = float(calc.measureLine(p1, p2))
        if math.isfinite(dist) and dist > 0.01:
            return dist
    raw_w = abs(extent.xMaximum() - extent.xMinimum())
    if crs.isValid() and crs.isGeographic():
        return max(10.0, raw_w * 111_320.0)
    return raw_w if raw_w > 0.01 else 1000.0


def configure_scalebar_for_map(
    scalebar: QgsLayoutItemScaleBar,
    map_item: QgsLayoutItemMap,
    style_name: str = "Single Box",
    target_segments: int = 2,
    compact: bool = False,
) -> None:
    """Configure a ``QgsLayoutItemScaleBar`` so ``unitsPerSegment`` is always positive and readable."""
    scalebar.setLinkedMap(map_item)
    scalebar.applyDefaultSettings()

    ground_w_m = _measure_extent_ground_width_m(map_item.extent(), map_item.crs())
    use_km = ground_w_m >= 2500.0
    units = QgsUnitTypes.DistanceKilometers if use_km else QgsUnitTypes.DistanceMeters
    unit_label = "km" if use_km else "m"

    scalebar.setUnits(units)
    scalebar.setUnitLabel(unit_label)
    with suppress(Exception):
        scalebar.applyDefaultSize(units)

    scalebar.setNumberOfSegments(max(1, target_segments))
    scalebar.setNumberOfSegmentsLeft(0)

    # Compute a clean segment length (~18% of ground width per segment) whenever
    # applyDefaultSize leaves unitsPerSegment at 0 or produces an awkward step.
    desired_per_seg_m = max(1.0, ground_w_m * (0.16 if compact else 0.18))
    if use_km:
        seg_val = _nice_scale_segment(desired_per_seg_m / 1000.0)
    else:
        seg_val = max(1.0, _nice_scale_segment(desired_per_seg_m))

    current_ups = scalebar.unitsPerSegment()
    if not math.isfinite(current_ups) or current_ups <= 0:
        scalebar.setUnitsPerSegment(seg_val)
    else:
        # Still prefer our clean metric/km step when default size is implausibly tiny or huge
        total_span_m = current_ups * (1000.0 if use_km else 1.0) * target_segments
        if total_span_m < ground_w_m * 0.05 or total_span_m > ground_w_m * 0.85:
            scalebar.setUnitsPerSegment(seg_val)

    # Apply requested style AFTER applyDefaultSettings/applyDefaultSize (which reset style to Single Box)
    if style_name:
        with suppress(Exception):
            scalebar.setStyle(style_name)

    font_size = 6.5 if compact else 8.0
    with suppress(Exception):
        tf = QgsTextFormat()
        tf.setFont(QFont("Segoe UI", int(round(font_size))))
        tf.setSize(font_size)
        tf.setColor(QColor("#16323f"))
        scalebar.setTextFormat(tf)
    if compact:
        with suppress(Exception):
            scalebar.setHeight(1.6)
            scalebar.setBoxContentSpace(0.8)
    else:
        with suppress(Exception):
            scalebar.setHeight(2.4)
            scalebar.setBoxContentSpace(1.2)
    scalebar.update()


class PrintLayoutDialog(QDialog):
    """Provides configuration and execution for multi-panel print layout and HTML dashboard exports."""

    def __init__(self, parent_dialog, iface):
        super().__init__(parent_dialog if isinstance(parent_dialog, QWidget) else None)
        self.parent_dialog = parent_dialog
        self.iface = iface
        self.setWindowTitle("Export Comparative Maps & Dashboards")
        self.setMinimumSize(560, 490)

        # Apply same style QSS
        if hasattr(parent_dialog, "styleSheet"):
            self.setStyleSheet(parent_dialog.styleSheet())
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.tab_widget = QTabWidget()

        # ──────────────── Tab 1: Static Print Layouts (PDF, PNG, JPG, SVG) ────────────────
        self.print_tab = QWidget()
        print_layout_form = QFormLayout(self.print_tab)
        print_layout_form.setContentsMargins(12, 12, 12, 12)
        print_layout_form.setSpacing(8)

        self.title_input = QLineEdit("Comparative Map Grid")
        print_layout_form.addRow("Map Title:", self.title_input)

        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["A4", "A3", "Letter"])
        print_layout_form.addRow("Page Size:", self.page_size_combo)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["Landscape", "Portrait"])
        print_layout_form.addRow("Orientation:", self.orientation_combo)

        self.north_arrow_combo = QComboBox()
        self.north_arrow_combo.addItem("Built-in Studio Compass (Recommended)", "builtin")
        self.north_arrow_combo.addItem("Modern Minimalist", "arrows/NorthArrow_01.svg")
        self.north_arrow_combo.addItem("Classic Compass", "arrows/NorthArrow_02.svg")
        self.north_arrow_combo.addItem("Military Pointer", "arrows/NorthArrow_04.svg")
        self.north_arrow_combo.addItem("Sleek Navigation Cross", "arrows/NorthArrow_07.svg")
        self.north_arrow_combo.addItem("Tech Pointer", "arrows/NorthArrow_09.svg")
        print_layout_form.addRow("North Arrow Style:", self.north_arrow_combo)

        self.scalebar_combo = QComboBox()
        self.scalebar_combo.addItems([
            "Single Box",
            "Double Box",
            "Line Ticks Up",
            "Line Ticks Down",
            "Stepped Line",
        ])
        print_layout_form.addRow("Scalebar Style:", self.scalebar_combo)

        carto_opts_widget = QWidget()
        carto_opts_layout = QVBoxLayout(carto_opts_widget)
        carto_opts_layout.setContentsMargins(0, 2, 0, 2)
        carto_opts_layout.setSpacing(4)

        self.include_legend_chk = QCheckBox("Include Master Cartographic Footer (North Arrow, Multi-Layer Legend & Scale Bar)")
        self.include_legend_chk.setChecked(True)
        carto_opts_layout.addWidget(self.include_legend_chk)

        self.per_panel_overlays_chk = QCheckBox("Stamp Mini North Arrow, Legend & Scale Bar on Every Panel (Ideal for 4–8 Panels)")
        self.per_panel_overlays_chk.setChecked(True)
        carto_opts_layout.addWidget(self.per_panel_overlays_chk)

        self.include_panel_headers_chk = QCheckBox("Include Panel Title Headers & Crisp Viewport Borders")
        self.include_panel_headers_chk.setChecked(True)
        carto_opts_layout.addWidget(self.include_panel_headers_chk)

        print_layout_form.addRow("Cartographic Elements:", carto_opts_widget)

        self.format_combo = QComboBox()
        self.format_combo.addItem("PDF Document (*.pdf)", ".pdf")
        self.format_combo.addItem("PNG Image (*.png)", ".png")
        self.format_combo.addItem("JPEG Image (*.jpg)", ".jpg")
        self.format_combo.addItem("SVG Graphic (*.svg)", ".svg")
        print_layout_form.addRow("Export Format:", self.format_combo)

        path_layout = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Select output image/PDF file path...")
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self.browse_export_path)
        path_layout.addWidget(self.path_input, 1)
        path_layout.addWidget(self.browse_btn)
        print_layout_form.addRow("Save To:", path_layout)

        self.tab_widget.addTab(self.print_tab, "🖨️ Print Layout Export (PDF / Images)")

        # ──────────────── Tab 2: Interactive HTML Dashboard Studio ────────────────
        self.html_tab = QWidget()
        html_form = QFormLayout(self.html_tab)
        html_form.setContentsMargins(12, 12, 12, 12)
        html_form.setSpacing(10)

        self.html_title_input = QLineEdit("Comparative Map Dashboard")
        html_form.addRow("Dashboard Title:", self.html_title_input)

        self.html_theme_combo = QComboBox()
        self.html_theme_combo.addItem("Slate Light (Default)", "slate")
        self.html_theme_combo.addItem("Dark Midnight (Night Mode)", "dark")
        self.html_theme_combo.addItem("Emerald Clean", "emerald")
        html_form.addRow("Color Theme:", self.html_theme_combo)

        html_info = QLabel(
            "<b>🌐 Interactive Web Dashboard Features:</b><br>"
            "• Synchronized multi-viewport panning & wheel zoom<br>"
            "• Embedded North Arrow, dynamic ground Scale Bar & Legend on every panel (🧭)<br>"
            "• Synchronized laser crosshairs across all panels<br>"
            "• Interactive distance & area measurement tools (📏)<br>"
            "• Full-screen presentation slide mode (📺) with arrow key navigation<br>"
            "• Multi-theme switching & standalone offline compatibility"
        )
        html_info.setStyleSheet(
            "background-color: #eef1f4; border: 1px solid #cbd3da; border-radius: 6px; "
            "padding: 8px; font-size: 11px; color: #16323f;"
        )
        html_info.setWordWrap(True)
        html_form.addRow("", html_info)

        html_path_layout = QHBoxLayout()
        self.html_path_input = QLineEdit()
        self.html_path_input.setPlaceholderText("Select output .html dashboard file path...")
        self.html_browse_btn = QPushButton("Browse...")
        self.html_browse_btn.clicked.connect(self.browse_html_export_path)
        html_path_layout.addWidget(self.html_path_input, 1)
        html_path_layout.addWidget(self.html_browse_btn)
        html_form.addRow("Save To:", html_path_layout)

        self.tab_widget.addTab(self.html_tab, "🌐 Interactive HTML Dashboard")

        layout.addWidget(self.tab_widget, 1)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        self.export_btn = QPushButton("Export Only")
        self.export_btn.clicked.connect(lambda: self.execute_export(open_after=False))

        self.export_open_btn = QPushButton("Export & Open")
        self.export_open_btn.clicked.connect(lambda: self.execute_export(open_after=True))
        self.export_open_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a8f85;
                color: #ffffff;
                border: 1px solid #237a72;
                font-weight: bold;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #319c91;
            }
        """)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addWidget(self.export_open_btn)
        layout.addLayout(btn_layout)

        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(0)

    def _on_tab_changed(self, index: int) -> None:
        """Update action button text based on active tab."""
        if index == 0:
            self.export_btn.setText("Export Layout Only")
            self.export_open_btn.setText("Export & Open File 🖨️")
        else:
            self.export_btn.setText("Export Dashboard Only")
            self.export_open_btn.setText("Export & Open in Browser 🌐")

    def browse_export_path(self) -> None:
        """Opens file dialog for print layout formats."""
        format_filter = self.format_combo.currentText()
        last_dir = QSettings().value("zero2multimap/last_export_dir", "")
        if not (isinstance(last_dir, str) and os.path.isdir(last_dir)):
            last_dir = os.path.expanduser("~")

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Select Print Layout Export Path", last_dir, format_filter
        )
        if filepath:
            filepath = self._normalized_export_path(filepath)
            self.path_input.setText(filepath)
            QSettings().setValue("zero2multimap/last_export_dir", os.path.dirname(filepath))

    def browse_html_export_path(self) -> None:
        """Opens file dialog for HTML dashboard export."""
        last_dir = QSettings().value("zero2multimap/last_export_dir", "")
        if not (isinstance(last_dir, str) and os.path.isdir(last_dir)):
            last_dir = os.path.expanduser("~")

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Select HTML Dashboard Path", last_dir, "Interactive HTML Dashboard (*.html)"
        )
        if filepath:
            filepath = self._normalized_html_path(filepath)
            self.html_path_input.setText(filepath)
            QSettings().setValue("zero2multimap/last_export_dir", os.path.dirname(filepath))

    def _normalized_export_path(self, raw_path: str) -> str:
        """Make the selected format authoritative and return an absolute path."""
        path = os.path.expanduser(raw_path.strip())
        if not path:
            return ""
        desired_extension = self.format_combo.currentData()
        root, current_extension = os.path.splitext(path)
        known_extensions = {".png", ".jpg", ".jpeg", ".pdf", ".svg"}
        if current_extension.lower() in known_extensions:
            path = root + desired_extension
        elif current_extension.lower() != desired_extension:
            path += desired_extension
        return os.path.abspath(path)

    def _normalized_html_path(self, raw_path: str) -> str:
        """Return absolute path ending in .html."""
        path = os.path.expanduser(raw_path.strip())
        if not path:
            return ""
        root, ext = os.path.splitext(path)
        if ext.lower() != ".html":
            path = root + ".html"
        return os.path.abspath(path)

    def _panel_caption(self, panel, index: int) -> str:
        """Return a concise caption describing a panel's assigned layer or theme."""
        if getattr(panel, "mode", "") == "layer":
            sel = panel.selected_layer() if hasattr(panel, "selected_layer") else None
            name = sel.name() if sel else (panel.layer_combo.currentText() if hasattr(panel, "layer_combo") else "Layer")
            return f"Panel {index + 1} · {name or 'Layer'}"
        if getattr(panel, "mode", "") == "theme":
            theme_name = panel.theme_combo.currentText() if hasattr(panel, "theme_combo") else "Theme"
            return f"Panel {index + 1} · {theme_name or 'Theme'}"
        layers = list(panel.canvas.layers()) if hasattr(panel, "canvas") and panel.canvas else []
        if layers and layers[0] and layers[0].isValid():
            if len(layers) == 1:
                return f"Panel {index + 1} · {layers[0].name()}"
            return f"Panel {index + 1} · {layers[0].name()} (+{len(layers) - 1})"
        return f"Panel {index + 1} · Main Map"

    def build_qgis_print_layout(
        self,
        title: str | None = None,
        page_size_name: str | None = None,
        orientation: str | None = None,
        north_arrow_key: str | None = None,
        scalebar_style: str | None = None,
        include_footer: bool | None = None,
        per_panel_overlays: bool | None = None,
        include_headers: bool | None = None,
    ) -> QgsLayout:
        """Construct and return a complete ``QgsLayout`` with all panels and cartographic elements.

        Keeping layout assembly in a dedicated method lets automated QGIS tests
        inspect every generated ``QgsLayoutItemMap``, ``QgsLayoutItemPicture``,
        ``QgsLayoutItemLegend``, and ``QgsLayoutItemScaleBar`` across 2–8 panel grids.
        """
        title = title if title is not None else (self.title_input.text().strip() or "Comparative Map Grid")
        page_size_name = page_size_name or self.page_size_combo.currentText()
        orientation = orientation or self.orientation_combo.currentText()
        north_arrow_key = north_arrow_key if north_arrow_key is not None else self.north_arrow_combo.currentData()
        scalebar_style = scalebar_style or self.scalebar_combo.currentText()
        include_footer = self.include_legend_chk.isChecked() if include_footer is None else bool(include_footer)
        per_panel_overlays = (
            self.per_panel_overlays_chk.isChecked() if per_panel_overlays is None else bool(per_panel_overlays)
        )
        include_headers = (
            self.include_panel_headers_chk.isChecked() if include_headers is None else bool(include_headers)
        )

        if page_size_name == "A3":
            w, h = (420.0, 297.0) if orientation == "Landscape" else (297.0, 420.0)
        elif page_size_name == "Letter":
            w, h = (279.4, 215.9) if orientation == "Landscape" else (215.9, 279.4)
        else:  # A4
            w, h = (297.0, 210.0) if orientation == "Landscape" else (210.0, 297.0)

        project = QgsProject.instance()
        layout = QgsLayout(project)
        layout.initializeDefaults()

        page = layout.pageCollection().pages()[0]
        page.setPageSize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))

        margin_side = 8.0
        margin_top_base = 6.0
        title_h = 10.0

        # 1. Main Title Label
        title_item = QgsLayoutItemLabel(layout)
        title_item.setId("MasterTitle")
        title_item.setText(title)
        font_weight = getattr(QFont, "Weight", QFont)
        bold_flag = getattr(font_weight, "Bold")
        normal_flag = getattr(font_weight, "Normal")
        with suppress(Exception):
            tf = QgsTextFormat()
            tf.setFont(QFont("Segoe UI", 14, bold_flag))
            tf.setSize(14.0)
            tf.setColor(QColor("#16323f"))
            title_item.setTextFormat(tf)

        alignment_flags = getattr(Qt, "AlignmentFlag", Qt)
        align_center = getattr(alignment_flags, "AlignCenter")
        align_left = getattr(alignment_flags, "AlignLeft")
        align_vcenter = getattr(alignment_flags, "AlignVCenter")
        title_item.setHAlign(align_center)
        title_item.setVAlign(align_vcenter)
        title_item.attemptMove(QgsLayoutPoint(margin_side, margin_top_base, QgsUnitTypes.LayoutMillimeters))
        title_item.attemptResize(QgsLayoutSize(w - 2.0 * margin_side, title_h, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(title_item)

        # 2. Grid Geometry Calculation
        grid_top = margin_top_base + title_h + 2.5
        footer_h = 22.0 if include_footer else 4.0
        avail_w = w - (2.0 * margin_side)
        avail_h = h - grid_top - footer_h - 4.0

        cols = max(1, int(getattr(self.parent_dialog, "cols", 2)))
        rows = max(1, int(getattr(self.parent_dialog, "rows", 2)))
        panels = list(getattr(self.parent_dialog, "panels", []))

        item_w = avail_w / cols
        item_h = avail_h / rows
        is_dense_grid = len(panels) >= 6 or cols >= 3

        resolved_arrow_svg = resolve_north_arrow_svg(north_arrow_key)
        map_items: list[QgsLayoutItemMap] = []
        unique_layers = []
        seen_layer_ids: set[str] = set()
        panel_summary_parts: list[str] = []

        fallback_main_layers = []
        if hasattr(self, "iface") and self.iface and hasattr(self.iface, "mapCanvas") and self.iface.mapCanvas():
            with suppress(Exception):
                fallback_main_layers = list(self.iface.mapCanvas().layers())

        for i, panel in enumerate(panels):
            r = i // cols
            c = i % cols

            cell_x = margin_side + c * item_w
            cell_y = grid_top + r * item_h
            pad = 1.0
            inner_x = cell_x + pad
            inner_w = max(12.0, item_w - 2.0 * pad)

            header_h = (4.8 if is_dense_grid else 5.6) if include_headers else 0.0
            caption_text = self._panel_caption(panel, i)

            if include_headers:
                hdr_label = QgsLayoutItemLabel(layout)
                hdr_label.setId(f"PanelHeader_{i + 1}")
                scale_val = int(panel.canvas.scale()) if hasattr(panel, "canvas") and panel.canvas else 0
                scale_suffix = f"  [1:{scale_val:,}]" if scale_val > 0 else ""
                hdr_label.setText(f" {caption_text}{scale_suffix}")
                hdr_font_sz = 7.0 if is_dense_grid else 8.0
                with suppress(Exception):
                    htf = QgsTextFormat()
                    htf.setFont(QFont("Segoe UI", int(round(hdr_font_sz)), bold_flag))
                    htf.setSize(hdr_font_sz)
                    htf.setColor(QColor("#16323f"))
                    hdr_label.setTextFormat(htf)
                hdr_label.setHAlign(align_left)
                hdr_label.setVAlign(align_vcenter)
                hdr_label.setBackgroundEnabled(True)
                hdr_label.setBackgroundColor(QColor("#eef1f4"))
                hdr_label.setFrameEnabled(True)
                hdr_label.attemptMove(QgsLayoutPoint(inner_x, cell_y + pad, QgsUnitTypes.LayoutMillimeters))
                hdr_label.attemptResize(QgsLayoutSize(inner_w, header_h, QgsUnitTypes.LayoutMillimeters))
                layout.addLayoutItem(hdr_label)

            map_y = cell_y + pad + header_h
            map_h = max(12.0, item_h - 2.0 * pad - header_h)

            map_item = QgsLayoutItemMap(layout)
            map_item.setId(f"PanelMap_{i + 1}")
            map_item.attemptMove(QgsLayoutPoint(inner_x, map_y, QgsUnitTypes.LayoutMillimeters))
            map_item.attemptResize(QgsLayoutSize(inner_w, map_h, QgsUnitTypes.LayoutMillimeters))
            map_item.setFrameEnabled(True)
            map_item.setBackgroundEnabled(True)
            if hasattr(panel, "canvas") and panel.canvas:
                map_item.setBackgroundColor(panel.canvas.canvasColor())

            panel_crs = panel.canvas.mapSettings().destinationCrs()
            map_item.setCrs(panel_crs)

            # Fit full panel extent into the layout map item after resizing so narrow cells (e.g. 2x4) do not clip
            panel_extent = panel.canvas.extent()
            if panel_extent and not panel_extent.isEmpty():
                map_item.zoomToExtent(panel_extent)
            else:
                map_item.setExtent(panel_extent)

            panel_layers = list(panel.canvas.layers())
            if not panel_layers and getattr(panel, "mode", "") == "layer" and hasattr(panel, "selected_layer"):
                sel_lyr = panel.selected_layer()
                if sel_lyr is not None:
                    panel_layers = [sel_lyr]
            if not panel_layers and getattr(panel, "mode", "") == "sync" and fallback_main_layers:
                panel_layers = list(fallback_main_layers)

            for lyr in panel_layers:
                if lyr and lyr.isValid() and lyr.id() not in seen_layer_ids:
                    seen_layer_ids.add(lyr.id())
                    unique_layers.append(lyr)

            if getattr(panel, "mode", "") == "theme" and panel.theme_combo.currentText():
                map_item.setFollowVisibilityPresetName(panel.theme_combo.currentText())
                map_item.setFollowVisibilityPreset(True)
            else:
                map_item.setLayers(panel_layers)
                map_item.setKeepLayerSet(True)
                style_overrides = panel.canvas.mapSettings().layerStyleOverrides()
                if style_overrides:
                    map_item.setLayerStyleOverrides(style_overrides)

            layout.addLayoutItem(map_item)
            map_items.append(map_item)

            # Extract legend items for this panel
            panel_leg_items = extract_panel_legend_items(panel, max_items=6, fallback_layers=panel_layers)
            if panel_leg_items:
                swatches_txt = ", ".join(item["label"] for item in panel_leg_items[:3])
                panel_summary_parts.append(f"P{i + 1}: {swatches_txt}")
            else:
                panel_summary_parts.append(f"P{i + 1}: {caption_text.split('·')[-1].strip()}")

            # 3. Per-Panel Cartographic Overlays (Mini North Arrow, Legend Card & Scale Bar on every panel)
            if per_panel_overlays:
                # 3a. Per-Panel Mini North Arrow (top-right inside panel map)
                if resolved_arrow_svg:
                    p_arrow_sz = 6.5 if is_dense_grid else 8.0
                    p_arrow = QgsLayoutItemPicture(layout)
                    p_arrow.setId(f"PanelNorthArrow_{i + 1}")
                    p_arrow.setPicturePath(resolved_arrow_svg)
                    p_arrow.setLinkedMap(map_item)
                    p_arrow.setBackgroundEnabled(True)
                    p_arrow.setBackgroundColor(QColor(255, 255, 255, 220))
                    p_arrow.setFrameEnabled(True)
                    p_arrow.attemptMove(
                        QgsLayoutPoint(
                            inner_x + inner_w - p_arrow_sz - 1.2,
                            map_y + 1.2,
                            QgsUnitTypes.LayoutMillimeters,
                        )
                    )
                    p_arrow.attemptResize(QgsLayoutSize(p_arrow_sz, p_arrow_sz, QgsUnitTypes.LayoutMillimeters))
                    layout.addLayoutItem(p_arrow)

                # 3b. Per-Panel Mini Legend Card (bottom-left inside panel map)
                leg_lines = []
                if panel_leg_items:
                    for entry in panel_leg_items[: (3 if is_dense_grid else 4)]:
                        lbl_clean = entry["label"]
                        if len(lbl_clean) > 28:
                            lbl_clean = lbl_clean[:26] + "…"
                        leg_lines.append(f"▪ {lbl_clean}")
                else:
                    short_cap = caption_text.split("·")[-1].strip()
                    leg_lines.append(f"▪ {short_cap}")

                p_leg_label = QgsLayoutItemLabel(layout)
                p_leg_label.setId(f"PanelLegend_{i + 1}")
                p_leg_label.setText("\n".join(leg_lines))
                leg_font_sz = 5.8 if is_dense_grid else 6.8
                with suppress(Exception):
                    ltf = QgsTextFormat()
                    ltf.setFont(QFont("Segoe UI", int(round(leg_font_sz)), bold_flag))
                    ltf.setSize(leg_font_sz)
                    ltf.setColor(QColor("#16323f"))
                    p_leg_label.setTextFormat(ltf)
                p_leg_label.setHAlign(align_left)
                p_leg_label.setVAlign(align_vcenter)
                p_leg_label.setMarginX(1.2)
                p_leg_label.setMarginY(0.8)
                p_leg_label.setBackgroundEnabled(True)
                p_leg_label.setBackgroundColor(QColor(255, 255, 255, 225))
                p_leg_label.setFrameEnabled(True)

                leg_box_w = min(inner_w * 0.52, 34.0 if is_dense_grid else 44.0)
                leg_box_h = min(map_h * 0.36, 3.2 + len(leg_lines) * (2.6 if is_dense_grid else 3.2))
                p_leg_label.attemptMove(
                    QgsLayoutPoint(
                        inner_x + 1.2,
                        map_y + map_h - leg_box_h - 1.2,
                        QgsUnitTypes.LayoutMillimeters,
                    )
                )
                p_leg_label.attemptResize(QgsLayoutSize(leg_box_w, leg_box_h, QgsUnitTypes.LayoutMillimeters))
                layout.addLayoutItem(p_leg_label)

                # 3c. Per-Panel Mini Scale Bar (bottom-right inside panel map)
                p_sb = QgsLayoutItemScaleBar(layout)
                p_sb.setId(f"PanelScaleBar_{i + 1}")
                configure_scalebar_for_map(p_sb, map_item, style_name=scalebar_style, target_segments=2, compact=True)
                p_sb.setBackgroundEnabled(True)
                p_sb.setBackgroundColor(QColor(255, 255, 255, 220))
                sb_w = min(inner_w * 0.42, 26.0 if is_dense_grid else 34.0)
                sb_h = 7.5 if is_dense_grid else 9.0
                p_sb.attemptMove(
                    QgsLayoutPoint(
                        inner_x + inner_w - sb_w - 1.2,
                        map_y + map_h - sb_h - 1.2,
                        QgsUnitTypes.LayoutMillimeters,
                    )
                )
                p_sb.attemptResize(QgsLayoutSize(sb_w, sb_h, QgsUnitTypes.LayoutMillimeters))
                layout.addLayoutItem(p_sb)

        # 4. Master Cartographic Footer Strip (North Arrow + Multi-Layer QgsLayoutItemLegend + Master Scale Bar)
        if include_footer:
            footer_y = h - footer_h - 2.0

            # 4a. Master North Arrow Picture
            arrow_w, arrow_h = 15.0, 16.0
            if resolved_arrow_svg:
                picture = QgsLayoutItemPicture(layout)
                picture.setId("MasterNorthArrow")
                picture.setPicturePath(resolved_arrow_svg)
                if map_items:
                    picture.setLinkedMap(map_items[0])
                picture.setBackgroundEnabled(True)
                picture.setBackgroundColor(QColor(255, 255, 255, 235))
                picture.setFrameEnabled(True)
                picture.attemptMove(QgsLayoutPoint(margin_side, footer_y + 2.0, QgsUnitTypes.LayoutMillimeters))
                picture.attemptResize(QgsLayoutSize(arrow_w, arrow_h, QgsUnitTypes.LayoutMillimeters))
                layout.addLayoutItem(picture)

            # 4b. Master Legend (QgsLayoutItemLegend + Panel Legend Summary)
            legend_x = margin_side + arrow_w + 3.0
            scalebar_reserve_w = 62.0
            legend_avail_w = max(50.0, avail_w - arrow_w - scalebar_reserve_w - 6.0)

            legend_item = QgsLayoutItemLegend(layout)
            legend_item.setId("MasterLegend")
            legend_item.setTitle("Legend")
            if map_items:
                legend_item.setLinkedMap(map_items[0])
            legend_item.setAutoUpdateModel(False)

            root_group = legend_item.model().rootGroup()
            root_group.clear()
            for lyr in unique_layers:
                if project.mapLayer(lyr.id()) is None:
                    project.addMapLayer(lyr, False)
                root_group.addLayer(lyr)

            col_count = min(4, max(1, len(unique_layers)))
            legend_item.setColumnCount(col_count)
            legend_item.setEqualColumnWidth(False)
            legend_item.setSymbolWidth(3.5)
            legend_item.setSymbolHeight(2.5)
            with suppress(Exception):
                for st in (
                    QgsLegendStyle.Title,
                    QgsLegendStyle.Group,
                    QgsLegendStyle.Subgroup,
                    QgsLegendStyle.SymbolLabel,
                ):
                    tf_leg = QgsTextFormat()
                    sz = 7.5 if st == QgsLegendStyle.Title else 6.5
                    tf_leg.setFont(
                        QFont("Segoe UI", int(round(sz)), bold_flag if st == QgsLegendStyle.Title else normal_flag)
                    )
                    tf_leg.setSize(sz)
                    tf_leg.setColor(QColor("#16323f"))
                    legend_item.style(st).setTextFormat(tf_leg)

            legend_item.setBackgroundEnabled(True)
            legend_item.setBackgroundColor(QColor(255, 255, 255, 235))
            legend_item.setFrameEnabled(True)
            legend_item.setResizeToContents(False)
            legend_w = legend_avail_w * (0.55 if panel_summary_parts else 1.0)
            legend_item.attemptMove(QgsLayoutPoint(legend_x, footer_y + 1.0, QgsUnitTypes.LayoutMillimeters))
            legend_item.attemptResize(QgsLayoutSize(legend_w, footer_h - 3.0, QgsUnitTypes.LayoutMillimeters))
            layout.addLayoutItem(legend_item)
            with suppress(Exception):
                legend_item.updateLegend()

            # Panel-by-panel Legend Summary Card alongside Master Legend (guarantees all 8 panel names/categories appear)
            if panel_summary_parts:
                summary_x = legend_x + legend_w + 2.0
                summary_w = max(25.0, legend_avail_w - legend_w - 2.0)
                summary_label = QgsLayoutItemLabel(layout)
                summary_label.setId("MasterPanelLegendSummary")
                # Arrange in 2 lines when 4-8 panels
                mid = max(1, (len(panel_summary_parts) + 1) // 2)
                line1 = "  |  ".join(panel_summary_parts[:mid])
                line2 = "  |  ".join(panel_summary_parts[mid:])
                summary_text = f"Panels · {line1}" + (f"\n{line2}" if line2 else "")
                summary_label.setText(summary_text)
                with suppress(Exception):
                    stf = QgsTextFormat()
                    stf.setFont(QFont("Segoe UI", 6))
                    stf.setSize(6.2)
                    stf.setColor(QColor("#16323f"))
                    summary_label.setTextFormat(stf)
                summary_label.setHAlign(align_left)
                summary_label.setVAlign(align_vcenter)
                summary_label.setMarginX(1.5)
                summary_label.setMarginY(1.0)
                summary_label.setBackgroundEnabled(True)
                summary_label.setBackgroundColor(QColor("#f8fafc"))
                summary_label.setFrameEnabled(True)
                summary_label.attemptMove(QgsLayoutPoint(summary_x, footer_y + 1.0, QgsUnitTypes.LayoutMillimeters))
                summary_label.attemptResize(QgsLayoutSize(summary_w, footer_h - 3.0, QgsUnitTypes.LayoutMillimeters))
                layout.addLayoutItem(summary_label)

            # 4c. Master Scalebar linked to Panel 1
            linked_map = map_items[0] if map_items else None
            if linked_map:
                scalebar = QgsLayoutItemScaleBar(layout)
                scalebar.setId("MasterScaleBar")
                configure_scalebar_for_map(
                    scalebar,
                    linked_map,
                    style_name=scalebar_style,
                    target_segments=2,
                    compact=False,
                )
                scalebar.setBackgroundEnabled(True)
                scalebar.setBackgroundColor(QColor(255, 255, 255, 235))
                scalebar.setFrameEnabled(True)
                scalebar_x = w - margin_side - scalebar_reserve_w
                scalebar.attemptMove(QgsLayoutPoint(scalebar_x, footer_y + 2.0, QgsUnitTypes.LayoutMillimeters))
                scalebar.attemptResize(QgsLayoutSize(scalebar_reserve_w, 16.0, QgsUnitTypes.LayoutMillimeters))
                layout.addLayoutItem(scalebar)

        return layout

    def execute_export(self, open_after: bool = False) -> None:
        """Constructs layout or dashboard depending on active tab."""
        if self.tab_widget.currentIndex() == 1:
            title = self.html_title_input.text()
            export_path = self._normalized_html_path(self.html_path_input.text())
            if not export_path:
                QMessageBox.warning(self, "Export Path", "Please select an HTML export file path.")
                return
            output_directory = os.path.dirname(export_path)
            if not os.path.isdir(output_directory):
                QMessageBox.warning(self, "Export Path", "The selected output folder does not exist.")
                return
            self.html_path_input.setText(export_path)
            try:
                self.export_to_html(export_path, title, open_after=open_after)
            except Exception as e:
                QMessageBox.critical(
                    self, "Export Error", f"An unexpected error occurred during HTML export:\n{str(e)}"
                )
            return

        export_path = self._normalized_export_path(self.path_input.text())
        if not export_path:
            QMessageBox.warning(self, "Export Path", "Please select an export file path.")
            return
        output_directory = os.path.dirname(export_path)
        if not os.path.isdir(output_directory):
            QMessageBox.warning(
                self,
                "Export Path",
                "The selected output folder does not exist. Choose an existing folder.",
            )
            return
        self.path_input.setText(export_path)

        layout = self.build_qgis_print_layout()

        # Run Exporter
        exporter = QgsLayoutExporter(layout)
        ext = os.path.splitext(export_path)[1].lower()

        try:
            if ext == ".pdf":
                settings = QgsLayoutExporter.PdfExportSettings()
                result = exporter.exportToPdf(export_path, settings)
            elif ext == ".svg":
                settings = QgsLayoutExporter.SvgExportSettings()
                result = exporter.exportToSvg(export_path, settings)
            else:  # PNG, JPG, JPEG
                settings = QgsLayoutExporter.ImageExportSettings()
                result = exporter.exportToImage(export_path, settings)

            if result == QgsLayoutExporter.Success:
                QSettings().setValue(
                    "zero2multimap/last_export_dir",
                    os.path.dirname(export_path),
                )
                if open_after:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(export_path))
                else:
                    QMessageBox.information(
                        self, "Export Success", f"Layout exported successfully to:\n{export_path}"
                    )
                self.accept()
            else:
                QMessageBox.critical(
                    self, "Export Failed", f"Failed to export print layout. Exporter code: {result}"
                )
        except Exception as e:
            QMessageBox.critical(
                self, "Export Error", f"An unexpected error occurred during export:\n{str(e)}"
            )

    @staticmethod
    def _write_only_mode():
        """Return QIODevice's write-only flag on both Qt 5 and Qt 6."""
        open_mode = getattr(QIODevice, "OpenModeFlag", QIODevice)
        return getattr(open_mode, "WriteOnly")

    def _rendered_panel_data(self, panel, index: int) -> dict[str, object]:
        """Render one canvas to PNG and collect its browser metadata."""
        canvas_layers = panel.canvas.layers()
        if not canvas_layers and getattr(panel, "mode", "") == "layer" and hasattr(panel, "selected_layer"):
            sel = panel.selected_layer()
            if sel is not None:
                canvas_layers = [sel]
        if not canvas_layers and getattr(panel, "mode", "") == "sync" and self.iface and self.iface.mapCanvas():
            canvas_layers = list(self.iface.mapCanvas().layers())

        settings = QgsMapSettings()
        settings.setDestinationCrs(panel.canvas.mapSettings().destinationCrs())
        settings.setLayers(canvas_layers)
        settings.setExtent(panel.canvas.extent())
        settings.setBackgroundColor(panel.canvas.canvasColor())
        settings.setTransformContext(QgsProject.instance().transformContext())
        settings.setLayerStyleOverrides(panel.canvas.mapSettings().layerStyleOverrides())

        flag_antialiasing = getattr(QgsMapSettings, "Antialiasing", None)
        flag_advanced = getattr(QgsMapSettings, "UseAdvancedEffects", None)
        flag_hq = getattr(QgsMapSettings, "HighQualityRender", None)

        flags = QgsMapSettings.Flags()
        if flag_antialiasing is not None:
            flags |= flag_antialiasing
        if flag_advanced is not None:
            flags |= flag_advanced
        if flag_hq is not None:
            flags |= flag_hq
        settings.setFlags(flags)

        source_width = max(1, panel.canvas.width())
        source_height = max(1, panel.canvas.height())
        scale_factor = min(2.0, 1600.0 / source_width, 1200.0 / source_height)
        output_width = max(640, int(source_width * scale_factor))
        output_height = max(420, int(source_height * scale_factor))
        settings.setOutputSize(QSize(output_width, output_height))

        render_job = QgsMapRendererSequentialJob(settings)
        render_job.start()
        render_job.waitForFinished()
        image = render_job.renderedImage()

        if image.isNull():
            p_job = QgsMapRendererParallelJob(settings)
            p_job.start()
            p_job.waitForFinished()
            image = p_job.renderedImage()
            if image.isNull():
                raise RuntimeError(f"Panel {index + 1} could not be rendered.")

        image_bytes = QByteArray()
        buffer = QBuffer(image_bytes)
        if not buffer.open(self._write_only_mode()):
            raise OSError(f"Panel {index + 1} image buffer could not be opened.")
        if not image.save(buffer, "PNG"):
            buffer.close()
            raise OSError(f"Panel {index + 1} image could not be encoded as PNG.")
        buffer.close()
        encoded_image = bytes(image_bytes.toBase64()).decode("ascii")

        layer_details = []
        rendered_layer_names = []
        for lyr in canvas_layers:
            if not lyr or not lyr.isValid():
                continue
            rendered_layer_names.append(lyr.name())
            if isinstance(lyr, QgsVectorLayer):
                geom_name = (
                    QgsWkbTypes.displayString(lyr.wkbType()) if hasattr(QgsWkbTypes, "displayString") else "Vector"
                )
                fc = lyr.featureCount()
                fc_str = f"{fc:,} features" if fc >= 0 else "Vector"
                layer_details.append(f"{lyr.name()} ({geom_name}, {fc_str})")
            elif isinstance(lyr, QgsRasterLayer):
                layer_details.append(f"{lyr.name()} (Raster)")

        if panel.mode == "layer":
            selected_layer = panel.selected_layer()
            layer_name = selected_layer.name() if selected_layer else "No layer selected"
            panel_title = f"Panel {index + 1} · {layer_name}"
            detail = "One-layer comparison"
        elif panel.mode == "theme":
            theme_name = panel.theme_combo.currentText() or "No theme selected"
            panel_title = f"Panel {index + 1} · {theme_name}"
            detail = "Map theme"
        else:
            panel_title = f"Panel {index + 1} · Main map"
            detail = "Follows QGIS canvas"

        detail_tooltip = "Layers: " + " | ".join(layer_details) if layer_details else detail

        extent = panel.canvas.extent()
        destination_crs = panel.canvas.mapSettings().destinationCrs()
        crs_label = destination_crs.authid() or destination_crs.description()
        scale_val = int(panel.canvas.scale())

        extent_wgs84 = [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]
        with suppress(Exception):
            wgs84_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            if destination_crs.isValid() and destination_crs != wgs84_crs:
                transform = QgsCoordinateTransform(destination_crs, wgs84_crs, QgsProject.instance())
                rect_wgs84 = transform.transformBoundingBox(extent)
                extent_wgs84 = [
                    rect_wgs84.xMinimum(),
                    rect_wgs84.yMinimum(),
                    rect_wgs84.xMaximum(),
                    rect_wgs84.yMaximum(),
                ]

        legend_items = self._extract_legend_items(panel, fallback_layers=canvas_layers)

        return {
            "title": panel_title,
            "detail": detail,
            "detail_tooltip": detail_tooltip,
            "layers": rendered_layer_names,
            "layer_details": layer_details,
            "scale": f"1:{scale_val:,}",
            "image_data": f"data:image/png;base64,{encoded_image}",
            "extent": [
                extent.xMinimum(),
                extent.yMinimum(),
                extent.xMaximum(),
                extent.yMaximum(),
            ],
            "extent_wgs84": extent_wgs84,
            "crs": crs_label or "Unknown CRS",
            "legend": legend_items,
        }

    def _extract_legend_items(self, panel, fallback_layers=None) -> list[dict[str, str]]:
        """Extract category/range/rule/symbol labels and colors for HTML micro-legend."""
        return extract_panel_legend_items(panel, max_items=10, fallback_layers=fallback_layers)

    def _write_html_atomically(self, export_path: str, html_content: str) -> None:
        """Commit a UTF-8 HTML file only after the full payload is written."""
        output = QSaveFile(export_path)
        if not output.open(self._write_only_mode()):
            raise OSError(output.errorString())
        payload = html_content.encode("utf-8")
        written = output.write(payload)
        if written != len(payload):
            output.cancelWriting()
            raise OSError("The complete HTML payload could not be written.")
        if not output.commit():
            raise OSError(output.errorString())

    def export_to_html(self, export_path: str, title: str, open_after: bool = False) -> None:
        """Generate an atomic, self-contained dashboard from exact panel renders."""
        panels = self.parent_dialog.panels
        if not panels:
            raise ValueError("There are no map panels to export.")

        theme = self.html_theme_combo.currentData() or "slate"
        self.export_btn.setEnabled(False)
        self.export_btn.setText("Rendering panels…")
        try:
            panels_data = [
                self._rendered_panel_data(panel, index)
                for index, panel in enumerate(panels)
            ]
            html_content = build_dashboard_html(
                title=title,
                rows=self.parent_dialog.rows,
                cols=self.parent_dialog.cols,
                panels=panels_data,
                theme=theme,
            )
            self._write_html_atomically(export_path, html_content)

        finally:
            self.export_btn.setEnabled(True)
            self._on_tab_changed(self.tab_widget.currentIndex())

        QSettings().setValue(
            "zero2multimap/last_export_dir",
            os.path.dirname(export_path),
        )
        if open_after:
            QDesktopServices.openUrl(QUrl.fromLocalFile(export_path))
        else:
            QMessageBox.information(
                self,
                "Export Success",
                "Offline interactive HTML dashboard exported successfully to:\n"
                f"{export_path}\n\n"
                "The file includes all panel styles, north arrow, dynamic scale bar, legends, and raster content.",
            )
        self.accept()
