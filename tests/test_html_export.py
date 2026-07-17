from __future__ import annotations

import unittest

from html_export import build_dashboard_html


class DashboardHtmlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = {
            "title": "Roads & Buildings",
            "detail": "One-layer comparison",
            "image_data": "data:image/png;base64,iVBORw0KGgo=",
            "extent": [10, 20, 30, 40],
            "crs": "EPSG:3857",
        }

    def test_builds_offline_dashboard_without_external_assets(self) -> None:
        result = build_dashboard_html("City <Comparison>", 1, 2, [self.panel, self.panel])

        self.assertIn("City &lt;Comparison&gt;", result)
        self.assertEqual(result.count('class="map-view"'), 2)
        self.assertIn("data:image/png;base64,iVBORw0KGgo=", result)
        self.assertNotIn("https://", result)
        self.assertNotIn("leaflet", result.lower())
        self.assertIn("pointermove", result)
        self.assertIn("wheel", result)

    def test_escapes_script_terminators_in_crs_metadata(self) -> None:
        panel = dict(self.panel)
        panel["crs"] = "</script><script>alert(1)</script>"

        result = build_dashboard_html("Safe", 1, 1, [panel])

        self.assertNotIn("</script><script>alert(1)</script>", result)
        self.assertIn("\\u003c/script\\u003e", result)

    def test_rejects_grid_panel_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            build_dashboard_html("Mismatch", 2, 2, [self.panel])

    def test_rejects_missing_snapshot(self) -> None:
        panel = dict(self.panel)
        panel["image_data"] = ""

        with self.assertRaises(ValueError):
            build_dashboard_html("No image", 1, 1, [panel])


if __name__ == "__main__":
    unittest.main()
