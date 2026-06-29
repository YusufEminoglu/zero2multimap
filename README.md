# PlanX MultiMap

Multi-panel synchronized map visualization workspace for QGIS 4.

**PlanX MultiMap** enables side-by-side comparative spatial analysis. Users can configure a grid layout containing 2, 3, 4, 6, or 8 independent map views. Panning and zooming in one view synchronizes all others, and a coordinated laser crosshair tracks cursor position across all canvases in real time.

## Features

- **Dynamic Layout Grids**: Supports grids of 2, 3, 4, 6, or 8 map views (layouts: 1x2, 2x1, 1x3, 2x2, 2x3, 2x4).
- **Extent Synchronization**: Real-time panning and zooming synchronization between all panels.
- **Bidirectional QGIS Sync**: Optional synchronization between the multi-map workspace and the main QGIS map canvas.
- **Laser Crosshair Tracking**: High-precision cursor coordinates shown in a status bar and crosshairs drawn on all other canvases.
- **Independent Layer Management**: Each panel can run in one of three source modes:
  - *Sync Main Map*: Follows QGIS layer tree active visibility.
  - *Focus Layer*: Renders a selected focus layer on top of a global base layer.
  - *Map Theme*: Follows a custom QGIS Map Theme (visibility presets).
- **Sleek Dark Theme UI**: Custom QSS stylesheet styling modern controls, hover states, active frame highlights, and coordinate readouts.

## Architecture

- [__init__.py](file:///C:/Users/YE/PyCharmMiscProject/qgis_plugins/planx_multimap/__init__.py) — Standard QGIS plugin entry hook.
- [main_plugin.py](file:///C:/Users/YE/PyCharmMiscProject/qgis_plugins/planx_multimap/main_plugin.py) — Plugin actions and menu hook registrations.
- [dialog.py](file:///C:/Users/YE/PyCharmMiscProject/qgis_plugins/planx_multimap/dialog.py) — Layout grid container, map synchronization logic, and coordinate event filtering.

## License

GPL-3.0-or-later. Part of the [PlanX Ecosystem](https://github.com/YusufEminoglu).
