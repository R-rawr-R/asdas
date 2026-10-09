"""Widget that paints the latest frame with an optional status overlay."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


def bgr_to_qimage(frame: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    # .copy() detaches the QImage from the numpy buffer so the array can be released.
    return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()


class VideoView(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAutoFillBackground(False)
        self._image: QImage | None = None
        self._overlay: str | None = None
        self._overlay_color = QColor(255, 200, 0)
        self._placeholder = "Not connected"
        self._badge: str | None = None

    @property
    def has_image(self) -> bool:
        return self._image is not None

    @property
    def overlay_text(self) -> str | None:
        return self._overlay

    def set_image(self, image: QImage | None) -> None:
        self._image = image
        self.update()

    def clear(self, placeholder: str = "Not connected") -> None:
        self._image = None
        self._overlay = None
        self._badge = None
        self._placeholder = placeholder
        self.update()

    def set_overlay(self, text: str | None, color: QColor | None = None) -> None:
        if text == self._overlay and (color is None or color == self._overlay_color):
            return
        self._overlay = text
        if color is not None:
            self._overlay_color = color
        self.update()

    def set_badge(self, text: str | None) -> None:
        if text != self._badge:
            self._badge = text
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(16, 16, 16))
        if self._image is None:
            painter.setPen(QColor(170, 170, 170))
            painter.setFont(QFont(self.font().family(), 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._placeholder)
            painter.end()
            return

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scaled = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRect(0, 0, scaled.width(), scaled.height())
        target.moveCenter(self.rect().center())
        painter.drawImage(target, self._image)

        if self._badge:
            painter.setFont(QFont(self.font().family(), 10, QFont.Weight.Bold))
            badge_rect = QRect(target.left() + 8, target.top() + 8, 260, 24)
            painter.fillRect(badge_rect, QColor(0, 0, 0, 160))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, self._badge)

        if self._overlay:
            painter.fillRect(target, QColor(0, 0, 0, 170))
            painter.setPen(self._overlay_color)
            painter.setFont(QFont(self.font().family(), 16, QFont.Weight.Bold))
            painter.drawText(target, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._overlay)
        painter.end()
