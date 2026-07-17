# -*- coding: utf-8 -*-
"""Pure-Python builder for self-contained 02Multimap HTML dashboards."""
from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence


_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="generator" content="02Multimap">
    <title>__TITLE__</title>
    <style>
        :root {
            color-scheme: light;
            --ink: #16323f;
            --muted: #5d6b73;
            --line: #cbd3da;
            --panel: #ffffff;
            --soft: #eef1f4;
            --accent: #2a8f85;
            --laser: #e74c3c;
        }
        * { box-sizing: border-box; }
        html, body { width: 100%; height: 100%; margin: 0; }
        body {
            display: flex;
            flex-direction: column;
            overflow: hidden;
            background: #dfe4e8;
            color: var(--ink);
            font-family: "Segoe UI", Inter, Arial, sans-serif;
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
            box-shadow: 0 2px 8px rgba(22, 50, 63, 0.08);
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
        .toolbar { display: flex; align-items: center; gap: 7px; }
        button {
            min-height: 32px;
            padding: 5px 11px;
            border: 1px solid var(--line);
            border-radius: 6px;
            background: var(--soft);
            color: var(--ink);
            font: 600 12px "Segoe UI", sans-serif;
            cursor: pointer;
        }
        button:hover { border-color: var(--accent); background: #e4e9ed; }
        button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
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
            gap: 4px;
            padding: 4px;
        }
        .panel {
            display: flex;
            min-width: 0;
            min-height: 0;
            flex-direction: column;
            overflow: hidden;
            border: 1px solid var(--line);
            border-radius: 7px;
            background: var(--panel);
            box-shadow: 0 1px 3px rgba(22, 50, 63, 0.1);
        }
        .panel-header {
            display: flex;
            align-items: center;
            gap: 8px;
            min-height: 35px;
            padding: 5px 9px;
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
        .mode-chip {
            max-width: 48%;
            overflow: hidden;
            padding: 2px 6px;
            border: 1px solid #b5d8d3;
            border-radius: 10px;
            background: #e4f2f0;
            color: #237a72;
            font-size: 10px;
            font-weight: 700;
            text-overflow: ellipsis;
            white-space: nowrap;
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
        .map-view.dragging { cursor: grabbing; }
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
        }
        .laser {
            position: absolute;
            z-index: 3;
            display: none;
            width: 17px;
            height: 17px;
            border: 2px solid #ffffff;
            border-radius: 50%;
            background: var(--laser);
            box-shadow: 0 0 0 2px var(--laser), 0 0 10px rgba(231, 76, 60, 0.7);
            pointer-events: none;
            transform: translate(-50%, -50%);
        }
        footer {
            display: flex;
            align-items: center;
            justify-content: space-between;
            min-height: 28px;
            padding: 4px 12px;
            border-top: 1px solid var(--line);
            background: var(--panel);
            color: var(--muted);
            font-size: 11px;
        }
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
            footer { position: sticky; bottom: 0; }
        }
    </style>
</head>
<body>
    <header>
        <div class="heading">
            <h1>__TITLE__</h1>
            <div class="subtitle">
                Self-contained export · exact QGIS panel rendering · synchronized navigation and laser
            </div>
        </div>
        <div class="toolbar" aria-label="Map navigation">
            <button id="zoom-out" type="button" title="Zoom out">−</button>
            <span id="zoom-readout" class="zoom-readout">100%</span>
            <button id="zoom-in" type="button" title="Zoom in">+</button>
            <button id="reset-view" type="button">Reset view</button>
        </div>
    </header>
    <main class="grid">
__PANELS__
    </main>
    <footer>
        <span>Drag or use the mouse wheel in any panel; every panel stays synchronized.</span>
        <span id="coordinates">Cursor: —</span>
    </footer>
    <script>
        "use strict";
        const panelData = __PANEL_DATA__;
        const views = Array.from(document.querySelectorAll(".map-view"));
        const images = Array.from(document.querySelectorAll(".map-image"));
        const lasers = Array.from(document.querySelectorAll(".laser"));
        const coordinateReadout = document.getElementById("coordinates");
        const zoomReadout = document.getElementById("zoom-readout");
        const state = { scale: 1, offsetX: 0, offsetY: 0 };
        let drag = null;

        function clampState() {
            state.scale = Math.min(12, Math.max(1, state.scale));
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
        }

        function zoomAt(factor, anchorX, anchorY) {
            const oldScale = state.scale;
            const nextScale = Math.min(12, Math.max(1, oldScale * factor));
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
            view.addEventListener("pointerdown", (event) => {
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
) -> str:
    """Return a complete offline HTML dashboard for rendered panel snapshots."""
    if rows < 1 or cols < 1:
        raise ValueError("Dashboard rows and columns must be positive.")
    if len(panels) != rows * cols:
        raise ValueError("Dashboard panel count does not match the selected grid.")

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

        panel_markup.append(
            "        <section class=\"panel\">\n"
            "            <div class=\"panel-header\">\n"
            f"                <span class=\"panel-title\">{panel_title}</span>\n"
            f"                <span class=\"mode-chip\" title=\"{detail}\">{detail}</span>\n"
            "            </div>\n"
            f"            <div id=\"{panel_id}\" class=\"map-view\" aria-label=\"{panel_title}\">\n"
            f"                <img class=\"map-image\" src=\"{image_data}\" alt=\"{panel_title}\">\n"
            "                <span class=\"laser\"></span>\n"
            "            </div>\n"
            "        </section>"
        )
        script_data.append({"extent": extent, "crs": crs})

    content = _DASHBOARD_TEMPLATE
    content = content.replace("__ROWS__", str(rows))
    content = content.replace("__COLS__", str(cols))
    content = content.replace("__PANEL_DATA__", _safe_script_json(script_data))
    content = content.replace("__PANELS__", "\n".join(panel_markup))
    content = content.replace("__TITLE__", safe_title)
    return content
