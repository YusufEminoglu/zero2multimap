# Changelog

## [1.0.0] - 2026-08-13

### Added
- **Dynamic Real-Time Scale Readout**: Status bar scale readout (`Scale: 1:25,000`) now updates dynamically on every viewport zoom and pan event.
- **Visual Spatial Difference Engine (⚡)**: Compute geometric differences between comparison layers with green (added) and red (removed) overlays and a live Overlap % readout.
- **Timeline Transition Player (▶)**: Auto-cycle active panel focus or cross-fade timeline layers sequentially across viewports.
- **HTML Presentation Slide Mode (📺)**: Export offline HTML dashboards featuring full-screen slide presentation mode with arrow key navigation.
- **Two-Row Spacious Toolbar Layout**: Restructured setup and navigation toolbars into 2 clean rows ensuring 100% visibility for all action buttons.
- **Upgraded GitHub Pages Site**: Complete redesign of documentation landing site and user manual.
- **Official v1.0.0 Icon**: Modern 3D glass icon with neon map panels and central laser crosshairs.

## [0.6.0] - 2026-08-13

### Added
- **Visual Spatial Difference Engine (⚡)**: Compute geometric differences between Panel 1 & 2 layers inside visible extent.
- **Timeline Transition Player (▶)**: Auto-cycle panel focus and timeline layers on timer ticks.
- **HTML Presentation Mode (📺)**: Slide presentation view in exported HTML dashboards.

## [0.5.0] - 2026-08-13


### Added
- **Multi-Panel Coordinated Feature Inspector (🎯)**: Click any coordinate point on a panel viewport to query vector attributes and raster cell values across all active viewports simultaneously in a summary popover.
- **Live Viewport Spatial KPIs (📊)**: Real-time calculation of visible bounding box polygon areas (ha/km²) and feature counts displayed as chip bars under each panel.
- **Workspace Comparison Presets & Scenario Templates (💾)**: Save/load workspace configurations to QSettings or use built-in scenario templates (*Before & After*, *4-Scenario Matrix*, *Temporal Timeline*).
- **Unified Stacked Legend Drawer (📋)**: Collapsible right-side sidebar panel stacking micro-legends for all active viewports into a single organized column.
- **Canvas HUD Overlay (🧭)**: On-screen North arrow graphic indicator (`▲ N`) and scale readout rendered on viewport canvas corners.

## [0.4.0] - 2026-08-13


### Added
- **Synchronized Measurement Tool**: Interactive distance and area measurement tool integrated into workspace viewports and exported HTML dashboards.
- **100% Complete Multi-Layer & Tile Rendering Engine**: Switched HTML export renderer to dual-engine sequential and parallel map settings, ensuring all raster tiles, background layers, and vector features are captured without omissions.
- **Rich Panel Metadata & Scale Chips**: HTML export cards now include active scale readouts (e.g. `1:25,000`) and detailed layer feature count tooltips.

## [0.3.1] - 2026-08-13


### Added
- **Micro-Legend Overlay (🎨)**: Toggle floating legend cards directly on individual map viewports showing vector categories and single-symbol colors.
- **Time-Series Auto-Fill**: Automatically parse years/dates from layer names and populate comparison viewports in chronological order.
- **HTML Dashboard Embedded Legends**: Exported interactive HTML dashboards now include micro-legend cards matching QGIS vector layer symbology.

## [0.3.0] - 2026-08-13


### Added
- **Dynamic Extent Bounding Box Overlay**: Overlay active viewport rectangle on all other viewports in real time to provide immediate macro/micro spatial context.
- **Quick Panel Header Controls**: Added *Zoom to Layer Extent* (🔍), *Layer Opacity slider popup* (🌓), *PNG snapshot button* (📷), and layer geometry type badges (`Vector`, `Raster`, `Theme`) on each panel header card.
- **Cartographic Scale Presets**: Dropdown to jump all viewports simultaneously to standard scales (1:1,000, 1:2,500, 1:5,000, 1:10,000, 1:25,000, 1:50,000, 1:100,000, 1:250,000).
- **Multi-Theme HTML Dashboards**: Export interactive HTML dashboards in *Slate Light*, *Dark Midnight*, or *Emerald Clean* themes, complete with client-side theme switcher and layer opacity sliders.
- **Active Scale Readout**: Real-time cartographic scale readout in status bar for active panel viewports.

## [0.2.3] - 2026-08-07


- Added online user manual link (https://yusufeminoglu.github.io/zero2multimap/) and GitHub repository star call-to-action.

## [0.2.2] - 2026-08-07

- Add floating Save as PDF button to reference manual

## [0.2.1] - 2026-08-07

- Add comprehensive academic reference manual

All notable changes to **02Multimap** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · versioning: [SemVer](https://semver.org/).

## [0.2.0] - 2026-07-17

### Added
- Added **Choose for Me · Current Extent**, which assigns a different spatial layer to every active panel. Layers intersecting the current QGIS canvas extent are prioritized; remaining project layers are used only when needed to fill the selected grid.
- Added a self-contained HTML dashboard renderer with synchronized drag, wheel zoom, reset controls, coordinates, and laser tracking.
- Added pure-Python HTML builder tests and expanded the real-QGIS E2E harness to cover automatic layer assignment and four-panel HTML export.

### Changed
- Renamed the ambiguous **Focus Layer** mode to **Compare One Layer** and added persistent workflow guidance explaining that each panel can display a different isolated layer.
- Reorganized the crowded workspace toolbar into setup and navigation rows, and renamed **Print Layout…** to **Export / Print…**.
- HTML export now embeds exact QGIS panel renders, preserving vector symbology, labels, raster layers, shared backgrounds, and map themes without a network connection.
- Layer selectors now store stable QGIS layer IDs, so projects containing duplicate layer names are handled correctly.

### Fixed
- Fixed synchronized-main-map print items not receiving their panel layers because the exporter checked the obsolete `canvas` mode instead of `sync`.
- Made export format selection authoritative, validated output folders, and used atomic writes so incomplete HTML files are never reported as successful.

## [0.1.5] - 2026-07-10

### Changed
- Remove the top-level "&02Multimap" QGIS menu registration; the plugin now only adds its launch icon to the standard toolbar, so it no longer shows up as its own menu tab next to Help.

### Added
- Remember the last export folder used by the print/HTML layout exporter between QGIS sessions.

## [0.1.4] - 2026-07-10

### Added
- Added a **Fit All Panels** action that zooms every comparison canvas to the full extent of its visible layers, with optional main-canvas alignment.
- Replaced opaque white icon padding with a transparent background for clean display on QGIS themes.

## [0.1.3] - 2026-06-29

### Changed
- **Elite Icon Asset**: Replaced the toolbar and plugin icon with a clean, high-contrast, minimalist vector layout, ensuring high visibility and elite look on both light and dark QGIS backgrounds.

## [0.1.2] - 2026-06-29

### Fixed
- **QGIS Hub Security Scans**: Fully stripped base64 Subresource Integrity (SRI) hashes from CDN JavaScript and CSS Leaflet link elements inside the HTML export template.
- **Code Quality**: Fixed double empty blank lines in `dialog.py` (`E303`).

## [0.1.1] - 2026-06-29

### Fixed
- **QGIS Hub Security Scans**: Removed base64 Subresource Integrity (SRI) hashes from CDN JavaScript and CSS Leaflet link elements in print layouts exporter to resolve secrets scanner triggers.
- **Repository Cleanups**: Deleted empty hidden placeholder files (`icons/.gitkeep`) flagged by the Hub's hidden-file scanner.
- **Code Quality Formatting**: Removed unused PyQt/QGIS imports and formatted Python files to meet Flake8 standard checks.

## [0.1.0] - 2026-06-29

### Added
- **Initial Release** of **02Multimap: Sync-up Map Layers** workspace supporting QGIS 3.40+ LTR and QGIS 4.x.
- **Coordinated Grid Viewports**: Snap and sync maps side-by-side (2, 3, 4, 6, or 8 panels).
- **Adjustable Panel Resizing**: Integrates horizontal and vertical split dividers (`QSplitter`) with styled handles for smooth viewport resizing.
- **Bi-directional Navigation Syncing**: Zoom and pan maps simultaneously across all viewports (including synchronization with QGIS main canvas).
- **Manual Alignment Controls**: Dedicated *Match Scale* (aligns zoom levels while keeping neighborhood centers) and *Match Extent* buttons.
- **Coordinated Laser Pointer**: Real-time cursor coordinates and custom crosshair trackers visible across all active panels.
- **Multi-Render Display Modes**: Canvases can sync the main map, follow designated project Map Themes, or lock unique focus layers on top of a global base layer.
- **Premium Light Design System**: Fresh `02viz`-inspired light UI system with slate backgrounds, teal accents, and card container borders.
- **Print Layout Exporter**: Customizable print sheet outputs with 5 North Arrow styles, 5 Scalebar styles, and vector layouts.
- **Multi-Format Print Exports**: Export maps to high-resolution PNG, JPEG, SVG, or PDF files.
- **Interactive HTML Dashboard Exporter**: Exports the entire comparative workspace as a fully functional, synchronized Leaflet.js HTML page with embedded GeoJSON vector layers and laser cursors.
