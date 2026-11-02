# 02Multimap — Development Set for v1.3.0

**Goal: panels that stay where the user put them, numbers that are right, a
window that fits a laptop, and the swipe comparison QGIS 4 users cannot get
elsewhere.**
Written 2026-11-02 from two audits run in real QGIS 3.44 and 4.2 (code and
behaviour; first-minute UX, truth and demand).

## 0. Baseline (2026-11-02)
- Hub downloads 10,555, steady at about 85–100 a day; `pf verify` green
  (15 pure tests, 1 QGIS test).
- `tracker=` is a 404 (GitLab); GitHub issues is public.
- Measured correct and left alone: true-area values (within 5e-5 of the
  ellipsoid; US-feet and 70°N cases), Diff values, raster sampling, HTML
  escaping, the fully offline HTML file, PNG/PDF extents, grid switch frees
  old canvases.

## 1. What the audits found
1. **Sync drifts.** Extent rectangles are copied raw: each pan between a panel
   and the main map zooms everything out ×2.15 (1:75,043 → 1:3.4M in five
   rounds); a grid change, a splitter drag or a resize moves the user's main
   map; a CRS change sends degrees into EPSG:3857 panels; rotation is ignored;
   the main canvas's signals are blocked, so QGIS's scale box goes stale.
2. **The dialog is never torn down.** Closed and unloaded dialogs keep
   rendering hidden panels on every main-map pan (tile requests with a
   basemap); signal receivers and actions pile up on reload.
3. **Project loading stalls** once the dialog has been opened: every added
   layer rebuilds every combo (150 layers +3 s; 300 never finished).
4. **Multi-Inspect reports the wrong feature** (bounding-box test, first hit)
   and puts layer names and values into rich text unescaped.
5. **Readouts mislead.** Area chips and Diff go stale after a panel's layer
   changes; the shared background is added to every panel's area; Show Diff
   with a raster silently compares the background; points read "0 ha";
   live overlays run synchronously on every extent change (1.3–1.9 s per pan
   at 8 panels × 10k polygons); Diff rubber bands leak.
6. **The window does not fit a laptop**: at least 1,388–1,437 px wide at four
   panels (2,000 px at eight) and 790 px tall.
7. **Templates and presets do not do what they say**: "Before & After" shows
   one layer twice, "4-Scenario Matrix" only sets the grid, time-series
   auto-fill misses `landuse_2015`, presets drop map-theme panels and the
   scale, nothing is saved with the project.
8. **Export defects**: the print legend style is edited on a copy (titles
   16 pt, entries clipped); the summary needs 347 mm in an 87 mm box; the
   layout never reaches the Layout Manager; the HTML measures distances
   against the letterboxed view box (≈63% of the truth); in QGIS 4 dark mode
   the Export dialog's text is dark on dark (≈1.5:1); "&" is swallowed in
   labels.
9. **Smaller**: Laser Crosshair untick leaves markers; scale readout stale;
   low contrast spots; toolbar icon only, no menu entry.
10. **Copy is not true**: "synchronized measuring" (no such tool), the Quick
    Guide (v0.6.0, a measurement tool and an extent box that do not exist,
    adjustable speeds), README's hero image is a mock-up, "100% E2E
    verified", 16 ecosystem links that all point to this repo, a stale
    ROADMAP, a 404 tracker.
11. **Demand**: swipe/overlay comparison is the largest gap. MapSwipe Tool has
    240,286 downloads but no QGIS 4 support (issue open since 2026-03-12);
    docked compare plugins are growing. Hub search misses "swipe", "mirror",
    "linked views", "split screen", "time series".

## 2. The set (acceptance = what a test asserts)
- **A. Sync that holds.** Centre + scale + rotation are synced, the centre
  transformed between CRSs; panels follow `destinationCrsChanged`;
  resize-only and layout events never move another map; a grid change keeps
  the scale. Tests: five ping-pong rounds and four resizes keep the scale
  within 1%; panel centre = main centre after a CRS change; rotation copied;
  exactly one `scaleChanged` on the main canvas after a panel zoom.
- **B. Lifecycle.** One `cleanup()` disconnects every signal; hidden dialogs
  do no work and resync on show; unload removes dialog, actions and menu
  entries (`setParent(None)` + `deleteLater`). Layer-set changes are
  debounced and rebuilt only when visible. Tests: receivers back to baseline
  after three load/unload cycles; zero renders while hidden; adding 300
  layers costs under 0.1 s; a removed layer leaves its panel consistent.
- **C. Right answers.** Multi-Inspect uses an exact geometry test and escapes
  everything; area chips exclude the shared background, count points instead
  of "0 ha", say why when nothing is measured, refresh when a panel's layer
  changes; Show Diff refuses a raster or background comparison with a plain
  message; overlays are debounced (250 ms) and bands reused. Tests: the ring
  hole, the L corner and the small square clicks; 13.57 km² stays 13.57 with
  a background; stale-after-change cases.
- **D. Fits a laptop.** At four panels the dialog fits 1,280 × 680 px of
  usable screen (default height ≤ 680); eight panels stay within it by
  scrolling or tighter headers. Test: minimum size at 2/4/8 panels.
- **E. Templates, presets, project.** Before & After assigns two different
  layers (the two most recent of the same geometry type, or tells the user
  what to pick); 4-Scenario assigns four; time-series auto-fill finds years
  in names such as `landuse_2015` and reports what it assigned; presets keep
  map themes and scale; the panel setup is saved in and restored from the
  project. Tests for each.
- **F. Exports.** Legend style edited in place (`rstyle`); every footer label
  fits its box (`sizeForText`); "Open in Layout Designer" adds the layout to
  the project's Layout Manager; HTML coordinates, measuring and scale bar use
  the image's content rectangle; the Export dialog is readable in QGIS 4
  dark mode and under Night Mapping; "&" shows as "&". Tests assert sizes,
  styles and a measured distance against the true value.
- **G. Small.** Laser Crosshair untick removes markers; scale readout stays
  current; text contrast ≥ 4.5:1 under the built-in themes; a Plugins-menu
  entry next to the toolbar icon.
- **H. Swipe (new).** A "Swipe" layout: one map showing a bottom layer set
  everywhere and a top layer set on one side of a draggable divider (vertical
  or horizontal), synced like any panel. Contract in `swipe_view.py`. Tests:
  pixels left and right of the divider come from the right layer set;
  dragging moves the boundary; the panel follows extent changes; cleanup
  releases it.
- **T. Truthful copy.** Remove or implement every false claim (§1.10);
  README without the mock-up hero or with a real screenshot; tracker →
  GitHub issues; Hub description and tags with swipe, mirror, linked views,
  split screen, time series; ROADMAP brought to 1.3; CHANGELOG 1.3.0;
  version 1.3.0.

## 3. Owner decisions
- `email=` in metadata is still a university address although the 1.2.2
  changelog says the affiliation was removed: the owner decides.
- Pushing, tagging and the Hub upload.

## 4. Left out
- 3D view sync, raster band/stretch sync, web hosting deployment (ROADMAP
  ideas without measured demand; 3D sync is large).
- Temporal-controller sync: worth doing after swipe; not in this round.
