from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

plugin_dir = str(Path(__file__).resolve().parent.parent)
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

try:
    from zero2multimap.spatial_metrics import format_area, resolve_ellipsoid
except ImportError:  # pragma: no cover - script layout without the package
    from spatial_metrics import format_area, resolve_ellipsoid


class ResolveEllipsoidTests(unittest.TestCase):
    """A project that never set an ellipsoid reports "NONE".

    Handing that straight to QgsDistanceArea does not raise: it silently
    degrades to planimetric measurement, which is the bug being fixed here.
    """

    def test_placeholder_values_fall_back_to_wgs84(self) -> None:
        for placeholder in (None, "", "   ", "NONE", "none", "Undefined", "null"):
            with self.subTest(placeholder=placeholder):
                self.assertEqual(resolve_ellipsoid(placeholder), "WGS84")

    def test_a_real_ellipsoid_is_preserved(self) -> None:
        for name in ("WGS84", "GRS80", "clrk66", "  WGS84  "):
            with self.subTest(name=name):
                self.assertEqual(resolve_ellipsoid(name), name.strip())


class FormatAreaTests(unittest.TestCase):
    def test_empty_and_negative_areas_read_as_zero_hectares(self) -> None:
        for value in (None, 0, 0.0, -12.5):
            with self.subTest(value=value):
                self.assertEqual(format_area(value), "0 ha")

    def test_hectares_below_the_square_kilometre_threshold(self) -> None:
        self.assertEqual(format_area(1.0), "0.00 ha")
        self.assertEqual(format_area(10_000), "1.00 ha")
        self.assertEqual(format_area(5_000), "0.50 ha")
        self.assertEqual(format_area(999_999), "100.00 ha")

    def test_square_kilometres_at_and_above_the_threshold(self) -> None:
        self.assertEqual(format_area(1_000_000), "1.00 km²")
        self.assertEqual(format_area(2_500_000), "2.50 km²")

    def test_a_degree_square_reads_as_thousands_of_square_kilometres(self) -> None:
        """The regression this release is about.

        1° x 1° at 41°N is ~9 413 km² of ground. Measured planimetrically in
        EPSG:4326 the same polygon has area 1.0, which formatted as "0.00 ha".
        """
        self.assertEqual(format_area(9_412_788_745.689), "9412.79 km²")


class ImportIsolationTests(unittest.TestCase):
    def test_module_imports_without_qgis_on_the_path(self) -> None:
        """The pure suite runs on plain Python; a top-level qgis import breaks it.

        Checked in a subprocess so the assertion is about a genuinely QGIS-free
        interpreter rather than about whatever this process already imported.
        """
        program = (
            "import sys;"
            "assert 'qgis' not in sys.modules, 'qgis was already loaded';"
            "from zero2multimap.spatial_metrics import format_area, resolve_ellipsoid;"
            "print(format_area(10_000), resolve_ellipsoid('NONE'))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", program],
            # The package root, not the plugin folder: `cwd` is sys.path[0] for
            # `-c`, and the interpreter has to find `zero2multimap` from there.
            cwd=Path(plugin_dir).parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "1.00 ha WGS84")


if __name__ == "__main__":
    unittest.main()
