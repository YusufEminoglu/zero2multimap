"""Behaviour checks for the 02Multimap workspace dialog, on a real QGIS runtime.

Every check asserts a value a user would see or feel: a scale, a centre, a
signal count, a render count, a chip's text, a message, a pixel colour. The
dialog is driven exactly as the plugin drives it, against a stand-in main
window that holds a real QgsMapCanvas. No network access: every layer is a
memory layer or a GeoTIFF written to a temporary folder.

Run:  python-qgis-ltr.bat tests/test_dialog_behaviour_qgis.py
      python-qgis.bat     tests/test_dialog_behaviour_qgis.py
"""
from __future__ import annotations

import array
import os
import re
import sys
import tempfile
import time
import traceback
from collections import Counter

# A headless QgsApplication without a throwaway profile reads and writes the
# real user profile and can hang in QgsSettings (TRAPS 4.6).
os.environ.setdefault("QGIS_CUSTOM_CONFIG_PATH", tempfile.mkdtemp(prefix="mm_dialog_cfg_"))

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLayerTreeModel,
    QgsMapThemeCollection,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_ROOT = os.path.dirname(PLUGIN_DIR)
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)
PACKAGE = os.path.basename(PLUGIN_DIR)

RESULTS = []
MESSAGES = []
KEEP = []

X0, Y0 = 2900000.0, 5009377.085697312  # about 26 E, 41 N in EPSG:3857
R0 = QgsRectangle(X0, Y0, X0 + 10000.0, Y0 + 5600.0)
MERCATOR = QgsCoordinateReferenceSystem("EPSG:3857")
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition and detail:
        print(f"        {detail}")
    return bool(condition)


def skip(name, why):
    print(f"  [SKIP] {name}: {why}")


def close(actual, expected, rel_tolerance):
    if expected == 0:
        return abs(actual) <= rel_tolerance
    return abs(actual - expected) / abs(expected) <= rel_tolerance


def keep_off_screen(widget):
    """A window this suite shows is laid out, rendered and grabbed, but never put on the desktop."""
    from qgis.PyQt.QtCore import Qt

    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.move(-32000, -32000)


def keep_dialogs_off_screen():
    """Every dialog the plugin creates (also inside show_dialog / exec) stays off the desktop."""
    for module_name, class_name in (("dialog", "MultiMapDialog"), ("dialog", "QuickGuideDialog"),
                                    ("print_layout", "PrintLayoutDialog")):
        cls = getattr(__import__(f"{PACKAGE}.{module_name}", fromlist=[class_name]), class_name)
        original = cls.__init__

        def patched(self, *args, _original=original, **kwargs):
            _original(self, *args, **kwargs)
            keep_off_screen(self)

        cls.__init__ = patched


def pump(ms=60):
    from qgis.PyQt.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def flush_deletes():
    from qgis.PyQt.QtCore import QCoreApplication, QEvent

    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value
                                          if hasattr(QEvent.Type.DeferredDelete, "value")
                                          else int(QEvent.Type.DeferredDelete))
        pump(20)


def settle(dialog, ms=150):
    pump(ms)
    for canvas in canvases_of(dialog):
        canvas.waitWhileRendering()
    pump(30)


def canvases_of(dialog):
    canvases = [panel.canvas for panel in getattr(dialog, "panels", [])]
    swipe = getattr(dialog, "swipe_panel", None)
    if swipe is not None:
        canvases.append(swipe.canvas())
    return canvases


# ───────────────────────────── test data ─────────────────────────────


def polygon_layer(name, crs, geometries, fields="id:integer&field=name:string", add=True):
    layer = QgsVectorLayer(f"Polygon?crs={crs}&field={fields}", name, "memory")
    features = []
    for index, geometry in enumerate(geometries):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([index + 1, f"f{index + 1}"][: len(layer.fields())])
        feature.setGeometry(geometry if isinstance(geometry, QgsGeometry) else QgsGeometry.fromRect(geometry))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    KEEP.append(layer)
    if add:
        QgsProject.instance().addMapLayer(layer)
    return layer


def point_layer(name, crs, points, add=True):
    layer = QgsVectorLayer(f"Point?crs={crs}&field=id:integer", name, "memory")
    features = []
    for index, (x, y) in enumerate(points):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([index + 1])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    KEEP.append(layer)
    if add:
        QgsProject.instance().addMapLayer(layer)
    return layer


def raster_layer(name, extent, value=1.0, add=True):
    from osgeo import gdal, osr

    gdal.UseExceptions()
    folder = tempfile.mkdtemp(prefix="mm_dialog_raster_")
    path = os.path.join(folder, f"{name}.tif")
    dataset = gdal.GetDriverByName("GTiff").Create(path, 20, 20, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((extent.xMinimum(), extent.width() / 20.0, 0.0,
                             extent.yMaximum(), 0.0, -extent.height() / 20.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    dataset.SetProjection(srs.ExportToWkt())
    dataset.GetRasterBand(1).WriteRaster(0, 0, 20, 20, array.array("f", [value] * 400).tobytes())
    dataset.FlushCache()
    dataset = None
    layer = QgsRasterLayer(path, name, "gdal")
    KEEP.append(layer)
    if add:
        QgsProject.instance().addMapLayer(layer)
    return layer


def solid(layer, color):
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"color": color, "outline_style": "no"})))


def grid_rects(x0, y0, cell, n):
    return [QgsRectangle(x0 + i * cell, y0 + j * cell, x0 + (i + 1) * cell, y0 + (j + 1) * cell)
            for i in range(n) for j in range(n)]


class StubIface:
    """Just enough of QgisInterface: a real main canvas, a toolbar and a Plugins menu."""

    def __init__(self):
        from qgis.gui import QgsMapCanvas
        from qgis.PyQt.QtWidgets import QMainWindow

        self.mw = QMainWindow()
        self.canvas = QgsMapCanvas(self.mw)
        self.mw.setCentralWidget(self.canvas)
        self.mw.resize(900, 600)
        self.canvas.setDestinationCrs(MERCATOR)
        keep_off_screen(self.mw)
        self.mw.show()
        self.toolbar = []
        self.plugin_menu = []

    def mapCanvas(self):
        return self.canvas

    def mainWindow(self):
        return self.mw

    def addToolBarIcon(self, action):
        self.toolbar.append(action)

    def removeToolBarIcon(self, action):
        if action in self.toolbar:
            self.toolbar.remove(action)

    def addPluginToMenu(self, name, action):
        self.plugin_menu.append((name, action))

    def removePluginMenu(self, name, action):
        self.plugin_menu = [(n, a) for n, a in self.plugin_menu if not (n == name and a is action)]


def new_dialog(iface, layout=None, show=True):
    module = __import__(f"{PACKAGE}.dialog", fromlist=["MultiMapDialog"])
    dialog = module.MultiMapDialog(iface, iface.mainWindow())
    if layout:
        dialog.grid_combo.setCurrentText(layout)
    if show:
        dialog.show()
        settle(dialog, 250)
    return dialog


def dispose(dialog):
    cleanup = getattr(dialog, "cleanup", None)
    if callable(cleanup):
        cleanup()
    dialog.hide()
    dialog.setParent(None)
    dialog.deleteLater()
    flush_deletes()


def scales(dialog):
    return [panel.canvas.scale() for panel in dialog.panels]


def main_reset(iface, extent=R0):
    canvas = iface.canvas
    canvas.setRotation(0.0)
    if canvas.mapSettings().destinationCrs() != MERCATOR:
        canvas.setDestinationCrs(MERCATOR)
    canvas.setExtent(extent)
    pump(60)


# ───────────────────────────── A. sync ─────────────────────────────


def test_a_sync_holds(iface):
    print("\nA. Sync that holds")
    main = iface.canvas
    main_reset(iface)
    dialog = new_dialog(iface)
    try:
        main_scale = main.scale()
        start = scales(dialog)
        check("A1 panels open at the main map's scale (within 1 %)",
              all(close(s, main_scale, 0.01) for s in start), f"main={main_scale:,.0f} panels={start}")

        for _round in range(5):
            panel = dialog.panels[0]
            c = panel.canvas.center()
            panel.canvas.setCenter(QgsPointXY(c.x() + 100.0, c.y()))
            pump(40)
            c = main.center()
            main.setCenter(QgsPointXY(c.x() - 100.0, c.y()))
            pump(40)
        after = scales(dialog)
        check("A1 five panel/main ping-pong rounds keep every panel scale within 1 %",
              all(close(a, b, 0.01) for a, b in zip(after, start)), f"before={start} after={after}")
        check("A1 ... and the main map scale within 1 %", close(main.scale(), main_scale, 0.01),
              f"before={main_scale:,.0f} after={main.scale():,.0f}")

        before_main = (main.scale(), main.center().x(), main.center().y())
        before_panels = scales(dialog)
        sizes = ((1250, 700), (1100, 620), (1180, 660), (1300, 720))
        for width, height in sizes:
            dialog.resize(width, height)
            pump(150)
        after_panels = scales(dialog)
        check("A2 four dialog resizes keep every panel scale within 1 %",
              all(close(a, b, 0.01) for a, b in zip(after_panels, before_panels)),
              f"before={before_panels} after={after_panels}")
        check("A2 four dialog resizes leave the main map where it was",
              close(main.scale(), before_main[0], 0.001)
              and abs(main.center().x() - before_main[1]) < 1.0
              and abs(main.center().y() - before_main[2]) < 1.0,
              f"main scale {before_main[0]:,.0f}->{main.scale():,.0f}")

        from qgis.PyQt.QtWidgets import QSplitter

        splitters = [s for s in dialog.findChildren(QSplitter) if s.count() >= 2 and s.isVisible()]
        if splitters:
            sizes_now = splitters[-1].sizes()
            splitters[-1].setSizes([int(sizes_now[0] * 1.4)] + [max(60, int(x * 0.7)) for x in sizes_now[1:]])
            pump(150)
        check("A2 a splitter drag moves neither the panels' scale nor the main map",
              all(close(a, b, 0.01) for a, b in zip(scales(dialog), before_panels))
              and close(main.scale(), before_main[0], 0.001),
              f"panels={scales(dialog)} main={main.scale():,.0f}")

        panel_scale = dialog.panels[0].canvas.scale()
        dialog.grid_combo.setCurrentText("8 Panels (2x4)")
        settle(dialog, 250)
        eight = scales(dialog)
        dialog.grid_combo.setCurrentText("2 Panels (1x2)")
        settle(dialog, 250)
        two = scales(dialog)
        check("A3 a grid change keeps the scale (8 and then 2 panels, within 1 %)",
              all(close(s, panel_scale, 0.01) for s in eight + two),
              f"before={panel_scale:,.0f} eight={eight} two={two}")
        check("A3 a grid change leaves the main map where it was",
              close(main.scale(), before_main[0], 0.001), f"main={main.scale():,.0f}")

        dialog.grid_combo.setCurrentText("4 Panels (2x2)")
        settle(dialog, 200)
        main.setDestinationCrs(WGS84)
        to_wgs = QgsCoordinateTransform(MERCATOR, WGS84, QgsProject.instance())
        main.setExtent(to_wgs.transformBoundingBox(R0))
        pump(80)
        c = main.center()
        main.setCenter(QgsPointXY(c.x() + 0.001, c.y()))
        pump(80)
        panel = dialog.panels[0]
        panel_crs = panel.canvas.mapSettings().destinationCrs()
        # Compare in one CRS: a panel left in EPSG:3857 holding degree values
        # would otherwise "match" the main map numerically.
        pc = QgsCoordinateTransform(panel_crs, WGS84, QgsProject.instance()).transform(panel.canvas.center())
        mc = main.center()
        check("A4 panels follow the main map's CRS change", panel_crs.authid() == "EPSG:4326",
              f"panel CRS={panel_crs.authid()}")
        check("A4 panel centre = main centre after the CRS change (within 1e-6 deg)",
              abs(pc.x() - mc.x()) < 1e-6 and abs(pc.y() - mc.y()) < 1e-6,
              f"panel={pc.toString(6)} main={mc.toString(6)} (lon/lat)")
        check("A4 ... at the main map's scale (within 1 %)", close(panel.canvas.scale(), main.scale(), 0.01),
              f"panel={panel.canvas.scale():,.0f} main={main.scale():,.0f}")

        main_reset(iface)
        main.setRotation(30.0)
        pump(80)
        rotations = [p.canvas.rotation() for p in dialog.panels]
        check("A5 the main map's rotation is copied to every panel",
              all(abs(r - 30.0) < 1e-9 for r in rotations), f"rotations={rotations}")
        check("A5 a rotated sync keeps the scale (within 1 %)",
              all(close(s, main.scale(), 0.01) for s in scales(dialog)),
              f"main={main.scale():,.0f} panels={scales(dialog)}")
        main_reset(iface)

        counts = {"scale": 0}

        def _count(_value):
            counts["scale"] += 1

        main.scaleChanged.connect(_count)
        try:
            dialog.panels[1].canvas.zoomByFactor(0.5)
            pump(80)
        finally:
            main.scaleChanged.disconnect(_count)
        check("A6 exactly one scaleChanged on the main canvas after a panel zoom",
              counts["scale"] == 1, f"scaleChanged emitted {counts['scale']} times")
        check("A6 ... and the main map took the panel's scale (within 1 %)",
              close(main.scale(), dialog.panels[1].canvas.scale(), 0.01),
              f"main={main.scale():,.0f} panel={dialog.panels[1].canvas.scale():,.0f}")
    finally:
        dispose(dialog)
        main_reset(iface)


# ───────────────────────────── B. lifecycle ─────────────────────────────


def receiver_counts(iface):
    project = QgsProject.instance()
    main = iface.canvas
    signals = {
        "main.extentsChanged": (main, main.extentsChanged),
        "main.layersChanged": (main, main.layersChanged),
        "main.destinationCrsChanged": (main, main.destinationCrsChanged),
        "project.layersAdded": (project, project.layersAdded),
        "project.layersRemoved": (project, project.layersRemoved),
        "project.layerWasAdded": (project, project.layerWasAdded),
        "project.layerRemoved": (project, project.layerRemoved),
        "project.writeProject": (project, project.writeProject),
        "project.readProject": (project, project.readProject),
        "themes.mapThemesChanged": (project.mapThemeCollection(),
                                    project.mapThemeCollection().mapThemesChanged),
    }
    return {name: owner.receivers(signal) for name, (owner, signal) in signals.items()}


def test_b_lifecycle(iface):
    print("\nB. Lifecycle")
    from qgis.PyQt.QtWidgets import QDialog

    try:
        from qgis.PyQt.QtWidgets import QAction
    except ImportError:
        from qgis.PyQt.QtGui import QAction
    plugin_module = __import__(f"{PACKAGE}.main_plugin", fromlist=["O2MultiMapPlugin"])

    main_reset(iface)
    baseline = receiver_counts(iface)
    for _cycle in range(3):
        plugin = plugin_module.O2MultiMapPlugin(iface)
        plugin.initGui()
        plugin.show_dialog()
        pump(150)
        plugin.unload()
        pump(40)
    flush_deletes()
    after = receiver_counts(iface)
    changed = {k: (baseline[k], after[k]) for k in baseline if baseline[k] != after[k]}
    check("B1 signal receivers back to baseline after three load/unload cycles", not changed,
          f"changed: {changed}")
    dialogs = [w for w in iface.mw.findChildren(QDialog) if w.windowTitle().startswith("02Multimap")]
    actions = [a for a in iface.mw.findChildren(QAction) if a.text() == "02Multimap Workspace"]
    check("B1 unload removes the dialog, the action, the toolbar icon and the menu entry",
          not dialogs and not actions and not iface.toolbar and not iface.plugin_menu,
          f"dialogs={len(dialogs)} actions={len(actions)} toolbar={len(iface.toolbar)} menu={len(iface.plugin_menu)}")

    project = QgsProject.instance()

    def add_one_by_one(tag):
        """Seconds to add 300 layers one at a time (stops after 1 s), and how many were added."""
        layers = [polygon_layer(f"{tag} {i:03d}", "EPSG:3857", [R0], add=False) for i in range(300)]
        started = time.perf_counter()
        count = 0
        for layer in layers:
            project.addMapLayer(layer)
            count += 1
            if time.perf_counter() - started > 1.0:
                break
        return time.perf_counter() - started, count, layers[:count]

    # QGIS itself spends about 0.13 s on 300 one-by-one additions (layer tree,
    # registry); the criterion is what the open dialog adds on top of that.
    baseline, _count, plain = add_one_by_one("Plain")
    project.removeMapLayers([layer.id() for layer in plain])
    pump(200)

    dialog = new_dialog(iface)
    try:
        renders = {"n": 0}

        def _render():
            renders["n"] += 1

        for canvas in canvases_of(dialog):
            canvas.renderStarting.connect(_render)
        dialog.close()
        pump(120)
        renders["n"] = 0
        for _step in range(5):
            c = iface.canvas.center()
            iface.canvas.setCenter(QgsPointXY(c.x() + 200.0, c.y()))
            pump(100)
        iface.canvas.setLayers(list(iface.canvas.layers()))
        pump(150)
        check("B2 zero panel renders while the dialog is closed (5 main-map pans)",
              renders["n"] == 0, f"renders started: {renders['n']}")
        dialog.show()
        settle(dialog, 250)
        pc, mc = dialog.panels[0].canvas.center(), iface.canvas.center()
        check("B2 the dialog catches up with the main map when shown again",
              abs(pc.x() - mc.x()) < 0.5 and abs(pc.y() - mc.y()) < 0.5,
              f"panel={pc.toString(1)} main={mc.toString(1)}")

        dialog.grid_combo.setCurrentText("8 Panels (2x4)")
        settle(dialog, 200)
        elapsed, added, many = add_one_by_one("Bulk")
        check("B3 adding 300 layers with an 8-panel dialog open costs under 0.1 s more than without it",
              added == 300 and elapsed - baseline < 0.1,
              f"added {added} layers in {elapsed:.3f} s; without the dialog {baseline:.3f} s")
        pump(400)
        combo = dialog.panels[0].layer_combo
        listed = {combo.itemText(i) for i in range(combo.count())}
        check("B3 one rebuild afterwards lists every added layer",
              all(layer.name() in listed for layer in many),
              f"listed {len(listed)} names for {added} layers")
        project.removeMapLayers([layer.id() for layer in many])
        pump(300)

        dialog.grid_combo.setCurrentText("2 Panels (1x2)")
        settle(dialog, 200)
        # Another layer stays, so a list that silently jumps to it would show.
        polygon_layer("Alpha keeper", "EPSG:3857", grid_rects(X0, Y0, 200.0, 2))
        doomed = polygon_layer("Doomed", "EPSG:3857", grid_rects(X0, Y0, 300.0, 3))
        pump(300)
        panel = dialog.panels[0]
        panel.set_comparison_layer(doomed)
        pump(100)
        shown_before = [layer.name() for layer in panel.canvas.layers()]
        project.removeMapLayer(doomed.id())
        pump(50)  # at once, not after the debounced list rebuild
        shown = [layer.name() for layer in panel.canvas.layers()]
        check("B4 a removed layer leaves its panel consistent (no layer named, none shown)",
              shown_before == ["Doomed"] and panel.layer_combo.currentData() is None and not shown,
              f"combo={panel.layer_combo.currentText()!r} data={panel.layer_combo.currentData()!r} "
              f"canvas={shown} (before: {shown_before})")
        check("B4 ... and the dialog says what happened",
              "removed" in dialog.status_label.text().lower(), f"status={dialog.status_label.text()!r}")
    finally:
        dispose(dialog)


# ───────────────────────────── C. right answers ─────────────────────────────


def test_c_right_answers(iface):
    print("\nC. Right answers")
    main_reset(iface, QgsRectangle(X0 - 500, Y0 - 500, X0 + 3500, Y0 + 3500))
    ring = QgsGeometry.fromWkt(
        f"POLYGON(({X0} {Y0},{X0 + 1000} {Y0},{X0 + 1000} {Y0 + 1000},{X0} {Y0 + 1000},{X0} {Y0}),"
        f"({X0 + 200} {Y0 + 200},{X0 + 800} {Y0 + 200},{X0 + 800} {Y0 + 800},{X0 + 200} {Y0 + 800},{X0 + 200} {Y0 + 200}))")
    ell = QgsGeometry.fromWkt(
        f"POLYGON(({X0 + 2000} {Y0},{X0 + 3000} {Y0},{X0 + 3000} {Y0 + 200},{X0 + 2200} {Y0 + 200},"
        f"{X0 + 2200} {Y0 + 1000},{X0 + 2000} {Y0 + 1000},{X0 + 2000} {Y0}))")
    small = QgsGeometry.fromRect(QgsRectangle(X0 + 2600, Y0 + 600, X0 + 2900, Y0 + 900))
    parcels = polygon_layer("<b>Parcels</b>", "EPSG:3857", [ring, ell, small])
    parcels.dataProvider().changeAttributeValues({2: {1: "a<b>c</b> & </script>"}})

    dialog = new_dialog(iface, "2 Panels (1x2)")
    try:
        dialog.panels[0].set_comparison_layer(parcels)
        dialog.panels[1].set_comparison_layer(parcels)
        settle(dialog)

        def panel_one(text):
            return text.split("<b>Panel 1:</b>")[1].split("<br>")[0] if "<b>Panel 1:</b>" in text else text

        probes = {
            "the ring's hole": (QgsPointXY(X0 + 500, Y0 + 500), None),
            "the empty corner of the L": (QgsPointXY(X0 + 2500, Y0 + 500), None),
            "the small square inside the L's box": (QgsPointXY(X0 + 2750, Y0 + 750), "id: 3"),
            "the L's arm": (QgsPointXY(X0 + 2100, Y0 + 900), "id: 2"),
        }
        texts = {}
        for label, (point, expected) in probes.items():
            MESSAGES.clear()
            dialog.perform_multi_inspect(point, source_panel=dialog.panels[0])
            text = MESSAGES[-1][2] if MESSAGES else ""
            texts[label] = text
            reported = panel_one(text)
            if expected is None:
                check(f"C1 Multi-Inspect reports nothing in {label}", "No features at point" in reported,
                      f"Panel 1 reported {reported!r}")
            else:
                check(f"C1 Multi-Inspect reports the right feature in {label} ({expected})",
                      expected in reported and reported.count("(") == 1, f"Panel 1 reported {reported!r}")
        arm = texts.get("the L's arm", "")
        check("C1 layer names are escaped in the rich-text result",
              "&lt;b&gt;Parcels&lt;/b&gt;" in arm and "<b>Parcels</b>" not in arm, f"text={arm[:200]!r}")
        check("C1 attribute values are escaped in the rich-text result",
              "a&lt;b&gt;c&lt;/b&gt; &amp; &lt;/script&gt;" in arm and "</script>" not in arm,
              f"text={arm[:300]!r}")

        # Chips: background excluded, points counted, reasons given, refreshed on change.
        to_wgs = QgsCoordinateTransform(MERCATOR, WGS84, QgsProject.instance())
        block = polygon_layer("Block", "EPSG:3857", [QgsRectangle(X0, Y0, X0 + 1000, Y0 + 1000)])
        backdrop = polygon_layer("Backdrop", "EPSG:3857", [QgsRectangle(X0 - 400, Y0 - 400, X0 + 3400, Y0 + 3400)])
        wells = point_layer("Wells", "EPSG:3857", [(X0 + 100, Y0 + 100), (X0 + 300, Y0 + 400), (X0 + 900, Y0 + 50)])
        grid_raster = raster_layer("Elevation", QgsRectangle(X0, Y0, X0 + 3000, Y0 + 3000))
        dialog.populate_layer_combos()
        panel = dialog.panels[0]
        panel.set_comparison_layer(block)
        panel.canvas.setExtent(QgsRectangle(X0 - 200, Y0 - 200, X0 + 1200, Y0 + 1200))
        dialog.kpi_chk.setChecked(True)
        settle(dialog, 350)
        chip_plain = panel.kpi_bar.text()
        area = re.search(r"(\d+(?:\.\d+)?) (ha|km²)", chip_plain)
        # The real path: changing the background refreshes the chip by itself.
        dialog.base_layer_combo.setCurrentIndex(dialog.base_layer_combo.findData(backdrop.id()))
        settle(dialog, 350)
        chip_bg = panel.kpi_bar.text()
        refreshed = "Backdrop" in panel.kpi_bar.toolTip()
        check("C2 the background change refreshes the chip, and the area leaves the background out",
              refreshed and area is not None and area.group(0) in chip_bg and "2 polygons" not in chip_bg,
              f"refreshed={refreshed} without={chip_plain!r} with background={chip_bg!r}")
        check("C2 the chip's tooltip says the background is not counted",
              "Backdrop" in panel.kpi_bar.toolTip(), f"tooltip={panel.kpi_bar.toolTip()!r}")
        dialog.base_layer_combo.setCurrentIndex(0)
        panel.set_comparison_layer(wells)
        settle(dialog, 350)
        chip_points = panel.kpi_bar.text()
        check("C2 a point layer is counted, not reported as 0 ha (and refreshed on change)",
              "3 points" in chip_points and "ha" not in chip_points, f"chip={chip_points!r}")
        panel.set_comparison_layer(grid_raster)
        settle(dialog, 350)
        chip_raster = panel.kpi_bar.text()
        check("C2 a raster-only panel says why nothing is measured",
              "nothing to measure" in chip_raster and "raster" in chip_raster, f"chip={chip_raster!r}")
        dialog.kpi_chk.setChecked(False)
        del to_wgs

        # Show Diff refuses a raster, and never compares the background.
        dialog.base_layer_combo.setCurrentIndex(dialog.base_layer_combo.findData(backdrop.id()))
        dialog.panels[0].set_comparison_layer(grid_raster)
        dialog.panels[1].set_comparison_layer(block)
        settle(dialog, 300)
        MESSAGES.clear()
        dialog.diff_btn.setChecked(True)
        pump(400)
        visible_bands = [k for k, b in getattr(dialog, "diff_rubber_bands", {}).items() if b.isVisible()]
        refusal = MESSAGES[-1][2] if MESSAGES else ""
        check("C3 Show Diff refuses a raster with a plain message",
              "raster" in refusal.lower() and "polygon" in refusal.lower(), f"message={refusal!r}")
        check("C3 ... draws nothing and stays off", not visible_bands and not dialog.diff_btn.isChecked(),
              f"bands={visible_bands} checked={dialog.diff_btn.isChecked()}")
        dialog.diff_btn.setChecked(False)
        dialog.base_layer_combo.setCurrentIndex(0)

        # Diff refreshes when a panel's layer changes; bands are reused.
        before = polygon_layer("Before", "EPSG:3857", [QgsRectangle(X0, Y0, X0 + 1000, Y0 + 1000)])
        after = polygon_layer("After", "EPSG:3857", [QgsRectangle(X0 + 500, Y0, X0 + 1500, Y0 + 1000)])
        dialog.populate_layer_combos()
        dialog.panels[0].set_comparison_layer(before)
        dialog.panels[1].set_comparison_layer(after)
        dialog.panels[0].canvas.setExtent(QgsRectangle(X0 - 500, Y0 - 500, X0 + 2000, Y0 + 1500))
        settle(dialog, 300)
        dialog.diff_btn.setChecked(True)
        pump(400)
        overlap = dialog.overlap_label.text()
        check("C4 Show Diff on two polygon layers reports 50 % overlap", overlap == "Overlap: 50.0%",
              f"overlap={overlap!r}")
        dialog.panels[1].set_comparison_layer(before)
        pump(400)
        visible_bands = [k for k, b in getattr(dialog, "diff_rubber_bands", {}).items() if b.isVisible()]
        check("C4 Diff refreshes after Panel 2's layer changes (no pan needed)",
              dialog.overlap_label.text() == "Overlap: 100.0%" and not visible_bands,
              f"overlap={dialog.overlap_label.text()!r} bands={visible_bands}")
        dialog.panels[1].set_comparison_layer(after)
        pump(400)
        scene_items = len(dialog.panels[0].canvas.scene().items())
        for step in range(30):
            c = dialog.panels[0].canvas.center()
            dialog.panels[0].canvas.setCenter(QgsPointXY(c.x() + (5.0 if step % 2 else -5.0), c.y()))
            pump(10)
        pump(400)
        check("C4 Diff bands are reused, not piled up (30 pans)",
              len(dialog.panels[0].canvas.scene().items()) <= scene_items,
              f"scene items {scene_items} -> {len(dialog.panels[0].canvas.scene().items())}")
        dialog.diff_btn.setChecked(False)

        # Overlays are debounced: nothing runs inside the pan, one refresh after 250 ms.
        dialog.kpi_chk.setChecked(True)
        pump(400)
        calls = {"n": 0}
        originals = []
        for p in dialog.panels:
            original = p.update_kpi_bar

            def counting(visible=True, _original=original):
                calls["n"] += 1
                return _original(visible)

            originals.append((p, original))
            p.update_kpi_bar = counting
        c = dialog.panels[0].canvas.center()
        dialog.panels[0].canvas.setCenter(QgsPointXY(c.x() + 50.0, c.y()))
        during = calls["n"]
        pump(450)
        check("C5 overlays are debounced: none inside the pan, one per panel after 250 ms",
              during == 0 and calls["n"] == len(dialog.panels), f"during={during} after={calls['n']}")
        for p, original in originals:
            p.update_kpi_bar = original
        dialog.kpi_chk.setChecked(False)
    finally:
        dispose(dialog)
        main_reset(iface)


# ───────────────────────────── D. fits a laptop ─────────────────────────────


def test_d_fits_a_laptop(iface):
    print("\nD. Fits a laptop (1,280 x 680 px of usable screen)")
    from qgis.PyQt.QtGui import QGuiApplication

    dialog = new_dialog(iface)
    try:
        check("D1 default height is at most 680 px", dialog.height() <= 680,
              f"default size {dialog.width()}x{dialog.height()}")
        if QGuiApplication.platformName() in ("offscreen", "minimal"):
            skip("D2-D3 minimum sizes", f"the {QGuiApplication.platformName()} platform has no font engine "
                 "to size text widgets with (TRAPS 4.3)")
            return
        for layout in ("2 Panels (1x2)", "4 Panels (2x2)", "8 Panels (2x4)"):
            dialog.grid_combo.setCurrentText(layout)
            settle(dialog, 200)
            hint = dialog.minimumSizeHint()
            check(f"D2 minimum size at {layout} fits 1,264 x 640 (frame excluded)",
                  hint.width() <= 1264 and hint.height() <= 640, f"minimum {hint.width()}x{hint.height()}")
        dialog.grid_combo.setCurrentText("4 Panels (2x2)")
        dialog.resize(1264, 640)
        settle(dialog, 250)
        scroll = getattr(dialog, "grid_scroll", None)
        needs_scroll = scroll.horizontalScrollBar().maximum() if scroll is not None else -1
        check("D3 at 1,264 x 640 four panels show without horizontal scrolling",
              dialog.width() <= 1264 and dialog.height() <= 640 and needs_scroll == 0,
              f"size {dialog.width()}x{dialog.height()} horizontal scroll range {needs_scroll}")
    finally:
        dispose(dialog)


# ───────────────────────────── E. templates, presets, project ─────────────────────────────


def make_theme(names, theme_name):
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    keep_ids = {project.mapLayersByName(n)[0].id() for n in names}
    for node in root.findLayers():
        node.setItemVisibilityChecked(node.layerId() in keep_ids)
    model = QgsLayerTreeModel(root)
    record = QgsMapThemeCollection.createThemeFromCurrentState(root, model)
    project.mapThemeCollection().insert(theme_name, record)
    for node in root.findLayers():
        node.setItemVisibilityChecked(True)


def panel_layers(dialog):
    out = []
    for panel in dialog.panels:
        layer = panel.selected_layer() if panel.mode == "layer" else None
        out.append(layer.name() if layer is not None else None)
    return out


def test_e_templates_presets_project(iface):
    print("\nE. Templates, presets, project")
    project = QgsProject.instance()
    project.removeAllMapLayers()
    pump(100)
    main_reset(iface)
    cells = grid_rects(X0, Y0, 500.0, 2)
    for name in ("districts", "landuse_2015", "landuse_2023", "population_2020"):
        polygon_layer(name, "EPSG:3857", cells)
    point_layer("schools_2019", "EPSG:3857", [(X0 + 100, Y0 + 100)])
    raster_layer("ndvi_2020", QgsRectangle(X0, Y0, X0 + 1000, Y0 + 1000))
    dialog = new_dialog(iface)
    try:
        MESSAGES.clear()
        dialog.load_built_in_preset("before_after")
        settle(dialog, 200)
        check("E1 Before & After assigns two different layers: landuse_2015 then landuse_2023",
              panel_layers(dialog) == ["landuse_2015", "landuse_2023"], f"panels={panel_layers(dialog)}")

        dialog.load_built_in_preset("matrix_2x2")
        settle(dialog, 200)
        assigned = panel_layers(dialog)
        check("E2 4-Scenario Matrix assigns four different layers",
              len(assigned) == 4 and None not in assigned and len(set(assigned)) == 4, f"panels={assigned}")

        dialog.grid_combo.setCurrentText("3 Panels (1x3)")
        settle(dialog, 150)
        polygon_layer("landuse_2019", "EPSG:3857", cells)
        dialog.auto_fill_time_series()
        settle(dialog, 200)
        status = dialog.status_label.text()
        check("E3 time-series auto-fill finds landuse_2015/2019/2023 in year order",
              panel_layers(dialog) == ["landuse_2015", "landuse_2019", "landuse_2023"], f"panels={panel_layers(dialog)}")
        check("E3 ... and reports what it assigned",
              all(n in status for n in ("landuse_2015", "landuse_2019", "landuse_2023")), f"status={status!r}")

        make_theme(["districts", "schools_2019"], "Flood review")
        pump(200)
        # Sync Canvas off: the main map keeps its own scale, so a saved panel
        # scale can only come back from the preset or the project setup.
        dialog.sync_main_chk.setChecked(False)
        main_scale = iface.canvas.scale()
        dialog.grid_combo.setCurrentText("4 Panels (2x2)")
        settle(dialog, 200)
        dialog.populate_theme_combos()
        p1, p2, p3, p4 = dialog.panels[:4]
        p1.set_mode("theme") if hasattr(p1, "set_mode") else p1.mode_combo.setCurrentText("Use Map Theme")
        p1.theme_combo.setCurrentText("Flood review")
        p2.set_comparison_layer(project.mapLayersByName("landuse_2015")[0])
        p3.set_comparison_layer(project.mapLayersByName("ndvi_2020")[0])
        p4.mode_combo.setCurrentText("Follow Main Map")
        dialog._apply_target_scale(25000.0)
        settle(dialog, 200)
        saved = (panel_layers(dialog), p1.mode, p1.theme_combo.currentText())
        from qgis.PyQt.QtWidgets import QInputDialog

        QInputDialog.getText = staticmethod(lambda *a, **k: ("mm test preset", True))
        dialog.save_workspace_preset()
        dialog.grid_combo.setCurrentText("2 Panels (1x2)")
        dialog._apply_target_scale(100000.0)
        settle(dialog, 200)
        dialog.load_saved_preset("mm test preset")
        settle(dialog, 250)
        loaded = (panel_layers(dialog), dialog.panels[0].mode, dialog.panels[0].theme_combo.currentText())
        check("E4 a preset keeps the map-theme panel", loaded[1] == "theme" and loaded[2] == "Flood review",
              f"saved={saved} loaded={loaded}")
        check("E4 a preset keeps the layout and the layers",
              dialog.grid_combo.currentText() == "4 Panels (2x2)" and loaded[0] == saved[0],
              f"grid={dialog.grid_combo.currentText()} saved={saved[0]} loaded={loaded[0]}")
        check("E4 a preset keeps the scale (1:25,000 within 1 %)",
              all(close(s, 25000.0, 0.01) for s in scales(dialog)), f"scales={scales(dialog)}")
        check("E4 ... without moving the main map", close(iface.canvas.scale(), main_scale, 0.01),
              f"main 1:{main_scale:,.0f} -> 1:{iface.canvas.scale():,.0f}")

        # The panel setup travels with the project file.
        dialog._apply_target_scale(10000.0)
        settle(dialog, 150)
        expected = (dialog.grid_combo.currentText(), panel_layers(dialog), dialog.panels[0].mode,
                    dialog.panels[0].theme_combo.currentText())
        path = os.path.join(tempfile.mkdtemp(prefix="mm_dialog_project_"), "setup.qgs")
        project.write(path)
        with open(path, encoding="utf-8") as handle:
            stored = "panel_setup" in handle.read()
        check("E5 saving the project stores the panel setup in the project file", stored, f"file={path}")
        dialog.grid_combo.setCurrentText("2 Panels (1x2)")
        dialog._apply_target_scale(50000.0)
        settle(dialog, 150)
        project.read(path)
        settle(dialog, 500)
        restored = (dialog.grid_combo.currentText(), panel_layers(dialog), dialog.panels[0].mode,
                    dialog.panels[0].theme_combo.currentText())
        check("E5 reading the project restores grid, layers and the map theme", restored == expected,
              f"expected={expected} restored={restored}")
        # The panels sat at 1:50,000 before the read and the main map never was at
        # 1:10,000, so this scale can only come from the stored setup.
        check("E5 reading the project restores the panel scale (1:10,000 within 1 %)",
              all(close(s, 10000.0, 0.01) for s in scales(dialog)), f"scales={scales(dialog)}")
        check("E5 ... and leaves the main map at its saved scale", close(iface.canvas.scale(), main_scale, 0.01),
              f"main 1:{main_scale:,.0f} -> 1:{iface.canvas.scale():,.0f}")
    finally:
        dispose(dialog)


def test_e6_restore_keeps_main_map(iface):
    """Review S6: restoring a setup must never move the user's main map."""
    print("\nE6. Restoring a setup never moves the main map")
    project = QgsProject.instance()
    main = iface.canvas
    project.clear()
    pump(100)
    main_reset(iface)
    polygon_layer("restore_a", "EPSG:3857", grid_rects(X0, Y0, 500.0, 2))
    polygon_layer("restore_b", "EPSG:3857", grid_rects(X0 + 250, Y0, 500.0, 2))
    dialog = new_dialog(iface, "2 Panels (1x2)")
    path = os.path.join(tempfile.mkdtemp(prefix="mm_dialog_restore_"), "restore.qgs")
    try:
        dialog.sync_main_chk.setChecked(False)
        dialog._apply_target_scale(5000.0)  # panels only: Sync Canvas is off
        settle(dialog, 150)
        main_saved = main.scale()
        project.write(path)

        dialog._apply_target_scale(50000.0)
        settle(dialog, 150)
        project.read(path)
        settle(dialog, 500)
        check("E6 restore with Sync Canvas off keeps the main map scale (within 1 %)",
              close(main.scale(), main_saved, 0.01), f"saved 1:{main_saved:,.0f} now 1:{main.scale():,.0f}")
        check("E6 ... and gives the panels the saved 1:5,000",
              all(close(s, 5000.0, 0.01) for s in scales(dialog)), f"panels={scales(dialog)}")

        dialog.sync_main_chk.setChecked(True)
        project.read(path)
        settle(dialog, 500)
        check("E6 restore with Sync Canvas on keeps the main map scale (within 1 %)",
              close(main.scale(), main_saved, 0.01), f"saved 1:{main_saved:,.0f} now 1:{main.scale():,.0f}")
        check("E6 ... and the panels follow the main map",
              all(close(s, main.scale(), 0.01) for s in scales(dialog)),
              f"main 1:{main.scale():,.0f} panels={scales(dialog)}")
    finally:
        dispose(dialog)

    project.read(path)
    pump(300)
    before = main.scale()
    dialog = new_dialog(iface)
    try:
        check("E6 opening the dialog after a project read keeps the main map scale (within 1 %)",
              close(main.scale(), before, 0.01), f"1:{before:,.0f} -> 1:{main.scale():,.0f}")
    finally:
        dispose(dialog)


def test_e_before_after_fallbacks(iface):
    project = QgsProject.instance()
    project.clear()
    pump(100)
    main_reset(iface)
    cells = grid_rects(X0, Y0, 500.0, 2)
    polygon_layer("parks", "EPSG:3857", cells)
    polygon_layer("zoning", "EPSG:3857", cells)
    point_layer("trees", "EPSG:3857", [(X0 + 100, Y0 + 100)])
    dialog = new_dialog(iface)
    try:
        dialog.load_built_in_preset("before_after")
        settle(dialog, 200)
        check("E1 without years, Before & After takes the two most recent layers of one kind",
              panel_layers(dialog) == ["parks", "zoning"], f"panels={panel_layers(dialog)}")
        project.removeMapLayer(project.mapLayersByName("zoning")[0].id())
        pump(300)
        MESSAGES.clear()
        dialog.load_built_in_preset("before_after")
        settle(dialog, 200)
        told = MESSAGES[-1][2] if MESSAGES else ""
        check("E1 with no pair available, Before & After tells the user what to pick",
              "Pick" in told and panel_layers(dialog) == [None, None]
              and [p.mode for p in dialog.panels] == ["layer", "layer"],
              f"message={told!r} panels={panel_layers(dialog)}")
    finally:
        dispose(dialog)


# ───────────────────────────── G. small ─────────────────────────────


def luminance(rgb):
    def channel(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def text_contrasts(root):
    """Contrast of every visible text widget, measured on the composited window image."""
    from qgis.PyQt.QtCore import QPoint, QRect
    from qgis.PyQt.QtWidgets import QAbstractButton, QComboBox, QLabel, QWidget

    image = root.grab().toImage()
    ratio_x = image.width() / max(1, root.width())
    rows = []
    for widget in root.findChildren(QWidget):
        if not widget.isVisible() or widget.width() < 8 or widget.height() < 8:
            continue
        if not isinstance(widget, (QLabel, QAbstractButton, QComboBox)):
            continue
        text = widget.currentText() if isinstance(widget, QComboBox) else widget.text()
        if not text or not re.search(r"[A-Za-z0-9]", text):
            continue  # emoji-only icon buttons carry no text
        top_left = widget.mapTo(root, QPoint(0, 0))
        rect = QRect(int(top_left.x() * ratio_x), int(top_left.y() * ratio_x),
                     int(widget.width() * ratio_x), int(widget.height() * ratio_x)).intersected(image.rect())
        counts = Counter()
        for y in range(rect.top(), rect.bottom() + 1):
            for x in range(rect.left(), rect.right() + 1):
                counts[image.pixel(x, y) & 0xFFFFFF] += 1
        if not counts:
            continue
        background = counts.most_common(1)[0][0]
        candidates = [c for c, n in counts.items() if n >= 3]
        foreground = max(candidates, key=lambda c: contrast(c, background))
        rows.append((round(contrast(foreground, background), 2), type(widget).__name__,
                     re.sub(r"<[^>]+>", "", str(text))[:40], f"#{background:06x}", f"#{foreground:06x}"))
    return sorted(rows)


def test_g_small(iface):
    print("\nG. Small")
    from qgis.PyQt.QtCore import QPoint
    from qgis.PyQt.QtGui import QGuiApplication

    main_reset(iface)
    dialog = new_dialog(iface)
    try:
        panel = dialog.panels[0]
        dialog.laser_chk.setChecked(True)
        dialog.on_mouse_moved_in_panel(panel, QPoint(panel.canvas.width() // 2, panel.canvas.height() // 2))
        pump(50)
        shown = [p.marker.isVisible() for p in dialog.panels]
        dialog.laser_chk.setChecked(False)
        pump(50)
        left = [p.marker.isVisible() for p in dialog.panels]
        main_marker = dialog.main_canvas_marker
        check("G1 unticking Laser Crosshair removes every marker",
              any(shown) and not any(left) and (main_marker is None or not main_marker.isVisible()),
              f"before={shown} after={left} main marker={main_marker}")
        dialog.laser_chk.setChecked(True)

        iface.canvas.zoomByFactor(0.5)
        pump(120)
        readout = dialog.scale_readout.text()
        expected = f"Scale: 1:{round(dialog.active_panel.canvas.scale()):,}"
        check("G2 the scale readout follows a main-map zoom", readout == expected
              and close(dialog.active_panel.canvas.scale(), iface.canvas.scale(), 0.01),
              f"readout={readout!r} expected={expected!r} main=1:{iface.canvas.scale():,.0f}")
    finally:
        dispose(dialog)
        main_reset(iface)

    if QGuiApplication.platformName() in ("offscreen", "minimal"):
        skip("G3 text contrast", f"the {QGuiApplication.platformName()} platform renders no text")
    else:
        dialog_module = __import__(f"{PACKAGE}.dialog", fromlist=["QuickGuideDialog"])
        themes = [t for t in ("default", "Blend of Gray", "Night Mapping") if t in QgsApplication.uiThemes()]
        for theme in themes:
            QgsApplication.setUITheme(theme)
            pump(100)
            dialog = new_dialog(iface)
            try:
                vectors = [layer for layer in QgsProject.instance().mapLayers().values()
                           if isinstance(layer, QgsVectorLayer)]
                for index, p in enumerate(dialog.panels):
                    if vectors:
                        p.set_comparison_layer(vectors[index % len(vectors)])
                dialog.kpi_chk.setChecked(True)
                settle(dialog, 400)
                rows = text_contrasts(dialog)
                worst = rows[0] if rows else None
                check(f"G3 every text in the dialog reaches 4.5:1 under the '{theme}' theme",
                      rows and worst[0] >= 4.5, f"lowest: {rows[:4]}")
                guide = dialog_module.QuickGuideDialog(dialog)
                guide.show()
                pump(150)
                guide_rows = text_contrasts(guide)
                check(f"G3 the Quick Guide's text reaches 4.5:1 under the '{theme}' theme",
                      guide_rows and guide_rows[0][0] >= 4.5, f"lowest: {guide_rows[:3]}")
                guide.close()
                guide.deleteLater()
            finally:
                dispose(dialog)
        QgsApplication.setUITheme("default")
        pump(100)

    plugin_module = __import__(f"{PACKAGE}.main_plugin", fromlist=["O2MultiMapPlugin"])
    plugin = plugin_module.O2MultiMapPlugin(iface)
    plugin.initGui()
    entries = [(name, action.text()) for name, action in iface.plugin_menu]
    check("G4 a Plugins-menu entry sits next to the toolbar icon",
          len(entries) == 1 and "02Multimap" in entries[0][0] and iface.toolbar,
          f"menu={entries} toolbar={len(iface.toolbar)}")
    plugin.unload()
    flush_deletes()

    from qgis.PyQt.QtWidgets import QDialog

    # The openers run for real; only exec() returns at once, so no modal window appears.
    print_layout = __import__(f"{PACKAGE}.print_layout", fromlist=["PrintLayoutDialog"])
    guide_cls = __import__(f"{PACKAGE}.dialog", fromlist=["QuickGuideDialog"]).QuickGuideDialog
    saved_exec = (print_layout.PrintLayoutDialog.exec, guide_cls.exec)
    print_layout.PrintLayoutDialog.exec = lambda self: 0
    guide_cls.exec = lambda self: 0
    dialog = new_dialog(iface)
    try:
        for _opening in range(3):
            dialog.show_print_layout()
            flush_deletes()
        for _opening in range(2):
            dialog.show_quick_guide()
            flush_deletes()
        exports = [w for w in iface.mw.findChildren(QDialog) if w.windowTitle() == "02Multimap Export"]
        guides = [w for w in iface.mw.findChildren(QDialog) if w.windowTitle() == "02Multimap Workspace Guide"]
        check("G5 three Export openings leave no export dialog under the main window", not exports,
              f"export dialogs left: {len(exports)}")
        check("G5 ... and two Quick Guide openings leave no guide behind", not guides,
              f"guides left: {len(guides)}")
    finally:
        print_layout.PrintLayoutDialog.exec, guide_cls.exec = saved_exec
        dispose(dialog)


# ───────────────────────────── H. swipe (dialog side) ─────────────────────────────


def swipe_available():
    module = __import__(f"{PACKAGE}.swipe_view", fromlist=["SwipePanel"])
    try:
        probe = module.SwipePanel()
    except NotImplementedError:
        return False
    cleanup = getattr(probe, "cleanup", None)
    if callable(cleanup):
        cleanup()
    probe.deleteLater()
    return True


def pixel(widget, x, y):
    image = widget.grab().toImage()
    ratio = image.width() / max(1, widget.width())
    return image.pixel(int(x * ratio), int(y * ratio)) & 0xFFFFFF


def is_red(rgb):
    return ((rgb >> 16) & 255) > 180 and ((rgb >> 8) & 255) < 90 and (rgb & 255) < 90


def is_blue(rgb):
    return ((rgb >> 16) & 255) < 90 and ((rgb >> 8) & 255) < 90 and (rgb & 255) > 180


def png_dimensions(base64_text):
    import base64
    import struct

    raw = base64.b64decode(base64_text[:64])
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    return struct.unpack(">II", raw[16:24])


def check_swipe_export(iface, dialog, canvas):
    """Review S2: a swipe exports as a 1 x 2 pair at the swipe map's extent, scale and CRS."""
    from qgis.core import QgsLayoutItemLabel, QgsLayoutItemMap

    print_layout = __import__(f"{PACKAGE}.print_layout", fromlist=["PrintLayoutDialog"])
    exporter = print_layout.PrintLayoutDialog(dialog, iface)
    try:
        layout = exporter.build_qgis_print_layout(title="Swipe export", page_size_name="A4",
                                                  orientation="Landscape")
        page = layout.pageCollection().pages()[0].pageSize()
        maps = sorted((it for it in layout.items() if isinstance(it, QgsLayoutItemMap)), key=lambda m: m.id())
        placed = [(m.id(), round(m.positionWithUnits().x(), 1), round(m.positionWithUnits().y(), 1),
                   round(m.sizeWithUnits().width(), 1), round(m.sizeWithUnits().height(), 1)) for m in maps]
        inside = all(x >= -0.01 and y >= -0.01 and x + w <= page.width() + 0.5 and y + h <= page.height() + 0.5
                     for _id, x, y, w, h in placed)
        check("H6 a swipe prints as two maps, both inside the page", len(maps) == 2 and inside,
              f"page {page.width():.0f}x{page.height():.0f} maps={placed}")

        reference = canvas.extent()

        def same_extent(extent):
            return (close(extent.width(), reference.width(), 0.01)
                    and close(extent.height(), reference.height(), 0.01)
                    and abs(extent.center().x() - reference.center().x()) <= 0.01 * reference.width()
                    and abs(extent.center().y() - reference.center().y()) <= 0.01 * reference.height())

        extents = [m.extent() for m in maps]
        check("H6 both printed maps show the swipe map's extent (within 1 %)",
              len(maps) == 2 and all(same_extent(e) for e in extents),
              f"swipe {reference.toString(0)} printed {[e.toString(0) for e in extents]}")
        crs = canvas.mapSettings().destinationCrs()
        layer_names = [[layer.name() for layer in m.layers()] for m in maps]
        check("H6 ... in the swipe map's CRS, bottom layers on the left and top layers on the right",
              all(m.crs() == crs for m in maps) and layer_names == [["Swipe bottom"], ["Swipe top"]],
              f"crs={[m.crs().authid() for m in maps]} layers={layer_names}")
        labels = [it for it in layout.items() if isinstance(it, QgsLayoutItemLabel)]
        headers = {it.id(): it.text() for it in labels if it.id().startswith("PanelHeader_")}
        overflowing = [it.id() for it in labels
                       if it.sizeForText().width() > it.rect().width() + 0.5
                       or it.sizeForText().height() > it.rect().height() + 0.5]
        check("H6 the headers read 'Bottom layers' and 'Top layers', and every label fits its box",
              "Bottom layers" in headers.get("PanelHeader_1", "") and "Top layers" in headers.get("PanelHeader_2", "")
              and not overflowing, f"headers={headers} overflowing={overflowing}")

        stored = exporter.add_layout_to_manager(title="Swipe designer")
        stored_maps = [it for it in stored.items() if isinstance(it, QgsLayoutItemMap)]
        check("H6 Open in Layout Designer stores the same two-map swipe layout in the Layout Manager",
              QgsProject.instance().layoutManager().layoutByName(stored.name()) is not None and len(stored_maps) == 2,
              f"name={stored.name()!r} maps={len(stored_maps)}")

        html_path = os.path.join(tempfile.mkdtemp(prefix="mm_dialog_swipe_html_"), "swipe.html")
        MESSAGES.clear()
        error = ""
        try:
            exporter.export_to_html(html_path, "Swipe export", open_after=False)
        except Exception as exc:  # noqa: BLE001 - recorded as the check's evidence
            error = f"{type(exc).__name__}: {exc}"
        text = ""
        if os.path.exists(html_path):
            with open(html_path, encoding="utf-8") as handle:
                text = handle.read()
        images = [m.replace("\\", "") for m in re.findall(r"data:image/png;base64,([A-Za-z0-9+/=\\]{64,})", text)]
        sizes = [png_dimensions(b64) for b64 in images]
        check("H6 the HTML dashboard of a swipe holds two panel images of a usable size",
              len(images) == 2 and all(w >= 200 and h >= 100 for w, h in sizes)
              and "Bottom layers" in text and "Top layers" in text,
              f"written={bool(text)} images={sizes} error={error!r}")
    finally:
        exporter.deleteLater()

    exporter = print_layout.PrintLayoutDialog(dialog, iface)
    try:
        def broken(*_args, **_kwargs):
            raise RuntimeError("a panel could not be rendered")

        exporter._rendered_panel_data = broken
        exporter.tab_widget.setCurrentIndex(1)
        exporter.html_path_input.setText(os.path.join(tempfile.mkdtemp(prefix="mm_dialog_broken_"), "broken.html"))
        MESSAGES.clear()
        exporter.execute_export(open_after=False)
        told = " ".join(" ".join(m) for m in MESSAGES)
        check("H6 a failure that is not a file problem does not blame the folder",
              "could not be rendered" in told and "folder can be written" not in told, f"message={told!r}")
    finally:
        exporter.deleteLater()


def check_swipe_setup_applied(dialog, swipe):
    """Review: a Swipe preset or project applied while already in Swipe reaches the swipe map."""
    dialog_module = __import__(f"{PACKAGE}.dialog", fromlist=["SWIPE_LAYOUT"])
    dialog.swipe_orientation_combo.setCurrentIndex(0)
    swipe.set_divider_ratio(0.5)
    settle(dialog, 300)
    dialog.apply_setup({"version": 2, "grid": dialog_module.SWIPE_LAYOUT, "scale": 0, "panels": [],
                        "swipe": {"vertical": False, "ratio": 0.3}})
    settle(dialog, 700)
    live = dialog.swipe_panel
    w, h = live.width(), live.height()
    above, below = pixel(live, w * 0.5, h * 0.12), pixel(live, w * 0.5, h * 0.6)
    check("H7 a Swipe setup applied while in Swipe lays the divider flat at 0.3",
          dialog.swipe_orientation_combo.currentIndex() == 1 and abs(live.divider_ratio() - 0.3) < 1e-6
          and is_blue(above) and is_red(below),
          f"combo={dialog.swipe_orientation_combo.currentIndex()} ratio={live.divider_ratio():.3f} "
          f"above=#{above:06x} below=#{below:06x}")
    stored = dialog._setup_snapshot()["swipe"]
    check("H7 ... and the next save stores what the user sees", stored == {"vertical": False, "ratio": 0.3},
          f"stored={stored}")
    live.set_divider_ratio(0.6)
    dialog.swipe_orientation_combo.setCurrentIndex(0)
    settle(dialog, 200)
    stored = dialog._setup_snapshot()["swipe"]
    check("H7 after the user moves the divider and stands it upright, a save stores that",
          stored == {"vertical": True, "ratio": 0.6}, f"stored={stored}")


def test_h_swipe(iface):
    print("\nH. Swipe (dialog side)")
    dialog_module = __import__(f"{PACKAGE}.dialog", fromlist=["SWIPE_LAYOUT"])
    swipe_module = __import__(f"{PACKAGE}.swipe_view", fromlist=["SwipePanel"])
    layout = getattr(dialog_module, "SWIPE_LAYOUT", "Swipe")
    main_reset(iface)
    bottom = polygon_layer("Swipe bottom", "EPSG:3857", [QgsRectangle(X0 - 50000, Y0 - 50000, X0 + 60000, Y0 + 60000)])
    top = polygon_layer("Swipe top", "EPSG:3857", [QgsRectangle(X0 - 50000, Y0 - 50000, X0 + 60000, Y0 + 60000)])
    solid(bottom, "255,0,0,255")
    solid(top, "0,0,255,255")
    calls = {"cleanup": 0}
    available = swipe_available()
    original_cleanup = swipe_module.SwipePanel.cleanup
    if available:
        def counting_cleanup(self):
            calls["cleanup"] += 1
            return original_cleanup(self)

        swipe_module.SwipePanel.cleanup = counting_cleanup
    dialog = new_dialog(iface)
    try:
        items = [dialog.grid_combo.itemText(i) for i in range(dialog.grid_combo.count())]
        check("H1 the layout list offers Swipe", layout in items, f"items={items}")
        MESSAGES.clear()
        dialog.grid_combo.setCurrentText(layout)
        settle(dialog, 300)
        if not available:
            told = MESSAGES[-1][2] if MESSAGES else ""
            check("H1 without a swipe implementation the dialog falls back to two panels and says so",
                  getattr(dialog, "swipe_panel", "missing") is None and len(dialog.panels) == 2 and "Swipe" in told,
                  f"panels={len(dialog.panels)} message={told!r}")
            skip("H2-H5 swipe pixels, sync and cleanup", "swipe not implemented yet")
            return
        swipe = dialog.swipe_panel
        check("H1 choosing Swipe creates a SwipePanel with two layer-set pickers",
              isinstance(swipe, swipe_module.SwipePanel) and len(dialog.panels) == 2
              and [p.title_label.text() for p in dialog.panels] == ["Bottom layers", "Top layers"],
              f"swipe={swipe!r} pickers={[p.title_label.text() for p in dialog.panels]}")
        dialog.panels[0].set_comparison_layer(bottom)
        dialog.panels[1].set_comparison_layer(top)
        dialog.swipe_orientation_combo.setCurrentIndex(0)
        swipe.set_divider_ratio(0.5)
        canvas = swipe.canvas()
        settle(dialog, 600)
        w, h = swipe.width(), swipe.height()
        left, right = pixel(swipe, w * 0.2, h * 0.5), pixel(swipe, w * 0.8, h * 0.5)
        check("H2 vertical divider: top layers (blue) on the left, bottom layers (red) on the right",
              is_blue(left) and is_red(right), f"left=#{left:06x} right=#{right:06x}")
        dialog.swipe_orientation_combo.setCurrentIndex(1)
        settle(dialog, 600)
        above, below = pixel(swipe, w * 0.5, h * 0.2), pixel(swipe, w * 0.5, h * 0.8)
        check("H2 the orientation choice in the Swipe header lays the divider flat (top layers above)",
              is_blue(above) and is_red(below), f"above=#{above:06x} below=#{below:06x}")

        c = iface.canvas.center()
        iface.canvas.setCenter(QgsPointXY(c.x() + 700.0, c.y() + 300.0))
        pump(150)
        sc, mc = canvas.center(), iface.canvas.center()
        check("H3 the swipe map follows the main map (centre and scale)",
              abs(sc.x() - mc.x()) < 0.5 and abs(sc.y() - mc.y()) < 0.5 and close(canvas.scale(), iface.canvas.scale(), 0.01),
              f"swipe={sc.toString(1)} 1:{canvas.scale():,.0f} main={mc.toString(1)} 1:{iface.canvas.scale():,.0f}")
        canvas.zoomByFactor(0.5)
        pump(150)
        check("H3 zooming the swipe map moves the main map like any panel",
              close(iface.canvas.scale(), canvas.scale(), 0.01),
              f"swipe=1:{canvas.scale():,.0f} main=1:{iface.canvas.scale():,.0f}")

        check_swipe_export(iface, dialog, canvas)
        check_swipe_setup_applied(dialog, swipe)

        calls["cleanup"] = 0
        dialog.grid_combo.setCurrentText("2 Panels (1x2)")
        settle(dialog, 200)
        check("H4 a grid change calls SwipePanel.cleanup()", calls["cleanup"] == 1 and dialog.swipe_panel is None,
              f"cleanup calls={calls['cleanup']}")
        dialog.grid_combo.setCurrentText(layout)
        settle(dialog, 300)
        calls["cleanup"] = 0
        dialog.close()
        pump(150)
        check("H4 closing the dialog calls SwipePanel.cleanup()", calls["cleanup"] == 1, f"cleanup calls={calls['cleanup']}")
        dialog.show()
        settle(dialog, 300)
        check("H4 showing the dialog again brings the swipe map back",
              isinstance(dialog.swipe_panel, swipe_module.SwipePanel), f"swipe={dialog.swipe_panel!r}")
        calls["cleanup"] = 0
        dialog.cleanup()
        check("H5 unload (cleanup) calls SwipePanel.cleanup()", calls["cleanup"] == 1, f"cleanup calls={calls['cleanup']}")
    finally:
        swipe_module.SwipePanel.cleanup = original_cleanup
        dispose(dialog)
        main_reset(iface)


# ───────────────────────────── T. truthful copy ─────────────────────────────


def test_t_copy(iface):
    print("\nT. Truthful copy (dialog side)")
    from qgis.PyQt.QtWidgets import QAbstractButton, QComboBox, QLabel, QWidget

    dialog_module = __import__(f"{PACKAGE}.dialog", fromlist=["QuickGuideDialog"])
    dialog = new_dialog(iface, show=False)
    try:
        guide = dialog_module.QuickGuideDialog(dialog)
        text = guide.browser.toPlainText()
        strings = [text]
        for widget in dialog.findChildren(QWidget):
            strings.append(widget.toolTip())
            if isinstance(widget, (QLabel, QAbstractButton)):
                strings.append(widget.text())
            if isinstance(widget, QComboBox):
                strings.extend(widget.itemText(i) for i in range(widget.count()))
        blob = "\n".join(strings).lower()
        false_claims = [claim for claim in ("v0.6", "measurement tool", "measuring", "extent box",
                                            "playback speed", "customizable", "advanced")
                        if claim in blob]
        check("T1 the Quick Guide and the dialog's text make no false claim", not false_claims,
              f"found: {false_claims}")
        check("T1 the Quick Guide explains Swipe", "swipe" in text.lower() and "divider" in text.lower(),
              f"guide text starts {text[:120]!r}")
        guide.deleteLater()
    finally:
        dispose(dialog)


# ───────────────────────────── runner ─────────────────────────────


def run(iface):
    print("02Multimap - workspace dialog behaviour (QGIS runtime)")
    for test in (test_t_copy, test_a_sync_holds, test_b_lifecycle, test_c_right_answers,
                 test_d_fits_a_laptop, test_g_small, test_h_swipe, test_e_before_after_fallbacks,
                 test_e_templates_presets_project, test_e6_restore_keeps_main_map):
        try:
            test(iface)
        except Exception:  # noqa: BLE001 - one broken section must not hide the others
            check(f"{test.__name__} ran to the end", False, traceback.format_exc()[-1200:])
    print()
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    APP = QgsApplication([], True)
    APP.initQgis()
    from qgis.PyQt.QtWidgets import QMessageBox

    def _capture(*args, **_kwargs):
        MESSAGES.append(tuple(str(a) for a in args[:3]) + ("",) * (3 - len(args[:3])))
        return QMessageBox.StandardButton.Ok

    # The suite has no user: modal boxes are recorded instead of shown.
    QMessageBox.information = staticmethod(_capture)
    QMessageBox.warning = staticmethod(_capture)
    QMessageBox.critical = staticmethod(_capture)
    # Nothing this suite shows may appear on the desktop of whoever runs it.
    keep_dialogs_off_screen()
    IFACE = StubIface()
    CODE = 1
    try:
        CODE = run(IFACE)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("FAIL")
    sys.stdout.flush()
    sys.stderr.flush()
    # Exit from the frame that holds the QgsApplication (TRAPS 4.5/4.6).
    os._exit(CODE)
