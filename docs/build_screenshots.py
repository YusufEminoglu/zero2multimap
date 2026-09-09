"""Render the real MultiMapDialog headlessly (offscreen) and grab a genuine
screenshot -- no mock-ups, no AI-generated fakes. The existing docs/hero.png
is a stylized marketing graphic that doesn't resemble the real dialog at all
(it invents a fictional "Premium Dashboard" window that never existed); this
script produces an actual capture of the actual widget so a real one can sit
next to it.

Data shown is intentionally, honestly synthetic: four small in-memory demo
layers labelled "Demo Zone A/B/C/D", not invented "real-looking" city names
or statistics -- the point is to show the genuine QGIS chrome (panel
borders, toolbar, checkboxes, KPI chips), not to fabricate a plausible-
looking dataset.

Run:
    set QT_QPA_PLATFORM=offscreen
    C:\\OSGeo4W\\bin\\python-qgis.bat zero2multimap\\docs\\build_screenshots.py

Output: docs/images/*.png. This script and its output are not part of the
plugin zip; images are served from GitHub Pages only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
IMAGES = HERE / "images"

sys.path.insert(0, r"C:\OSGeo4W\apps\qgis\python\plugins")
if str(PLUGIN_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT.parent))

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRendererCategory,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant  # noqa: E402

qgs = QgsApplication([], True)
qgs.initQgis()

from qgis.gui import QgsMapCanvas  # noqa: E402
from qgis.PyQt.QtGui import QFont, QFontDatabase  # noqa: E402

# The offscreen QPA platform doesn't do OS-level font substitution, so a
# QSS font-family chain like "Segoe UI", Inter, Helvetica, Arial falls back
# to whatever last-resort font Qt finds, which renders wrong glyphs for many
# characters (not missing-glyph boxes, actually incorrect letterforms).
# Registering the real Segoe UI file explicitly and setting it as the
# application default is the fix that actually works here.
_font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\segoeui.ttf")
if _font_id != -1:
    _family = QFontDatabase.applicationFontFamilies(_font_id)[0]
    QgsApplication.setFont(QFont(_family, 9))

sys.path.insert(0, str(PLUGIN_ROOT.parent))
from zero2multimap.dialog import MultiMapDialog  # noqa: E402


class _Iface:
    def __init__(self, canvas):
        self._canvas = canvas

    def mainWindow(self):
        return None

    def mapCanvas(self):
        return self._canvas

    def layerTreeView(self):
        return None


def _demo_layer(name: str, cells: list[tuple[float, float, str]]) -> QgsVectorLayer:
    """A tiny in-memory grid of labelled demo zones -- honestly synthetic."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", name, "memory")
    layer.dataProvider().addAttributes([QgsField("zone", QVariant.String)])
    layer.updateFields()
    feats = []
    for x, y, zone in cells:
        ring = [
            QgsPointXY(x, y), QgsPointXY(x + 900, y),
            QgsPointXY(x + 900, y + 900), QgsPointXY(x, y + 900),
        ]
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPolygonXY([ring]))
        f.setAttribute("zone", zone)
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()

    categories = []
    palette = {"A": "#4fc3b6", "B": "#dbbc3f", "C": "#e07a5f", "D": "#5c6b8a"}
    for zone, color in palette.items():
        sym = QgsFillSymbol.createSimple({"color": color, "outline_color": "#1a1a1a"})
        categories.append(QgsRendererCategory(zone, sym, f"Demo Zone {zone}"))
    layer.setRenderer(QgsCategorizedSymbolRenderer("zone", categories))
    return layer


def grab_png(widget, name: str) -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    path = IMAGES / f"{name}.png"
    widget.grab().save(str(path), "PNG")
    print(f"wrote {path.relative_to(PLUGIN_ROOT)} ({path.stat().st_size // 1024} KB)", flush=True)


def main() -> None:
    project = QgsProject.instance()
    cells = [(0, 0, "A"), (1000, 0, "B"), (0, 1000, "C"), (1000, 1000, "D")]
    layer_a = _demo_layer("Demo Zone A layer", [cells[0]])
    layer_b = _demo_layer("Demo Zone B layer", [cells[1]])
    layer_c = _demo_layer("Demo Zone C layer", [cells[2]])
    layer_d = _demo_layer("Demo Zone D layer", [cells[3]])
    project.addMapLayers([layer_a, layer_b, layer_c, layer_d])

    canvas = QgsMapCanvas()
    canvas.setLayers([layer_a, layer_b, layer_c, layer_d])
    combined_extent = layer_a.extent()
    combined_extent.combineExtentWith(layer_d.extent())
    canvas.setExtent(combined_extent)
    canvas.resize(400, 300)

    dialog = MultiMapDialog(_Iface(canvas), parent=None)
    dialog.resize(1180, 790)
    dialog.show()
    for _ in range(10):
        QCoreApplication.processEvents()

    dialog.auto_fill_from_current_extent()
    for _ in range(10):
        QCoreApplication.processEvents()

    grab_png(dialog, "real_dialog_4panel")

    qgs.exitQgis()


if __name__ == "__main__":
    main()
