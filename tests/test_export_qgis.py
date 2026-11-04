"""Value-level checks for 02Multimap's exports, on a real QGIS runtime.

Covers the 1.3.0 export fixes:

* the footer legend's text sizes are set on the legend itself (``rstyle``);
* every label in the print layout fits its box (``sizeForText``) on A4, A3 and
  Letter, landscape and portrait, 2 to 8 panels, with long layer names;
* "Open in Layout Designer" adds the layout to the project's Layout Manager
  under a unique name and opens it;
* the HTML dashboard's coordinate readout, distance measuring and scale bar
  are right when the panel image is letterboxed (``object-fit: contain``):
  the exported JavaScript runs in ``QJSEngine`` against a minimal DOM;
* the Export dialog is readable (text contrast >= 4.5:1) with the system
  theme, a dark palette and the Night Mapping theme, and shows "&";
* a failed export explains itself instead of printing an exporter code.

Run:  python-qgis-ltr.bat tests/test_export_qgis.py   (and python-qgis.bat)
"""
from __future__ import annotations

import base64
import importlib
import json
import math
import os
import re
import struct
import sys
import tempfile
import traceback

if sys.platform == "win32":
    # The offscreen platform has no font engine on Windows: labels would measure
    # zero and every "fits its box" check would pass vacuously.
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
if not os.environ.get("QGIS_CUSTOM_CONFIG_PATH"):
    os.environ["QGIS_CUSTOM_CONFIG_PATH"] = tempfile.mkdtemp(prefix="z2mm_export_cfg_")

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsDistanceArea,
    QgsFeature,
    QgsGeometry,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutUtils,
    QgsLegendRenderer,
    QgsLegendSettings,
    QgsLegendStyle,
    QgsMapSettings,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsRendererCategory,
    QgsSymbol,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSize, Qt, PYQT_VERSION_STR  # noqa: E402
from qgis.PyQt.QtGui import QColor, QPalette  # noqa: E402
from qgis.PyQt.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabBar,
    QWidget,
)

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_ROOT = os.path.dirname(PLUGIN_DIR)
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

RESULTS = []
MESSAGES = []
X0, Y0 = 2900000.0, 5009377.085697312  # Web Mercator, about 41 deg N
WEB_MERCATOR = "EPSG:3857"
_RETAINED = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if detail and not condition:
        print(f"        {detail}")
    return bool(condition)


def plugin_module(name):
    return importlib.import_module(f"zero2multimap.{name}")


def _capture_message(*args, **_kwargs):
    MESSAGES.append(tuple(str(a) for a in args[1:3]))
    return QMessageBox.StandardButton.Ok


QMessageBox.information = staticmethod(_capture_message)
QMessageBox.warning = staticmethod(_capture_message)
QMessageBox.critical = staticmethod(_capture_message)


# ───────────────────────────── fixtures ─────────────────────────────

def polygon_layer(name, x0, y0, cell, count, categories=None):
    layer = QgsVectorLayer(f"Polygon?crs={WEB_MERCATOR}&field=id:integer&field=cls:string", name, "memory")
    features = []
    for i in range(count):
        for j in range(count):
            feature = QgsFeature(layer.fields())
            cls = categories[(i + j) % len(categories)] if categories else "a"
            feature.setAttributes([i * count + j, cls])
            feature.setGeometry(QgsGeometry.fromRect(
                QgsRectangle(x0 + i * cell, y0 + j * cell, x0 + (i + 1) * cell, y0 + (j + 1) * cell)))
            features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    if categories:
        cats = []
        for k, value in enumerate(categories):
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            symbol.setColor(QColor(("#d7191c", "#fdae61", "#2b83ba", "#1a9641")[k % 4]))
            cats.append(QgsRendererCategory(value, symbol, value))
        layer.setRenderer(QgsCategorizedSymbolRenderer("cls", cats))
    QgsProject.instance().addMapLayer(layer)
    _RETAINED.append(layer)
    return layer


class FakePanel:
    """What the exporter reads from a 02Multimap panel, around a real QgsMapCanvas."""

    def __init__(self, layer, extent, size=(490, 296)):
        from qgis.gui import QgsMapCanvas

        self.canvas = QgsMapCanvas()
        self.canvas.resize(*size)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem(WEB_MERCATOR))
        self.canvas.setLayers([layer])
        self.canvas.setExtent(extent)
        self.mode = "layer"
        self._layer = layer
        self.layer_combo = QComboBox()
        self.theme_combo = QComboBox()

    def selected_layer(self):
        return self._layer


class FakeParent:
    def __init__(self, rows, cols, panels):
        self.rows, self.cols, self.panels = rows, cols, panels


class StubIface:
    def __init__(self):
        from qgis.gui import QgsMapCanvas

        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem(WEB_MERCATOR))
        self.window.setCentralWidget(self.canvas)
        self.opened = []

    def mainWindow(self):  # noqa: N802 (QGIS API)
        return self.window

    def mapCanvas(self):  # noqa: N802 (QGIS API)
        return self.canvas

    def openLayoutDesigner(self, layout):  # noqa: N802 (QGIS API)
        self.opened.append(layout)
        return None

    def addToolBarIcon(self, action):  # noqa: N802 (QGIS API)
        return None

    def removeToolBarIcon(self, action):  # noqa: N802 (QGIS API)
        return None


LONG_NAMES = [
    "Land use 2015 — municipal survey, revised edition",
    "Land use 2020 (provisional) with an unusually long descriptive layer name",
    "Flood hazard <T100> & 'storm surge'",
    "Population density per hectare",
    "Scenario B: compact growth with transit corridors",
    "Green space access",
    "Heat island index (summer nights)",
    "Building footprints",
]
CATEGORIES = ["Residential (low density)", "Commercial & mixed use", "Industrial / logistics"]


def make_panels(count, size=(490, 296)):
    extent = QgsRectangle(X0 - 100, Y0 - 100, X0 + 2100, Y0 + 2100)
    panels = []
    for i in range(count):
        layer = polygon_layer(LONG_NAMES[i % len(LONG_NAMES)], X0 + 40 * i, Y0, 250.0, 8,
                              CATEGORIES if i % 2 == 0 else None)
        panels.append(FakePanel(layer, extent, size))
    return panels


GRIDS = {2: (1, 2), 3: (1, 3), 4: (2, 2), 6: (2, 3), 8: (2, 4)}


# ───────────────────────────── print layout ─────────────────────────────

def legend_need(legend):
    """Size the legend content needs (mm), title included, measured independently."""
    settings = QgsLegendSettings(legend.legendSettings())
    settings.setTitle(legend.title())
    context = QgsLayoutUtils.createRenderContextForLayout(legend.layout(), None)
    return QgsLegendRenderer(legend.model(), settings).minimumSize(context)


def test_print_layout_labels_and_legend(iface):
    print_layout = plugin_module("print_layout")
    cases = [(n, page, orientation) for n in (2, 3, 4, 6, 8) for page in ("A4",)
             for orientation in ("Landscape", "Portrait")]
    cases += [(8, page, orientation) for page in ("A3", "Letter") for orientation in ("Landscape", "Portrait")]
    panels_by_count = {n: make_panels(n) for n in GRIDS}
    title = "Comparison of land use, flood hazard and density scenarios for the metropolitan area (draft 3)"

    overflow, vacuous, legend_overflow, outside_map, off_page, legend_sizes = [], [], [], [], [], set()
    labels_seen = 0
    for count, page_name, orientation in cases:
        rows, cols = GRIDS[count]
        dlg = print_layout.PrintLayoutDialog(FakeParent(rows, cols, panels_by_count[count]), iface)
        layout = dlg.build_qgis_print_layout(
            title=title, page_size_name=page_name, orientation=orientation, north_arrow_key="builtin",
            scalebar_style="Single Box", include_footer=True, per_panel_overlays=True, include_headers=True,
        )
        tag = f"{count} panels {page_name} {orientation}"
        items = {it.id(): it for it in layout.items() if hasattr(it, "id") and it.id()}
        page = layout.pageCollection().pages()[0].sceneBoundingRect()
        for item_id, item in items.items():
            if not page.contains(item.sceneBoundingRect()):
                off_page.append(f"{tag}: {item_id}")
            if isinstance(item, QgsLayoutItemLabel):
                labels_seen += 1
                need, have = item.sizeForText(), item.rect()
                if need.width() > have.width() + 0.01 or need.height() > have.height() + 0.01:
                    overflow.append(f"{tag}: {item_id} needs {need.width():.1f}x{need.height():.1f} mm, "
                                    f"box {have.width():.1f}x{have.height():.1f} mm")
                if item.text().strip() and need.width() - 2 * item.marginX() < 1.0:
                    vacuous.append(f"{tag}: {item_id} measured {need.width():.2f} mm")
            if isinstance(item, QgsLayoutItemLegend):
                need = legend_need(item)
                have = item.rect()
                if need.width() > have.width() + 0.01 or need.height() > have.height() + 0.01:
                    legend_overflow.append(f"{tag}: legend needs {need.width():.1f}x{need.height():.1f} mm, "
                                           f"box {have.width():.1f}x{have.height():.1f} mm")
                legend_sizes.add((item.style(QgsLegendStyle.Style.Title).textFormat().size(),
                                  item.style(QgsLegendStyle.Style.SymbolLabel).textFormat().size()))
            if item_id.startswith(("PanelScaleBar_", "PanelLegend_", "PanelNorthArrow_")):
                number = item_id.split("_")[1]
                panel_map = items.get(f"PanelMap_{number}")
                if panel_map is not None and not panel_map.sceneBoundingRect().contains(item.sceneBoundingRect()):
                    outside_map.append(f"{tag}: {item_id}")
        if count == 8 and page_name == "A4":
            summary = items.get("MasterPanelLegendSummary")
            check(f"{tag}: the footer summary names all 8 panels",
                  summary is not None and all(f"P{k}:" in summary.text() for k in range(1, 9)),
                  repr(summary.text() if summary else None))
        dlg.deleteLater()

    check(f"text is really measured (fonts available; {labels_seen} labels)", labels_seen > 100 and not vacuous,
          "; ".join(vacuous[:4]))
    check("every title, panel header, panel legend and footer summary label fits its box (sizeForText)",
          not overflow, f"{len(overflow)} overflow(s): " + "; ".join(overflow[:6]))
    check("the footer legend's content fits its box", not legend_overflow, "; ".join(legend_overflow[:4]))
    check("per-panel north arrows, legends and scale bars stay inside their panel map",
          not outside_map, "; ".join(outside_map[:6]))
    check("no layout item leaves the page", not off_page, "; ".join(off_page[:6]))
    check("legend text sizes are set on the legend itself: title 6-7.5 pt, entries 5-6.5 pt (was 16 pt)",
          bool(legend_sizes) and all(6.0 <= t <= 7.5 and 5.0 <= s <= 6.5 and abs(t - s - 1.0) < 1e-6
                                     for t, s in legend_sizes),
          f"(title, entry) sizes seen: {sorted(legend_sizes)}")


def test_open_in_layout_designer(iface):
    print_layout = plugin_module("print_layout")
    manager = QgsProject.instance().layoutManager()
    opener = getattr(print_layout, "open_in_layout_designer", None)
    if not check("print_layout exposes open_in_layout_designer(parent_dialog, iface, **options)", callable(opener)):
        return
    parent = FakeParent(2, 4, make_panels(8))
    before = len(manager.layouts())
    iface.opened.clear()
    first = opener(parent, iface, title="Flood scenarios")
    second = opener(parent, iface, title="Flood scenarios")
    names = sorted(layout.name() for layout in manager.layouts())
    check("two calls add two layouts to the project's Layout Manager",
          len(manager.layouts()) == before + 2, f"names={names}")
    check("the second layout gets a unique name",
          first.name() == "Flood scenarios" and second.name() == "Flood scenarios (2)",
          f"{first.name()!r}, {second.name()!r}")
    check("each layout is opened in the Layout Designer",
          [layout.name() for layout in iface.opened] == ["Flood scenarios", "Flood scenarios (2)"],
          f"opened={[layout.name() for layout in iface.opened]}")
    stored = manager.layoutByName("Flood scenarios (2)")
    map_count = sum(1 for it in stored.items() if hasattr(it, "id") and it.id().startswith("PanelMap_")) if stored else 0
    check("the stored layout holds all 8 panel maps", map_count == 8, f"maps={map_count}")

    dlg = print_layout.PrintLayoutDialog(parent, iface)
    button = getattr(dlg, "designer_btn", None)
    if check("the Export dialog has an 'Open in Layout Designer' button",
             isinstance(button, QPushButton) and "Layout Designer" in button.text()):
        dlg.title_input.setText("From the button")
        button.click()
        check("the button adds the layout and opens the designer",
              manager.layoutByName("From the button") is not None
              and iface.opened and iface.opened[-1].name() == "From the button",
              f"opened={[layout.name() for layout in iface.opened]}")


def test_failed_export_message(iface):
    print_layout = plugin_module("print_layout")
    with tempfile.TemporaryDirectory() as tmp:
        blocked = os.path.join(tmp, "layout.png")
        os.makedirs(blocked)  # a folder where the image should go: the exporter cannot write it
        dlg = print_layout.PrintLayoutDialog(FakeParent(1, 2, make_panels(2)), iface)
        dlg.format_combo.setCurrentIndex(dlg.format_combo.findData(".png"))
        dlg.path_input.setText(blocked)
        MESSAGES.clear()
        dlg.execute_export(open_after=False)
        text = " ".join(" ".join(m) for m in MESSAGES)
        check("a failed export does not print an exporter code", MESSAGES and "code" not in text.lower(), text)
        check("a failed export names the file and says what to do next",
              "layout.png" in text and ("close it" in text or "choose a folder" in text), text)


# ───────────────────────────── HTML dashboard ─────────────────────────────

FAKE_DOM = r"""
function FakeElement(tag, classes, id) {
    this.tagName = tag; this.classes = classes || []; this.id = id || "";
    this.children = []; this.style = {}; this.textContent = ""; this.attrs = {};
    this.listeners = {}; this.clientWidth = 0; this.clientHeight = 0;
    this.naturalWidth = 0; this.naturalHeight = 0;
    var self = this;
    this.classList = {
        add: function (c) { if (self.classes.indexOf(c) < 0) self.classes.push(c); },
        remove: function (c) { var i = self.classes.indexOf(c); if (i >= 0) self.classes.splice(i, 1); },
        toggle: function (c, on) {
            if (on === undefined) on = self.classes.indexOf(c) < 0;
            if (on) this.add(c); else this.remove(c);
            return on;
        },
        contains: function (c) { return self.classes.indexOf(c) >= 0; }
    };
}
FakeElement.prototype.addEventListener = function (type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
};
FakeElement.prototype.dispatch = function (type, ev) {
    ev = ev || {}; ev.target = this; ev.preventDefault = function () {};
    (this.listeners[type] || []).forEach(function (fn) { fn(ev); });
};
FakeElement.prototype.appendChild = function (c) { this.children.push(c); return c; };
FakeElement.prototype.insertBefore = function (c, ref) {
    var i = this.children.indexOf(ref);
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
    return c;
};
Object.defineProperty(FakeElement.prototype, "firstChild", {
    get: function () { return this.children.length ? this.children[0] : null; }
});
Object.defineProperty(FakeElement.prototype, "innerHTML", {
    get: function () { return ""; }, set: function (v) { this.children = []; }
});
FakeElement.prototype.setAttribute = function (k, v) {
    this.attrs[k] = String(v);
    if (k === "class") this.classes = String(v).split(/\s+/);
};
FakeElement.prototype.getAttribute = function (k) { return this.attrs.hasOwnProperty(k) ? this.attrs[k] : null; };
FakeElement.prototype.setPointerCapture = function () {};
FakeElement.prototype.getBoundingClientRect = function () {
    return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight,
             right: this.clientWidth, bottom: this.clientHeight };
};
FakeElement.prototype.matches = function (sel) {
    var m = /^([a-z]*)((?:\.[\w-]+)*)$/i.exec(sel);
    if (!m) return false;
    if (m[1] && this.tagName.toLowerCase() !== m[1].toLowerCase()) return false;
    var self = this;
    return m[2].split(".").filter(Boolean).every(function (c) { return self.classes.indexOf(c) >= 0; });
};
FakeElement.prototype.all = function () {
    var out = [];
    this.children.forEach(function (c) { out.push(c); out = out.concat(c.all()); });
    return out;
};
FakeElement.prototype.querySelector = function (sel) {
    var found = this.all().filter(function (e) { return e.matches(sel); });
    return found.length ? found[0] : null;
};
FakeElement.prototype.querySelectorAll = function (sel) {
    return this.all().filter(function (e) { return e.matches(sel); });
};

var fakeBody = new FakeElement("body");
var fakeGrid = fakeBody.appendChild(new FakeElement("main", ["grid"]));
var fakeIds = {};
["coordinates", "measure-readout", "zoom-readout", "theme-toggle", "measure-btn", "carto-btn",
 "presentation-btn", "zoom-in", "zoom-out", "reset-view", "help-text"].forEach(function (id) {
    fakeIds[id] = fakeBody.appendChild(new FakeElement("span", [], id));
});
for (var i = 0; i < FAKE_PANELS.length; i++) {
    var panel = fakeGrid.appendChild(new FakeElement("section", ["panel"]));
    panel.appendChild(new FakeElement("input", ["opacity-slider"]));
    var view = panel.appendChild(new FakeElement("div", ["map-view"]));
    view.clientWidth = FAKE_VIEW[0]; view.clientHeight = FAKE_VIEW[1];
    var img = view.appendChild(new FakeElement("img", ["map-image"]));
    img.naturalWidth = FAKE_PANELS[i][0]; img.naturalHeight = FAKE_PANELS[i][1];
    var hud = view.appendChild(new FakeElement("div", ["scale-bar-hud"]));
    hud.appendChild(new FakeElement("span", ["scale-bar-label"]));
    var segs = hud.appendChild(new FakeElement("div", ["scale-bar-segments"]));
    segs.appendChild(new FakeElement("span", ["scale-seg-dark"]));
    segs.appendChild(new FakeElement("span", ["scale-seg-light"]));
    view.appendChild(new FakeElement("span", ["laser"]));
}
var fakeHtml = new FakeElement("html");
fakeHtml.attrs["data-theme"] = "slate";
var document = {
    documentElement: fakeHtml,
    querySelectorAll: function (sel) { return fakeBody.querySelectorAll(sel); },
    querySelector: function (sel) { return fakeBody.querySelector(sel); },
    getElementById: function (id) { return fakeIds[id] || null; },
    createElementNS: function (ns, tag) { return new FakeElement(tag); }
};
var window = { addEventListener: function () {} };
function fakeView(k) { return fakeGrid.querySelectorAll(".map-view")[k]; }
"""


def js_engine():
    # qgis.PyQt has no QtQml shim, so import from the binding QGIS runs on.
    binding = "PyQt6" if PYQT_VERSION_STR.startswith("6") else "PyQt5"
    return importlib.import_module(binding + ".QtQml").QJSEngine()


def png_size(data_uri):
    raw = base64.b64decode(data_uri.split(",", 1)[1][:64])
    return struct.unpack(">II", raw[16:24])


def distance_calculator(crs):
    calc = QgsDistanceArea()
    calc.setSourceCrs(crs, QgsProject.instance().transformContext())
    calc.setEllipsoid("WGS84")
    return calc


def parse_metres(text):
    match = re.search(r"([\d.]+)\s*(km|m)\b", text or "")
    if not match:
        return None
    return float(match.group(1)) * (1000.0 if match.group(2) == "km" else 1.0)


def test_html_letterboxed_measuring(iface):
    """8 panels in a 2x4 grid; each 980x592 image letterboxed in a 390x380 view."""
    print_layout = plugin_module("print_layout")
    panels = make_panels(8, size=(490, 296))
    parent = FakeParent(2, 4, panels)
    dlg = print_layout.PrintLayoutDialog(parent, iface)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "dashboard.html")
        dlg.export_to_html(path, "Letterbox test", open_after=False)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()

    visible_text = re.sub(r'src="data:[^"]+"', "", text)
    check("the dashboard makes no area-measuring claim", not re.search(r"\barea\b", visible_text, re.I),
          str(re.findall(r".{30}\barea\b.{30}", visible_text, re.I)[:2]))
    script = re.search(r"<script>(.*?)</script>", text, re.S).group(1)
    images = re.findall(r'src="(data:image/png;base64,[^"]+)"', text)
    sizes = [png_size(uri) for uri in images]
    view_w, view_h = 390.0, 380.0

    try:
        engine = js_engine()
    except ImportError as exc:
        check("QJSEngine is available to run the dashboard script", False, str(exc))
        return
    setup = f"var FAKE_PANELS = {json.dumps(sizes)}; var FAKE_VIEW = [{view_w}, {view_h}];\n" + FAKE_DOM
    result = engine.evaluate(setup)
    check("the minimal DOM loads", not result.isError(), result.toString())
    result = engine.evaluate(script)
    if not check("the exported dashboard script runs", not result.isError(), result.toString()):
        return

    k = 5  # panel 6
    panel = panels[k]
    iw, ih = sizes[k]
    crs = panel.canvas.mapSettings().destinationCrs()
    settings = QgsMapSettings()
    settings.setDestinationCrs(crs)
    settings.setExtent(panel.canvas.extent())
    settings.setOutputSize(QSize(iw, ih))
    shown = settings.visibleExtent()  # what the PNG really shows
    # CSS object-fit: contain -> the image fills a centred content rectangle.
    fit = min(view_w / iw, view_h / ih)
    cw, ch = iw * fit, ih * fit
    ox, oy = (view_w - cw) / 2.0, (view_h - ch) / 2.0
    check("the test view letterboxes the image (content height < 75 % of the view)", ch < 0.75 * view_h,
          f"content {cw:.1f}x{ch:.1f} in {view_w:.0f}x{view_h:.0f}")

    def to_view(x, y):
        u = (x - shown.xMinimum()) / shown.width()
        v = (shown.yMaximum() - y) / shown.height()
        return ox + u * cw, oy + v * ch

    a = QgsPointXY(shown.xMinimum() + 0.15 * shown.width(), shown.yMinimum() + 0.2 * shown.height())
    b = QgsPointXY(shown.xMinimum() + 0.85 * shown.width(), shown.yMinimum() + 0.9 * shown.height())
    ax, ay = to_view(a.x(), a.y())
    bx, by = to_view(b.x(), b.y())
    calc = distance_calculator(crs)
    true_m = calc.measureLine(a, b)

    engine.evaluate(f'fakeView({k}).dispatch("pointermove", {{clientX: {ax!r}, clientY: {ay!r}, pointerId: 1}});')
    readout = engine.evaluate('fakeIds["coordinates"].textContent').toString()
    numbers = [float(n) for n in re.findall(r"-?\d+\.\d+", readout)[:2]]
    tolerance = shown.width() * 1e-5
    check("cursor readout gives the true map coordinate in a letterboxed panel",
          len(numbers) == 2 and abs(numbers[0] - a.x()) <= tolerance and abs(numbers[1] - a.y()) <= tolerance,
          f"readout={readout!r} expected=({a.x():.2f}, {a.y():.2f})")

    engine.evaluate('fakeIds["measure-btn"].dispatch("click", {});')
    engine.evaluate(f'fakeView({k}).dispatch("click", {{clientX: {ax!r}, clientY: {ay!r}}});')
    engine.evaluate(f'fakeView({k}).dispatch("click", {{clientX: {bx!r}, clientY: {by!r}}});')
    measured_text = engine.evaluate('fakeIds["measure-readout"].textContent').toString()
    measured = parse_metres(measured_text)
    check(f"measured distance matches the ellipsoidal distance {true_m:,.1f} m within 0.2 %",
          measured is not None and abs(measured - true_m) / true_m < 0.002,
          f"readout={measured_text!r} true={true_m:,.2f} m")

    label = engine.evaluate(f'fakeView({k}).querySelector(".scale-bar-label").textContent').toString()
    dark = engine.evaluate(f'fakeView({k}).querySelector(".scale-seg-dark").style.width').toString()
    light = engine.evaluate(f'fakeView({k}).querySelector(".scale-seg-light").style.width').toString()
    bar_px = sum(float(re.sub(r"[^\d.]", "", w) or 0) for w in (dark, light))
    bar_m = parse_metres(label)
    mid_y = 0.5 * (shown.yMinimum() + shown.yMaximum())
    true_m_per_px = calc.measureLine(QgsPointXY(shown.xMinimum(), mid_y), QgsPointXY(shown.xMaximum(), mid_y)) / cw
    shown_m_per_px = (bar_m / bar_px) if bar_m and bar_px else float("nan")
    check("scale bar length matches the ground distance it labels within 0.5 %",
          math.isfinite(shown_m_per_px) and abs(shown_m_per_px - true_m_per_px) / true_m_per_px < 0.005,
          f"label={label!r} bar={bar_px:.2f}px -> {shown_m_per_px:.3f} m/px, true {true_m_per_px:.3f} m/px")

    data = json.loads(re.search(r"const panelData = (.*?);\n", text).group(1))
    check("panel data carries the image size and a WGS 84 grid",
          data[k].get("image_size") == [iw, ih] and (data[k].get("lonlat_grid") or {}).get("n", 0) >= 2,
          f"image_size={data[k].get('image_size')} grid={bool(data[k].get('lonlat_grid'))}")


# ───────────────────────────── Export dialog ─────────────────────────────

def _lum(color):
    def channel(value):
        value /= 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def _ratio(c1, c2):
    hi, lo = sorted((_lum(c1), _lum(c2)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def crop_contrast(image, rect):
    """Background = most common colour; text = the colour (3+ pixels) most different from it."""
    counts = {}
    for y in range(max(0, rect.top()), min(image.height(), rect.bottom() + 1)):
        for x in range(max(0, rect.left()), min(image.width(), rect.right() + 1)):
            rgb = image.pixel(x, y) & 0xFFFFFF
            counts[rgb] = counts.get(rgb, 0) + 1
    if not counts:
        return None
    background = QColor(max(counts, key=counts.get))
    candidates = [QColor(rgb) for rgb, n in counts.items() if n >= 3]
    if not candidates:
        return None
    text = max(candidates, key=lambda c: _ratio(c, background))
    return _ratio(text, background), background.name(), text.name()


def text_rects(dialog):
    """(description, rectangle in dialog coordinates) of every visible text on the current tab."""
    rects = []
    for widget in dialog.findChildren(QWidget):
        if not widget.isVisible() or widget.width() < 8 or widget.height() < 8:
            continue
        top_left = widget.mapTo(dialog, widget.rect().topLeft())
        rect = widget.rect().translated(top_left)
        if isinstance(widget, QCheckBox):
            rect = rect.adjusted(24, 1, -1, -1)
        elif isinstance(widget, QPushButton):
            rect = rect.adjusted(rect.width() // 5, rect.height() // 4, -rect.width() // 5, -rect.height() // 4)
        elif isinstance(widget, QComboBox):
            rect = rect.adjusted(4, 4, -rect.width() // 4, -4)
        elif isinstance(widget, QLineEdit):
            if not widget.text():
                continue
            rect = rect.adjusted(4, 4, -4, -4)
        elif isinstance(widget, QLabel):
            if not widget.text().strip():
                continue
            rect = rect.adjusted(3, 3, -3, -3)
        elif isinstance(widget, QTabBar):
            for index in range(widget.count()):
                tab = widget.tabRect(index).translated(top_left).adjusted(4, 4, -4, -4)
                rects.append((f"tab {widget.tabText(index)!r}", tab))
            continue
        else:
            continue
        text = widget.currentText() if isinstance(widget, QComboBox) else widget.text()
        rects.append((f"{type(widget).__name__} {str(text)[:40]!r}", rect))
    return rects


LIGHT_PLUGIN_QSS = (
    "QDialog { background-color: #fbfbfd; color: #2c3e46; }"
    "QLabel { color: #2c3e46; font-size: 12px; }"
    "QCheckBox { color: #2c3e46; } QComboBox { background-color: #ffffff; color: #16323f; }"
)


def plugin_dialog(iface):
    """The real 02Multimap dialog when it can be built here, else a stand-in with its light style sheet."""
    try:
        from zero2multimap.dialog import MultiMapDialog

        return MultiMapDialog(iface, iface.mainWindow()), "MultiMapDialog"
    except Exception as exc:  # noqa: BLE001 (stand-in keeps the check meaningful)
        stand_in = QDialog(iface.mainWindow())
        stand_in.setStyleSheet(LIGHT_PLUGIN_QSS)
        stand_in.panels, stand_in.rows, stand_in.cols = [], 1, 1
        return stand_in, f"stand-in ({type(exc).__name__})"


def dark_palette():
    palette = QPalette()
    roles = QPalette.ColorRole
    for role, color in (
        (roles.Window, "#1e1e1e"), (roles.WindowText, "#ffffff"), (roles.Base, "#2b2b2b"),
        (roles.AlternateBase, "#323232"), (roles.Text, "#ffffff"), (roles.Button, "#2d2d2d"),
        (roles.ButtonText, "#ffffff"), (roles.ToolTipBase, "#2b2b2b"), (roles.ToolTipText, "#ffffff"),
        (roles.Highlight, "#0078d4"), (roles.HighlightedText, "#ffffff"), (roles.PlaceholderText, "#a0a0a0"),
    ):
        palette.setColor(role, QColor(color))
    return palette


def test_export_dialog_readable(iface):
    print_layout = plugin_module("print_layout")
    app = QApplication.instance()
    original_palette = QPalette(app.palette())
    original_style = app.style().objectName()
    scenarios = ["system theme", "dark palette (QGIS 4 dark mode)", "Night Mapping"]
    for scenario in scenarios:
        if scenario.startswith("dark"):
            app.setStyle("Fusion")
            app.setPalette(dark_palette())
        elif scenario == "Night Mapping":
            app.setStyle(original_style)
            app.setPalette(original_palette)
            if "Night Mapping" not in QgsApplication.uiThemes():
                check("Night Mapping theme available", False, str(list(QgsApplication.uiThemes())))
                continue
            QgsApplication.setUITheme("Night Mapping")
        parent, kind = plugin_dialog(iface)
        dlg = print_layout.PrintLayoutDialog(parent, iface)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dlg.resize(640, 560)
        dlg.show()
        worst = []
        for tab in (0, 1):
            dlg.tab_widget.setCurrentIndex(tab)
            for _ in range(5):
                app.processEvents()
            image = dlg.grab().toImage()
            for name, rect in text_rects(dlg):
                measured = crop_contrast(image, rect)
                if measured is None:
                    continue
                ratio, background, foreground = measured
                if ratio < 4.5:
                    worst.append(f"{name} {foreground} on {background} = {ratio:.2f}:1")
        inherited = parent.styleSheet() and dlg.parentWidget() is parent
        check(f"{scenario}: every Export dialog text reaches 4.5:1 contrast (parent: {kind})",
              not worst and not inherited, "; ".join(worst[:6]) + (" [inherits the plugin style sheet]"
                                                                   if inherited else ""))
        dlg.close()
        dlg.deleteLater()
        parent.close()
        parent.deleteLater()
        app.processEvents()
    QgsApplication.setUITheme("default")
    app.setStyle(original_style)
    app.setPalette(original_palette)


def test_ampersands_and_copy(iface):
    print_layout = plugin_module("print_layout")
    dlg = print_layout.PrintLayoutDialog(FakeParent(1, 2, make_panels(2)), iface)
    texts = [w.text() for w in dlg.findChildren(QAbstractButton)]
    texts += [dlg.tab_widget.tabText(i) for i in range(dlg.tab_widget.count())]
    lone = [t for t in texts if re.search(r"(?<!&)&(?!&)", t.replace("&&", ""))]
    check("no button, check box or tab swallows an '&' as a shortcut marker", not lone, str(lone))
    shown = [t.replace("&&", "&") for t in texts]
    check("'Export & Open' and '... legend & scale bar' show the ampersand",
          any(t.startswith("Export & Open") for t in shown) and any("legend & scale bar" in t for t in shown),
          str(shown))
    info = dlg.html_info.text() if hasattr(dlg, "html_info") else " ".join(
        w.text() for w in dlg.findChildren(QLabel))
    check("the Export dialog does not claim area measuring", "area" not in info.lower(), info)


def run():
    print(f"02Multimap - exports (QGIS runtime, PyQt {PYQT_VERSION_STR})")
    print()
    iface = StubIface()
    for test in (
        test_print_layout_labels_and_legend,
        test_open_in_layout_designer,
        test_failed_export_message,
        test_html_letterboxed_measuring,
        test_ampersands_and_copy,
        test_export_dialog_readable,
    ):
        print(f"[{test.__name__}]")
        try:
            test(iface)
        except Exception:  # noqa: BLE001 (report and continue with the next test)
            traceback.print_exc()
            check(f"{test.__name__} ran to completion", False)
    print()
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    app = QgsApplication([], True)
    app.initQgis()
    code = 1
    try:
        code = run()
    except Exception:  # noqa: BLE001 (any crash is a failed run)
        traceback.print_exc()
        print("FAIL")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
