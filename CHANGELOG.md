# Changelog

All notable changes to **02-Multimap** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · versioning: [SemVer](https://semver.org/).

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
- **Initial Release** of **02-Multimap: Sync-up Multimaps** workspace supporting QGIS 3.40+ LTR and QGIS 4.x.
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
