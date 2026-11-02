"""Swipe comparison: two layer sets in one map, split by a draggable divider.

This module is the contract between the dialog and the swipe view.

The dialog uses it like this::

    panel = SwipePanel(parent)
    panel.set_layers(bottom_layers, top_layers)
    canvas = panel.canvas()          # synced like any other panel canvas
    ...
    panel.cleanup()                  # on close, grid change and unload

How it draws: the panel's ``QgsMapCanvas`` renders the bottom set as usual.
The top set is rendered off the GUI thread with a copy of that canvas's map
settings (extent, scale, rotation, CRS, size and device pixel ratio) on a
transparent background. The picture is drawn as a canvas item just above the
canvas's own map picture, clipped to the top side of the divider. Being a canvas
item anchored in map coordinates, it moves with the map while the user pans or
zooms, and a fresh render replaces it once the view has been still for 150 ms.

Rendering the swipe layers into a picture of their own and painting it as a
``QgsMapCanvasItem`` is the approach of MapSwipe Tool by Hirofumi Hayashi and
Luiz Motta (github.com/lmotta/mapswipetool_plugin, GPL-2.0-or-later). This
module adapts that idea; its code is written independently.
"""
from __future__ import annotations

import contextlib
import math
from typing import List

from qgis.core import (
    QgsMapLayer,
    QgsMapRendererParallelJob,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsRectangle,
)
from qgis.gui import QgsMapCanvas, QgsMapCanvasItem, QgsMapToolPan
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from qgis.PyQt.QtWidgets import QVBoxLayout, QWidget

# Wait this long after the view stops changing before rendering the top set.
RENDER_DELAY_MS = 150
# Share of the width (or height) one arrow key press moves the divider; Shift
# moves it further.
KEY_STEP = 0.01
KEY_STEP_LARGE = 0.1
# Width of the strip the user can grab, in logical pixels.
HANDLE_THICKNESS = 12
# Same ground colour as the grid panels in dialog.py.
CANVAS_COLOR = QColor(240, 242, 245)
ACCENT_COLOR = QColor(42, 143, 133)


def _render_key(settings: QgsMapSettings) -> tuple:
    """Everything about a render request that changes the top set's picture."""
    extent = settings.visibleExtent()
    size = settings.outputSize()
    return (
        extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum(),
        size.width(), size.height(),
        settings.devicePixelRatio(), settings.outputDpi(), settings.magnificationFactor(),
        settings.rotation(),
        settings.destinationCrs().toWkt(),
        tuple(layer.id() for layer in settings.layers()),
    )


def _event_point(event) -> QPointF:
    """Local position of a mouse event on Qt 5 and Qt 6."""
    if hasattr(event, "position"):
        return event.position()
    return QPointF(event.pos())


class _TopSetItem(QgsMapCanvasItem):
    """The top layer set's picture, clipped to the top side of the divider.

    It sits just above the canvas's own map picture (z -10) and below rubber
    bands, markers and the other canvas items (z 0 and up), so tools drawn on
    the map stay visible on both sides.
    """

    def __init__(self, canvas: QgsMapCanvas) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self._image = None
        self._vertical = True
        self._ratio = 0.5
        self.setZValue(-5)

    def set_divider(self, vertical: bool, ratio: float) -> None:
        self._vertical = vertical
        self._ratio = ratio
        self.update()

    def set_content(self, image, settings: QgsMapSettings) -> None:
        """Show ``image``, rendered with ``settings``, where it belongs on the map."""
        map_to_pixel = settings.mapToPixel()
        top_left = map_to_pixel.toMapCoordinates(0, 0)
        pixel_ratio = image.devicePixelRatio() or 1.0
        units_per_pixel = map_to_pixel.mapUnitsPerPixel() / pixel_ratio
        width = image.width() * units_per_pixel
        height = image.height() * units_per_pixel
        self._image = image
        # setRect() reads the rectangle as "top-left corner plus ground size".
        # The canvas places its own map picture the same way, which keeps the
        # two pictures in register on a rotated map as well.
        self.setRect(QgsRectangle(top_left.x(), top_left.y() - height,
                                  top_left.x() + width, top_left.y()))

    def clear_content(self) -> None:
        self._image = None
        self.update()

    def _top_side(self) -> QPolygonF:
        """The top side of the divider, in item coordinates."""
        viewport = self._canvas.viewport()
        width = float(viewport.width())
        height = float(viewport.height())
        if self._vertical:
            area = QRectF(0.0, 0.0, width * self._ratio, height)
        else:
            area = QRectF(0.0, 0.0, width, height * self._ratio)
        # While the user drags the map the scene scrolls under the viewport;
        # going through the view transform keeps the divider where it is.
        to_scene, _invertible = self._canvas.viewportTransform().inverted()
        return self.mapFromScene(to_scene.map(QPolygonF(area)))

    def paint(self, painter, *args) -> None:  # also called as paint(painter, option, widget)
        image = self._image
        if image is None or self._ratio <= 0.0:
            return
        bounds = self.boundingRect()  # setRect() pads the item by one pixel on each side
        target = QRectF(0.0, 0.0, bounds.width() - 2.0, bounds.height() - 2.0)
        if target.width() <= 0.0 or target.height() <= 0.0:
            return
        clip = QPainterPath()
        clip.addPolygon(self._top_side())
        clip.closeSubpath()
        painter.save()
        painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
        pixel_ratio = image.devicePixelRatio() or 1.0
        if (abs(image.width() / pixel_ratio - target.width()) < 0.01
                and abs(image.height() / pixel_ratio - target.height()) < 0.01):
            # Drawn at its own size: one image pixel per device pixel, so the
            # picture stays sharp on high-DPI screens.
            painter.drawImage(QPointF(0.0, 0.0), image)
        else:
            # The view has changed and the new render has not arrived yet:
            # stretch the previous picture, as the canvas does with its own.
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(target, image)
        painter.restore()


class _DividerHandle(QWidget):
    """The thin strip the user drags. It draws the divider line and a grip."""

    def __init__(self, panel: QWidget) -> None:
        super().__init__(panel)
        self._vertical = True
        self._grab_offset = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Swipe divider")
        self.setToolTip("Drag to move the divider. When it is selected, the arrow keys move it.")
        self.set_vertical(True)

    def set_vertical(self, vertical: bool) -> None:
        self._vertical = vertical
        self.setCursor(Qt.CursorShape.SplitHCursor if vertical else Qt.CursorShape.SplitVCursor)
        self.update()

    def _along(self, point: QPointF) -> float:
        return point.x() if self._vertical else point.y()

    def _centre(self) -> float:
        return (self.width() if self._vertical else self.height()) / 2.0

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._grab_offset = self._along(_event_point(event)) - self._centre()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._grab_offset is None:
            super().mouseMoveEvent(event)
            return
        self._follow(event)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._grab_offset is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self._follow(event)
        self._grab_offset = None
        event.accept()

    def _follow(self, event) -> None:
        point = _event_point(event)
        if self._vertical:
            point = QPointF(point.x() - self._grab_offset, point.y())
        else:
            point = QPointF(point.x(), point.y() - self._grab_offset)
        self.parentWidget()._move_divider_to(self.mapToParent(point.toPoint()))

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            direction = -1.0
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            direction = 1.0
        else:
            super().keyPressEvent(event)
            return
        shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        step = KEY_STEP_LARGE if shifted else KEY_STEP
        panel = self.parentWidget()
        panel.set_divider_ratio(panel.divider_ratio() + direction * step)
        event.accept()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = float(self.width())
        height = float(self.height())
        if self._vertical:
            start, end = QPointF(width / 2.0, 0.0), QPointF(width / 2.0, height)
            grip = QRectF(0.0, 0.0, HANDLE_THICKNESS - 2.0, 36.0)
        else:
            start, end = QPointF(0.0, height / 2.0), QPointF(width, height / 2.0)
            grip = QRectF(0.0, 0.0, 36.0, HANDLE_THICKNESS - 2.0)
        # A light line on a dark edge reads on light and dark maps alike.
        painter.setPen(QPen(QColor(20, 24, 28, 150), 4.0))
        painter.drawLine(start, end)
        painter.setPen(QPen(QColor(255, 255, 255), 2.0))
        painter.drawLine(start, end)
        grip.moveCenter(QPointF(width / 2.0, height / 2.0))
        painter.setPen(QPen(QColor(20, 24, 28, 170), 1.0))
        painter.setBrush(ACCENT_COLOR if self.hasFocus() else QColor(255, 255, 255))
        painter.drawRoundedRect(grip, 4.0, 4.0)
        painter.end()


class SwipePanel(QWidget):
    """One map: the bottom layer set everywhere, the top set on one side.

    Public contract (used by ``dialog.py``):

    - ``canvas()`` returns the ``QgsMapCanvas`` the dialog syncs exactly like
      a panel canvas (centre, scale, rotation, CRS). The top layer set always
      follows that canvas's map settings.
    - ``set_layers(bottom, top)`` takes lists of ``QgsMapLayer`` and redraws.
    - ``set_orientation(vertical)``: True puts the divider upright (top set on
      the left), False lays it flat (top set above).
    - ``divider_ratio()`` / ``set_divider_ratio(ratio)``: the divider's
      position as a share of the width (or height), from 0.0 to 1.0.
    - ``layers_changed`` is emitted after ``set_layers``.
    - ``cleanup()`` stops any render in progress, disconnects every signal and
      releases the canvas; the dialog calls it before discarding the panel.
    """

    layers_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._vertical = True
        self._ratio = 0.5
        self._top_layers: List[QgsMapLayer] = []
        self._job = None
        self._job_key = None
        self._picture_key = None
        self._retired_jobs = []
        self._cleaned = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = QgsMapCanvas(self)
        self._canvas.setCanvasColor(CANVAS_COLOR)
        layout.addWidget(self._canvas, 1)
        self._pan_tool = QgsMapToolPan(self._canvas)
        self._canvas.setMapTool(self._pan_tool)
        self._viewport = self._canvas.viewport()

        self._item = _TopSetItem(self._canvas)
        self._item.set_divider(self._vertical, self._ratio)
        self._handle = _DividerHandle(self)
        self._handle.hide()

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(RENDER_DELAY_MS)
        self._render_timer.timeout.connect(self._start_render)

        # A finished canvas refresh also catches what the other signals do not
        # announce (a new device pixel ratio, a magnifier change); the render
        # key turns it into a no-op when nothing that matters has changed.
        self._canvas_connections = (
            (self._canvas.extentsChanged, self._schedule_render),
            (self._canvas.scaleChanged, self._schedule_render),
            (self._canvas.rotationChanged, self._schedule_render),
            (self._canvas.destinationCrsChanged, self._schedule_render),
            (self._canvas.mapCanvasRefreshed, self._on_canvas_refreshed),
        )
        for signal, slot in self._canvas_connections:
            signal.connect(slot)
        self._viewport.installEventFilter(self)

    # ----------------------------------------------------------- contract
    def canvas(self) -> QgsMapCanvas:
        return self._canvas

    def set_layers(self, bottom: List, top: List) -> None:
        """Show ``bottom`` everywhere and ``top`` on the top side of the divider.

        Both lists are in canvas order (the first layer is drawn on top). With
        an empty ``top`` nothing is painted over the map and the divider is
        hidden.
        """
        if self._cleaned:
            return
        self._watch_top_layers(False)
        self._top_layers = [layer for layer in (top or []) if layer is not None]
        self._watch_top_layers(True)
        self._canvas.setLayers([layer for layer in (bottom or []) if layer is not None])
        self._handle.setVisible(bool(self._top_layers))
        self._place_handle()
        if self._top_layers:
            self._schedule_render()
        else:
            self._clear_picture()
        self.layers_changed.emit()

    def set_orientation(self, vertical: bool) -> None:
        self._vertical = bool(vertical)
        self._item.set_divider(self._vertical, self._ratio)
        self._handle.set_vertical(self._vertical)
        self._place_handle()

    def divider_ratio(self) -> float:
        return self._ratio

    def set_divider_ratio(self, ratio: float) -> None:
        """Move the divider; values outside 0.0-1.0 are clamped."""
        value = float(ratio)
        if math.isnan(value):
            return
        value = min(1.0, max(0.0, value))
        if value == self._ratio:
            return
        self._ratio = value
        self._item.set_divider(self._vertical, value)
        self._place_handle()

    def cleanup(self) -> None:
        """Stop rendering, disconnect every signal and release the canvas.

        Safe to call more than once. The panel shows nothing afterwards; build a
        new one to show a swipe again.
        """
        if self._cleaned:
            return
        self._cleaned = True
        self._render_timer.stop()
        with contextlib.suppress(TypeError, RuntimeError):
            self._render_timer.timeout.disconnect(self._start_render)
        for signal, slot in self._canvas_connections:
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)
        self._watch_top_layers(False)
        self._top_layers = []
        self._cancel_job(blocking=True)
        for job in self._retired_jobs:
            if not sip.isdeleted(job) and job.isActive():
                job.cancel()
        self._retired_jobs = []
        with contextlib.suppress(RuntimeError):
            self._viewport.removeEventFilter(self)
        self._item.clear_content()
        scene = self._item.scene()
        if scene is not None:
            scene.removeItem(self._item)
        self._handle.hide()
        self._canvas.stopRendering()
        self._canvas.unsetMapTool(self._pan_tool)
        self._canvas.freeze(True)
        self._canvas.setLayers([])
        with contextlib.suppress(TypeError, RuntimeError):
            self.layers_changed.disconnect()

    # ----------------------------------------------------------- divider
    def _move_divider_to(self, point: QPoint) -> None:
        """Put the divider under ``point`` (in this widget's coordinates)."""
        local = self._viewport.mapFrom(self, point)
        if self._vertical:
            self.set_divider_ratio(local.x() / max(1, self._viewport.width()))
        else:
            self.set_divider_ratio(local.y() / max(1, self._viewport.height()))

    def _place_handle(self) -> None:
        if self._cleaned:
            return
        origin = self._viewport.mapTo(self, QPoint(0, 0))
        width = self._viewport.width()
        height = self._viewport.height()
        half = HANDLE_THICKNESS // 2
        if self._vertical:
            centre = origin.x() + round(width * self._ratio)
            self._handle.setGeometry(centre - half, origin.y(), HANDLE_THICKNESS, height)
        else:
            centre = origin.y() + round(height * self._ratio)
            self._handle.setGeometry(origin.x(), centre - half, width, HANDLE_THICKNESS)
        self._handle.raise_()

    # ----------------------------------------------------------- Qt events
    def eventFilter(self, watched, event) -> bool:
        if watched is self._viewport and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._place_handle()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_handle()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_handle()
        self._schedule_render()

    def hideEvent(self, event) -> None:
        # A hidden panel does no work; showing it again renders what changed.
        super().hideEvent(event)
        self._render_timer.stop()
        self._cancel_job()

    # ----------------------------------------------------------- top set
    def _live_top_layers(self) -> List[QgsMapLayer]:
        return [layer for layer in self._top_layers if not sip.isdeleted(layer) and layer.isValid()]

    def _watch_top_layers(self, connect: bool) -> None:
        for layer in self._top_layers:
            if sip.isdeleted(layer):
                continue
            if connect:
                layer.repaintRequested.connect(self._on_top_layer_repaint)
                layer.willBeDeleted.connect(self._on_top_layer_deleted)
                continue
            with contextlib.suppress(TypeError, RuntimeError):
                layer.repaintRequested.disconnect(self._on_top_layer_repaint)
            with contextlib.suppress(TypeError, RuntimeError):
                layer.willBeDeleted.disconnect(self._on_top_layer_deleted)

    def _on_top_layer_repaint(self, *_args) -> None:
        # Same view, new content: neither the running render nor the picture
        # on screen can be reused.
        self._job_key = None
        self._picture_key = None
        self._schedule_render()

    def _on_top_layer_deleted(self) -> None:
        # Qt drops the dying layer's connections itself; only forget it here,
        # before anything can touch the deleted object. For a layer created in
        # Python, sip has already detached our wrapper by now and sender() is a
        # new one, so match on the layer id as well as on identity.
        gone = self.sender()
        gone_id = None
        if gone is not None:
            with contextlib.suppress(RuntimeError, AttributeError):
                gone_id = gone.id()
        self._top_layers = [
            layer for layer in self._top_layers
            if layer is not gone and not sip.isdeleted(layer) and layer.id() != gone_id
        ]
        if self._top_layers:
            self._schedule_render()
            return
        self._clear_picture()

    def _clear_picture(self) -> None:
        """Paint nothing over the map and hide the divider."""
        self._render_timer.stop()
        self._cancel_job()
        self._item.clear_content()
        self._picture_key = None
        self._handle.hide()

    # ----------------------------------------------------------- rendering
    def _schedule_render(self, *_args) -> None:
        """(Re)start the wait: a render starts once the view has been still."""
        if not self._cleaned and self.isVisible():
            self._render_timer.start()

    def _on_canvas_refreshed(self) -> None:
        # Check after every canvas refresh, without postponing a pending render.
        if not self._render_timer.isActive():
            self._schedule_render()

    def _start_render(self) -> None:
        if self._cleaned:
            return
        layers = self._live_top_layers()
        if not layers:
            self._clear_picture()
            return
        if not self.isVisible() or self._canvas.isFrozen():
            return
        if self._handle.isHidden():
            self._handle.show()
            self._place_handle()
        settings = QgsMapSettings(self._canvas.mapSettings())
        if not settings.hasValidSettings():
            return
        settings.setLayers(layers)
        settings.setBackgroundColor(QColor(0, 0, 0, 0))
        # Style overrides belong to the bottom set's map theme.
        settings.setLayerStyleOverrides({})
        key = _render_key(settings)
        if self._job is not None and key == self._job_key:
            return
        if self._job is None and key == self._picture_key:
            return
        self._cancel_job()
        if self._canvas.isParallelRenderingEnabled():
            job = QgsMapRendererParallelJob(settings)
        else:
            job = QgsMapRendererSequentialJob(settings)
        job.setParent(self)
        job.finished.connect(self._on_render_finished)
        self._job = job
        self._job_key = key
        job.start()

    def _on_render_finished(self) -> None:
        job = self.sender()
        if job is None or job is not self._job:
            return
        key = self._job_key
        self._job = None
        self._job_key = None
        if not self._cleaned:
            self._item.set_content(job.renderedImage(), job.mapSettings())
            self._picture_key = key
        job.deleteLater()

    def _cancel_job(self, blocking: bool = False) -> None:
        """Stop the running render; it deletes itself once it has stopped."""
        self._retired_jobs = [
            old for old in self._retired_jobs if not sip.isdeleted(old) and old.isActive()
        ]
        job = self._job
        self._job = None
        self._job_key = None
        if job is None:
            return
        with contextlib.suppress(TypeError, RuntimeError):
            job.finished.disconnect(self._on_render_finished)
        job.finished.connect(job.deleteLater)
        if blocking:
            job.cancel()
        else:
            job.cancelWithoutBlocking()
            self._retired_jobs.append(job)
