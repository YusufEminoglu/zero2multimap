# -*- coding: utf-8 -*-
"""02Multimap — print layout and HTML dashboard exporter.

The export dialog builds a multi-panel print layout (title, panel headers, a
north arrow, legend and scale bar per panel, a footer with a legend and a
scale bar), exports it to PDF, PNG, JPEG or SVG, adds it to the project's
Layout Manager for further editing in the QGIS Layout Designer, or writes a
self-contained HTML dashboard from exact panel renders.
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
    QSaveFile,
    QUrl,
)
from qgis.PyQt.QtGui import QColor, QFont, QDesktopServices

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
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
    Qgis,
    QgsApplication,
    QgsCsException,
    QgsProject,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutExporter,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLayoutUtils,
    QgsLegendRenderer,
    QgsLegendSettings,
    QgsLegendStyle,
    QgsMapLayerLegendUtils,
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

MM = Qgis.LayoutUnit.Millimeters
INK = "#16323f"
ELLIPSIS = "…"
DEFAULT_LAYOUT_TITLE = "Comparative Map Grid"
# The dialog follows the QGIS theme. Only the text colour of buttons and combo
# boxes is pinned to the theme's own text colour: Night Mapping draws them in its
# dimmer window-text grey on a mid-grey gradient (3.5:1); its text colour reaches 5.5:1.
DIALOG_QSS = "QComboBox, QPushButton { color: palette(text); }"
# Accent for the primary button: white text on this teal is 5.1:1 (WCAG AA).
ACCENT_BUTTON_QSS = (
    "QPushButton { background-color: #237a72; color: #ffffff; border: 1px solid #1b5f59; "
    "border-radius: 4px; font-weight: bold; padding: 5px 14px; }"
    "QPushButton:hover { background-color: #1f6f67; }"
)


def mnemonic_safe(text: str) -> str:
    """Return button/check box/tab text whose "&" is shown instead of read as a shortcut key."""
    return str(text).replace("&", "&&")


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


def _without_layer_prefix(entry: dict) -> str:
    """A legend entry's class label without the leading "layer name: "."""
    label = str(entry.get("label", ""))
    prefix = f"{entry.get('layer', '')}: "
    return label[len(prefix):] if entry.get("layer") and label.startswith(prefix) else label


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


def lonlat_grid(extent, crs: QgsCoordinateReferenceSystem, samples: int = 9) -> dict | None:
    """Longitude/latitude of a ``samples`` x ``samples`` grid spanning ``extent``.

    Rows run from the top edge to the bottom edge and columns from left to right,
    so the HTML dashboard can turn a position in the panel image into longitude
    and latitude by bilinear interpolation and measure true ground distance.
    Returns ``None`` when the extent cannot be transformed to WGS 84.
    """
    if extent is None or extent.isEmpty() or crs is None or not crs.isValid() or samples < 2:
        return None
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    try:
        transform = QgsCoordinateTransform(crs, wgs84, QgsProject.instance())
        points = []
        for row in range(samples):
            y = extent.yMaximum() - extent.height() * row / (samples - 1)
            for col in range(samples):
                x = extent.xMinimum() + extent.width() * col / (samples - 1)
                point = transform.transform(QgsPointXY(x, y))
                if not (math.isfinite(point.x()) and math.isfinite(point.y())):
                    return None
                points.append([round(point.x(), 9), round(point.y(), 9)])
    except QgsCsException:
        return None
    return {"n": samples, "points": points}


def map_unit_metres(crs: QgsCoordinateReferenceSystem) -> float | None:
    """Metres per map unit for a projected CRS; ``None`` for degrees or unknown units."""
    if crs is None or not crs.isValid():
        return None
    units = crs.mapUnits()
    if units in (Qgis.DistanceUnit.Degrees, Qgis.DistanceUnit.Unknown):
        return None
    factor = QgsUnitTypes.fromUnitToUnitFactor(units, Qgis.DistanceUnit.Meters)
    return float(factor) if math.isfinite(factor) and factor > 0 else None


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
    units = Qgis.DistanceUnit.Kilometers if use_km else Qgis.DistanceUnit.Meters
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
    scalebar.setTextFormat(text_format(font_size))
    if compact:
        scalebar.setHeight(1.6)
        scalebar.setBoxContentSpace(0.8)
    else:
        scalebar.setHeight(2.4)
        scalebar.setBoxContentSpace(1.2)
    scalebar.update()


def text_format(size_pt: float, bold: bool = False, color: str = INK) -> QgsTextFormat:
    """A ``QgsTextFormat`` in Segoe UI (or the system fallback) at ``size_pt`` points."""
    font = QFont("Segoe UI")
    font.setPointSizeF(float(size_pt))
    font.setBold(bool(bold))
    fmt = QgsTextFormat()
    fmt.setFont(font)
    fmt.setSize(float(size_pt))
    fmt.setColor(QColor(color))
    return fmt


def _shorten(cell: str, keep: int | None = None) -> str:
    """Shorten ``cell`` (by one character, or to ``keep`` characters) and end it with an ellipsis."""
    core = cell[:-1] if cell.endswith(ELLIPSIS) else cell
    core = core[: (len(core) - 1 if keep is None else max(0, keep))].rstrip()
    return (core + ELLIPSIS) if core else ELLIPSIS


def fit_label_rows(
    label: QgsLayoutItemLabel,
    rows,
    box_width: float,
    box_height: float,
    start_size: float,
    min_size: float,
    bold: bool = False,
    separator: str = "    ",
) -> float:
    """Write ``rows`` into ``label`` so that ``label.sizeForText()`` fits the box (mm).

    ``rows`` is a list of rows, each a list of cells joined by ``separator``. The
    font shrinks in 0.25 pt steps down to ``min_size``; rows that still do not fit
    the height are replaced by a "+N more" line; a row that is still too wide has
    its longest cell shortened and ended with an ellipsis. Returns the font size.
    """
    rows = [[str(cell) for cell in row if str(cell)] for row in rows]
    rows = [row for row in rows if row] or [[""]]
    tolerance = 1e-6

    def render(candidate, size):
        label.setTextFormat(text_format(size, bold))
        label.setText("\n".join(separator.join(row) for row in candidate))
        return label.sizeForText()

    def fits(need):
        return need.width() <= box_width + tolerance and need.height() <= box_height + tolerance

    size = float(start_size)
    need = render(rows, size)
    while not fits(need) and size - 0.25 >= min_size - tolerance:
        size -= 0.25
        need = render(rows, size)

    if need.height() > box_height + tolerance and len(rows) > 1:
        total = len(rows)
        chosen = rows[:1]
        for keep in range(total - 1, 0, -1):
            candidate = rows[:keep] + [[f"+{total - keep} more"]]
            if render(candidate, size).height() <= box_height + tolerance:
                chosen = candidate
                break
        rows = chosen

    for index, row in enumerate(rows):
        cells = list(row)
        guard = sum(len(cell) for cell in cells) + 2
        width = render([cells], size).width()
        while width > box_width + tolerance and guard > 0:
            guard -= 1
            longest = max(range(len(cells)), key=lambda i: len(cells[i]))
            if len(cells[longest]) <= 1:
                if len(cells) == 1:
                    break
                cells.pop()
            else:
                ratio = max(0.5, min(0.97, box_width / max(width, tolerance)))
                target = max(1, int(len(cells[longest]) * ratio))
                shortened = _shorten(cells[longest])
                if len(shortened) > target + 1:
                    shortened = _shorten(cells[longest], keep=target - 1)
                cells[longest] = shortened
            width = render([cells], size).width()
        rows[index] = cells

    render(rows, size)
    return size


def legend_content_size(legend: QgsLayoutItemLegend):
    """Size (mm) the legend's content needs, title included, measured by QGIS's legend renderer."""
    settings = QgsLegendSettings(legend.legendSettings())
    settings.setTitle(legend.title())
    renderer = QgsLegendRenderer(legend.model(), settings)
    context = QgsLayoutUtils.createRenderContextForLayout(legend.layout(), None)
    return renderer.minimumSize(context)


def _style_legend(legend: QgsLayoutItemLegend, body_size: float) -> None:
    """Set the legend's text sizes and spacing on the legend itself (``rstyle``), not on a copy."""
    styles = QgsLegendStyle.Style
    side = QgsLegendStyle.Side
    for style_id, size, bold in (
        (styles.Title, body_size + 1.0, True),
        (styles.Group, body_size, True),
        (styles.Subgroup, body_size, True),
        (styles.SymbolLabel, body_size, False),
    ):
        legend.rstyle(style_id).setTextFormat(text_format(size, bold))
    # QGIS's defaults (2.5 mm above every entry) suit a full-page legend, not a footer strip.
    legend.rstyle(styles.Title).setMargin(side.Bottom, 1.0)
    legend.rstyle(styles.Group).setMargin(side.Top, 1.0)
    legend.rstyle(styles.Subgroup).setMargin(side.Top, 1.0)
    legend.rstyle(styles.Symbol).setMargin(side.Top, 0.6)
    legend.rstyle(styles.SymbolLabel).setMargin(side.Left, 1.0)
    legend.setBoxSpace(1.2)
    legend.setColumnSpace(2.5)
    legend.setSymbolWidth(3.5 if body_size >= 6.0 else 3.0)
    legend.setSymbolHeight(2.5 if body_size >= 6.0 else 2.0)


def _clip(text: str, max_chars: int | None) -> str:
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + ELLIPSIS


def _legend_texts(legend: QgsLayoutItemLegend) -> list:
    """(layer node, layer title, entry labels) for every layer in the legend's own tree."""
    model = legend.model()
    entries = []
    for node in model.rootGroup().findLayers():
        layer = node.layer()
        title = layer.name() if layer is not None else node.name()
        labels = [str(item.data(Qt.ItemDataRole.DisplayRole) or "") for item in model.layerOriginalLegendNodes(node)]
        entries.append((node, title, labels))
    return entries


def _shorten_legend_texts(legend: QgsLayoutItemLegend, entries: list, max_chars: int | None) -> None:
    """Shorten (or, with ``None``, restore) layer titles and entry labels in the legend only.

    The legend has its own layer tree, so the project's layers keep their names.
    """
    model = legend.model()
    for node, title, labels in entries:
        node.setUseLayerName(False)
        node.setName(_clip(title, max_chars))
        for index, label in enumerate(labels):
            if label:
                QgsMapLayerLegendUtils.setLegendNodeUserLabel(node, index, _clip(label, max_chars))
        model.refreshLayerLegend(node)


def fit_legend(legend: QgsLayoutItemLegend, box_width: float, box_height: float) -> bool:
    """Choose text size, column count and, if needed, shorter names so the legend fits its box.

    Returns True when the legend content (measured by QGIS's legend renderer) fits;
    otherwise the legend keeps the attempt that came closest.
    """
    legend.setSplitLayer(True)
    entries = _legend_texts(legend)
    plans = [(None, size) for size in (6.5, 6.0, 5.5)]
    plans += [(chars, size) for chars in (48, 40, 32, 26, 20, 16, 12, 8) for size in (5.5, 5.0)]
    best = None
    for max_chars, body_size in plans:
        if max_chars is not None:
            _shorten_legend_texts(legend, entries, max_chars)
        _style_legend(legend, body_size)
        for columns in range(1, 7):
            legend.setColumnCount(columns)
            need = legend_content_size(legend)
            overflow = max(need.width() / box_width, need.height() / box_height)
            if best is None or overflow < best[0]:
                best = (overflow, max_chars, body_size, columns)
            if overflow <= 1.0:
                break
        if best is not None and best[0] <= 1.0:
            break
    if best is not None and best[0] > 1.0:
        _overflow, max_chars, body_size, columns = best
        _shorten_legend_texts(legend, entries, max_chars)
        _style_legend(legend, body_size)
        legend.setColumnCount(columns)
    legend.setResizeToContents(False)
    legend.attemptResize(QgsLayoutSize(box_width, box_height, MM))
    return best is not None and best[0] <= 1.0


def unique_layout_name(base_name: str, project: QgsProject | None = None) -> str:
    """``base_name``, or ``base_name (2)``, ``(3)``… if the project already has a layout by that name."""
    project = project or QgsProject.instance()
    base = (base_name or "").strip() or DEFAULT_LAYOUT_TITLE
    taken = {layout.name() for layout in project.layoutManager().layouts()}
    if base not in taken:
        return base
    number = 2
    while f"{base} ({number})" in taken:
        number += 1
    return f"{base} ({number})"


def add_layout_to_project(layout: QgsPrintLayout, base_name: str, project: QgsProject | None = None) -> str:
    """Name ``layout`` uniquely and hand it to the project's Layout Manager. Returns the name."""
    project = project or layout.project() or QgsProject.instance()
    name = unique_layout_name(base_name, project)
    layout.setName(name)
    if not project.layoutManager().addLayout(layout):
        raise RuntimeError(f"The layout “{name}” could not be added to the project's Layout Manager.")
    return name


def export_panels(parent_dialog) -> tuple[list, int, int]:
    """``(panels, rows, cols)`` an export shows.

    The 02Multimap dialog decides through ``export_panels()``: in the Swipe
    layout it returns the bottom and the top layer set as a 1 x 2 pair, each at
    the swipe map's extent, scale and CRS. Anything else that offers
    ``panels``, ``rows`` and ``cols`` is exported as it is.
    """
    provider = getattr(parent_dialog, "export_panels", None)
    if callable(provider):
        panels, rows, cols = provider()
        return list(panels), max(1, int(rows)), max(1, int(cols))
    panels = list(getattr(parent_dialog, "panels", []))
    rows = max(1, int(getattr(parent_dialog, "rows", 2)))
    cols = max(1, int(getattr(parent_dialog, "cols", 2)))
    return panels, rows, cols


def panel_label(panel, index: int) -> str:
    """The name a panel goes by on paper: "Panel 2", or "Top layers" in a swipe export."""
    title = getattr(panel, "export_title", None)
    if callable(title):
        with suppress(Exception):
            text = str(title()).strip()
            if text:
                return text
    return f"Panel {index + 1}"


def open_in_layout_designer(parent_dialog, iface, **layout_options) -> QgsPrintLayout:
    """Build the multi-panel layout, add it to the Layout Manager and open it in the Layout Designer.

    ``parent_dialog`` is the 02Multimap dialog (it provides ``panels``, ``rows`` and
    ``cols``); ``layout_options`` are the keyword arguments of
    :meth:`PrintLayoutDialog.build_qgis_print_layout` (title, page_size_name,
    orientation, north_arrow_key, scalebar_style, include_footer,
    per_panel_overlays, include_headers). Returns the layout, now owned by the
    project; raises ``RuntimeError`` if the Layout Manager refuses it.
    """
    exporter = PrintLayoutDialog(parent_dialog, iface)
    try:
        layout = exporter.add_layout_to_manager(**layout_options)
        exporter.show_in_designer(layout)
        return layout
    finally:
        exporter.deleteLater()


def export_failure_message(result, export_path: str) -> str:
    """Plain explanation of a failed ``QgsLayoutExporter`` run, with what to try next."""
    codes = QgsLayoutExporter.ExportResult
    reasons = {
        codes.FileError: (
            "The file could not be written. If it is open in another program, close it; "
            "otherwise choose a folder you can save to, then export again."
        ),
        codes.MemoryError: (
            "There was not enough memory to draw the page. Try a smaller page size or fewer panels."
        ),
        codes.PrintError: "The page could not be drawn. Try another format, such as PNG instead of PDF.",
        codes.SvgLayerError: "The SVG could not be split into layers. Try PDF or PNG instead.",
        codes.IteratorError: "The layout could not be prepared for export. Try exporting again.",
        codes.Canceled: "The export was cancelled before it finished. Export again to create the file.",
    }
    reason = reasons.get(result, "The export stopped before the file was written. Try another format or folder.")
    return f"The print layout was not saved to:\n{export_path}\n\n{reason}"


class PrintLayoutDialog(QDialog):
    """Provides configuration and execution for multi-panel print layout and HTML dashboard exports."""

    def __init__(self, parent_dialog, iface):
        # Parent the window to the QGIS main window, not to the 02Multimap dialog:
        # Qt style sheets cascade from parent to child windows, and the plugin
        # dialog's light style sheet made this dialog dark-on-dark in dark themes.
        super().__init__(PrintLayoutDialog._qt_parent(iface))
        self.parent_dialog = parent_dialog
        self.iface = iface
        self._centred = False
        self.setWindowTitle("02Multimap Export")
        self.setMinimumSize(560, 490)
        self.setStyleSheet(DIALOG_QSS)
        self._setup_ui()

    @staticmethod
    def _qt_parent(iface):
        main_window = None
        if iface is not None and hasattr(iface, "mainWindow"):
            with suppress(Exception):
                main_window = iface.mainWindow()
        return main_window if isinstance(main_window, QWidget) else None

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if self._centred:
            return
        self._centred = True
        anchor = self.parent_dialog if isinstance(self.parent_dialog, QWidget) else None
        if anchor is not None and anchor.isVisible():
            frame = self.frameGeometry()
            frame.moveCenter(anchor.frameGeometry().center())
            self.move(frame.topLeft())

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

        self.title_input = QLineEdit(DEFAULT_LAYOUT_TITLE)
        print_layout_form.addRow("Map title:", self.title_input)

        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["A4", "A3", "Letter"])
        print_layout_form.addRow("Page size:", self.page_size_combo)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["Landscape", "Portrait"])
        print_layout_form.addRow("Orientation:", self.orientation_combo)

        self.north_arrow_combo = QComboBox()
        self.north_arrow_combo.addItem("Built-in compass", "builtin")
        self.north_arrow_combo.addItem("Minimal arrow", "arrows/NorthArrow_01.svg")
        self.north_arrow_combo.addItem("Classic compass", "arrows/NorthArrow_02.svg")
        self.north_arrow_combo.addItem("Pointer", "arrows/NorthArrow_04.svg")
        self.north_arrow_combo.addItem("Navigation cross", "arrows/NorthArrow_07.svg")
        self.north_arrow_combo.addItem("Narrow pointer", "arrows/NorthArrow_09.svg")
        print_layout_form.addRow("North arrow:", self.north_arrow_combo)

        self.scalebar_combo = QComboBox()
        self.scalebar_combo.addItems([
            "Single Box",
            "Double Box",
            "Line Ticks Up",
            "Line Ticks Down",
            "Stepped Line",
        ])
        print_layout_form.addRow("Scale bar style:", self.scalebar_combo)

        carto_opts_widget = QWidget()
        carto_opts_layout = QVBoxLayout(carto_opts_widget)
        carto_opts_layout.setContentsMargins(0, 2, 0, 2)
        carto_opts_layout.setSpacing(4)

        self.include_legend_chk = QCheckBox(mnemonic_safe("Footer with north arrow, legend & scale bar"))
        self.include_legend_chk.setChecked(True)
        carto_opts_layout.addWidget(self.include_legend_chk)

        self.per_panel_overlays_chk = QCheckBox(mnemonic_safe("North arrow, legend & scale bar on every panel"))
        self.per_panel_overlays_chk.setChecked(True)
        carto_opts_layout.addWidget(self.per_panel_overlays_chk)

        self.include_panel_headers_chk = QCheckBox(mnemonic_safe("Panel titles & frames"))
        self.include_panel_headers_chk.setChecked(True)
        carto_opts_layout.addWidget(self.include_panel_headers_chk)

        print_layout_form.addRow("Include:", carto_opts_widget)

        self.format_combo = QComboBox()
        self.format_combo.addItem("PDF document (*.pdf)", ".pdf")
        self.format_combo.addItem("PNG image (*.png)", ".png")
        self.format_combo.addItem("JPEG image (*.jpg)", ".jpg")
        self.format_combo.addItem("SVG graphic (*.svg)", ".svg")
        print_layout_form.addRow("Format:", self.format_combo)

        path_layout = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Choose where to save the PDF or image")
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self.browse_export_path)
        path_layout.addWidget(self.path_input, 1)
        path_layout.addWidget(self.browse_btn)
        print_layout_form.addRow("Save to:", path_layout)

        self.designer_btn = QPushButton("Open in Layout Designer")
        self.designer_btn.setToolTip(
            "Add this layout to the project's Layout Manager and open it in the QGIS Layout "
            "Designer, where every item can be edited before printing."
        )
        self.designer_btn.clicked.connect(self._on_open_in_designer)
        print_layout_form.addRow("", self.designer_btn)

        self.tab_widget.addTab(self.print_tab, "Print layout")

        # ──────────────── Tab 2: Interactive HTML Dashboard ────────────────
        self.html_tab = QWidget()
        html_form = QFormLayout(self.html_tab)
        html_form.setContentsMargins(12, 12, 12, 12)
        html_form.setSpacing(10)

        self.html_title_input = QLineEdit("Comparative Map Dashboard")
        html_form.addRow("Dashboard title:", self.html_title_input)

        self.html_theme_combo = QComboBox()
        self.html_theme_combo.addItem("Slate (light)", "slate")
        self.html_theme_combo.addItem("Midnight (dark)", "dark")
        self.html_theme_combo.addItem("Emerald (light)", "emerald")
        html_form.addRow("Colour theme:", self.html_theme_combo)

        self.html_info = QLabel(
            "<b>One HTML file that works offline in any web browser:</b><br>"
            "• Pan and zoom all panels together<br>"
            "• North arrow, scale bar and legend on every panel<br>"
            "• A cursor marker that follows the same place in every panel<br>"
            "• Distance measuring<br>"
            "• Slide mode: one panel at a time, arrow keys to move<br>"
            "• Three colour themes"
        )
        self.html_info.setWordWrap(True)
        self.html_info.setFrameShape(QFrame.Shape.StyledPanel)
        self.html_info.setMargin(6)
        html_form.addRow("", self.html_info)

        html_path_layout = QHBoxLayout()
        self.html_path_input = QLineEdit()
        self.html_path_input.setPlaceholderText("Choose where to save the .html file")
        self.html_browse_btn = QPushButton("Browse…")
        self.html_browse_btn.clicked.connect(self.browse_html_export_path)
        html_path_layout.addWidget(self.html_path_input, 1)
        html_path_layout.addWidget(self.html_browse_btn)
        html_form.addRow("Save to:", html_path_layout)

        self.tab_widget.addTab(self.html_tab, "Interactive HTML dashboard")

        layout.addWidget(self.tab_widget, 1)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(lambda: self.execute_export(open_after=False))

        self.export_open_btn = QPushButton(mnemonic_safe("Export & Open"))
        self.export_open_btn.clicked.connect(lambda: self.execute_export(open_after=True))
        self.export_open_btn.setStyleSheet(ACCENT_BUTTON_QSS)
        self.export_open_btn.setDefault(True)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addWidget(self.export_open_btn)
        layout.addLayout(btn_layout)

        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(0)

    def _on_tab_changed(self, index: int) -> None:
        """Update action button text based on active tab."""
        self.export_btn.setText("Export")
        if index == 0:
            self.export_open_btn.setText(mnemonic_safe("Export & Open"))
        else:
            self.export_open_btn.setText(mnemonic_safe("Export & Open in Browser"))

    def browse_export_path(self) -> None:
        """Opens file dialog for print layout formats."""
        format_filter = self.format_combo.currentText()
        last_dir = QSettings().value("zero2multimap/last_export_dir", "")
        if not (isinstance(last_dir, str) and os.path.isdir(last_dir)):
            last_dir = os.path.expanduser("~")

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save the print layout as", last_dir, format_filter
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
            self, "Save the HTML dashboard as", last_dir, "HTML dashboard (*.html)"
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
        label = panel_label(panel, index)
        if getattr(panel, "mode", "") == "layer":
            sel = panel.selected_layer() if hasattr(panel, "selected_layer") else None
            name = sel.name() if sel else (panel.layer_combo.currentText() if hasattr(panel, "layer_combo") else "Layer")
            return f"{label} · {name or 'Layer'}"
        if getattr(panel, "mode", "") == "theme":
            theme_name = panel.theme_combo.currentText() if hasattr(panel, "theme_combo") else "Theme"
            return f"{label} · {theme_name or 'Theme'}"
        layers = list(panel.canvas.layers()) if hasattr(panel, "canvas") and panel.canvas else []
        if layers and layers[0] and layers[0].isValid():
            if len(layers) == 1:
                return f"{label} · {layers[0].name()}"
            return f"{label} · {layers[0].name()} (+{len(layers) - 1})"
        return f"{label} · Main Map"

    @staticmethod
    def _place_scalebar(scalebar, right: float, bottom: float, width: float, height: float, inset: float):
        """Size the scale bar, then anchor its real (possibly larger) box to the bottom-right corner."""
        scalebar.attemptResize(QgsLayoutSize(width, height, MM))
        actual = scalebar.rect()
        scalebar.attemptMove(QgsLayoutPoint(right - actual.width() - inset, bottom - actual.height() - inset, MM))
        return actual

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
    ) -> QgsPrintLayout:
        """Construct and return a complete ``QgsPrintLayout`` with all panels and cartographic elements.

        Every text label is fitted to its box with ``QgsLayoutItemLabel.sizeForText()``
        and the legend content with QGIS's legend renderer, in both orientations and
        on every page size. The result can be exported directly or handed to the
        project's Layout Manager (it is a ``QgsPrintLayout``).
        """
        title = title if title is not None else (self.title_input.text().strip() or DEFAULT_LAYOUT_TITLE)
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
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName(title or DEFAULT_LAYOUT_TITLE)

        page = layout.pageCollection().pages()[0]
        page.setPageSize(QgsLayoutSize(w, h, MM))

        margin_side = 8.0
        margin_top_base = 6.0
        title_h = 10.0
        align_left = Qt.AlignmentFlag.AlignLeft
        align_center = Qt.AlignmentFlag.AlignHCenter
        align_vcenter = Qt.AlignmentFlag.AlignVCenter

        # 1. Main Title Label
        title_item = QgsLayoutItemLabel(layout)
        title_item.setId("MasterTitle")
        title_item.setHAlign(align_center)
        title_item.setVAlign(align_vcenter)
        layout.addLayoutItem(title_item)
        title_item.attemptMove(QgsLayoutPoint(margin_side, margin_top_base, MM))
        title_item.attemptResize(QgsLayoutSize(w - 2.0 * margin_side, title_h, MM))
        fit_label_rows(title_item, [[title]], w - 2.0 * margin_side, title_h, 14.0, 9.0, bold=True)

        # 2. Grid Geometry Calculation
        grid_top = margin_top_base + title_h + 2.5
        footer_h = 22.0 if include_footer else 4.0
        avail_w = w - (2.0 * margin_side)
        avail_h = h - grid_top - footer_h - 4.0

        panels, rows, cols = export_panels(self.parent_dialog)

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
            map_h = max(12.0, item_h - 2.0 * pad - header_h)
            block_y = cell_y + pad
            if getattr(panel, "export_keep_aspect", False):
                # A swipe side shows exactly the swipe map's extent: give the map
                # item the canvas's shape and centre header + map in the cell.
                aspect = max(1, panel.canvas.width()) / max(1, panel.canvas.height())
                if inner_w / map_h > aspect:
                    fitted_w = map_h * aspect
                    inner_x += (inner_w - fitted_w) / 2.0
                    inner_w = fitted_w
                else:
                    fitted_h = inner_w / aspect
                    block_y += (map_h - fitted_h) / 2.0
                    map_h = fitted_h

            if include_headers:
                hdr_label = QgsLayoutItemLabel(layout)
                hdr_label.setId(f"PanelHeader_{i + 1}")
                scale_val = int(panel.canvas.scale()) if hasattr(panel, "canvas") and panel.canvas else 0
                scale_suffix = f"1:{scale_val:,}" if scale_val > 0 else ""
                hdr_label.setHAlign(align_left)
                hdr_label.setVAlign(align_vcenter)
                hdr_label.setMarginX(1.0)
                hdr_label.setMarginY(0.0)
                hdr_label.setBackgroundEnabled(True)
                hdr_label.setBackgroundColor(QColor("#eef1f4"))
                hdr_label.setFrameEnabled(True)
                layout.addLayoutItem(hdr_label)
                hdr_label.attemptMove(QgsLayoutPoint(inner_x, block_y, MM))
                hdr_label.attemptResize(QgsLayoutSize(inner_w, header_h, MM))
                hdr_font_sz = 7.0 if is_dense_grid else 8.0
                fit_label_rows(
                    hdr_label, [[caption_text, scale_suffix]], inner_w, header_h,
                    hdr_font_sz, 5.0, bold=True, separator="   ",
                )

            map_y = block_y + header_h

            map_item = QgsLayoutItemMap(layout)
            map_item.setId(f"PanelMap_{i + 1}")
            map_item.attemptMove(QgsLayoutPoint(inner_x, map_y, MM))
            map_item.attemptResize(QgsLayoutSize(inner_w, map_h, MM))
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
            card_layers = {item.get("layer", "") for item in panel_leg_items}
            single_layer = len(card_layers) == 1
            tag = panel_label(panel, i) if getattr(panel, "export_title", None) else f"P{i + 1}"
            if panel_leg_items and not single_layer:
                swatches_txt = ", ".join(item["label"] for item in panel_leg_items[:3])
                panel_summary_parts.append(f"{tag}: {swatches_txt}")
            elif panel_leg_items:
                panel_summary_parts.append(f"{tag}: {panel_leg_items[0].get('layer') or caption_text}")
            else:
                panel_summary_parts.append(f"{tag}: {caption_text.split('·')[-1].strip()}")

            # 3. Per-panel north arrow, legend card and scale bar inside the panel map
            if per_panel_overlays:
                inset = 1.2
                map_right = inner_x + inner_w
                map_bottom = map_y + map_h

                # 3a. North arrow (top right)
                if resolved_arrow_svg:
                    p_arrow_sz = 6.5 if is_dense_grid else 8.0
                    p_arrow = QgsLayoutItemPicture(layout)
                    p_arrow.setId(f"PanelNorthArrow_{i + 1}")
                    p_arrow.setPicturePath(resolved_arrow_svg)
                    p_arrow.setLinkedMap(map_item)
                    p_arrow.setBackgroundEnabled(True)
                    p_arrow.setBackgroundColor(QColor(255, 255, 255, 220))
                    p_arrow.setFrameEnabled(True)
                    p_arrow.attemptMove(QgsLayoutPoint(map_right - p_arrow_sz - inset, map_y + inset, MM))
                    p_arrow.attemptResize(QgsLayoutSize(p_arrow_sz, p_arrow_sz, MM))
                    layout.addLayoutItem(p_arrow)

                # 3b. Scale bar (bottom right), placed by its real size
                p_sb = QgsLayoutItemScaleBar(layout)
                p_sb.setId(f"PanelScaleBar_{i + 1}")
                configure_scalebar_for_map(p_sb, map_item, style_name=scalebar_style, target_segments=2, compact=True)
                p_sb.setBackgroundEnabled(True)
                p_sb.setBackgroundColor(QColor(255, 255, 255, 220))
                layout.addLayoutItem(p_sb)
                sb_w = min(inner_w * 0.42, 26.0 if is_dense_grid else 34.0)
                sb_h = 7.5 if is_dense_grid else 9.0
                sb_rect = self._place_scalebar(p_sb, map_right, map_bottom, sb_w, sb_h, inset)
                if sb_rect.width() > inner_w * 0.5:
                    p_sb.setNumberOfSegments(1)
                    sb_rect = self._place_scalebar(p_sb, map_right, map_bottom, sb_w, sb_h, inset)

                # 3c. Legend card (bottom left), never under the scale bar
                # One layer per card: its name is in the panel header, so show the classes alone.
                leg_rows = [
                    [f"▪ {_without_layer_prefix(entry) if single_layer else entry['label']}"]
                    for entry in panel_leg_items[: (3 if is_dense_grid else 4)]
                ]
                if not leg_rows:
                    leg_rows = [[f"▪ {caption_text.split('·')[-1].strip()}"]]
                p_leg_label = QgsLayoutItemLabel(layout)
                p_leg_label.setId(f"PanelLegend_{i + 1}")
                p_leg_label.setHAlign(align_left)
                p_leg_label.setVAlign(align_vcenter)
                p_leg_label.setMarginX(1.2)
                p_leg_label.setMarginY(0.8)
                p_leg_label.setBackgroundEnabled(True)
                p_leg_label.setBackgroundColor(QColor(255, 255, 255, 225))
                p_leg_label.setFrameEnabled(True)
                room_w = inner_w - sb_rect.width() - 3.0 * inset
                leg_box_w = max(8.0, min(inner_w * 0.52, 34.0 if is_dense_grid else 44.0, room_w))
                leg_box_h = min(map_h * 0.36, 3.2 + len(leg_rows) * (2.6 if is_dense_grid else 3.2))
                layout.addLayoutItem(p_leg_label)
                p_leg_label.attemptMove(QgsLayoutPoint(inner_x + inset, map_bottom - leg_box_h - inset, MM))
                p_leg_label.attemptResize(QgsLayoutSize(leg_box_w, leg_box_h, MM))
                fit_label_rows(
                    p_leg_label, leg_rows, leg_box_w, leg_box_h,
                    5.8 if is_dense_grid else 6.8, 4.5, bold=True,
                )

        # 4. Footer: north arrow, legend, panel summary and scale bar
        if include_footer:
            footer_y = h - footer_h - 2.0
            footer_box_h = footer_h - 3.0

            # 4a. North arrow
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
                picture.attemptMove(QgsLayoutPoint(margin_side, footer_y + 2.0, MM))
                picture.attemptResize(QgsLayoutSize(arrow_w, arrow_h, MM))
                layout.addLayoutItem(picture)

            # 4b. Legend with every layer shown in a panel
            legend_x = margin_side + arrow_w + 3.0
            scalebar_reserve_w = 62.0
            legend_avail_w = max(50.0, avail_w - arrow_w - scalebar_reserve_w - 6.0)

            legend_item = QgsLayoutItemLegend(layout)
            legend_item.setId("MasterLegend")
            legend_item.setTitle("Legend")
            if map_items:
                legend_item.setLinkedMap(map_items[0])
            if hasattr(legend_item, "setSyncMode"):
                legend_item.setSyncMode(Qgis.LegendSyncMode.Manual)
            else:
                legend_item.setAutoUpdateModel(False)

            root_group = legend_item.model().rootGroup()
            root_group.clear()
            for lyr in unique_layers:
                if project.mapLayer(lyr.id()) is None:
                    project.addMapLayer(lyr, False)
                root_group.addLayer(lyr)

            legend_item.setEqualColumnWidth(False)
            legend_item.setBackgroundEnabled(True)
            legend_item.setBackgroundColor(QColor(255, 255, 255, 235))
            legend_item.setFrameEnabled(True)
            legend_w = legend_avail_w * (0.55 if panel_summary_parts else 1.0)
            layout.addLayoutItem(legend_item)
            legend_item.attemptMove(QgsLayoutPoint(legend_x, footer_y + 1.0, MM))
            fit_legend(legend_item, legend_w, footer_box_h)

            # Panel-by-panel summary next to the legend (names every panel's content)
            if panel_summary_parts:
                summary_x = legend_x + legend_w + 2.0
                summary_w = max(25.0, legend_avail_w - legend_w - 2.0)
                summary_label = QgsLayoutItemLabel(layout)
                summary_label.setId("MasterPanelLegendSummary")
                per_row = 1 if len(panel_summary_parts) <= 4 else 2
                summary_rows = [
                    panel_summary_parts[k:k + per_row] for k in range(0, len(panel_summary_parts), per_row)
                ]
                summary_label.setHAlign(align_left)
                summary_label.setVAlign(align_vcenter)
                summary_label.setMarginX(1.5)
                summary_label.setMarginY(1.0)
                summary_label.setBackgroundEnabled(True)
                summary_label.setBackgroundColor(QColor("#f8fafc"))
                summary_label.setFrameEnabled(True)
                layout.addLayoutItem(summary_label)
                summary_label.attemptMove(QgsLayoutPoint(summary_x, footer_y + 1.0, MM))
                summary_label.attemptResize(QgsLayoutSize(summary_w, footer_box_h, MM))
                fit_label_rows(summary_label, summary_rows, summary_w, footer_box_h, 6.2, 4.5)

            # 4c. Scale bar linked to panel 1
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
                layout.addLayoutItem(scalebar)
                self._place_scalebar(
                    scalebar, w - margin_side, footer_y + 2.0 + 16.0, scalebar_reserve_w, 16.0, 0.0
                )

        return layout

    # ───────────────────────── Layout Manager / Layout Designer ─────────────────────────

    def add_layout_to_manager(self, **layout_options) -> QgsPrintLayout:
        """Build the layout and add it to the project's Layout Manager under a unique name."""
        layout = self.build_qgis_print_layout(**layout_options)
        base_name = layout_options.get("title") or self.title_input.text().strip() or DEFAULT_LAYOUT_TITLE
        add_layout_to_project(layout, base_name, layout.project())
        return layout

    def show_in_designer(self, layout: QgsPrintLayout):
        """Open ``layout`` in the QGIS Layout Designer (when running inside QGIS)."""
        if self.iface is not None and hasattr(self.iface, "openLayoutDesigner"):
            return self.iface.openLayoutDesigner(layout)
        return None

    def _on_open_in_designer(self) -> None:
        try:
            layout = self.add_layout_to_manager()
        except Exception as e:  # noqa: BLE001 (shown to the user)
            QMessageBox.warning(
                self,
                "Open in Layout Designer",
                "The layout could not be added to the project's Layout Manager.\n\n"
                f"{e}\n\nSave the project and try again, or export the layout to a file instead.",
            )
            return
        # Close this (modal) dialog first so the designer window can be used at once.
        self.accept()
        self.show_in_designer(layout)

    # ───────────────────────── Export ─────────────────────────

    def execute_export(self, open_after: bool = False) -> None:
        """Constructs layout or dashboard depending on active tab."""
        if self.tab_widget.currentIndex() == 1:
            title = self.html_title_input.text()
            export_path = self._normalized_html_path(self.html_path_input.text())
            if not export_path:
                QMessageBox.warning(self, "Export", "Choose where to save the HTML file (Browse…).")
                return
            output_directory = os.path.dirname(export_path)
            if not os.path.isdir(output_directory):
                QMessageBox.warning(
                    self, "Export", "The folder does not exist. Choose an existing folder with Browse…."
                )
                return
            self.html_path_input.setText(export_path)
            try:
                self.export_to_html(export_path, title, open_after=open_after)
            except OSError as e:
                QMessageBox.critical(
                    self,
                    "Export",
                    f"The HTML dashboard was not saved.\n\n{e}\n\n"
                    "Check that the folder can be written to, then export again.",
                )
            except Exception as e:  # noqa: BLE001 (shown to the user)
                QMessageBox.critical(
                    self,
                    "Export",
                    f"The HTML dashboard was not saved: the panels could not be prepared.\n\n{e}",
                )
            return

        export_path = self._normalized_export_path(self.path_input.text())
        if not export_path:
            QMessageBox.warning(self, "Export", "Choose where to save the file (Browse…).")
            return
        output_directory = os.path.dirname(export_path)
        if not os.path.isdir(output_directory):
            QMessageBox.warning(
                self,
                "Export",
                "The folder does not exist. Choose an existing folder with Browse….",
            )
            return
        self.path_input.setText(export_path)

        try:
            layout = self.build_qgis_print_layout()
        except Exception as e:  # noqa: BLE001 (shown to the user)
            QMessageBox.critical(self, "Export", f"The print layout could not be built.\n\n{e}")
            return

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

            if result == QgsLayoutExporter.ExportResult.Success:
                QSettings().setValue(
                    "zero2multimap/last_export_dir",
                    os.path.dirname(export_path),
                )
                if open_after:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(export_path))
                else:
                    QMessageBox.information(self, "Export", f"The layout was saved to:\n{export_path}")
                self.accept()
            else:
                QMessageBox.critical(self, "Export", export_failure_message(result, export_path))
        except OSError as e:
            QMessageBox.critical(
                self,
                "Export",
                f"The print layout was not saved.\n\n{e}\n\nChoose a folder you can save to, then export again.",
            )
        except Exception as e:  # noqa: BLE001 (shown to the user)
            QMessageBox.critical(
                self,
                "Export",
                f"The print layout was not saved: the page could not be drawn.\n\n{e}\n\n"
                "Try another format, such as PNG instead of PDF.",
            )

    @staticmethod
    def _write_only_mode():
        """Return QIODevice's write-only flag on both Qt 5 and Qt 6."""
        return QIODevice.OpenModeFlag.WriteOnly

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
        # The extent the image really shows: QGIS widens the requested extent to
        # the image's aspect ratio, and the browser maps positions onto this one.
        visible_extent = settings.visibleExtent()

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

        # In-memory steps: a failure here is not a file problem (RuntimeError, not OSError).
        image_bytes = QByteArray()
        buffer = QBuffer(image_bytes)
        if not buffer.open(self._write_only_mode()):
            raise RuntimeError(f"{panel_label(panel, index)}: the image buffer could not be opened.")
        if not image.save(buffer, "PNG"):
            buffer.close()
            raise RuntimeError(f"{panel_label(panel, index)}: the image could not be encoded as PNG.")
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

        label = panel_label(panel, index)
        if panel.mode == "layer":
            selected_layer = panel.selected_layer()
            layer_name = selected_layer.name() if selected_layer else "No layer selected"
            panel_title = f"{label} · {layer_name}"
            detail = "One-layer comparison"
        elif panel.mode == "theme":
            theme_name = panel.theme_combo.currentText() or "No theme selected"
            panel_title = f"{label} · {theme_name}"
            detail = "Map theme"
        else:
            panel_title = f"{label} · Main map"
            detail = "Follows QGIS canvas"

        detail_tooltip = "Layers: " + " | ".join(layer_details) if layer_details else detail

        destination_crs = panel.canvas.mapSettings().destinationCrs()
        crs_label = destination_crs.authid() or destination_crs.description()
        scale_val = int(panel.canvas.scale())

        extent_wgs84 = [
            visible_extent.xMinimum(), visible_extent.yMinimum(),
            visible_extent.xMaximum(), visible_extent.yMaximum(),
        ]
        with suppress(Exception):
            wgs84_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            if destination_crs.isValid() and destination_crs != wgs84_crs:
                transform = QgsCoordinateTransform(destination_crs, wgs84_crs, QgsProject.instance())
                rect_wgs84 = transform.transformBoundingBox(visible_extent)
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
            "image_size": [image.width(), image.height()],
            "extent": [
                visible_extent.xMinimum(),
                visible_extent.yMinimum(),
                visible_extent.xMaximum(),
                visible_extent.yMaximum(),
            ],
            "extent_wgs84": extent_wgs84,
            "ground_width_m": _measure_extent_ground_width_m(visible_extent, destination_crs),
            "lonlat_grid": lonlat_grid(visible_extent, destination_crs),
            "map_unit_m": map_unit_metres(destination_crs),
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
        panels, rows, cols = export_panels(self.parent_dialog)
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
                rows=rows,
                cols=cols,
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
                "Export",
                f"The HTML dashboard was saved to:\n{export_path}\n\n"
                "It works offline: open it in any web browser.",
            )
        self.accept()
