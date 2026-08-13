# -*- coding: utf-8 -*-
"""02Multimap — print layout exporter.

Provides a dedicated print layout dialog allowing users to customize title, page size,
orientation, scalebar style, north arrow graphic, and export to PNG, JPEG, SVG, or PDF.
"""
from __future__ import annotations

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
from qgis.PyQt.QtGui import QFont, QDesktopServices

from qgis.PyQt.QtWidgets import (
    QDialog,
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



class PrintLayoutDialog(QDialog):
    """Provides configuration and execution for multi-panel print layout and HTML dashboard exports."""

    def __init__(self, parent_dialog, iface):
        super().__init__(parent_dialog)
        self.parent_dialog = parent_dialog
        self.iface = iface
        self.setWindowTitle("Export Comparative Maps & Dashboards")
        self.setMinimumSize(540, 440)

        # Apply same style QSS
        self.setStyleSheet(parent_dialog.styleSheet())
        self._setup_ui()

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

        self.title_input = QLineEdit("Comparative Map Grid")
        print_layout_form.addRow("Map Title:", self.title_input)

        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["A4", "A3", "Letter"])
        print_layout_form.addRow("Page Size:", self.page_size_combo)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["Landscape", "Portrait"])
        print_layout_form.addRow("Orientation:", self.orientation_combo)

        self.north_arrow_combo = QComboBox()
        self.north_arrow_combo.addItem("Modern Minimalist", "arrows/NorthArrow_01.svg")
        self.north_arrow_combo.addItem("Classic Compass", "arrows/NorthArrow_02.svg")
        self.north_arrow_combo.addItem("Military Pointer", "arrows/NorthArrow_04.svg")
        self.north_arrow_combo.addItem("Sleek Navigation Cross", "arrows/NorthArrow_07.svg")
        self.north_arrow_combo.addItem("Tech Pointer", "arrows/NorthArrow_09.svg")
        print_layout_form.addRow("North Arrow Style:", self.north_arrow_combo)

        self.scalebar_combo = QComboBox()
        self.scalebar_combo.addItems([
            "Single Box",
            "Double Box",
            "Line Ticks Up",
            "Line Ticks Down",
            "Stepped Line"
        ])
        print_layout_form.addRow("Scalebar Style:", self.scalebar_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItem("PDF Document (*.pdf)", ".pdf")
        self.format_combo.addItem("PNG Image (*.png)", ".png")
        self.format_combo.addItem("JPEG Image (*.jpg)", ".jpg")
        self.format_combo.addItem("SVG Graphic (*.svg)", ".svg")
        print_layout_form.addRow("Export Format:", self.format_combo)

        path_layout = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Select output image/PDF file path...")
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self.browse_export_path)
        path_layout.addWidget(self.path_input, 1)
        path_layout.addWidget(self.browse_btn)
        print_layout_form.addRow("Save To:", path_layout)

        self.tab_widget.addTab(self.print_tab, "🖨️ Print Layout Export (PDF / Images)")

        # ──────────────── Tab 2: Interactive HTML Dashboard Studio ────────────────
        self.html_tab = QWidget()
        html_form = QFormLayout(self.html_tab)
        html_form.setContentsMargins(12, 12, 12, 12)
        html_form.setSpacing(10)

        self.html_title_input = QLineEdit("Comparative Map Dashboard")
        html_form.addRow("Dashboard Title:", self.html_title_input)

        self.html_theme_combo = QComboBox()
        self.html_theme_combo.addItem("Slate Light (Default)", "slate")
        self.html_theme_combo.addItem("Dark Midnight (Night Mode)", "dark")
        self.html_theme_combo.addItem("Emerald Clean", "emerald")
        html_form.addRow("Color Theme:", self.html_theme_combo)

        html_info = QLabel(
            "<b>🌐 Interactive Web Dashboard Features:</b><br>"
            "• Synchronized multi-viewport panning & wheel zoom<br>"
            "• Synchronized laser crosshairs across all panels<br>"
            "• Interactive distance & area measurement tools (📏)<br>"
            "• Full-screen presentation slide mode (📺) with arrow key navigation<br>"
            "• Multi-theme switching & standalone offline compatibility"
        )
        html_info.setStyleSheet("background-color: #eef1f4; border: 1px solid #cbd3da; border-radius: 6px; padding: 8px; font-size: 11px; color: #16323f;")
        html_info.setWordWrap(True)
        html_form.addRow("", html_info)

        html_path_layout = QHBoxLayout()
        self.html_path_input = QLineEdit()
        self.html_path_input.setPlaceholderText("Select output .html dashboard file path...")
        self.html_browse_btn = QPushButton("Browse...")
        self.html_browse_btn.clicked.connect(self.browse_html_export_path)
        html_path_layout.addWidget(self.html_path_input, 1)
        html_path_layout.addWidget(self.html_browse_btn)
        html_form.addRow("Save To:", html_path_layout)

        self.tab_widget.addTab(self.html_tab, "🌐 Interactive HTML Dashboard")

        layout.addWidget(self.tab_widget, 1)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        self.export_btn = QPushButton("Export Only")
        self.export_btn.clicked.connect(lambda: self.execute_export(open_after=False))

        self.export_open_btn = QPushButton("Export & Open")
        self.export_open_btn.clicked.connect(lambda: self.execute_export(open_after=True))
        self.export_open_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a8f85;
                color: #ffffff;
                border: 1px solid #237a72;
                font-weight: bold;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #319c91;
            }
        """)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addWidget(self.export_open_btn)
        layout.addLayout(btn_layout)

        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(0)

    def _on_tab_changed(self, index: int) -> None:
        """Update action button text based on active tab."""
        if index == 0:
            self.export_btn.setText("Export Layout Only")
            self.export_open_btn.setText("Export & Open File 🖨️")
        else:
            self.export_btn.setText("Export Dashboard Only")
            self.export_open_btn.setText("Export & Open in Browser 🌐")


    def browse_export_path(self) -> None:
        """Opens file dialog for print layout formats."""
        format_filter = self.format_combo.currentText()
        last_dir = QSettings().value("zero2multimap/last_export_dir", "")
        if not (isinstance(last_dir, str) and os.path.isdir(last_dir)):
            last_dir = os.path.expanduser("~")

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Select Print Layout Export Path", last_dir, format_filter
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
            self, "Select HTML Dashboard Path", last_dir, "Interactive HTML Dashboard (*.html)"
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

    def execute_export(self, open_after: bool = False) -> None:
        """Constructs layout or dashboard depending on active tab."""
        if self.tab_widget.currentIndex() == 1:
            title = self.html_title_input.text()
            export_path = self._normalized_html_path(self.html_path_input.text())
            if not export_path:
                QMessageBox.warning(self, "Export Path", "Please select an HTML export file path.")
                return
            output_directory = os.path.dirname(export_path)
            if not os.path.isdir(output_directory):
                QMessageBox.warning(self, "Export Path", "The selected output folder does not exist.")
                return
            self.html_path_input.setText(export_path)
            try:
                self.export_to_html(export_path, title, open_after=open_after)
            except Exception as e:
                QMessageBox.critical(
                    self, "Export Error", f"An unexpected error occurred during HTML export:\n{str(e)}"
                )
            return


        title = self.title_input.text()
        page_size_name = self.page_size_combo.currentText()
        orientation = self.orientation_combo.currentText()
        north_arrow_path = self.north_arrow_combo.currentData()
        scalebar_style = self.scalebar_combo.currentText()
        export_path = self._normalized_export_path(self.path_input.text())

        if not export_path:
            QMessageBox.warning(self, "Export Path", "Please select an export file path.")
            return
        output_directory = os.path.dirname(export_path)
        if not os.path.isdir(output_directory):
            QMessageBox.warning(
                self,
                "Export Path",
                "The selected output folder does not exist. Choose an existing folder.",
            )
            return
        self.path_input.setText(export_path)


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
        font_weight = getattr(QFont, "Weight", QFont)
        title_item.setFont(QFont("Segoe UI", 16, getattr(font_weight, "Bold")))

        # Dynamic Alignment compatibility
        alignment_flags = getattr(Qt, "AlignmentFlag", Qt)
        align_center = getattr(alignment_flags, "AlignCenter")
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

            # Lock the print item to exactly the layers currently visible in
            # the panel. Themes additionally follow their stored layer styles.
            if panel.mode == "theme" and panel.theme_combo.currentText():
                map_item.setFollowVisibilityPresetName(panel.theme_combo.currentText())
                map_item.setFollowVisibilityPreset(True)
            else:
                map_item.setLayers(panel.canvas.layers())
                map_item.setKeepLayerSet(True)
                style_overrides = panel.canvas.mapSettings().layerStyleOverrides()
                if style_overrides:
                    map_item.setLayerStyleOverrides(style_overrides)

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
            scalebar.attemptMove(QgsLayoutPoint(
                margin_side + arrow_w + 10,
                h - margin_bottom + 4,
                QgsUnitTypes.LayoutMillimeters,
            ))
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
                QSettings().setValue(
                    "zero2multimap/last_export_dir",
                    os.path.dirname(export_path),
                )
                if open_after:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(export_path))
                else:
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

    @staticmethod
    def _write_only_mode():
        """Return QIODevice's write-only flag on both Qt 5 and Qt 6."""
        open_mode = getattr(QIODevice, "OpenModeFlag", QIODevice)
        return getattr(open_mode, "WriteOnly")

    def _rendered_panel_data(self, panel, index: int) -> dict[str, object]:
        """Render one canvas to PNG and collect its browser metadata."""
        canvas_layers = panel.canvas.layers()
        if not canvas_layers and panel.mode == "sync":
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

        image_bytes = QByteArray()
        buffer = QBuffer(image_bytes)
        if not buffer.open(self._write_only_mode()):
            raise OSError(f"Panel {index + 1} image buffer could not be opened.")
        if not image.save(buffer, "PNG"):
            buffer.close()
            raise OSError(f"Panel {index + 1} image could not be encoded as PNG.")
        buffer.close()
        encoded_image = bytes(image_bytes.toBase64()).decode("ascii")

        layer_details = []
        rendered_layer_names = []
        for lyr in canvas_layers:
            if not lyr or not lyr.isValid():
                continue
            rendered_layer_names.append(lyr.name())
            if isinstance(lyr, QgsVectorLayer):
                geom_name = QgsWkbTypes.displayString(lyr.wkbType()) if hasattr(QgsWkbTypes, "displayString") else "Vector"
                fc = lyr.featureCount()
                fc_str = f"{fc:,} features" if fc >= 0 else "Vector"
                layer_details.append(f"{lyr.name()} ({geom_name}, {fc_str})")
            elif isinstance(lyr, QgsRasterLayer):
                layer_details.append(f"{lyr.name()} (Raster)")

        if panel.mode == "layer":
            selected_layer = panel.selected_layer()
            layer_name = selected_layer.name() if selected_layer else "No layer selected"
            panel_title = f"Panel {index + 1} · {layer_name}"
            detail = "One-layer comparison"
        elif panel.mode == "theme":
            theme_name = panel.theme_combo.currentText() or "No theme selected"
            panel_title = f"Panel {index + 1} · {theme_name}"
            detail = "Map theme"
        else:
            panel_title = f"Panel {index + 1} · Main map"
            detail = "Follows QGIS canvas"

        detail_tooltip = "Layers: " + " | ".join(layer_details) if layer_details else detail

        extent = panel.canvas.extent()
        destination_crs = panel.canvas.mapSettings().destinationCrs()
        crs_label = destination_crs.authid() or destination_crs.description()
        scale_val = int(panel.canvas.scale())

        extent_wgs84 = [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]
        with suppress(Exception):
            wgs84_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            if destination_crs.isValid() and destination_crs != wgs84_crs:
                transform = QgsCoordinateTransform(destination_crs, wgs84_crs, QgsProject.instance())
                rect_wgs84 = transform.transformBoundingBox(extent)
                extent_wgs84 = [rect_wgs84.xMinimum(), rect_wgs84.yMinimum(), rect_wgs84.xMaximum(), rect_wgs84.yMaximum()]


        legend_items = self._extract_legend_items(panel)

        return {
            "title": panel_title,
            "detail": detail,
            "detail_tooltip": detail_tooltip,
            "layers": rendered_layer_names,
            "layer_details": layer_details,
            "scale": f"1:{scale_val:,}",
            "image_data": f"data:image/png;base64,{encoded_image}",
            "extent": [
                extent.xMinimum(),
                extent.yMinimum(),
                extent.xMaximum(),
                extent.yMaximum(),
            ],
            "extent_wgs84": extent_wgs84,
            "crs": crs_label or "Unknown CRS",
            "legend": legend_items,
        }


    def _extract_legend_items(self, panel) -> list[dict[str, str]]:
        """Extract category labels and colors for HTML micro-legend from visible layers."""
        layers = panel.canvas.layers()
        if not layers:
            return []
        items = []
        for lyr in layers:
            if not lyr or not lyr.isValid():
                continue
            if isinstance(lyr, QgsVectorLayer) and lyr.renderer():
                renderer = lyr.renderer()
                if hasattr(renderer, "categories"):
                    for cat in renderer.categories()[:6]:
                        lbl = cat.label() or str(cat.value())
                        sym = cat.symbol()
                        col = sym.color().name() if sym else "#2a8f85"
                        items.append({"label": f"{lyr.name()}: {lbl}", "color": col})
                elif hasattr(renderer, "ranges"):
                    for rng in renderer.ranges()[:6]:
                        lbl = rng.label()
                        sym = rng.symbol()
                        col = sym.color().name() if sym else "#2a8f85"
                        items.append({"label": f"{lyr.name()}: {lbl}", "color": col})
                elif hasattr(renderer, "symbol") and renderer.symbol():
                    sym = renderer.symbol()
                    col = sym.color().name() if sym else "#2a8f85"
                    items.append({"label": lyr.name(), "color": col})
            elif isinstance(lyr, QgsRasterLayer):
                items.append({"label": f"Raster: {lyr.name()}", "color": "#3498db"})
        return items[:10]



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
        panels = self.parent_dialog.panels
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
                rows=self.parent_dialog.rows,
                cols=self.parent_dialog.cols,
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
                "Export Success",
                "Offline interactive HTML dashboard exported successfully to:\n"
                f"{export_path}\n\n"
                "The file includes all panel styles, labels, and raster content.",
            )
        self.accept()

