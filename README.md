<div align="center">

<img src="icons/icon.png" width="96" alt="02Multimap icon"/>

# 02Multimap: Sync-up Map Layers

**Multi-panel synchronized map visualization workspace for QGIS 3.40 LTR and QGIS 4. Open 2, 3, 4, 6, or 8 map views in a coordinated grid layout with real-time navigation syncing and laser pointer crosshair tracking.**

[![QGIS](https://img.shields.io/badge/QGIS-3.40%2B%20%7C%204.x-93b023?logo=qgis&logoColor=white)](https://plugins.qgis.org/plugins/zero2multimap/)
[![Version](https://img.shields.io/github/v/tag/YusufEminoglu/zero2multimap?label=version&color=blue)](https://github.com/YusufEminoglu/zero2multimap/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0-orange)](LICENSE)
[![GitHub Pages](https://img.shields.io/badge/docs-GitHub%20Pages-blueviolet?logo=github&logoColor=white)](https://yusufeminoglu.github.io/zero2multimap/)

<img src="docs/hero.png" width="800" alt="02Multimap in action"/>

</div>

---

## Why 02Multimap?

Urban planners, environmental scientists, and demographers often need to compare multiple spatial patterns side-by-side: historical urban growth, demographic variations (e.g., age groups, income levels), hazard risks (flood zones vs. landslide vulnerabilities), or temporal policy variations. 

Instead of toggling layers on and off in a single QGIS map canvas, **02Multimap** provides a unified workspace container hosting up to 8 independent map views. Panning or zooming on one view immediately coordinates all others, while a coordinated laser crosshair tracks cursor positions dynamically across all panels.

## ✨ Features

- **Dynamic Layout Grids** — Instantly switch between multiple panel configurations: 2 panels (1x2 or 2x1), 3 panels (1x3), 4 panels (2x2), 6 panels (2x3), or 8 panels (2x4).
- **Choose for Me · Current Extent** — Pick a panel count and automatically assign a different layer to every panel, prioritizing layers that overlap the current QGIS map extent.
- **Bi-directional Navigation Sync** — Drag or scroll zoom in any panel to update the rest. Optionally link navigation back to the main QGIS map canvas.
- **Laser Crosshair Tracking** — Move your mouse on any panel to project neon-colored crosshairs tracking the exact same geographic coordinate across all other views and the main QGIS canvas.
- **Dynamic Render Modes** — Configure each panel to render different source layers:
  - *Follow Main Map*: Follows active layers in the main QGIS map canvas.
  - *Compare One Layer*: Isolates a specific layer on top of an optional shared background. Select a different layer in every panel to build a comparison grid.
  - *Use Map Theme*: Follows a custom QGIS Map Theme (layer visibility and style preset).
- **Offline HTML Dashboard Export** — Creates one self-contained interactive file that preserves exact QGIS panel rendering, including labels, vector styles, raster layers, shared backgrounds, and map themes.
- **Responsive Slate-Teal Interface** — Uses a clear two-row workflow toolbar, coordinate readouts, active-panel borders, contextual help, and resizable panel splitters.
- **QGIS 3.40 & 4 Compatible** — Uses `qgis.PyQt` compatibility APIs for both PyQt5 and PyQt6, with zero external Python dependencies.
- **100% E2E Verified** — The automatic four-layer comparison and offline four-panel HTML export workflows run headlessly under real QGIS 3.40 LTR and QGIS 4.

## 🚀 Installation

**From the QGIS Plugin Hub:** `Plugins → Manage and Install Plugins…` → search for **"02Multimap"** → *Install*.

**From a release zip:** Download the latest zip from [Releases](https://github.com/YusufEminoglu/zero2multimap/releases) → `Plugins → Install from ZIP`.

Requires QGIS 3.40 or newer and is ready for QGIS 4.x.

## 📖 Quick Start

1. Add the layers you want to compare and zoom the main QGIS map to your study area.
2. Open 02Multimap from its toolbar icon.
3. Choose the desired grid, for example **4 Panels (2x2)**.
4. Click **Choose for Me · Current Extent**. The four panels will switch to **Compare One Layer** and receive four different layers automatically.
5. Fine-tune any panel with its layer dropdown, then pan or zoom to compare all maps in sync.
6. Use **Export / Print… → Interactive HTML Dashboard** to create an offline shareable result.

## ⚙️ Layout Grid Reference

| Grid Configuration | Layout Matrix | Ideal For |
|--------------------|---------------|-----------|
| **2 Panels (1x2)** | 1 Row x 2 Columns | Before/after comparison, suitability vs. actual, side-by-side |
| **2 Panels (2x1)** | 2 Rows x 1 Column | Vertical terrain profiles, screen-optimized comparisons |
| **3 Panels (1x3)** | 1 Row x 3 Columns | Past, present, and future scenario timelines |
| **4 Panels (2x2)** | 2 Rows x 2 Columns | Multi-scenario comparison, demographic cross-checks |
| **6 Panels (2x3)** | 2 Rows x 3 Columns | Multi-criteria evaluations, regional comparisons |
| **8 Panels (2x4)** | 2 Rows x 4 Columns | Heavy temporal/categorical zonation analyses |

## 🧩 Part of the PlanX / 02viz ecosystem

**02Multimap** is part of the 16 open-source QGIS plugins for urban planning and visual analysis:

| Planning & analysis | CAD & production | Visualization & display |
|---|---|---|
| [PlanX](https://github.com/YusufEminoglu/PlanX) — spatial-planning suite | [PlanX CAD Toolset](https://github.com/YusufEminoglu/PlanX-CAD) — drafting-grade CAD | [PlanX 3D City](https://github.com/YusufEminoglu/planx_3d_city) — Three.js city viewer |
| [GeoStats Lab](https://github.com/YusufEminoglu/planx_geostats) — spatial statistics | [EasyFillet](https://github.com/YusufEminoglu/EasyFillet) — tangent-arc fillet | [3D OSM Model](https://github.com/YusufEminoglu/osm_3d_model) — OSM → 3D city in browser |
| [Suitability Lab](https://github.com/YusufEminoglu/planx_suitability_lab) — raster MCDA | [Settlement Toolset](https://github.com/YusufEminoglu/PlanX-Settlement) — 9-stage settlement plans | [OSM Quick 3D](https://github.com/YusufEminoglu/osm_quick_3d) — OSM → native QGIS 3D |
| [DataCube Lab](https://github.com/YusufEminoglu/planx_datacube) — spatiotemporal cubes | [UIP Toolset](https://github.com/YusufEminoglu/PlanX-UIP) — Turkish master-plan automation | [Urban Procedural 3D](https://github.com/YusufEminoglu/planx_urban_procedural_3d) — parametric zoning lab |
| [Urban Resilience](https://github.com/YusufEminoglu/planx_urban_resilience) — 28 resilience tools | [ParcelFlux](https://github.com/YusufEminoglu/parcelflux) — parcel subdivision | [CartoLab](https://github.com/YusufEminoglu/planx_cartolab) — publication cartography |
| [02viz](https://github.com/YusufEminoglu/02viz) — visualization studio | | [02Multimap](https://github.com/YusufEminoglu/zero2multimap) — sync-up map layers |

## 📜 License & author

GPL-3.0 © [Yusuf Eminoğlu](https://github.com/YusufEminoglu) — bug reports and feature requests welcome in [Issues](https://github.com/YusufEminoglu/zero2multimap/issues).
