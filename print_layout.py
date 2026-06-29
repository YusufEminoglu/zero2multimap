# -*- coding: utf-8 -*-
"""02-Multimap — print layout exporter.

Provides a dedicated print layout dialog allowing users to customize title, page size,
orientation, scalebar style, north arrow graphic, and export to PNG, JPEG, SVG, or PDF.
"""
from __future__ import annotations

import os
from qgis.PyQt.QtCore import Qt, QPoint, QSize
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QFormLayout,
)
from qgis.core import (
    QgsProject,
    QgsLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutExporter,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsUnitTypes,
)


class PrintLayoutDialog(QDialog):
    """Provides configuration and execution for multi-panel print layout exports."""

    def __init__(self, parent_dialog, iface):
        super().__init__(parent_dialog)
        self.parent_dialog = parent_dialog
        self.iface = iface
        self.setWindowTitle("Export Comparative Print Layout")
        self.setMinimumSize(420, 320)

        # Apply same style QSS
        self.setStyleSheet(parent_dialog.styleSheet())
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        # 1. Map Title
        self.title_input = QLineEdit("Comparative Map Grid")
        form.addRow("Map Title:", self.title_input)

        # 2. Page Setup
        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["A4", "A3", "Letter"])
        form.addRow("Page Size:", self.page_size_combo)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["Landscape", "Portrait"])
        form.addRow("Orientation:", self.orientation_combo)

        # 3. North Arrow Styles (using QGIS relative paths)
        self.north_arrow_combo = QComboBox()
        self.north_arrow_combo.addItem("Modern Minimalist", "arrows/NorthArrow_01.svg")
        self.north_arrow_combo.addItem("Classic Compass", "arrows/NorthArrow_02.svg")
        self.north_arrow_combo.addItem("Military Pointer", "arrows/NorthArrow_04.svg")
        self.north_arrow_combo.addItem("Sleek Navigation Cross", "arrows/NorthArrow_07.svg")
        self.north_arrow_combo.addItem("Tech Pointer", "arrows/NorthArrow_09.svg")
        form.addRow("North Arrow Style:", self.north_arrow_combo)

        # 4. Scalebar Styles
        self.scalebar_combo = QComboBox()
        self.scalebar_combo.addItems([
            "Single Box",
            "Double Box",
            "Line Ticks Up",
            "Line Ticks Down",
            "Stepped Line"
        ])
        form.addRow("Scalebar Style:", self.scalebar_combo)

        # 5. Export Format
        self.format_combo = QComboBox()
        self.format_combo.addItems(["PNG Image (*.png)", "JPEG Image (*.jpg)", "PDF Document (*.pdf)", "SVG Graphic (*.svg)", "Interactive HTML (*.html)"])
        form.addRow("Export Format:", self.format_combo)

        # 6. Export File Path
        path_layout = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Select output file path...")
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self.browse_export_path)
        path_layout.addWidget(self.path_input, 1)
        path_layout.addWidget(self.browse_btn)
        form.addRow("Save To:", path_layout)

        layout.addLayout(form)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        
        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.execute_export)
        # Apply accent highlight
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a8f85;
                color: #ffffff;
                border: 1px solid #237a72;
            }
            QPushButton:hover {
                background-color: #319c91;
            }
        """)
        
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.export_btn)
        layout.addLayout(btn_layout)

    def browse_export_path(self) -> None:
        """Opens file dialog based on selected format."""
        format_filter = self.format_combo.currentText()
        if "PNG" in format_filter:
            ext = ".png"
        elif "JPEG" in format_filter:
            ext = ".jpg"
        elif "PDF" in format_filter:
            ext = ".pdf"
        elif "HTML" in format_filter:
            ext = ".html"
        else:
            ext = ".svg"

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Select Export Path", "", format_filter
        )
        if filepath:
            if not filepath.lower().endswith(ext):
                filepath += ext
            self.path_input.setText(filepath)

    def execute_export(self) -> None:
        """Constructs layout in memory and exports to path."""
        title = self.title_input.text()
        page_size_name = self.page_size_combo.currentText()
        orientation = self.orientation_combo.currentText()
        north_arrow_path = self.north_arrow_combo.currentData()
        scalebar_style = self.scalebar_combo.currentText()
        export_path = self.path_input.text()

        if not export_path:
            QMessageBox.warning(self, "Export Path", "Please select an export file path.")
            return

        # Handle Interactive HTML format directly
        if export_path.lower().endswith(".html"):
            try:
                self.export_to_html(export_path, title)
            except Exception as e:
                QMessageBox.critical(
                    self, "Export Error", f"An unexpected error occurred during HTML export:\n{str(e)}"
                )
            return

        # Page Dimensions in mm
        if page_size_name == "A4":
            w, h = (297, 210) if orientation == "Landscape" else (210, 297)
        elif page_size_name == "A3":
            w, h = (420, 297) if orientation == "Landscape" else (297, 420)
        else:  # Letter
            w, h = (279.4, 215.9) if orientation == "Landscape" else (215.9, 279.4)

        # Initialize layout
        project = QgsProject.instance()
        layout = QgsLayout(project)
        layout.initializeDefaults()

        page = layout.pageCollection().pages()[0]
        page.setPageSize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))

        # Title Label
        title_h = 12
        title_item = QgsLayoutItemLabel(layout)
        title_item.setText(title)
        title_item.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        
        # Dynamic Alignment compatibility
        align_center = getattr(Qt.AlignmentFlag, "AlignCenter", getattr(Qt, "AlignCenter"))
        title_item.setHAlign(align_center)
        title_item.setVAlign(align_center)
        title_item.attemptMove(QgsLayoutPoint(10, 5, QgsUnitTypes.LayoutMillimeters))
        title_item.attemptResize(QgsLayoutSize(w - 20, title_h, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(title_item)

        # Grid Coordinates calculation
        margin_top = 5 + title_h + 5  # Position grid below title
        margin_bottom = 20  # Leave space for Scalebar and North Arrow
        margin_side = 10

        avail_w = w - (margin_side * 2)
        avail_h = h - margin_top - margin_bottom

        cols = self.parent_dialog.cols
        rows = self.parent_dialog.rows
        panels = self.parent_dialog.panels

        item_w = avail_w / cols
        item_h = avail_h / rows

        map_items = []

        for i, panel in enumerate(panels):
            r = i // cols
            c = i % cols

            map_x = margin_side + c * item_w
            map_y = margin_top + r * item_h

            map_item = QgsLayoutItemMap(layout)
            map_item.attemptMove(QgsLayoutPoint(map_x + 1, map_y + 1, QgsUnitTypes.LayoutMillimeters))
            map_item.attemptResize(QgsLayoutSize(item_w - 2, item_h - 2, QgsUnitTypes.LayoutMillimeters))

            # Sync spatial configuration
            map_item.setExtent(panel.canvas.extent())
            map_item.setCrs(panel.canvas.mapSettings().destinationCrs())

            # Configure rendering layer sets
            if panel.mode == "canvas":
                map_item.setLayers(self.parent_dialog.iface.mapCanvas().layers())
            elif panel.mode == "theme":
                map_item.setFollowVisibilityPresetName(panel.theme_combo.currentText())
            elif panel.mode == "layer":
                layers_to_render = []
                selected_layer_name = panel.layer_combo.currentText()
                layers = QgsProject.instance().mapLayersByName(selected_layer_name)
                focus_lyr = layers[0] if layers else None
                if focus_lyr:
                    layers_to_render.append(focus_lyr)
                base_lyr = self.parent_dialog.get_global_base_layer()
                if base_lyr:
                    layers_to_render.append(base_lyr)
                map_item.setLayers(layers_to_render)

            layout.addLayoutItem(map_item)
            map_items.append(map_item)

        # North Arrow Picture
        arrow_w, arrow_h = 12, 12
        picture = QgsLayoutItemPicture(layout)
        picture.setPicturePath(north_arrow_path)
        picture.attemptMove(QgsLayoutPoint(margin_side, h - margin_bottom + 4, QgsUnitTypes.LayoutMillimeters))
        picture.attemptResize(QgsLayoutSize(arrow_w, arrow_h, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(picture)

        # Scalebar linked to the active map item (or panel 1)
        linked_map = map_items[0] if map_items else None
        if linked_map:
            scalebar = QgsLayoutItemScaleBar(layout)
            scalebar.setStyle(scalebar_style)
            scalebar.setLinkedMap(linked_map)
            scalebar.setUnits(QgsUnitTypes.DistanceKilometers)
            scalebar.setNumberOfSegments(2)
            scalebar.setNumberOfSegmentsLeft(0)
            scalebar.setUnitLabel("km")
            scalebar.attemptMove(QgsLayoutPoint(margin_side + arrow_w + 10, h - margin_bottom + 4, QgsUnitTypes.LayoutMillimeters))
            layout.addLayoutItem(scalebar)

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

    def export_to_html(self, export_path: str, title: str) -> None:
        """Generates a responsive, synchronized Leaflet HTML map grid with vector GeoJSON layers."""
        import json
        from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsJsonExporter, QgsMapLayer
        
        cols = self.parent_dialog.cols
        rows = self.parent_dialog.rows
        panels = self.parent_dialog.panels
        
        grid_template_columns = " ".join(["1fr"] * cols)
        grid_template_rows = " ".join(["1fr"] * rows)
        
        project = QgsProject.instance()
        crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
        
        active = getattr(self.parent_dialog, "active_panel", None)
        if not active and panels:
            active = panels[0]
            
        if active:
            try:
                transform = QgsCoordinateTransform(
                    active.canvas.mapSettings().destinationCrs(),
                    crs_4326,
                    project
                )
                center_wgs = transform.transform(active.canvas.center())
                center_lat, center_lng = center_wgs.y(), center_wgs.x()
                
                scale = active.canvas.scale()
                import math
                zoom = int(round(math.log2(559000000.0 / scale)))
                zoom = max(0, min(18, zoom))
            except Exception:
                center_lat, center_lng, zoom = 39.9, 32.8, 6
        else:
            center_lat, center_lng, zoom = 39.9, 32.8, 6
            
        panels_data = []
        for i, panel in enumerate(panels):
            geojson_data = None
            mode = panel.mode
            panel_title = f"Panel {i + 1}"
            
            try:
                if mode == "layer":
                    selected_layer_name = panel.layer_combo.currentText()
                    panel_title = f"Focus: {selected_layer_name}"
                    layers = project.mapLayersByName(selected_layer_name)
                    if layers and layers[0].type() == QgsMapLayer.VectorLayer:
                        vlayer = layers[0]
                        exporter = QgsJsonExporter(vlayer)
                        exporter.setSourceCrs(vlayer.crs())
                        exporter.setDestinationCrs(crs_4326)
                        features = list(vlayer.getFeatures())[:3000]
                        geojson_str = exporter.exportFeatures(features)
                        geojson_data = json.loads(geojson_str)
                elif mode == "theme":
                    theme_name = panel.theme_combo.currentText()
                    panel_title = f"Theme: {theme_name}"
                    theme_layers = project.mapThemeCollection().mapThemeVisibleLayers(theme_name)
                    vector_layers = [lyr for lyr in theme_layers if lyr.type() == QgsMapLayer.VectorLayer]
                    if vector_layers:
                        vlayer = vector_layers[0]
                        exporter = QgsJsonExporter(vlayer)
                        exporter.setSourceCrs(vlayer.crs())
                        exporter.setDestinationCrs(crs_4326)
                        features = list(vlayer.getFeatures())[:3000]
                        geojson_str = exporter.exportFeatures(features)
                        geojson_data = json.loads(geojson_str)
                else:  # sync mode (canvas)
                    panel_title = "Sync: Main Map"
                    canvas_layers = self.parent_dialog.iface.mapCanvas().layers()
                    vector_layers = [lyr for lyr in canvas_layers if lyr.type() == QgsMapLayer.VectorLayer]
                    if vector_layers:
                        vlayer = vector_layers[0]
                        exporter = QgsJsonExporter(vlayer)
                        exporter.setSourceCrs(vlayer.crs())
                        exporter.setDestinationCrs(crs_4326)
                        features = list(vlayer.getFeatures())[:3000]
                        geojson_str = exporter.exportFeatures(features)
                        geojson_data = json.loads(geojson_str)
            except Exception as exc:
                print(f"Error exporting GeoJSON for panel {i + 1}: {exc}")
                
            panels_data.append({
                "id": f"map-panel-{i}",
                "title": panel_title,
                "geojson": geojson_data
            })
            
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    
    <!-- Leaflet CSS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />
    
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif;
            background-color: #fbfbfd;
            color: #2c3e46;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        header {{
            background-color: #ffffff;
            border-bottom: 1px solid #cbd3da;
            padding: 12px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        h1 {{
            font-size: 18px;
            font-weight: 700;
            color: #16323f;
        }}
        .brand {{
            font-size: 12px;
            color: #2a8f85;
            font-weight: 600;
            background: #eef1f4;
            padding: 4px 8px;
            border-radius: 4px;
        }}
        .grid-container {{
            flex: 1;
            display: grid;
            grid-template-columns: {grid_template_columns};
            grid-template-rows: {grid_template_rows};
            gap: 4px;
            padding: 4px;
            background-color: #cbd3da;
        }}
        .panel {{
            background-color: #ffffff;
            display: flex;
            flex-direction: column;
            border-radius: 4px;
            overflow: hidden;
        }}
        .panel-header {{
            background-color: #eef1f4;
            border-bottom: 1px solid #cbd3da;
            padding: 6px 12px;
            font-size: 12px;
            font-weight: 600;
            color: #16323f;
        }}
        .map-view {{
            flex: 1;
            background-color: #f0f2f5;
        }}
    </style>
</head>
<body>

    <header>
        <h1>{title}</h1>
        <div class="brand">02-Multimap Interactive Dashboard</div>
    </header>

    <div class="grid-container">
"""
        for p in panels_data:
            html_content += f"""
        <div class="panel">
            <div class="panel-header">{p['title']}</div>
            <div id="{p['id']}" class="map-view"></div>
        </div>"""
            
        html_content += f"""
    </div>

    <!-- Leaflet JS -->
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
    
    <script>
        const initialCenter = [{center_lat}, {center_lng}];
        const initialZoom = {zoom};
        const panelsData = {json.dumps(panels_data)};
        const maps = [];
        const cursors = [];
        
        let isSyncing = false;

        panelsData.forEach((pane, idx) => {{
            const map = L.map(pane.id, {{
                zoomControl: idx === 0,
                attributionControl: false
            }}).setView(initialCenter, initialZoom);
            
            L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
                maxZoom: 20
            }}).addTo(map);
            
            if (pane.geojson) {{
                const geoJsonLayer = L.geoJSON(pane.geojson, {{
                    style: {{
                        color: '#2a8f85',
                        weight: 2,
                        fillColor: '#2a8f85',
                        fillOpacity: 0.15
                    }}
                }}).addTo(map);
                
                try {{
                    const bounds = geoJsonLayer.getBounds();
                    if (bounds.isValid()) {{
                        map.fitBounds(bounds);
                    }}
                }} catch(e) {{}}
            }}
            
            maps.push(map);

            const cursorMarker = L.circleMarker([0, 0], {{
                radius: 6,
                color: '#e74c3c',
                fillColor: '#e74c3c',
                fillOpacity: 0.8,
                weight: 1,
                interactive: false
            }}).addTo(map);
            cursorMarker.setStyle({{ opacity: 0, fillOpacity: 0 }});
            cursors.push(cursorMarker);
        }});

        maps.forEach((map, idx) => {{
            map.on('move', () => {{
                if (isSyncing) return;
                isSyncing = true;
                
                const center = map.getCenter();
                const zoom = map.getZoom();
                
                maps.forEach((otherMap, otherIdx) => {{
                    if (otherIdx !== idx) {{
                        otherMap.setView(center, zoom, {{ animate: false }});
                    }}
                }});
                
                isSyncing = false;
            }});
            
            map.on('mousemove', (e) => {{
                const latlng = e.latlng;
                cursors.forEach((c) => {{
                    c.setLatLng(latlng);
                    c.setStyle({{ opacity: 0.8, fillOpacity: 0.8 }});
                }});
            }});
            
            map.on('mouseout', () => {{
                cursors.forEach((c) => {{
                    c.setStyle({{ opacity: 0, fillOpacity: 0 }});
                }});
            }});
        }});
    </script>
</body>
</html>
"""
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        QMessageBox.information(
            self, "Export Success", f"Interactive HTML dashboard exported successfully to:\n{export_path}"
        )
        self.accept()
