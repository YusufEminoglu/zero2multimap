# -*- coding: utf-8 -*-
"""CRS-aware area measurement and layer comparison for 02Multimap.

``QgsGeometry.area()`` returns planimetric area expressed in the *layer's own*
units. Read as hectares or square kilometres that number is wrong in the two
coordinate systems urban-planning data actually arrives in:

* **A geographic CRS** — the unit is square degrees. A 1° x 1° polygon (roughly
  9 400 km² of ground at 41°N) reports ``1.0``, which the KPI bar formatted as
  ``0.00 ha``.
* **Web Mercator (EPSG:3857)** — a square kilometre of *map* is only
  ``cos(latitude)`` square kilometres of ground: 0.569 km² at 41°N, so every
  area chip read **1.76x high**. This is the CRS of most web-tiled basemaps and
  of the vector data exported alongside them, so it is the common case, not the
  exotic one.

A third failure needs no unusual data at all: intersecting a geometry in the
layer CRS with the *canvas* extent rectangle. When the two CRSs differ the
intersection is not merely inaccurate, it is **empty** — the KPI bar printed
``0 ha`` next to a correct feature count, and the difference engine reported a
100% change against every layer.

Everything the plugin reports as an area now goes through
:func:`ellipsoidal_area`, which measures on the ellipsoid and is therefore
independent of both the layer CRS and the canvas CRS.

The QGIS imports live *inside* the functions on purpose: this module is imported
by the plain-Python test suite, which runs with no QGIS on ``sys.path``.
"""
from __future__ import annotations

from dataclasses import dataclass

# QGIS reports an ellipsoid of "NONE" for a project that has never set one, and
# QgsDistanceArea.setEllipsoid("NONE") does not raise — it silently falls back to
# planimetric measurement (willUseEllipsoid() is False), which is the exact bug
# this module exists to remove. Never hand it a placeholder.
DEFAULT_ELLIPSOID = "WGS84"
_UNSET_ELLIPSOIDS = frozenset({"", "NONE", "UNDEFINED", "UNKNOWN", "NULL"})

_HA_PER_M2 = 10_000.0
_KM2_PER_M2 = 1_000_000.0


def resolve_ellipsoid(project_ellipsoid: str | None) -> str:
    """Return an ellipsoid name :class:`QgsDistanceArea` will actually use."""
    name = (project_ellipsoid or "").strip()
    if not name or name.upper() in _UNSET_ELLIPSOIDS:
        return DEFAULT_ELLIPSOID
    return name


def format_area(area_m2: float | None) -> str:
    """Format a square-metre area as ``ha`` or ``km²`` (never a bare number).

    Thresholds match the plugin's historical output; only the *value* fed in
    changed, which is why the chips can look very different after this fix.
    """
    if area_m2 is None or area_m2 <= 0:
        return "0 ha"
    if area_m2 >= _KM2_PER_M2:
        return f"{area_m2 / _KM2_PER_M2:.2f} km²"
    return f"{area_m2 / _HA_PER_M2:.2f} ha"


# ─────────────────────────── QGIS-backed helpers ───────────────────────────


def make_area_calculator(crs, transform_context=None):
    """A ``QgsDistanceArea`` that measures on the ellipsoid for ``crs``."""
    from qgis.core import QgsDistanceArea, QgsProject

    calculator = QgsDistanceArea()
    context = transform_context or QgsProject.instance().transformContext()
    if crs is not None and crs.isValid():
        calculator.setSourceCrs(crs, context)
    calculator.setEllipsoid(resolve_ellipsoid(QgsProject.instance().ellipsoid()))
    return calculator


def ellipsoidal_area(geometry, crs, transform_context=None) -> float:
    """True ground area of ``geometry`` (given in ``crs``) in square metres."""
    if geometry is None or geometry.isEmpty():
        return 0.0
    try:
        return float(make_area_calculator(crs, transform_context).measureArea(geometry))
    except Exception:  # noqa: BLE001 - a measurement failure must not break the UI
        return 0.0


def transform_geometry(geometry, source_crs, target_crs, transform_context=None):
    """Return ``geometry`` reprojected into ``target_crs``, or ``None`` on failure.

    The transform's return code is deliberately not inspected: it is a
    ``Qgis.GeometryOperationResult`` enum, and comparing an enum to ``0`` is a
    PyQt5-only habit that breaks under PyQt6 (QGIS 4). Failure raises instead.
    """
    if geometry is None or geometry.isEmpty():
        return None
    if source_crs is None or target_crs is None:
        return geometry
    if not source_crs.isValid() or not target_crs.isValid() or source_crs == target_crs:
        return geometry

    from qgis.core import QgsCoordinateTransform, QgsGeometry, QgsProject

    try:
        transform = QgsCoordinateTransform(
            source_crs, target_crs, transform_context or QgsProject.instance()
        )
        reprojected = QgsGeometry(geometry)
        reprojected.transform(transform)
    except Exception:  # noqa: BLE001 - QgsCsException / RuntimeError
        return None
    return reprojected


def _combined_in_crs(layer, clip_geometry, clip_crs, transform_context=None):
    """Union of ``layer``'s features that fall inside ``clip_geometry``.

    Returns the union reprojected into ``clip_crs``, so two layers in different
    CRSs become directly comparable. The feature request is filtered by the clip
    region expressed in the *layer's* CRS, so a reprojected canvas extent still
    drives a cheap bounding-box fetch.
    """
    from qgis.core import QgsFeatureRequest, QgsGeometry

    if layer is None or not layer.isValid():
        return None
        return None

    layer_crs = layer.crs()
    request = QgsFeatureRequest()
    clip_in_layer_crs = transform_geometry(clip_geometry, clip_crs, layer_crs, transform_context)
    if clip_in_layer_crs is not None:
        request.setFilterRect(clip_in_layer_crs.boundingBox())

    geometries = [
        feature.geometry()
        for feature in layer.getFeatures(request)
        if feature.geometry() and not feature.geometry().isEmpty()
    ]
    if not geometries:
        return None

    union = QgsGeometry.unaryUnion(geometries)
    if union is None or union.isEmpty():
        return None

    in_clip_crs = transform_geometry(union, layer_crs, clip_crs, transform_context)
    if in_clip_crs is None:
        return None
    clipped = in_clip_crs.intersection(clip_geometry)
    if clipped is None or clipped.isEmpty():
        return None
    return clipped


@dataclass(frozen=True)
class ComparisonResult:
    """Outcome of comparing two layers inside one visible extent.

    ``removed`` and ``added`` are geometries in the comparison's working CRS;
    all ``*_area_m2`` values are ellipsoidal, i.e. real ground area.
    """

    crs: object
    removed: object = None
    added: object = None
    overlap_percent: float = 0.0
    area_a_m2: float = 0.0
    area_b_m2: float = 0.0
    removed_area_m2: float = 0.0
    added_area_m2: float = 0.0

    @property
    def has_difference(self) -> bool:
        return self.removed_area_m2 > 0 or self.added_area_m2 > 0


def compare_layers(layer_a, layer_b, clip_geometry, clip_crs, transform_context=None) -> ComparisonResult:
    """Geometric difference of ``layer_a`` -> ``layer_b`` inside a clip region.

    Both inputs are unioned in their own CRS and then reprojected into
    ``clip_crs`` before any geometry operation. Comparing raw geometries from two
    layers that disagree on CRS does not raise — it returns confident nonsense.
    """
    if clip_geometry is None or clip_geometry.isEmpty():
        return ComparisonResult(crs=clip_crs)

    union_a = _combined_in_crs(layer_a, clip_geometry, clip_crs, transform_context)
    union_b = _combined_in_crs(layer_b, clip_geometry, clip_crs, transform_context)
    if union_a is None or union_b is None:
        return ComparisonResult(crs=clip_crs)

    removed = union_a.difference(union_b)
    added = union_b.difference(union_a)
    intersection = union_a.intersection(union_b)

    area_a = ellipsoidal_area(union_a, clip_crs, transform_context)
    area_b = ellipsoidal_area(union_b, clip_crs, transform_context)
    overlap = 0.0
    if area_a > 0:
        overlap = (ellipsoidal_area(intersection, clip_crs, transform_context) / area_a) * 100.0

    return ComparisonResult(
        crs=clip_crs,
        removed=None if removed is None or removed.isEmpty() else removed,
        added=None if added is None or added.isEmpty() else added,
        overlap_percent=overlap,
        area_a_m2=area_a,
        area_b_m2=area_b,
        removed_area_m2=ellipsoidal_area(removed, clip_crs, transform_context),
        added_area_m2=ellipsoidal_area(added, clip_crs, transform_context),
    )
