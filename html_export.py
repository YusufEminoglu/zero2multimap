# -*- coding: utf-8 -*-
"""Pure-Python builder for self-contained 02Multimap HTML dashboards."""
from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence


_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="__INITIAL_THEME__">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="generator" content="02Multimap">
    <title>__TITLE__</title>
    <style>
        :root {
            color-scheme: light dark;
            --ink: #16323f;
            --muted: #5d6b73;
            --line: #cbd3da;
            --panel: #ffffff;
            --soft: #eef1f4;
            --accent: #2a8f85;
            --accent-hover: #319c91;
            --laser: #e74c3c;
            --bg-body: #dfe4e8;
            --card-shadow: rgba(22, 50, 63, 0.1);
        }
        [data-theme="dark"] {
            color-scheme: dark;
            --ink: #f0f4f8;
            --muted: #94a3b8;
            --line: #334155;
            --panel: #1e293b;
            --soft: #0f172a;
            --accent: #2dd4bf;
            --accent-hover: #14b8a6;
            --laser: #ff6b6b;
            --bg-body: #0b0f17;
            --card-shadow: rgba(0, 0, 0, 0.4);
        }
        [data-theme="emerald"] {
            --ink: #064e3b;
            --muted: #047857;
            --line: #a7f3d0;
            --panel: #ffffff;
            --soft: #ecfdf5;
            --accent: #059669;
            --accent-hover: #10b981;
            --laser: #e11d48;
            --bg-body: #d1fae5;
            --card-shadow: rgba(4, 120, 87, 0.12);
        }
        * { box-sizing: border-box; }
        html, body { width: 100%; height: 100%; margin: 0; }
        body {
            display: flex;
            flex-direction: column;
            overflow: hidden;
            background: var(--bg-body);
            color: var(--ink);
            font-family: "Segoe UI", Inter, system-ui, -apple-system, sans-serif;
            transition: background 0.2s ease, color 0.2s ease;
        }
        header {
            z-index: 5;
            display: flex;
            align-items: center;
            gap: 16px;
            min-height: 58px;
            padding: 9px 16px;
            background: var(--panel);
            border-bottom: 1px solid var(--line);
            box-shadow: 0 2px 8px var(--card-shadow);
        }
        .heading { min-width: 0; flex: 1; }
        h1 {
            overflow: hidden;
            margin: 0;
            color: var(--ink);
            font-size: 18px;
            line-height: 1.25;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .subtitle { margin-top: 2px; color: var(--muted); font-size: 11px; }
        .toolbar { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
        button {
            min-height: 32px;
            padding: 5px 12px;
            border: 1px solid var(--line);
            border-radius: 6px;
            background: var(--soft);
            color: var(--ink);
            font: 600 12px "Segoe UI", sans-serif;
            cursor: pointer;
            transition: background 0.15s ease, border-color 0.15s ease;
        }
        button:hover { border-color: var(--accent); background: var(--panel); }
        button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
        button.active {
            background: var(--accent);
            color: #ffffff;
            border-color: var(--accent);
        }
        .btn-accent {
            background: var(--accent);
            color: #ffffff;
            border-color: var(--accent);
        }
        .btn-accent:hover {
            background: var(--accent-hover);
            border-color: var(--accent-hover);
        }
        .zoom-readout {
            min-width: 48px;
            color: var(--accent);
            font: 700 12px Consolas, monospace;
            text-align: center;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(__COLS__, minmax(0, 1fr));
            grid-template-rows: repeat(__ROWS__, minmax(0, 1fr));
            flex: 1;
            min-height: 0;
            gap: 6px;
            padding: 6px;
        }
        .panel {
            display: flex;
            min-width: 0;
            min-height: 0;
            flex-direction: column;
            overflow: hidden;
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--panel);
            box-shadow: 0 2px 6px var(--card-shadow);
            transition: border-color 0.2s ease;
        }
        .panel:hover {
            border-color: var(--accent);
        }
        .panel-header {
            display: flex;
            align-items: center;
            gap: 8px;
            min-height: 36px;
            padding: 5px 10px;
            border-bottom: 1px solid var(--line);
            background: var(--soft);
        }
        .panel-title {
            overflow: hidden;
            flex: 1;
            font-size: 12px;
            font-weight: 700;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .scale-chip {
            font-size: 10px;
            font-weight: 700;
            font-family: Consolas, monospace;
            color: var(--accent);
            background: var(--panel);
            border: 1px solid var(--line);
            padding: 1px 5px;
            border-radius: 4px;
            white-space: nowrap;
        }
        .mode-chip {
            max-width: 45%;
            overflow: hidden;
            padding: 2px 7px;
            border: 1px solid var(--line);
            border-radius: 10px;
            background: var(--panel);
            color: var(--accent);
            font-size: 10px;
            font-weight: 700;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .opacity-control {
            display: flex;
            align-items: center;
            gap: 4px;
            font-size: 10px;
            color: var(--muted);
        }
        .opacity-slider {
            width: 54px;
            height: 4px;
            accent-color: var(--accent);
            cursor: pointer;
        }
        .map-view {
            position: relative;
            flex: 1;
            min-height: 0;
            overflow: hidden;
            background: #f0f2f5;
            cursor: grab;
            touch-action: none;
        }
        [data-theme="dark"] .map-view { background: #0f172a; }
        .map-view.dragging { cursor: grabbing; }
        .map-view.measuring { cursor: crosshair; }
        .map-image {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            object-fit: fill;
            pointer-events: none;
            transform-origin: 0 0;
            user-select: none;
            -webkit-user-drag: none;
            transition: opacity 0.15s ease;
        }
        .mini-legend {
            position: absolute;
            bottom: 8px;
            left: 8px;
            z-index: 4;
            max-width: 220px;
            max-height: 140px;
            overflow-y: auto;
            padding: 6px 9px;
            border: 1px solid var(--line);
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.92);
            box-shadow: 0 2px 6px rgba(0,0,0,0.15);
            font-size: 11px;
            color: #16323f;
            backdrop-filter: blur(4px);
        }
        [data-theme="dark"] .mini-legend {
            background: rgba(30, 41, 59, 0.92);
            color: #f0f4f8;
        }
        .mini-legend-title {
            font-weight: 700;
            font-size: 10px;
            margin-bottom: 4px;
            color: var(--accent);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 3px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .legend-swatch {
            width: 12px;
            height: 12px;
            border-radius: 3px;
            border: 1px solid rgba(0,0,0,0.2);
            flex-shrink: 0;
        }
        .laser {
            position: absolute;
            z-index: 5;
            display: none;
            width: 16px;
            height: 16px;
            border: 2px solid #ffffff;
            border-radius: 50%;
            background: var(--laser);
            box-shadow: 0 0 0 2px var(--laser), 0 0 12px rgba(231, 76, 60, 0.8);
            pointer-events: none;
            transform: translate(-50%, -50%);
        }
        svg.measure-svg {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 6;
        }
        footer {
            display: flex;
            align-items: center;
            justify-content: space-between;
            min-height: 30px;
            padding: 4px 14px;
            border-top: 1px solid var(--line);
            background: var(--panel);
            color: var(--muted);
            font-size: 11px;
        }
        #measure-readout { color: #e67e22; font-weight: 700; font-family: Consolas, monospace; }
        #coordinates { color: var(--accent); font-family: Consolas, monospace; font-weight: 700; }
        @media (max-width: 760px) {
            body { overflow: auto; }
            header { align-items: flex-start; flex-wrap: wrap; }
            .toolbar { width: 100%; }
            .grid {
                grid-template-columns: 1fr;
                grid-template-rows: none;
                flex: none;
                min-height: auto;
            }
            .panel { height: 52vh; min-height: 320px; }
            footer { position: sticky; bottom: 0; flex-direction: column; gap: 4px; text-align: center; }
        }
    </style>
</head>
<body>
    <header>
        <div class="heading">
            <h1>__TITLE__</h1>
            <div class="subtitle">
                02Multimap export · exact QGIS panel rendering · synchronized navigation, measure & laser cursor
            </div>
        </div>
        <div class="toolbar" aria-label="Map navigation">
            <button id="zoom-out" type="button" title="Zoom out">−</button>
            <span id="zoom-readout" class="zoom-readout">100%</span>
            <button id="zoom-in" type="button" title="Zoom in">+</button>
            <button id="reset-view" type="button" class="btn-accent">Reset View</button>
            <button id="measure-btn" type="button" title="Toggle measurement tool">📏 Measure</button>
            <button id="theme-toggle" type="button" title="Toggle color theme">🌓 Theme</button>
        </div>
    </header>
    <main class="grid">
__PANELS__
    </main>
    <footer>
        <span id="help-text">Drag or scroll wheel in any panel to navigate in sync. Double-click to reset.</span>
        <span id="measure-readout"></span>
        <span id="coordinates">Cursor: —</span>
    </footer>
    <script>
        "use strict";
        const panelData = __PANEL_DATA__;
        const views = Array.from(document.querySelectorAll(".map-view"));
        const images = Array.from(document.querySelectorAll(".map-image"));
        const lasers = Array.from(document.querySelectorAll(".laser"));
        const coordinateReadout = document.getElementById("coordinates");
        const measureReadout = document.getElementById("measure-readout");
        const zoomReadout = document.getElementById("zoom-readout");
        const themeToggle = document.getElementById("theme-toggle");
        const measureBtn = document.getElementById("measure-btn");
        const state = { scale: 1, offsetX: 0, offsetY: 0 };
        let drag = null;
        let isMeasuring = false;
        let measurePoints = [];

        const themes = ["slate", "dark", "emerald"];
        let currentThemeIndex = themes.indexOf(document.documentElement.getAttribute("data-theme") || "slate");

        themeToggle.addEventListener("click", () => {
            currentThemeIndex = (currentThemeIndex + 1) % themes.length;
            const nextTheme = themes[currentThemeIndex];
            document.documentElement.setAttribute("data-theme", nextTheme);
        });

        measureBtn.addEventListener("click", () => {
            isMeasuring = !isMeasuring;
            measureBtn.classList.toggle("active", isMeasuring);
            views.forEach(v => v.classList.toggle("measuring", isMeasuring));
            if (!isMeasuring) {
                measurePoints = [];
                measureReadout.textContent = "";
                drawMeasureSVGs();
            }
        });

        document.querySelectorAll(".opacity-slider").forEach((slider, idx) => {
            slider.addEventListener("input", (e) => {
                if (images[idx]) {
                    images[idx].style.opacity = e.target.value / 100;
                }
            });
        });

        function clampState() {
            state.scale = Math.min(16, Math.max(0.5, state.scale));
            state.offsetX = Math.min(0, Math.max(1 - state.scale, state.offsetX));
            state.offsetY = Math.min(0, Math.max(1 - state.scale, state.offsetY));
        }

        function applyState() {
            clampState();
            views.forEach((view, index) => {
                const x = state.offsetX * view.clientWidth;
                const y = state.offsetY * view.clientHeight;
                images[index].style.transform = `translate(${x}px, ${y}px) scale(${state.scale})`;
            });
            zoomReadout.textContent = `${Math.round(state.scale * 100)}%`;
            drawMeasureSVGs();
        }

        function zoomAt(factor, anchorX, anchorY) {
            const oldScale = state.scale;
            const nextScale = Math.min(16, Math.max(0.5, oldScale * factor));
            const mapX = (anchorX - state.offsetX) / oldScale;
            const mapY = (anchorY - state.offsetY) / oldScale;
            state.scale = nextScale;
            state.offsetX = anchorX - mapX * nextScale;
            state.offsetY = anchorY - mapY * nextScale;
            applyState();
        }

        function resetView() {
            state.scale = 1;
            state.offsetX = 0;
            state.offsetY = 0;
            applyState();
        }

        function calculateDistance(pt1, pt2) {
            const dx = pt2.x - pt1.x;
            const dy = pt2.y - pt1.y;
            return Math.sqrt(dx * dx + dy * dy);
        }

        function drawMeasureSVGs() {
            views.forEach((view) => {
                let svg = view.querySelector("svg.measure-svg");
                if (!svg) {
                    svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
                    svg.setAttribute("class", "measure-svg");
                    view.appendChild(svg);
                }
                svg.innerHTML = "";
                if (measurePoints.length < 2) return;

                let pathData = "";
                measurePoints.forEach((pt, i) => {
                    const screenX = (pt.normX * state.scale + state.offsetX) * view.clientWidth;
                    const screenY = (pt.normY * state.scale + state.offsetY) * view.clientHeight;
                    pathData += (i === 0 ? "M " : "L ") + screenX + " " + screenY + " ";

                    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                    circle.setAttribute("cx", screenX);
                    circle.setAttribute("cy", screenY);
                    circle.setAttribute("r", "4");
                    circle.setAttribute("fill", "#e67e22");
                    circle.setAttribute("stroke", "#ffffff");
                    circle.setAttribute("stroke-width", "1.5");
                    svg.appendChild(circle);
                });

                const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                path.setAttribute("d", pathData);
                path.setAttribute("stroke", "#e67e22");
                path.setAttribute("stroke-width", "2.5");
                path.setAttribute("fill", "none");
                path.setAttribute("stroke-dasharray", "4 4");
                svg.insertBefore(path, svg.firstChild);
            });
        }

        function showLaser(sourceIndex, clientX, clientY) {
            const source = views[sourceIndex];
            const rect = source.getBoundingClientRect();
            const screenX = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
            const screenY = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
            lasers.forEach((laser) => {
                laser.style.left = `${screenX * 100}%`;
                laser.style.top = `${screenY * 100}%`;
                laser.style.display = "block";
            });

            const data = panelData[sourceIndex];
            const mapX = (screenX - state.offsetX) / state.scale;
            const mapY = (screenY - state.offsetY) / state.scale;
            const x = data.extent[0] + mapX * (data.extent[2] - data.extent[0]);
            const y = data.extent[3] - mapY * (data.extent[3] - data.extent[1]);
            const digits = Math.max(Math.abs(x), Math.abs(y)) >= 1000 ? 2 : 6;
            coordinateReadout.textContent = `Cursor: ${x.toFixed(digits)}, ${y.toFixed(digits)} · ${data.crs}`;
        }

        function hideLasers() {
            lasers.forEach((laser) => { laser.style.display = "none"; });
            coordinateReadout.textContent = "Cursor: —";
        }

        views.forEach((view, index) => {
            view.addEventListener("click", (event) => {
                if (!isMeasuring) return;
                const rect = view.getBoundingClientRect();
                const screenX = (event.clientX - rect.left) / rect.width;
                const screenY = (event.clientY - rect.top) / rect.height;
                const normX = (screenX - state.offsetX) / state.scale;
                const normY = (screenY - state.offsetY) / state.scale;

                const data = panelData[index];
                const mapX = data.extent[0] + normX * (data.extent[2] - data.extent[0]);
                const mapY = data.extent[3] - normY * (data.extent[3] - data.extent[1]);

                measurePoints.push({ normX, normY, mapX, mapY });

                if (measurePoints.length >= 2) {
                    let totalDist = 0;
                    for (let i = 1; i < measurePoints.length; i++) {
                        totalDist += calculateDistance(measurePoints[i-1], measurePoints[i]);
                    }
                    const distStr = totalDist >= 1000 ? (totalDist/1000).toFixed(2) + " km" : totalDist.toFixed(1) + " m";
                    measureReadout.textContent = `Measure: ${distStr}`;
                }
                drawMeasureSVGs();
            });

            view.addEventListener("pointerdown", (event) => {
                if (isMeasuring) return;
                view.setPointerCapture(event.pointerId);
                view.classList.add("dragging");
                drag = {
                    pointerId: event.pointerId,
                    x: event.clientX,
                    y: event.clientY,
                    width: view.clientWidth,
                    height: view.clientHeight
                };
            });
            view.addEventListener("pointermove", (event) => {
                showLaser(index, event.clientX, event.clientY);
                if (!drag || drag.pointerId !== event.pointerId) return;
                state.offsetX += (event.clientX - drag.x) / drag.width;
                state.offsetY += (event.clientY - drag.y) / drag.height;
                drag.x = event.clientX;
                drag.y = event.clientY;
                applyState();
            });
            const endDrag = (event) => {
                if (drag && drag.pointerId === event.pointerId) drag = null;
                view.classList.remove("dragging");
            };
            view.addEventListener("pointerup", endDrag);
            view.addEventListener("pointercancel", endDrag);
            view.addEventListener("pointerleave", () => {
                if (!drag) hideLasers();
            });
            view.addEventListener("wheel", (event) => {
                event.preventDefault();
                const rect = view.getBoundingClientRect();
                const x = (event.clientX - rect.left) / rect.width;
                const y = (event.clientY - rect.top) / rect.height;
                zoomAt(event.deltaY < 0 ? 1.25 : 0.8, x, y);
            }, { passive: false });
            view.addEventListener("dblclick", resetView);
        });

        document.getElementById("zoom-in").addEventListener("click", () => zoomAt(1.25, 0.5, 0.5));
        document.getElementById("zoom-out").addEventListener("click", () => zoomAt(0.8, 0.5, 0.5));
        document.getElementById("reset-view").addEventListener("click", resetView);
        window.addEventListener("resize", applyState);
        applyState();
    </script>
</body>
</html>
"""


def _safe_script_json(value: object) -> str:
    """Serialize JSON without allowing user text to terminate the script tag."""
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def build_dashboard_html(
    title: str,
    rows: int,
    cols: int,
    panels: Sequence[Mapping[str, object]],
    theme: str = "slate",
) -> str:
    """Return a complete offline HTML dashboard for rendered panel snapshots."""
    if rows < 1 or cols < 1:
        raise ValueError("Dashboard rows and columns must be positive.")
    if len(panels) != rows * cols:
        raise ValueError("Dashboard panel count does not match the selected grid.")

    safe_theme = theme.lower() if theme.lower() in ("slate", "dark", "emerald") else "slate"
    safe_title = html.escape(title.strip() or "Comparative Map Grid", quote=True)
    panel_markup = []
    script_data = []

    for index, panel in enumerate(panels):
        image_data = str(panel.get("image_data", ""))
        if not image_data.startswith("data:image/png;base64,"):
            raise ValueError(f"Panel {index + 1} does not contain a PNG snapshot.")

        extent_value = panel.get("extent")
        if not isinstance(extent_value, (list, tuple)) or len(extent_value) != 4:
            raise ValueError(f"Panel {index + 1} has an invalid extent.")
        extent = [float(value) for value in extent_value]
        panel_id = f"map-panel-{index}"
        panel_title = html.escape(str(panel.get("title", f"Panel {index + 1}")), quote=True)
        detail = html.escape(str(panel.get("detail", "QGIS map render")), quote=True)
        crs = str(panel.get("crs", "Unknown CRS"))

        legend_html = ""
        legend_items = panel.get("legend")
        if isinstance(legend_items, list) and legend_items:
            item_htmls = []
            for item in legend_items[:8]:
                if isinstance(item, dict):
                    lbl = html.escape(str(item.get("label", "")), quote=True)
                    col = html.escape(str(item.get("color", "#2a8f85")), quote=True)
                    item_htmls.append(
                        f"                    <div class=\"legend-item\">\n"
                        f"                        <span class=\"legend-swatch\" style=\"background-color: {col};\"></span>\n"
                        f"                        <span>{lbl}</span>\n"
                        f"                    </div>"
                    )
            if item_htmls:
                legend_html = (
                    "                <div class=\"mini-legend\">\n"
                    "                    <div class=\"mini-legend-title\">Legend</div>\n"
                    + "\n".join(item_htmls) + "\n"
                    "                </div>\n"
                )

        detail_tooltip = html.escape(str(panel.get("detail_tooltip", detail)), quote=True)
        scale_str = html.escape(str(panel.get("scale", "")), quote=True)
        scale_chip = f"                <span class=\"scale-chip\" title=\"Panel Scale\">{scale_str}</span>\n" if scale_str else ""

        panel_markup.append(
            "        <section class=\"panel\">\n"
            "            <div class=\"panel-header\">\n"
            f"                <span class=\"panel-title\">{panel_title}</span>\n"
            f"{scale_chip}"
            f"                <span class=\"mode-chip\" title=\"{detail_tooltip}\">{detail}</span>\n"
            "                <div class=\"opacity-control\" title=\"Layer Opacity\">\n"
            "                    <span>Op</span>\n"
            "                    <input type=\"range\" class=\"opacity-slider\" min=\"10\" max=\"100\" value=\"100\">\n"
            "                </div>\n"
            "            </div>\n"
            f"            <div id=\"{panel_id}\" class=\"map-view\" aria-label=\"{panel_title}\">\n"
            f"                <img class=\"map-image\" src=\"{image_data}\" alt=\"{panel_title}\">\n"
            f"{legend_html}"
            "                <span class=\"laser\"></span>\n"
            "            </div>\n"
            "        </section>"
        )

        script_data.append({"extent": extent, "crs": crs})

    content = _DASHBOARD_TEMPLATE
    content = content.replace("__INITIAL_THEME__", safe_theme)
    content = content.replace("__ROWS__", str(rows))
    content = content.replace("__COLS__", str(cols))
    content = content.replace("__PANEL_DATA__", _safe_script_json(script_data))
    content = content.replace("__PANELS__", "\n".join(panel_markup))
    content = content.replace("__TITLE__", safe_title)
    return content
