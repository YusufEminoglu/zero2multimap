# -*- coding: utf-8 -*-
"""Pure-Python builder for self-contained 02Multimap HTML dashboards."""
from __future__ import annotations

import html
import json
import math
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
            --hud-bg: rgba(255, 255, 255, 0.92);
            --hud-ink: #16323f;
            --measure: #a84f00;
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
            --hud-bg: rgba(15, 23, 42, 0.90);
            --hud-ink: #f0f4f8;
            --measure: #fbbf24;
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
            --hud-bg: rgba(255, 255, 255, 0.92);
            --hud-ink: #064e3b;
            --measure: #9a3412;
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
            gap: 6px;
            min-height: 34px;
            padding: 4px 9px;
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
            max-width: 42%;
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
            width: 50px;
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
            object-fit: contain;
            object-position: center;
            pointer-events: none;
            transform-origin: 0 0;
            user-select: none;
            -webkit-user-drag: none;
            transition: opacity 0.15s ease;
        }

        .north-arrow-hud {
            position: absolute;
            top: 6px;
            right: 6px;
            z-index: 4;
            width: 30px;
            height: 34px;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 1px solid var(--line);
            border-radius: 6px;
            background: var(--hud-bg);
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.15);
            pointer-events: none;
            backdrop-filter: blur(4px);
        }
        .north-arrow-hud svg {
            width: 22px;
            height: 26px;
        }

        .scale-bar-hud {
            position: absolute;
            bottom: 6px;
            right: 6px;
            z-index: 4;
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 2px;
            padding: 4px 7px;
            border: 1px solid var(--line);
            border-radius: 5px;
            background: var(--hud-bg);
            color: var(--hud-ink);
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.15);
            font: 700 10px Consolas, monospace;
            pointer-events: none;
            backdrop-filter: blur(4px);
        }
        .scale-bar-segments {
            display: flex;
            height: 5px;
            border: 1px solid var(--hud-ink);
            overflow: hidden;
        }
        .scale-seg-dark {
            height: 100%;
            background: var(--hud-ink);
        }
        .scale-seg-light {
            height: 100%;
            background: var(--panel);
        }

        .mini-legend {
            position: absolute;
            bottom: 6px;
            left: 6px;
            z-index: 4;
            max-width: min(210px, 54%);
            max-height: 120px;
            overflow-y: auto;
            padding: 5px 8px;
            border: 1px solid var(--line);
            border-radius: 6px;
            background: var(--hud-bg);
            box-shadow: 0 2px 6px rgba(0,0,0,0.15);
            font-size: 10.5px;
            color: var(--hud-ink);
            backdrop-filter: blur(4px);
        }
        .mini-legend-title {
            font-weight: 700;
            font-size: 9.5px;
            margin-bottom: 3px;
            color: var(--accent);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 5px;
            margin-bottom: 2px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .legend-swatch {
            width: 11px;
            height: 11px;
            border-radius: 2px;
            border: 1px solid rgba(0,0,0,0.25);
            flex-shrink: 0;
        }
        .grid.carto-hidden .north-arrow-hud,
        .grid.carto-hidden .scale-bar-hud,
        .grid.carto-hidden .mini-legend {
            display: none;
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
        #measure-readout { color: var(--measure); font-weight: 700; font-family: Consolas, monospace; }
        #coordinates { color: var(--accent); font-family: Consolas, monospace; font-weight: 700; }
        .grid.presentation-mode {
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .grid.presentation-mode .panel {
            display: none;
            width: 100%;
            height: 100%;
        }
        .grid.presentation-mode .panel.active-slide {
            display: flex;
        }
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
                Exported from QGIS with 02Multimap · panels pan and zoom together · north arrow, scale bar and legend on every panel
            </div>
        </div>
        <div class="toolbar" aria-label="Map navigation">
            <button id="zoom-out" type="button" title="Zoom out">−</button>
            <span id="zoom-readout" class="zoom-readout">100%</span>
            <button id="zoom-in" type="button" title="Zoom in">+</button>
            <button id="reset-view" type="button" class="btn-accent">Reset View</button>
            <button id="carto-btn" type="button" class="active" title="Toggle North Arrow, Scale Bar & Legend Overlays">🧭 Carto HUD</button>
            <button id="measure-btn" type="button" title="Measure distance: click points on a map; click again to stop">📏 Measure</button>
            <button id="presentation-btn" type="button" title="Toggle presentation slide mode">📺 Presentation</button>
            <button id="theme-toggle" type="button" title="Toggle color theme">🌓 Theme</button>
        </div>
    </header>

    <main class="grid">
__PANELS__
    </main>
    <footer>
        <span id="help-text">Drag or use the mouse wheel in any panel; the others follow. Double-click to reset.</span>
        <span id="measure-readout"></span>
        <span id="coordinates">Cursor: —</span>
    </footer>
    <script>
        "use strict";
        const panelData = __PANEL_DATA__;

        // <geometry> Pure functions without DOM access; the test suite runs this block on its own.
        // Each panel image is drawn with object-fit: contain, so it fills a centred content
        // rectangle inside its view and leaves empty bands (letterboxing) on two sides.
        function contentRect(viewW, viewH, imgW, imgH) {
            if (!(viewW > 0) || !(viewH > 0) || !(imgW > 0) || !(imgH > 0)) {
                return { x: 0, y: 0, w: Math.max(1, viewW || 0), h: Math.max(1, viewH || 0) };
            }
            const fit = Math.min(viewW / imgW, viewH / imgH);
            const w = imgW * fit;
            const h = imgH * fit;
            return { x: (viewW - w) / 2, y: (viewH - h) / 2, w: w, h: h };
        }

        // View pixel -> image position (u, v from 0 to 1), undoing pan/zoom and letterboxing.
        function viewToImage(px, py, viewW, viewH, imgW, imgH, view) {
            const box = contentRect(viewW, viewH, imgW, imgH);
            const lx = (px - view.offsetX * viewW) / view.scale;
            const ly = (py - view.offsetY * viewH) / view.scale;
            return { u: (lx - box.x) / box.w, v: (ly - box.y) / box.h };
        }

        function imageToView(u, v, viewW, viewH, imgW, imgH, view) {
            const box = contentRect(viewW, viewH, imgW, imgH);
            return {
                x: (box.x + u * box.w) * view.scale + view.offsetX * viewW,
                y: (box.y + v * box.h) * view.scale + view.offsetY * viewH
            };
        }

        function insideImage(p) {
            return p.u >= 0 && p.u <= 1 && p.v >= 0 && p.v <= 1;
        }

        // The image shows exactly `extent` (xmin, ymin, xmax, ymax) in the panel's CRS.
        function imageToMap(u, v, extent) {
            return {
                x: extent[0] + u * (extent[2] - extent[0]),
                y: extent[3] - v * (extent[3] - extent[1])
            };
        }

        function mapToImage(x, y, extent) {
            return {
                u: (x - extent[0]) / (extent[2] - extent[0]),
                v: (extent[3] - y) / (extent[3] - extent[1])
            };
        }

        // Longitude/latitude by bilinear interpolation in a grid computed by QGIS at export.
        function imageToLonLat(u, v, grid) {
            if (!grid || !(grid.n >= 2) || !Array.isArray(grid.points) || grid.points.length !== grid.n * grid.n) {
                return null;
            }
            const n = grid.n;
            const gx = Math.min(n - 1, Math.max(0, u * (n - 1)));
            const gy = Math.min(n - 1, Math.max(0, v * (n - 1)));
            const c0 = Math.min(n - 2, Math.floor(gx));
            const r0 = Math.min(n - 2, Math.floor(gy));
            const fx = gx - c0;
            const fy = gy - r0;
            const a = grid.points[r0 * n + c0];
            const b = grid.points[r0 * n + c0 + 1];
            const c = grid.points[(r0 + 1) * n + c0];
            const d = grid.points[(r0 + 1) * n + c0 + 1];
            return {
                lon: (a[0] * (1 - fx) + b[0] * fx) * (1 - fy) + (c[0] * (1 - fx) + d[0] * fx) * fy,
                lat: (a[1] * (1 - fx) + b[1] * fx) * (1 - fy) + (c[1] * (1 - fx) + d[1] * fx) * fy
            };
        }

        function haversineMeters(lon1, lat1, lon2, lat2) {
            const R = 6371008.8;
            const toRad = Math.PI / 180.0;
            const dLat = (lat2 - lat1) * toRad;
            const dLon = (lon2 - lon1) * toRad;
            const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                      Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) *
                      Math.sin(dLon / 2) * Math.sin(dLon / 2);
            return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1 - a)));
        }

        // Distance on the WGS 84 ellipsoid (Vincenty's inverse formula); haversine as a fallback.
        function geodesicMeters(lon1, lat1, lon2, lat2) {
            const a = 6378137.0;
            const f = 1 / 298.257223563;
            const b = a * (1 - f);
            const rad = Math.PI / 180;
            const L = (lon2 - lon1) * rad;
            const U1 = Math.atan((1 - f) * Math.tan(lat1 * rad));
            const U2 = Math.atan((1 - f) * Math.tan(lat2 * rad));
            const sinU1 = Math.sin(U1), cosU1 = Math.cos(U1);
            const sinU2 = Math.sin(U2), cosU2 = Math.cos(U2);
            let lambda = L;
            let previous = 0;
            let iterations = 0;
            let sinSigma = 0, cosSigma = 0, sigma = 0, cosSqAlpha = 0, cos2SigmaM = 0;
            do {
                const sinLambda = Math.sin(lambda);
                const cosLambda = Math.cos(lambda);
                const t1 = cosU2 * sinLambda;
                const t2 = cosU1 * sinU2 - sinU1 * cosU2 * cosLambda;
                sinSigma = Math.sqrt(t1 * t1 + t2 * t2);
                if (sinSigma === 0) return 0;
                cosSigma = sinU1 * sinU2 + cosU1 * cosU2 * cosLambda;
                sigma = Math.atan2(sinSigma, cosSigma);
                const sinAlpha = cosU1 * cosU2 * sinLambda / sinSigma;
                cosSqAlpha = 1 - sinAlpha * sinAlpha;
                cos2SigmaM = cosSqAlpha !== 0 ? cosSigma - 2 * sinU1 * sinU2 / cosSqAlpha : 0;
                const C = f / 16 * cosSqAlpha * (4 + f * (4 - 3 * cosSqAlpha));
                previous = lambda;
                lambda = L + (1 - C) * f * sinAlpha *
                    (sigma + C * sinSigma * (cos2SigmaM + C * cosSigma * (-1 + 2 * cos2SigmaM * cos2SigmaM)));
                iterations += 1;
            } while (Math.abs(lambda - previous) > 1e-12 && iterations < 200);
            if (iterations >= 200) return haversineMeters(lon1, lat1, lon2, lat2);
            const uSq = cosSqAlpha * (a * a - b * b) / (b * b);
            const A = 1 + uSq / 16384 * (4096 + uSq * (-768 + uSq * (320 - 175 * uSq)));
            const B = uSq / 1024 * (256 + uSq * (-128 + uSq * (74 - 47 * uSq)));
            const deltaSigma = B * sinSigma * (cos2SigmaM + B / 4 * (cosSigma * (-1 + 2 * cos2SigmaM * cos2SigmaM) -
                B / 6 * cos2SigmaM * (-3 + 4 * sinSigma * sinSigma) * (-3 + 4 * cos2SigmaM * cos2SigmaM)));
            return b * A * (sigma - deltaSigma);
        }

        // Ground distance between two described points (see describePoint); NaN if unknown.
        function segmentMeters(p1, p2) {
            if (p1.lonlat && p2.lonlat) {
                return geodesicMeters(p1.lonlat.lon, p1.lonlat.lat, p2.lonlat.lon, p2.lonlat.lat);
            }
            if (p1.crs === p2.crs && p1.unitM > 0) {
                return Math.hypot(p2.x - p1.x, p2.y - p1.y) * p1.unitM;
            }
            return NaN;
        }

        // Ground metres per screen pixel along the image's horizontal centre line.
        function metersPerScreenPixel(viewW, viewH, imgW, imgH, groundWidthM, scale) {
            const box = contentRect(viewW, viewH, imgW, imgH);
            return groundWidthM / (box.w * scale);
        }

        function niceScaleNumber(val) {
            if (!(val > 0) || !isFinite(val)) return 0;
            const exp = Math.floor(Math.log10(val));
            const base = Math.pow(10, exp);
            const frac = val / base;
            const nice = frac >= 5 ? 5 : (frac >= 2 ? 2 : 1);
            return Number((nice * base).toPrecision(6));
        }

        function formatDistance(m) {
            if (!isFinite(m)) return "—";
            if (m < 1000) return `${m.toFixed(1)} m`;
            if (m < 10000) return `${(m / 1000).toFixed(3)} km`;
            return `${(m / 1000).toFixed(2)} km`;
        }
        // </geometry>

        const views = Array.from(document.querySelectorAll(".map-view"));
        const images = Array.from(document.querySelectorAll(".map-image"));
        const lasers = Array.from(document.querySelectorAll(".laser"));
        const scaleBars = Array.from(document.querySelectorAll(".scale-bar-hud"));
        const coordinateReadout = document.getElementById("coordinates");
        const measureReadout = document.getElementById("measure-readout");
        const zoomReadout = document.getElementById("zoom-readout");
        const themeToggle = document.getElementById("theme-toggle");
        const measureBtn = document.getElementById("measure-btn");
        const cartoBtn = document.getElementById("carto-btn");
        const themes = ["slate", "dark", "emerald"];
        let currentThemeIndex = Math.max(0, themes.indexOf(document.documentElement.getAttribute("data-theme") || "slate"));
        const state = { scale: 1, offsetX: 0, offsetY: 0 };
        let drag = null;
        let isMeasuring = false;
        let showCarto = true;
        let measurePoints = [];

        const presentationBtn = document.getElementById("presentation-btn");
        let isPresentation = false;
        let currentSlide = 0;

        if (presentationBtn) {
            presentationBtn.addEventListener("click", () => {
                isPresentation = !isPresentation;
                presentationBtn.classList.toggle("active", isPresentation);
                document.querySelector(".grid").classList.toggle("presentation-mode", isPresentation);
                updateSlideView();
                applyState();
            });
        }

        if (cartoBtn) {
            cartoBtn.addEventListener("click", () => {
                showCarto = !showCarto;
                cartoBtn.classList.toggle("active", showCarto);
                document.querySelector(".grid").classList.toggle("carto-hidden", !showCarto);
            });
        }

        function updateSlideView() {
            const panels = document.querySelectorAll(".panel");
            panels.forEach((p, idx) => {
                p.classList.toggle("active-slide", isPresentation && idx === currentSlide);
            });
        }

        window.addEventListener("keydown", (e) => {
            if (!isPresentation) return;
            const panels = document.querySelectorAll(".panel");
            if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                currentSlide = (currentSlide + 1) % panels.length;
                updateSlideView();
                applyState();
            } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                currentSlide = (currentSlide - 1 + panels.length) % panels.length;
                updateSlideView();
                applyState();
            }
        });

        themeToggle.addEventListener("click", () => {
            currentThemeIndex = (currentThemeIndex + 1) % themes.length;
            const nextTheme = themes[currentThemeIndex];
            document.documentElement.setAttribute("data-theme", nextTheme);
        });

        measureBtn.addEventListener("click", () => {
            isMeasuring = !isMeasuring;
            measureBtn.classList.toggle("active", isMeasuring);
            views.forEach(v => v.classList.toggle("measuring", isMeasuring));
            measurePoints = [];
            measureReadout.textContent = isMeasuring ? "Distance: click points on a map" : "";
            drawMeasureSVGs();
        });

        document.querySelectorAll(".opacity-slider").forEach((slider, idx) => {
            slider.addEventListener("input", (e) => {
                if (images[idx]) {
                    images[idx].style.opacity = e.target.value / 100;
                }
            });
        });

        function imageSize(index) {
            const data = panelData[index] || {};
            const size = data.image_size;
            if (Array.isArray(size) && size[0] > 0 && size[1] > 0) return { w: size[0], h: size[1] };
            const img = images[index];
            if (img && img.naturalWidth > 0 && img.naturalHeight > 0) return { w: img.naturalWidth, h: img.naturalHeight };
            return null;
        }

        function pointerToImage(index, clientX, clientY) {
            const rect = views[index].getBoundingClientRect();
            const img = imageSize(index) || { w: rect.width, h: rect.height };
            return viewToImage(clientX - rect.left, clientY - rect.top, rect.width, rect.height, img.w, img.h, state);
        }

        function describePoint(index, uv) {
            const data = panelData[index];
            const xy = imageToMap(uv.u, uv.v, data.extent);
            return {
                u: uv.u, v: uv.v, x: xy.x, y: xy.y, crs: data.crs,
                unitM: Number(data.map_unit_m) || 0,
                lonlat: imageToLonLat(uv.u, uv.v, data.lonlat_grid)
            };
        }

        // Where a described point lies in panel `index`: the same map position when the
        // panel uses the same CRS, otherwise the same image position.
        function placeOnPanel(index, point) {
            const data = panelData[index];
            const view = views[index];
            let uv = { u: point.u, v: point.v };
            if (data && data.crs === point.crs) uv = mapToImage(point.x, point.y, data.extent);
            const img = imageSize(index) || { w: view.clientWidth, h: view.clientHeight };
            const spot = imageToView(uv.u, uv.v, view.clientWidth, view.clientHeight, img.w, img.h, state);
            return { x: spot.x, y: spot.y, inside: insideImage(uv) };
        }

        function updateScaleBars() {
            views.forEach((view, idx) => {
                const hud = scaleBars[idx];
                if (!hud) return;
                const data = panelData[idx] || {};
                const img = imageSize(idx);
                const groundW = Number(data.ground_width_m);
                const viewW = view.clientWidth;
                const viewH = view.clientHeight;
                if (!(viewW > 0) || !(viewH > 0) || !img || !(groundW > 0)) {
                    hud.style.visibility = "hidden";
                    return;
                }
                const metersPerPx = metersPerScreenPixel(viewW, viewH, img.w, img.h, groundW, state.scale);
                const meters = niceScaleNumber(72 * metersPerPx);
                const barPx = meters / metersPerPx;
                hud.style.visibility = "visible";
                const segDark = hud.querySelector(".scale-seg-dark");
                const segLight = hud.querySelector(".scale-seg-light");
                const lblEl = hud.querySelector(".scale-bar-label");
                if (segDark && segLight) {
                    segDark.style.width = `${(barPx / 2).toFixed(2)}px`;
                    segLight.style.width = `${(barPx / 2).toFixed(2)}px`;
                }
                if (lblEl) {
                    lblEl.textContent = meters >= 1000 ? `${Number((meters / 1000).toPrecision(6))} km` : `${meters} m`;
                }
            });
        }

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
            updateScaleBars();
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

        function updateMeasureReadout() {
            if (measurePoints.length < 2) {
                measureReadout.textContent = "Distance: click the next point";
                return;
            }
            let total = 0;
            for (let i = 1; i < measurePoints.length; i++) {
                total += segmentMeters(measurePoints[i - 1], measurePoints[i]);
            }
            measureReadout.textContent = isFinite(total)
                ? `Distance: ${formatDistance(total)}`
                : "Distance: not available for this coordinate system";
        }

        function drawMeasureSVGs() {
            views.forEach((view, index) => {
                let svg = view.querySelector("svg.measure-svg");
                if (!svg) {
                    svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
                    svg.setAttribute("class", "measure-svg");
                    view.appendChild(svg);
                }
                svg.innerHTML = "";
                if (measurePoints.length < 1) return;

                let pathData = "";
                measurePoints.forEach((pt, i) => {
                    const spot = placeOnPanel(index, pt);
                    pathData += (i === 0 ? "M " : "L ") + spot.x + " " + spot.y + " ";

                    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                    circle.setAttribute("cx", spot.x);
                    circle.setAttribute("cy", spot.y);
                    circle.setAttribute("r", "4");
                    circle.setAttribute("fill", "#e67e22");
                    circle.setAttribute("stroke", "#ffffff");
                    circle.setAttribute("stroke-width", "1.5");
                    svg.appendChild(circle);
                });
                if (measurePoints.length < 2) return;

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
            const data = panelData[sourceIndex];
            const uv = pointerToImage(sourceIndex, clientX, clientY);
            if (!data || !insideImage(uv)) {
                lasers.forEach((laser) => { laser.style.display = "none"; });
                coordinateReadout.textContent = "Cursor: outside the map";
                return;
            }
            const point = describePoint(sourceIndex, uv);
            lasers.forEach((laser, idx) => {
                const spot = placeOnPanel(idx, point);
                if (!spot.inside) {
                    laser.style.display = "none";
                    return;
                }
                laser.style.left = `${spot.x}px`;
                laser.style.top = `${spot.y}px`;
                laser.style.display = "block";
            });
            const digits = Math.max(Math.abs(point.x), Math.abs(point.y)) >= 1000 ? 2 : 6;
            coordinateReadout.textContent = `Cursor: ${point.x.toFixed(digits)}, ${point.y.toFixed(digits)} · ${data.crs}`;
        }

        function hideLasers() {
            lasers.forEach((laser) => { laser.style.display = "none"; });
            coordinateReadout.textContent = "Cursor: —";
        }

        views.forEach((view, index) => {
            view.addEventListener("click", (event) => {
                if (!isMeasuring) return;
                const uv = pointerToImage(index, event.clientX, event.clientY);
                if (!insideImage(uv) || !panelData[index]) return;
                measurePoints.push(describePoint(index, uv));
                updateMeasureReadout();
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

_NORTH_ARROW_SVG = (
    '<div class="north-arrow-hud" title="North">'
    '<svg viewBox="0 0 40 48" aria-hidden="true">'
    '<text x="20" y="11" text-anchor="middle" font-size="10" font-weight="800" fill="currentColor">N</text>'
    '<polygon points="20,14 11,42 20,35" fill="currentColor"/>'
    '<polygon points="20,14 29,42 20,35" fill="#2a8f85"/>'
    '</svg>'
    '</div>'
)

_SCALE_BAR_HUD = (
    '<div class="scale-bar-hud" title="Scale bar (ground distance at the centre of the map)">'
    '<span class="scale-bar-label">100 m</span>'
    '<div class="scale-bar-segments">'
    '<span class="scale-seg-dark" style="width:24px;"></span>'
    '<span class="scale-seg-light" style="width:24px;"></span>'
    '</div>'
    '</div>'
)


def _safe_script_json(value: object) -> str:
    """Serialize JSON without allowing user text to terminate the script tag."""
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _positive_number(value: object) -> float | None:
    """A finite number above zero, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _positive_pair(value: object) -> list[float] | None:
    """``[width, height]`` with both values finite and above zero, or ``None``."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    pair = [_positive_number(item) for item in value]
    return pair if None not in pair else None


def _lonlat_grid(value: object) -> dict | None:
    """A ``{"n": n, "points": [[lon, lat], ...]}`` grid of n x n finite WGS 84 points, or ``None``."""
    if not isinstance(value, Mapping):
        return None
    size = value.get("n")
    points = value.get("points")
    if isinstance(size, bool) or not isinstance(size, int) or size < 2 or not isinstance(points, (list, tuple)):
        return None
    if len(points) != size * size:
        return None
    clean = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        lon, lat = point
        if isinstance(lon, bool) or isinstance(lat, bool) or not isinstance(lon, (int, float)) \
                or not isinstance(lat, (int, float)):
            return None
        lon, lat = float(lon), float(lat)
        if not (math.isfinite(lon) and math.isfinite(lat) and -360.0 <= lon <= 360.0 and -90.0 <= lat <= 90.0):
            return None
        clean.append([lon, lat])
    return {"n": size, "points": clean}


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

        legend_items = panel.get("legend")
        item_htmls = []
        if isinstance(legend_items, list) and legend_items:
            for item in legend_items[:10]:
                if isinstance(item, dict):
                    lbl = html.escape(str(item.get("label", "")), quote=True)
                    col = html.escape(str(item.get("color", "#2a8f85")), quote=True)
                    item_htmls.append(
                        f"                    <div class=\"legend-item\">\n"
                        f"                        <span class=\"legend-swatch\" style=\"background-color: {col};\"></span>\n"
                        f"                        <span>{lbl}</span>\n"
                        f"                    </div>"
                    )
        if not item_htmls:
            fallback_label = detail if detail and detail != "QGIS map render" else panel_title
            item_htmls.append(
                f"                    <div class=\"legend-item\">\n"
                f"                        <span class=\"legend-swatch\" style=\"background-color: #2a8f85;\"></span>\n"
                f"                        <span>{fallback_label}</span>\n"
                f"                    </div>"
            )
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
            f"                {_NORTH_ARROW_SVG}\n"
            f"{legend_html}"
            f"                {_SCALE_BAR_HUD}\n"
            "                <span class=\"laser\"></span>\n"
            "            </div>\n"
            "        </section>"
        )

        extent_wgs84_val = panel.get("extent_wgs84", extent)
        script_data.append({
            "extent": extent,
            "extent_wgs84": extent_wgs84_val,
            "crs": crs,
            "image_size": _positive_pair(panel.get("image_size")),
            "ground_width_m": _positive_number(panel.get("ground_width_m")),
            "lonlat_grid": _lonlat_grid(panel.get("lonlat_grid")),
            "map_unit_m": _positive_number(panel.get("map_unit_m")),
        })

    content = _DASHBOARD_TEMPLATE
    content = content.replace("__INITIAL_THEME__", safe_theme)
    content = content.replace("__ROWS__", str(rows))
    content = content.replace("__COLS__", str(cols))
    content = content.replace("__PANEL_DATA__", _safe_script_json(script_data))
    content = content.replace("__PANELS__", "\n".join(panel_markup))
    content = content.replace("__TITLE__", safe_title)
    return content
