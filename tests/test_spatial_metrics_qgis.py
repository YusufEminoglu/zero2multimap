"""Value-level checks for 02Multimap's CRS-aware area maths, on a real QGIS runtime.

These assertions are about *numbers*, not about importability. Every bug this
release fixes passed a load-only check: the KPI bar and the difference engine
both ran happily and printed confidently wrong values (1.76x inflated in EPSG:3857,
"0.00 ha" for a degree square in EPSG:4326, and a silently empty intersection
whenever the layer CRS differed from the canvas CRS).

Run:  python-qgis-ltr.bat tests/test_spatial_metrics_qgis.py
"""
from __future__ import annotations

import os
import sys
import traceback

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas  # noqa: E402

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_ROOT = os.path.dirname(PLUGIN_DIR)
for _path in (PACKAGE_ROOT, PLUGIN_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from spatial_metrics import (  # noqa: E402
    compare_layers,
    ellipsoidal_area,
    format_area,
    make_area_calculator,
    resolve_ellipsoid,
    transform_geometry,
)

RESULTS = []

# A 1 km x 1 km square in Web Mercator at 41 deg N (Ankara's latitude).
MERCATOR_Y0 = 5009377.085697312
SQUARE_3857 = QgsRectangle(2900000.0, MERCATOR_Y0, 2901000.0, MERCATOR_Y0 + 1000.0)
# The planimetric area of that square in EPSG:3857 is exactly 1 000 000 m^2; its
# true ground area is the value below, independently confirmed by reprojecting it
# into EPSG:32635 (UTM 35N, the zone that actually covers that point).
SQUARE_GROUND_M2 = 569_334.385


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition and detail:
        print(f"        {detail}")
    return bool(condition)


def close(actual, expected, rel_tolerance):
    if expected == 0:
        return abs(actual) <= rel_tolerance
    return abs(actual - expected) / abs(expected) <= rel_tolerance


# QgsMapCanvas holds raw QgsMapLayer pointers, not owning references. A layer
# built inline and handed straight to setLayers() is garbage-collected as soon as
# the call returns, and canvas.layers() then reports no layers at all -- which
# reads exactly like "the KPI bar found nothing", not like a lifetime bug.
_RETAINED_LAYERS = []


def make_layer(name, crs_authid, rectangle):
    layer = QgsVectorLayer(f"Polygon?crs={crs_authid}&field=id:integer", name, "memory")
    assert layer.isValid(), f"could not build memory layer {name}"
    feature = QgsFeature(layer.fields())
    feature.setAttributes([1])
    feature.setGeometry(QgsGeometry.fromRect(rectangle))
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    _RETAINED_LAYERS.append(layer)
    return layer


def test_ellipsoid_is_never_left_unset():
    """A bare project reports "NONE", which disables ellipsoidal measurement."""
    project_ellipsoid = QgsProject.instance().ellipsoid()
    check(
        "bare project ellipsoid is the NONE placeholder (the trap this guards)",
        resolve_ellipsoid(project_ellipsoid) == "WGS84",
        f"project={project_ellipsoid!r} resolved={resolve_ellipsoid(project_ellipsoid)!r}",
    )
    calculator = make_area_calculator(QgsCoordinateReferenceSystem("EPSG:3857"))
    check("area calculator actually uses the ellipsoid", calculator.willUseEllipsoid())


def test_web_mercator_area_is_not_inflated():
    geometry = QgsGeometry.fromRect(SQUARE_3857)
    naive = geometry.area()
    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    measured = ellipsoidal_area(geometry, crs)

    check("planimetric 3857 area of the square is exactly 1 000 000 m^2",
          abs(naive - 1_000_000.0) < 1e-6, f"naive={naive!r}")
    check(f"ellipsoidal area is {SQUARE_GROUND_M2:,.0f} m^2 (within 0.5 %)",
          close(measured, SQUARE_GROUND_M2, 0.005), f"measured={measured:,.3f}")

    # Independent cross-check: reproject the square into UTM 35N and measure there.
    utm = QgsCoordinateReferenceSystem("EPSG:32635")
    moved = transform_geometry(geometry, crs, utm)
    check("square reprojects into UTM 35N", moved is not None)
    utm_area = moved.area() if moved is not None else 0.0
    check("independent UTM 35N measurement agrees within 0.2 %",
          close(utm_area, measured, 0.002), f"utm={utm_area:,.3f} ellipsoidal={measured:,.3f}")
    check("the inflation the old code reported was 1.76x",
          1.74 < (naive / measured) < 1.78, f"ratio={naive / measured:.4f}")


def test_geographic_area_is_metres_not_degrees():
    """An EPSG:4326 geometry has a degree^2 area; the chip used to print "0.00 ha"."""
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    degree_square = QgsGeometry.fromRect(QgsRectangle(26.0, 40.0, 27.0, 41.0))
    measured = ellipsoidal_area(degree_square, crs)

    check("degree square reports ~9 413 km^2 of ground, within 1 %",
          close(measured, 9_412_788_745.689, 0.01), f"measured={measured:,.3f}")
    check("format_area renders it in km^2, never as hectares",
          format_area(measured) == "9412.79 km²", f"got {format_area(measured)!r}")
    check("the raw degree^2 area would have formatted as 0.00 ha",
          format_area(degree_square.area()) == "0.00 ha",
          f"raw={degree_square.area()!r} -> {format_area(degree_square.area())!r}")


def test_extent_must_be_reprojected_before_clipping():
    """Canvas extent in one CRS intersected with a layer in another is EMPTY."""
    mercator_crs = QgsCoordinateReferenceSystem("EPSG:3857")
    canvas_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    region_4326 = QgsGeometry.fromRect(QgsRectangle(26.0, 40.0, 27.0, 41.0))
    square = QgsGeometry.fromRect(SQUARE_3857)

    naive = region_4326.intersection(square)
    check("unreprojected intersection is silently empty (the bug)",
          naive is None or naive.isEmpty(), f"area={naive.area() if naive else None!r}")

    reprojected = transform_geometry(region_4326, canvas_crs, mercator_crs)
    check("the extent reprojects into the layer CRS", reprojected is not None)
    fixed = reprojected.intersection(square) if reprojected is not None else None
    check("reprojected intersection holds the whole square",
          fixed is not None and close(fixed.area(), 1_000_000.0, 1e-6),
          f"area={fixed.area() if fixed else None!r}")


def test_comparison_across_different_crs_finds_no_change():
    """The difference engine's silent failure: two CRSs, one confident wrong answer."""
    mercator_crs = QgsCoordinateReferenceSystem("EPSG:3857")
    layer_a = make_layer("panel1", "EPSG:3857", SQUARE_3857)

    # The same ground square, stored in EPSG:4326 as a layer B would be.
    to_4326 = QgsCoordinateTransform(
        mercator_crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()
    )
    bounds_4326 = to_4326.transformBoundingBox(SQUARE_3857)
    layer_b = make_layer("panel2", "EPSG:4326", bounds_4326)

    a_center = SQUARE_3857.center()
    b_center = bounds_4326.center()
    check("the two layers really are in different coordinate spaces",
          abs(a_center.x() - b_center.x()) > 1e5 and abs(a_center.y() - b_center.y()) > 1e5,
          f"A center=({a_center.x():,.1f}, {a_center.y():,.1f}) "
          f"B center=({b_center.x():.4f}, {b_center.y():.4f})")

    result = compare_layers(
        layer_a, layer_b, QgsGeometry.fromRect(SQUARE_3857), mercator_crs
    )
    check("identical ground footprints overlap ~100 %",
          result.overlap_percent > 99.9, f"overlap={result.overlap_percent:.3f}%")
    check("no phantom 'added' area from the CRS mismatch",
          result.added_area_m2 < SQUARE_GROUND_M2 * 0.01,
          f"added={result.added_area_m2:,.3f} m^2")
    check("no phantom 'removed' area from the CRS mismatch",
          result.removed_area_m2 < SQUARE_GROUND_M2 * 0.01,
          f"removed={result.removed_area_m2:,.3f} m^2")
    check("reported areas are ellipsoidal, not 3857 planimetric",
          close(result.area_a_m2, SQUARE_GROUND_M2, 0.005)
          and close(result.area_b_m2, SQUARE_GROUND_M2, 0.005),
          f"a={result.area_a_m2:,.3f} b={result.area_b_m2:,.3f}")


def test_comparison_still_detects_a_real_change():
    """A widened polygon must show up as added ground area, in real units."""
    mercator_crs = QgsCoordinateReferenceSystem("EPSG:3857")
    wider = QgsRectangle(SQUARE_3857.xMinimum(), SQUARE_3857.yMinimum(),
                         SQUARE_3857.xMaximum() + 500.0, SQUARE_3857.yMaximum())
    layer_a = make_layer("panel1", "EPSG:3857", SQUARE_3857)
    layer_b = make_layer("panel2", "EPSG:3857", wider)

    result = compare_layers(
        layer_a, layer_b, QgsGeometry.fromRect(wider), mercator_crs
    )
    expected_added = SQUARE_GROUND_M2 / 2.0  # a 500 m wide strip, same latitude
    check("500 m widening is reported as added ground area (within 1 %)",
          close(result.added_area_m2, expected_added, 0.01),
          f"added={result.added_area_m2:,.3f} expected={expected_added:,.3f}")
    check("nothing is reported as removed",
          result.removed_area_m2 < 1.0, f"removed={result.removed_area_m2:,.3f}")
    # Overlap is stated as a share of panel 1, and panel 1 sits entirely inside
    # the widened panel 2 — so 100 %, while panel 2 is half again as large.
    check("panel 1 is fully covered by panel 2 (overlap 100 %)",
          result.overlap_percent > 99.9, f"overlap={result.overlap_percent:.3f}%")
    check("panel 2 measures 1.5x panel 1",
          close(result.area_b_m2 / result.area_a_m2, 1.5, 0.01),
          f"ratio={result.area_b_m2 / result.area_a_m2:.4f}")

    # The clip region bounds the comparison: half the extent, half the area.
    half = QgsRectangle(SQUARE_3857.xMinimum(), SQUARE_3857.yMinimum(),
                        SQUARE_3857.xMinimum() + 500.0, SQUARE_3857.yMaximum())
    clipped = compare_layers(layer_a, layer_b, QgsGeometry.fromRect(half), mercator_crs)
    check("clipping to a half-extent halves the measured area",
          close(clipped.area_a_m2, SQUARE_GROUND_M2 / 2.0, 0.01),
          f"area={clipped.area_a_m2:,.3f}")


class StubIface:
    """``update_kpi_bar`` reads only the panel's own canvas."""

    def __init__(self):
        self._canvas = QgsMapCanvas()

    def mapCanvas(self):
        return self._canvas


def test_kpi_chip_carries_the_corrected_number():
    """The chip itself, not just the helper: this is what the user reads.

    Both cases below are rendered by the real MapPanelWidget. Before the fix the
    first printed ``100.00 ha`` for a footprint that is 56.93 ha of ground, and
    the second printed ``0.00 ha`` for 9 413 km².
    """
    from zero2multimap.dialog import MapPanelWidget

    panel = MapPanelWidget(0, StubIface())
    panel.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    panel.canvas.setExtent(SQUARE_3857)
    panel.canvas.setLayers([make_layer("merc", "EPSG:3857", SQUARE_3857)])
    panel.update_kpi_bar()
    chip = panel.kpi_bar.text()

    check("Mercator chip reads the true 56.93 ha",
          "56.93 ha" in chip, f"chip={chip!r}")
    check("Mercator chip no longer reads the inflated 100.00 ha",
          "100.00 ha" not in chip, f"chip={chip!r}")
    check("KPI tooltip names the ellipsoid it measured with",
          "WGS84" in panel.kpi_bar.toolTip(), f"tip={panel.kpi_bar.toolTip()!r}")

    # Same widget, a geographic layer: the case that used to print "0.00 ha".
    panel.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    panel.canvas.setExtent(QgsRectangle(26.0, 40.0, 27.0, 41.0))
    panel.canvas.setLayers([make_layer("geo", "EPSG:4326", QgsRectangle(26.0, 40.0, 27.0, 41.0))])
    panel.update_kpi_bar()
    chip = panel.kpi_bar.text()
    check("geographic chip reads 9412.79 km², not 0.00 ha",
          "9412.79 km²" in chip and "0.00 ha" not in chip, f"chip={chip!r}")


def test_utm_data_keeps_its_values():
    """Projected metre data were already right; the fix must not change them."""
    utm_crs = QgsCoordinateReferenceSystem("EPSG:32635")
    square = QgsGeometry.fromRect(QgsRectangle(500000.0, 4400000.0, 501000.0, 4401000.0))
    measured = ellipsoidal_area(square, utm_crs)
    check("1 km^2 of UTM 35N stays within 0.2 % of the planimetric value",
          close(measured, 1_000_000.0, 0.002), f"measured={measured:,.3f}")
    check("but is not identical to it (measurement is still ellipsoidal)",
          abs(measured - 1_000_000.0) > 1.0, f"measured={measured:,.3f}")


def test_eight_panel_print_layout_and_cartographic_elements():
    """Verify 8-panel (2x4) Print Layout export includes visible North Arrow, Scale Bar, and Legend."""
    import tempfile
    from qgis.core import (
        QgsGraduatedSymbolRenderer,
        QgsLayoutExporter,
        QgsLayoutItemLabel,
        QgsLayoutItemLegend,
        QgsLayoutItemMap,
        QgsLayoutItemPicture,
        QgsLayoutItemScaleBar,
        QgsRendererRange,
        QgsSymbol,
    )
    from zero2multimap.dialog import MapPanelWidget
    from zero2multimap.print_layout import PrintLayoutDialog, resolve_north_arrow_svg

    class DummyParentDialog:
        def __init__(self, panels):
            self.rows = 2
            self.cols = 4
            self.panels = panels

        def styleSheet(self):
            return ""

    iface = StubIface()
    panels = []
    for i in range(8):
        p = MapPanelWidget(i, iface)
        crs_code = "EPSG:4326" if i % 2 == 1 else "EPSG:3857"
        rect = QgsRectangle(26.0, 38.4, 26.2, 38.6) if crs_code == "EPSG:4326" else SQUARE_3857
        lyr = make_layer(f"Scenario_{i + 1}", crs_code, rect)
        if i == 0:
            sym = QgsSymbol.defaultSymbol(lyr.geometryType())
            rng = QgsRendererRange(0.0, 10.0, sym, "Low-Mid Density")
            grad = QgsGraduatedSymbolRenderer("id", [rng])
            lyr.setRenderer(grad)
        p.canvas.setDestinationCrs(QgsCoordinateReferenceSystem(crs_code))
        p.canvas.setExtent(rect)
        p.canvas.setLayers([lyr])
        panels.append(p)

    # Also verify panel legend overlay works in sync/layer mode and with graduated renderer
    panels[0]._rebuild_legend_overlay()
    check(
        "Panel 1 legend overlay builds for graduated renderer even in sync mode",
        panels[0].legend_card is not None,
    )
    panels[0].update_hud(True)
    check(
        "Panel 1 HUD includes ground distance and scale ratio",
        panels[0].hud_label is not None and ("m" in panels[0].hud_label.text() or "km" in panels[0].hud_label.text()),
        f"hud={panels[0].hud_label.text() if panels[0].hud_label else None!r}",
    )

    parent_stub = DummyParentDialog(panels)
    dlg = PrintLayoutDialog(parent_stub, iface)

    for rel_key in ("builtin", "arrows/NorthArrow_01.svg", "arrows/NorthArrow_02.svg"):
        resolved = resolve_north_arrow_svg(rel_key)
        check(
            f"North arrow key {rel_key!r} resolves to an existing SVG on disk",
            os.path.isfile(resolved),
            f"resolved={resolved!r}",
        )

    layout = dlg.build_qgis_print_layout(
        title="8-Panel Urban Comparison",
        page_size_name="A4",
        orientation="Landscape",
        north_arrow_key="arrows/NorthArrow_01.svg",
        scalebar_style="Single Box",
        include_footer=True,
        per_panel_overlays=True,
        include_headers=True,
    )

    items = layout.items()
    map_items = [it for it in items if isinstance(it, QgsLayoutItemMap)]
    pic_items = [it for it in items if isinstance(it, QgsLayoutItemPicture)]
    sb_items = [it for it in items if isinstance(it, QgsLayoutItemScaleBar)]
    leg_items = [it for it in items if isinstance(it, QgsLayoutItemLegend)]
    lbl_items = [it for it in items if isinstance(it, QgsLayoutItemLabel)]

    check("8-panel layout contains all 8 QgsLayoutItemMap items", len(map_items) == 8, f"got {len(map_items)}")
    check(
        "8-panel layout contains Master North Arrow + 8 Per-Panel North Arrows (9 total)",
        len(pic_items) == 9,
        f"got {len(pic_items)}",
    )
    check(
        "every North Arrow picture resolves to a valid image (isMissingImage is False)",
        all(not pic.isMissingImage() and os.path.isfile(pic.picturePath()) for pic in pic_items),
        f"missing={[pic.picturePath() for pic in pic_items if pic.isMissingImage()]}",
    )
    check(
        "8-panel layout contains Master Scale Bar + 8 Per-Panel Scale Bars (9 total)",
        len(sb_items) == 9,
        f"got {len(sb_items)}",
    )
    check(
        "every Scale Bar has a positive non-zero unitsPerSegment (> 0)",
        all(sb.unitsPerSegment() > 0 for sb in sb_items),
        f"unitsPerSegment={[sb.unitsPerSegment() for sb in sb_items]}",
    )
    check(
        "8-panel layout contains Master QgsLayoutItemLegend with all 8 layers",
        len(leg_items) == 1 and len(leg_items[0].model().rootGroup().children()) == 8,
        f"legends={len(leg_items)}",
    )
    panel_leg_labels = sorted(
        [lbl for lbl in lbl_items if lbl.id().startswith("PanelLegend_")],
        key=lambda x: x.id(),
    )
    check(
        "8-panel layout contains all 8 Per-Panel Legend cards",
        len(panel_leg_labels) == 8 and "Low-Mid Density" in panel_leg_labels[0].text(),
        f"count={len(panel_leg_labels)} p1={panel_leg_labels[0].text() if panel_leg_labels else None!r}",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        out_png = os.path.join(tmpdir, "eight_panels.png")
        exporter = QgsLayoutExporter(layout)
        img_settings = QgsLayoutExporter.ImageExportSettings()
        img_settings.dpi = 96
        res = exporter.exportToImage(out_png, img_settings)
        check(
            "QgsLayoutExporter exports 8-panel layout to PNG cleanly",
            res == QgsLayoutExporter.Success and os.path.isfile(out_png) and os.path.getsize(out_png) > 1024,
            f"res={res} exists={os.path.isfile(out_png)}",
        )


def run():
    print("02Multimap - CRS-aware spatial metrics & 8-panel layout export (QGIS runtime)")
    print()
    test_ellipsoid_is_never_left_unset()
    test_web_mercator_area_is_not_inflated()
    test_geographic_area_is_metres_not_degrees()
    test_extent_must_be_reprojected_before_clipping()
    test_comparison_across_different_crs_finds_no_change()
    test_comparison_still_detects_a_real_change()
    test_kpi_chip_carries_the_corrected_number()
    test_utm_data_keeps_its_values()
    test_eight_panel_print_layout_and_cartographic_elements()

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
    app = QgsApplication([], False)
    app.initQgis()
    code = 1
    try:
        code = run()
    except Exception:
        traceback.print_exc()
        print("FAIL")
    finally:
        app.exitQgis()
    sys.exit(code)
