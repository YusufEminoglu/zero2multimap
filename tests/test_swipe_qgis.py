"""Pixel checks for the swipe view (02Multimap 1.3, item H) on a real QGIS runtime.

The swipe panel paints a second ("top") layer set over one side of a divider.
These checks read pixels from the grabbed widget: which colour sits on which
side, whether dragging or the API moves the boundary, whether the top picture
is re-rendered in register with the canvas after the view changes (zoom, pan,
rotation, CRS, size), and whether ``cleanup()`` leaves no render running.
Positions go through the device pixel ratio, so the same checks hold when the
suite is run with ``QT_SCALE_FACTOR=2``.

Run:  python-qgis-ltr.bat tests/test_swipe_qgis.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

# Headless unless told otherwise. Nothing here rasterises text, so the offscreen
# platform's missing font engine (TRAPS 4.3) does not matter.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if not os.environ.get("QGIS_CUSTOM_CONFIG_PATH"):  # TRAPS 4.6
    os.environ["QGIS_CUSTOM_CONFIG_PATH"] = tempfile.mkdtemp(prefix="mm_swipe_profile_")

from qgis.core import (  # noqa: E402
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsMapRendererJob,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas  # noqa: E402
from qgis.PyQt import sip  # noqa: E402
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt  # noqa: E402
from qgis.PyQt.QtGui import QColor, QKeyEvent, QMouseEvent  # noqa: E402

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_ROOT = os.path.dirname(PLUGIN_DIR)
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from zero2multimap.swipe_view import SwipePanel  # noqa: E402

RESULTS = []

WHITE = (255, 255, 255)      # canvas background in these checks
BOTTOM = (30, 90, 200)       # blue: the bottom layer set
TOP = (220, 40, 40)          # red: the top layer set
OTHER = (40, 160, 70)        # green: a replacement top layer
RESTYLED = (235, 190, 0)     # amber: the replacement after a style change

# Every fill layer covers the ground below this northing, so each view has one
# horizontal feature edge with the background above it.
EDGE_Y = 240.0
VIEW = QgsRectangle(0.0, 0.0, 400.0, 300.0)
# Columns (share of the viewport width) where the edge position is compared.
COLUMNS = (0.03, 0.15, 0.3, 0.5, 0.7, 0.85, 0.97)

# QgsMapCanvas keeps weak layer pointers: the test owns its layers.
_RETAINED = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition and detail:
        print(f"        {detail}")
    return bool(condition)


# ---------------------------------------------------------------- event loop
def pump(ms=50):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.002)


def wait_until(predicate, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.003)
    return predicate()


def busy(panel):
    return (panel.canvas().isDrawing() or panel._render_timer.isActive()
            or panel._job is not None)


def settle(panel, quiet_ms=300, timeout_s=20.0):
    """Wait until the canvas and the top-set render have both been idle for a while."""
    deadline = time.monotonic() + timeout_s
    quiet_since = None
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        now = time.monotonic()
        if busy(panel):
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        elif (now - quiet_since) * 1000.0 >= quiet_ms:
            return True
        time.sleep(0.003)
    print("        (render did not settle in time)")
    return False


# ---------------------------------------------------------------- fixtures
def restyle(layer, rgb):
    props = {"color": "%d,%d,%d,255" % rgb, "outline_style": "no"}
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(props)))


def fill_layer(name, rgb, rects=None):
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857&field=id:integer", name, "memory")
    features = []
    for index, rect in enumerate(rects or [QgsRectangle(-1.0e6, -1.0e6, 1.0e6, EDGE_Y)]):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([index])
        feature.setGeometry(QgsGeometry.fromRect(rect))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    restyle(layer, rgb)
    _RETAINED.append(layer)
    return layer


def heavy_layer(rgb, cells=120):
    """Many small squares below the edge: slow enough to catch a render running."""
    size = 400.0 / cells
    rects = [
        QgsRectangle(i * size, j * size, (i + 0.9) * size, (j + 0.9) * size)
        for i in range(cells) for j in range(cells) if (j + 1) * size <= EDGE_Y
    ]
    return fill_layer("heavy", rgb, rects)


def move_view(canvas, extent=None, centre=None, rotation=None):
    """Change the view the way the dialog's sync does: set it, then refresh.

    ``setExtent``, ``setCenter`` and ``setRotation`` do not redraw a canvas on
    their own; without the refresh the bottom set would be the stale picture.
    """
    if extent is not None:
        canvas.setExtent(extent)
    if centre is not None:
        canvas.setCenter(centre)
    if rotation is not None:
        canvas.setRotation(rotation)
    canvas.refresh()


def make_panel(width=400, height=300):
    panel = SwipePanel()
    canvas = panel.canvas()
    canvas.setCanvasColor(QColor(*WHITE))
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    panel.resize(width, height)
    panel.show()
    pump(50)
    move_view(canvas, extent=VIEW)
    return panel


# ---------------------------------------------------------------- pixels
def grab(panel):
    pump(20)
    return panel.grab().toImage()


def viewport_box(panel):
    """The map viewport inside the panel, in logical pixels."""
    viewport = panel.canvas().viewport()
    origin = viewport.mapTo(panel, QPoint(0, 0))
    return origin.x(), origin.y(), viewport.width(), viewport.height()


def rgb_at(image, x, y):
    """Colour at a logical panel position."""
    ratio = image.devicePixelRatio()
    value = image.pixel(int(x * ratio), int(y * ratio))
    return (value >> 16) & 255, (value >> 8) & 255, value & 255


def near(rgb, expected, tolerance=40):
    return all(abs(a - b) <= tolerance for a, b in zip(rgb, expected))


def nearest(rgb, palette=(TOP, BOTTOM, WHITE)):
    """The palette colour ``rgb`` is closest to; edge pixels blend into the background."""
    return min(palette, key=lambda colour: sum((p - q) ** 2 for p, q in zip(rgb, colour)))


def misplaced(panel, image, vertical, ratio, top_rgb=TOP, bottom_rgb=BOTTOM):
    """Points that do not show the layer set their side of the divider should show."""
    x0, y0, width, height = viewport_box(panel)
    length = width if vertical else height
    divider = length * ratio
    wrong = []
    for step in range(2, 99, 3):
        along = length * step / 100.0
        if abs(along - divider) < 10.0:  # the handle is 12 px wide
            continue
        if not vertical and along < height * 0.3:  # above the feature edge
            continue
        expected = top_rgb if along < divider else bottom_rgb
        for across in ((45, 65, 85) if vertical else (15, 50, 85)):
            if vertical:
                x, y = x0 + along, y0 + height * across / 100.0
            else:
                x, y = x0 + width * across / 100.0, y0 + along
            got = rgb_at(image, x, y)
            if not near(got, expected):
                wrong.append(((round(x), round(y)), got, expected))
    return wrong


def edge_rows(panel, image, fill):
    """Device row where ``fill`` starts, scanning down each sampled column.

    A pixel counts only when ``fill`` is its nearest colour, so where the top
    picture is missing the bottom set's edge cannot pass for the top set's.
    """
    x0, y0, width, height = viewport_box(panel)
    ratio = image.devicePixelRatio()
    first, last = int(y0 * ratio), int((y0 + height) * ratio)
    rows = []
    for share in COLUMNS:
        x = int((x0 + width * share) * ratio)
        found = None
        for y in range(first, last):
            value = image.pixel(x, y)
            if nearest(((value >> 16) & 255, (value >> 8) & 255, value & 255)) == fill:
                found = y
                break
        rows.append(found)
    return rows, first


def check_alignment(panel, label):
    """The top set alone and the bottom set alone must put the edge on the same rows."""
    settled = settle(panel)
    keep = panel.divider_ratio()
    panel.set_divider_ratio(1.0)
    top_image = grab(panel)
    panel.set_divider_ratio(0.0)
    bottom_image = grab(panel)
    panel.set_divider_ratio(keep)
    top_rows, first = edge_rows(panel, top_image, TOP)
    bottom_rows, _first = edge_rows(panel, bottom_image, BOTTOM)
    aligned = all(
        t is not None and b is not None and t > first + 1 and abs(t - b) <= 1
        for t, b in zip(top_rows, bottom_rows)
    )
    return check(f"{label}: the top set is re-rendered in register with the canvas",
                 settled and aligned,
                 f"settled={settled} edge rows top={top_rows} bottom={bottom_rows}")


def send_mouse(widget, kind, local, button, buttons):
    event = QMouseEvent(kind, QPointF(local), button, buttons, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(widget, event)


def drag_divider(panel, share, vertical):
    """Press on the handle, move in six steps to ``share`` of the viewport, release."""
    handle = panel._handle
    x0, y0, width, height = viewport_box(panel)
    left = Qt.MouseButton.LeftButton
    start = QPoint(handle.width() // 2, handle.height() * 4 // 5) if vertical \
        else QPoint(handle.width() * 4 // 5, handle.height() // 2)
    send_mouse(handle, QEvent.Type.MouseButtonPress, start, left, left)
    begin = handle.mapToParent(start)
    if vertical:
        target = QPoint(round(x0 + width * share), begin.y())
    else:
        target = QPoint(begin.x(), round(y0 + height * share))
    for step in range(1, 7):
        point = QPoint(round(begin.x() + (target.x() - begin.x()) * step / 6.0),
                       round(begin.y() + (target.y() - begin.y()) * step / 6.0))
        send_mouse(handle, QEvent.Type.MouseMove, handle.mapFromParent(point),
                   Qt.MouseButton.NoButton, left)
    send_mouse(handle, QEvent.Type.MouseButtonRelease, handle.mapFromParent(target),
               left, Qt.MouseButton.NoButton)


def press_key(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    code = int(getattr(key, "value", key))
    QCoreApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, code, modifiers))


# ---------------------------------------------------------------- checks
def test_contract():
    panel = SwipePanel()
    canvas = panel.canvas()
    check("canvas() is the panel's QgsMapCanvas, the same object every call",
          isinstance(canvas, QgsMapCanvas) and canvas is panel.canvas())
    check("the divider starts in the middle", abs(panel.divider_ratio() - 0.5) < 1e-9,
          f"ratio={panel.divider_ratio()}")
    emitted = []
    panel.layers_changed.connect(lambda: emitted.append(1))
    panel.set_layers([], [])
    check("layers_changed is emitted once per set_layers()", len(emitted) == 1, f"emitted={len(emitted)}")
    panel.set_divider_ratio(1.7)
    high = panel.divider_ratio()
    panel.set_divider_ratio(-0.4)
    low = panel.divider_ratio()
    check("set_divider_ratio() clamps to 0.0-1.0", high == 1.0 and low == 0.0, f"high={high} low={low}")
    panel.cleanup()


def test_vertical_split(panel, bottom, top):
    panel.set_layers([bottom], [top])
    settle(panel)
    image = grab(panel)
    canvas = panel.canvas()
    ratio = image.devicePixelRatio()
    print(f"        device pixel ratio {ratio:g}; canvas {canvas.mapSettings().devicePixelRatio():g}")
    wrong = misplaced(panel, image, True, 0.5)
    check("vertical divider: top set on the left, bottom set on the right (99 points each side)",
          not wrong, f"{len(wrong)} wrong, first {wrong[:3]}")
    x0, y0, width, height = viewport_box(panel)
    above = [rgb_at(image, x0 + width * share, y0 + height * 0.1) for share in (0.25, 0.75)]
    check("above the feature edge both sides show the canvas background",
          all(near(rgb, WHITE, 10) for rgb in above), f"got {above}")
    line = rgb_at(image, x0 + round(width * 0.5), y0 + height * 0.8)
    check("the divider line is drawn on the boundary", near(line, WHITE, 30), f"got {line}")
    picture = panel._item._image
    settings = canvas.mapSettings()
    check("the top picture has the canvas's device pixel ratio and device size (sharp on high-DPI)",
          picture is not None
          and picture.devicePixelRatio() == settings.devicePixelRatio()
          and picture.size() == settings.deviceOutputSize(),
          f"picture={None if picture is None else (picture.size(), picture.devicePixelRatio())} "
          f"canvas={settings.deviceOutputSize()}, {settings.devicePixelRatio()}")


def test_horizontal_split(panel):
    panel.set_orientation(False)
    panel.set_divider_ratio(0.6)
    image = grab(panel)
    wrong = misplaced(panel, image, False, 0.6)
    check("horizontal divider: top set above, bottom set below", not wrong,
          f"{len(wrong)} wrong, first {wrong[:3]}")
    check("the flat divider shows a vertical-resize cursor",
          panel._handle.cursor().shape() == Qt.CursorShape.SplitVCursor)
    panel.set_orientation(True)
    check("the upright divider shows a horizontal-resize cursor",
          panel._handle.cursor().shape() == Qt.CursorShape.SplitHCursor)
    check("changing the orientation keeps the divider position",
          abs(panel.divider_ratio() - 0.6) < 1e-9, f"ratio={panel.divider_ratio()}")
    panel.set_divider_ratio(0.5)


def test_divider_moves(panel):
    panel.set_divider_ratio(0.25)
    idle = not panel._render_timer.isActive() and panel._job is None
    check("moving the divider repaints without starting a new render", idle)
    wrong = misplaced(panel, grab(panel), True, 0.25)
    check("set_divider_ratio(0.25) moves the boundary to a quarter of the width", not wrong,
          f"{len(wrong)} wrong, first {wrong[:3]}")

    _x0, _y0, width, height = viewport_box(panel)
    drag_divider(panel, 0.7, True)
    ratio = panel.divider_ratio()
    check("dragging the handle moves the divider to the pointer",
          abs(ratio - 0.7) <= 2.0 / width, f"ratio={ratio:.4f}")
    wrong = misplaced(panel, grab(panel), True, ratio)
    check("after the drag the boundary is drawn where the divider is", not wrong,
          f"{len(wrong)} wrong, first {wrong[:3]}")

    before = panel.divider_ratio()
    press_key(panel._handle, Qt.Key.Key_Right)
    after_right = panel.divider_ratio()
    press_key(panel._handle, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    after_shift = panel.divider_ratio()
    check("arrow keys nudge the divider (Right +0.01, Shift+Left -0.1)",
          abs(after_right - before - 0.01) < 1e-9 and abs(after_shift - after_right + 0.1) < 1e-9,
          f"{before:.4f} -> {after_right:.4f} -> {after_shift:.4f}")

    panel.set_orientation(False)
    drag_divider(panel, 0.4, False)
    ratio = panel.divider_ratio()
    flat_ok = abs(ratio - 0.4) <= 2.0 / height
    wrong = misplaced(panel, grab(panel), False, ratio)
    check("dragging a flat divider moves it vertically", flat_ok and not wrong,
          f"ratio={ratio:.4f}; {len(wrong)} wrong, first {wrong[:3]}")
    panel.set_orientation(True)
    panel.set_divider_ratio(0.5)


def test_follows_view_changes(panel):
    canvas = panel.canvas()
    canvas.zoomByFactor(2.0)
    check_alignment(panel, "zoom out x2")
    centre = canvas.center()
    move_view(canvas, centre=QgsPointXY(centre.x() + 60.0, centre.y() - 30.0))
    check_alignment(panel, "pan")
    move_view(canvas, rotation=10.0)
    check_alignment(panel, "rotation 10 degrees")
    move_view(canvas, rotation=0.0)
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    canvas.refresh()
    check_alignment(panel, "CRS changed to EPSG:4326")
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    move_view(canvas, extent=VIEW)
    settle(panel)
    # The canvas keeps its extent when resized; widening the panel this much
    # uncovers about 120 px on each side that only a new render can fill.
    panel.resize(640, 300)
    pump(700)  # the canvas refreshes itself 500 ms after a resize
    check_alignment(panel, "panel resized")
    panel.resize(400, 300)
    pump(700)
    move_view(canvas, extent=VIEW)
    settle(panel)

    canvas.setParallelRenderingEnabled(True)
    centre = canvas.center()
    move_view(canvas, centre=QgsPointXY(centre.x() + 15.0, centre.y()))
    check_alignment(panel, "parallel renderer, pan")
    canvas.setParallelRenderingEnabled(False)
    move_view(canvas, extent=VIEW)
    settle(panel)


def test_top_set_changes(panel, bottom, top):
    other = fill_layer("other", OTHER)
    panel.set_layers([bottom], [other])
    settle(panel)
    wrong = misplaced(panel, grab(panel), True, 0.5, top_rgb=OTHER)
    check("a new top set replaces the old one", not wrong, f"{len(wrong)} wrong, first {wrong[:3]}")
    restyle(other, RESTYLED)
    other.triggerRepaint()
    settle(panel)
    wrong = misplaced(panel, grab(panel), True, 0.5, top_rgb=RESTYLED)
    check("a top layer's repaint request re-renders it", not wrong,
          f"{len(wrong)} wrong, first {wrong[:3]}")
    panel.set_layers([bottom], [top])
    settle(panel)


def test_newer_render_cancels_older(panel, bottom):
    canvas = panel.canvas()
    slow = heavy_layer(TOP)
    panel.set_layers([bottom], [slow])
    settle(panel)
    canvas.setCenter(QgsPointXY(210.0, 150.0))
    panel._start_render()  # start now instead of after the 150 ms wait
    first = panel._job
    canvas.setCenter(QgsPointXY(190.0, 150.0))
    panel._start_render()
    second = panel._job
    check("a newer render replaces the running one",
          first is not None and second is not None and first is not second)
    stopped = first is not None and wait_until(lambda: sip.isdeleted(first) or not first.isActive(), 5.0)
    check("the replaced render stops", stopped)
    settle(panel)
    # The picture is anchored at the map position of the view's top-left pixel
    # when it was rendered: the first render's anchor is 20 m east of this one.
    anchor = panel._item.rect()
    corner = canvas.mapSettings().mapToPixel().toMapCoordinates(0, 0)
    tolerance = canvas.mapUnitsPerPixel() * 1e-3
    check("the picture on screen comes from the newest render",
          abs(anchor.xMinimum() - corner.x()) < tolerance and abs(anchor.yMaximum() - corner.y()) < tolerance,
          f"anchor=({anchor.xMinimum():.4f}, {anchor.yMaximum():.4f}) view=({corner.x():.4f}, {corner.y():.4f})")
    panel.set_layers([bottom], [fill_layer("top again", TOP)])
    move_view(canvas, extent=VIEW)
    settle(panel)


def test_hidden_panel_does_no_work(panel):
    canvas = panel.canvas()
    panel.hide()
    pump(50)
    centre = canvas.center()
    move_view(canvas, centre=QgsPointXY(centre.x() - 40.0, centre.y() + 20.0))
    pump(400)
    check("a hidden panel starts no top-set render",
          panel._job is None and not panel._render_timer.isActive())
    panel.show()
    check_alignment(panel, "shown again after a change made while hidden")
    move_view(canvas, extent=VIEW)
    settle(panel)


def test_removed_top_layer(panel, bottom, top):
    doomed = fill_layer("doomed", OTHER)
    QgsProject.instance().addMapLayer(doomed, False)
    panel.set_layers([bottom], [doomed])
    settle(panel)
    shown = not misplaced(panel, grab(panel), True, 0.5, top_rgb=OTHER)
    QgsProject.instance().removeMapLayer(doomed.id())  # deletes the layer
    _RETAINED.remove(doomed)
    del doomed
    settled = settle(panel)
    wrong = misplaced(panel, grab(panel), True, 0.5, top_rgb=BOTTOM)
    check("a top layer removed from the project leaves only the bottom set, divider hidden",
          shown and settled and not wrong and not panel._handle.isVisible(),
          f"shown before={shown} settled={settled}; {len(wrong)} wrong, first {wrong[:3]}")
    panel.set_layers([bottom], [top])
    settle(panel)


def test_empty_top_set(panel, bottom, top):
    panel.set_layers([bottom], [top])
    settle(panel)
    panel.set_layers([bottom], [])
    settled = settle(panel)
    image = grab(panel)
    x0, y0, width, height = viewport_box(panel)
    row = [rgb_at(image, x0 + x, y0 + height * 0.8) for x in range(2, width - 2)]
    off = [(index + 2, rgb) for index, rgb in enumerate(row) if not near(rgb, BOTTOM)]
    check("an empty top set paints nothing: the whole row is the bottom set, divider included",
          settled and not off and not panel._handle.isVisible(),
          f"settled={settled}; {len(off)} pixels off, first {off[:3]}")
    check("an empty top set leaves no render running or scheduled",
          panel._job is None and not panel._render_timer.isActive())
    panel.set_layers([bottom], [top])
    settle(panel)


def test_cleanup():
    panel = make_panel()
    canvas = panel.canvas()
    bottom = fill_layer("cleanup bottom", BOTTOM)
    slow = heavy_layer(TOP)
    signals = ("repaintRequested", "willBeDeleted")
    layer_baseline = {name: slow.receivers(getattr(slow, name)) for name in signals}
    panel.set_layers([bottom], [slow])
    settle(panel)
    canvas.setCenter(QgsPointXY(230.0, 140.0))
    panel._start_render()
    job = panel._job
    check("a top-set render is running when cleanup() is called",
          job is not None and job.isActive())
    panel.cleanup()
    check("cleanup() stops the running render before it returns",
          job is None or not job.isActive())
    running = [j for j in panel.findChildren(QgsMapRendererJob) if j.isActive()]
    check("no render of the panel is left running", not running and panel._job is None,
          f"running={len(running)}")
    check("cleanup() stops the render timer", not panel._render_timer.isActive())

    fresh = QgsMapCanvas()
    names = ("extentsChanged", "scaleChanged", "rotationChanged", "destinationCrsChanged",
             "mapCanvasRefreshed")
    counts = {name: (canvas.receivers(getattr(canvas, name)), fresh.receivers(getattr(fresh, name)))
              for name in names}
    check("cleanup() disconnects every canvas signal (receivers as on a fresh canvas)",
          all(mine == base for mine, base in counts.values()), f"(panel, fresh)={counts}")
    layer_now = {name: slow.receivers(getattr(slow, name)) for name in signals}
    check("cleanup() disconnects the top layers' signals", layer_now == layer_baseline,
          f"before={layer_baseline} after={layer_now}")
    check("cleanup() takes the top picture off the canvas and drops the layers",
          panel._item.scene() is None and canvas.layers() == [])

    second_ok = True
    try:
        panel.cleanup()
    except Exception:  # noqa: BLE001 - the check reports it
        traceback.print_exc()
        second_ok = False
    check("a second cleanup() is a harmless no-op", second_ok)
    move_view(canvas, extent=VIEW, rotation=5.0)
    pump(400)
    check("after cleanup() view changes start no render",
          panel._job is None and not panel._render_timer.isActive()
          and not [j for j in panel.findChildren(QgsMapRendererJob) if j.isActive()])
    panel.hide()


def run_case(function, *args):
    try:
        function(*args)
    except Exception:  # noqa: BLE001 - recorded as a failure, the suite goes on
        traceback.print_exc()
        check(f"{function.__name__} ran without an exception", False)


def run():
    print(f"02Multimap - swipe view (QGIS {Qgis.QGIS_VERSION}, "
          f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM')}, "
          f"QT_SCALE_FACTOR={os.environ.get('QT_SCALE_FACTOR', '1')})")
    print()
    run_case(test_contract)
    panel = None
    try:
        panel = make_panel()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("a SwipePanel can be built and shown", False)
    if panel is not None:
        bottom = fill_layer("bottom", BOTTOM)
        top = fill_layer("top", TOP)
        run_case(test_vertical_split, panel, bottom, top)
        run_case(test_horizontal_split, panel)
        run_case(test_divider_moves, panel)
        run_case(test_follows_view_changes, panel)
        run_case(test_top_set_changes, panel, bottom, top)
        run_case(test_newer_render_cancels_older, panel, bottom)
        run_case(test_hidden_panel_does_no_work, panel)
        run_case(test_removed_top_layer, panel, bottom, top)
        run_case(test_empty_top_set, panel, bottom, top)
        run_case(panel.cleanup)
    run_case(test_cleanup)

    print()
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", ""), True)
    app = QgsApplication([], True)
    app.initQgis()
    code = 1
    try:
        code = run()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("FAIL")
    sys.stdout.flush()
    # Exit from the frame that holds the application (TRAPS 4.5, 4.6).
    os._exit(code)
