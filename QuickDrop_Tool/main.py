import os
import sys

from PIL import Image
import pillow_heif

pillow_heif.register_heif_opener()
import pillow_avif
from rembg import remove

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView


class ConversionWorker(QThread):
    """Background worker thread for asynchronous image conversions."""

    started_processing = pyqtSignal()
    file_converted = pyqtSignal(str, str)
    conversion_failed = pyqtSignal(str, str)
    finished_all = pyqtSignal(int, int)

    FORMAT_MAP = {
        "PDF": {"format": "PDF", "ext": "pdf"},
        "JPG": {"format": "JPEG", "ext": "jpg"},
        "JPEG": {"format": "JPEG", "ext": "jpg"},
        "PNG": {"format": "PNG", "ext": "png"},
        "WEBP": {"format": "WEBP", "ext": "webp"},
        "HEIC": {"format": "HEIF", "ext": "heic"},
        "TIFF": {"format": "TIFF", "ext": "tiff"},
        "AVIF": {"format": "AVIF", "ext": "avif"},
        "BMP": {"format": "BMP", "ext": "bmp"},
    }

    def __init__(
        self,
        file_paths: list[str] | str,
        target_format: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if isinstance(file_paths, str):
            self.file_paths = [file_paths]
        else:
            self.file_paths = list(file_paths)
        self.target_format = target_format.strip().upper()

    def run(self) -> None:
        self.started_processing.emit()
        if self.target_format == "ADD BG":
            success_count = 0
            for file_path in self.file_paths:
                abs_path = os.path.abspath(file_path)
                if not os.path.isfile(abs_path):
                    self.conversion_failed.emit(file_path, f"File not found: {file_path}")
                    continue

                try:
                    # 1. Open the source image
                    img = Image.open(abs_path)

                    # 2. Run real rembg background removal
                    output_img = remove(img)

                    # 3. Determine output path in the same directory with _nobg.png
                    dir_name = os.path.dirname(abs_path)
                    base_name = os.path.splitext(os.path.basename(abs_path))[0]
                    output_path = os.path.join(dir_name, f"{base_name}_nobg.png")

                    # 4. Save output image to disk as PNG
                    if isinstance(output_img, bytes):
                        with open(output_path, "wb") as f:
                            f.write(output_img)
                    else:
                        output_img.save(output_path, "PNG")

                    # Verify the file exists on disk
                    if os.path.isfile(output_path):
                        success_count += 1
                        self.file_converted.emit(file_path, output_path)
                    else:
                        self.conversion_failed.emit(file_path, f"Failed to save file: {output_path}")
                except Exception as e:
                    self.conversion_failed.emit(file_path, str(e))

            self.finished_all.emit(success_count, len(self.file_paths))
            return

        fmt_info = self.FORMAT_MAP.get(self.target_format)
        if not fmt_info:
            err = f"Unsupported conversion format: {self.target_format}"
            for path in self.file_paths:
                self.conversion_failed.emit(path, err)
            self.finished_all.emit(0, len(self.file_paths))
            return

        pil_format = fmt_info["format"]
        extension = fmt_info["ext"]
        success_count = 0

        for file_path in self.file_paths:
            if not os.path.isfile(file_path):
                self.conversion_failed.emit(file_path, "File does not exist")
                continue

            try:
                # Open the source image
                with Image.open(file_path) as img:
                    # If target format is JPG, BMP, or PDF, convert to RGB to avoid alpha-channel crash errors
                    if self.target_format in ("JPG", "JPEG", "BMP", "PDF"):
                        img = img.convert("RGB")

                    # Determine new output path (same directory, new extension)
                    dir_name = os.path.dirname(file_path)
                    base_name = os.path.splitext(os.path.basename(file_path))[0]
                    output_path = os.path.join(dir_name, f"{base_name}.{extension}")

                    # Prevent overwriting the exact source file if same extension
                    if os.path.abspath(output_path) == os.path.abspath(file_path):
                        output_path = os.path.join(
                            dir_name, f"{base_name}_converted.{extension}"
                        )

                    # Save the image to the new path
                    img.save(output_path, format=pil_format)

                success_count += 1
                self.file_converted.emit(file_path, output_path)
            except Exception as e:
                self.conversion_failed.emit(file_path, str(e))

        self.finished_all.emit(success_count, len(self.file_paths))


class LoadingDialog(QDialog):
    """Frameless light frosted glass loading dialog with continuous indeterminate animation."""

    def __init__(
        self,
        title: str = "Processing... Please wait",
        subtitle: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._drag_start_pos = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(320, 130)

        self._title = title
        self._subtitle = subtitle
        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        # Light frosted glass container (#f7f7f7) with rounded corners and subtle border
        container = QFrame()
        container.setObjectName("loadingContainer")
        container.setStyleSheet("""
            #loadingContainer {
                background-color: #f7f7f7;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
            QProgressBar {
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 4px;
                background-color: rgba(0, 0, 0, 0.06);
                height: 8px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #ff5500;
                border-radius: 4px;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        # Label: "Processing... Please wait"
        self.title_label = QLabel(self._title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet(
            "font-size: 15px; font-weight: 700; color: #222222;"
        )
        layout.addWidget(self.title_label)

        if self._subtitle:
            self.sub_label = QLabel(self._subtitle)
            self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.sub_label.setStyleSheet(
                "font-size: 12px; color: #666666; margin-top: -4px;"
            )
            layout.addWidget(self.sub_label)

        # Continuous indeterminate progress bar (range 0 to 0)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        outer_layout.addWidget(container)

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
        ):
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)


class CompressDialog(QDialog):
    """Frameless light translucent frosted glass compression dialog."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None

        # Calculate original file size in bytes
        self._original_size = (
            os.path.getsize(self.file_path) if os.path.exists(self.file_path) else 0
        )

        # 1. UI/Design: Frameless window, rounded corners with transparent window frame
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(380, 240)

        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        # Light/translucent milky background (#f7f7f7) with rounded corners and subtle border
        container = QFrame()
        container.setObjectName("compressContainer")
        container.setStyleSheet("""
            #compressContainer {
                background-color: #f7f7f7;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: rgba(0, 0, 0, 0.12);
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #ff5500;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.22);
                width: 18px;
                height: 18px;
                margin-top: -6px;
                margin-bottom: -6px;
                border-radius: 9px;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(11)

        # 1. Title "Compress" centered boldly at the top
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)  # Symmetry for the close button

        title_label = QLabel("Compress")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #222222;")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(0, 0, 0, 0.35);
                font-size: 12px;
                font-weight: bold;
                border: none;
                border-radius: 11px;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 0.08);
                color: #222222;
            }
        """)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)
        layout.addLayout(header_layout)

        # 2. Text: "Original size: [Size] MB" clearly visible
        orig_mb = self._original_size / (1024.0 * 1024.0)
        self.orig_size_label = QLabel(f"Original size: {orig_mb:.2f} MB")
        self.orig_size_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #222222;")
        layout.addWidget(self.orig_size_label)

        # 3. Horizontal QSlider (Range 1-99) & dynamic label "Reduce by: [X]%"
        slider_row = QHBoxLayout()
        slider_row.setSpacing(12)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 99)
        self.slider.setValue(50)

        self.reduce_label = QLabel(f"Reduce by: {self.slider.value()}%")
        self.reduce_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #222222; min-width: 105px;")

        slider_row.addWidget(self.slider)
        slider_row.addWidget(self.reduce_label)
        layout.addLayout(slider_row)

        # 4. Text: "Output will be at most [Y] KB" (calculated dynamically)
        self.est_size_label = QLabel()
        self.est_size_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #222222;")
        self._on_slider_changed(50)
        self.slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self.est_size_label)

        layout.addStretch()

        # 5. Vibrant Tangerine/Red-Orange "Compress" button at the bottom right
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #444444;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #222222;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        compress_btn = QPushButton("Compress")
        compress_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5500;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #ff661a;
            }
            QPushButton:pressed {
                background-color: #e04b00;
            }
        """)
        compress_btn.clicked.connect(self._on_compress)
        btn_row.addWidget(compress_btn)

        layout.addLayout(btn_row)
        outer_layout.addWidget(container)

    def _on_slider_changed(self, value: int) -> None:
        """Update Reduce by and estimated size: original_size_kb * ((100 - slider_value) / 100)."""
        self.reduce_label.setText(f"Reduce by: {value}%")
        orig_kb = self._original_size / 1024.0
        est_kb = orig_kb * ((100 - value) / 100.0)
        self.est_size_label.setText(f"Output will be at most {est_kb:.1f} KB")

    def _center_on_screen(self) -> None:
        """Center the dialog on the primary display."""
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def _on_compress(self) -> None:
        """Compress the image preserving original format, extension, and transparency."""
        slider_val = self.slider.value()
        try:
            # 1. Detect Original Format: Extract original file's extension
            dir_name = os.path.dirname(self.file_path)
            base_name, ext = os.path.splitext(os.path.basename(self.file_path))
            ext_lower = ext.lower()

            # The output MUST keep this exact same extension
            output_path = os.path.join(dir_name, f"{base_name}_compressed{ext}")

            # Prevent overwriting if source file is already named *_compressed.*
            if os.path.abspath(output_path) == os.path.abspath(self.file_path):
                output_path = os.path.join(
                    dir_name, f"{base_name}_opt{ext}"
                )

            with Image.open(self.file_path) as img:
                # 2. If the original is PNG (or has transparency 'RGBA')
                if ext_lower == ".png" or img.mode in ("RGBA", "LA", "PA") or ("transparency" in img.info):
                    # DO NOT convert it to 'RGB'. Keep the transparency.
                    # To compress a PNG using the 1-100 slider, reduce its color palette (Quantize).
                    num_colors = max(8, int(256 * (slider_val / 100)))
                    img = img.quantize(colors=num_colors)
                    img.save(output_path, "PNG", optimize=True)
                # 3. If the original is JPEG/JPG
                elif ext_lower in (".jpg", ".jpeg"):
                    # Convert to safe RGB
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    img.save(output_path, "JPEG", quality=slider_val)
                elif ext_lower == ".webp":
                    img.save(output_path, "WEBP", quality=slider_val)
                else:
                    # Fallback for other formats, keeping same extension
                    fmt = img.format or "PNG"
                    if fmt.upper() in ("JPEG", "JPG", "BMP"):
                        if img.mode not in ("RGB", "L"):
                            img = img.convert("RGB")
                        img.save(output_path, format=fmt, quality=slider_val)
                    else:
                        img.save(output_path, format=fmt)

            print(
                f"[QuickDrop] Compressed '{self.file_path}' -> '{output_path}'"
            )
            self.accept()
        except Exception as e:
            print(f"[QuickDrop ERROR] Failed to compress '{self.file_path}': {e}")
            self.reject()

    # Window drag support for frameless window
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
        ):
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.reject()
        else:
            super().keyPressEvent(event)


class InteractiveCropLabel(QWidget):
    """Interactive preview canvas displaying the image with Photoshop-like crop and resize handles."""

    crop_changed = pyqtSignal(int, int)  # Emits (width, height)

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._pixmap = QPixmap(file_path)
        self._img_w = max(1, self._pixmap.width() if not self._pixmap.isNull() else 100)
        self._img_h = max(1, self._pixmap.height() if not self._pixmap.isNull() else 100)

        # Crop rect in image coordinates [0.._img_w, 0.._img_h]
        self._crop_x = 0.0
        self._crop_y = 0.0
        self._crop_w = float(self._img_w)
        self._crop_h = float(self._img_h)

        # Mouse interaction tracking
        self._active_handle: str | None = None
        self._mouse_press_pos: QPointF | None = None
        self._start_crop_rect: tuple[float, float, float, float] | None = None

        self.setMinimumSize(360, 200)
        self.setMouseTracking(True)

    def _get_display_rect(self) -> QRectF:
        """Calculate QRectF where the pixmap is displayed within the widget, centered with aspect preserved."""
        w_avail = float(max(10, self.width()))
        h_avail = float(max(10, self.height()))
        aspect_img = self._img_w / float(self._img_h)
        aspect_avail = w_avail / h_avail

        if aspect_avail > aspect_img:
            draw_h = h_avail
            draw_w = draw_h * aspect_img
            draw_x = (w_avail - draw_w) / 2.0
            draw_y = 0.0
        else:
            draw_w = w_avail
            draw_h = draw_w / aspect_img
            draw_x = 0.0
            draw_y = (h_avail - draw_h) / 2.0

        return QRectF(draw_x, draw_y, draw_w, draw_h)

    def _img_to_disp(self, ix: float, iy: float, iw: float, ih: float) -> QRectF:
        drect = self._get_display_rect()
        scale = drect.width() / float(self._img_w)
        dx = drect.x() + ix * scale
        dy = drect.y() + iy * scale
        dw = iw * scale
        dh = ih * scale
        return QRectF(dx, dy, dw, dh)

    def _get_handles(self, rect: QRectF) -> dict[str, QPointF]:
        """Return coordinates of the 8 handle anchor points on the crop rectangle."""
        return {
            "TL": rect.topLeft(),
            "TR": rect.topRight(),
            "BL": rect.bottomLeft(),
            "BR": rect.bottomRight(),
            "T": QPointF(rect.center().x(), rect.top()),
            "B": QPointF(rect.center().x(), rect.bottom()),
            "L": QPointF(rect.left(), rect.center().y()),
            "R": QPointF(rect.right(), rect.center().y()),
        }

    def _hit_test(self, pos: QPointF) -> tuple[str | None, Qt.CursorShape]:
        """Detect which handle or zone the cursor is hovering over and return appropriate cursor."""
        rect = self._img_to_disp(
            self._crop_x, self._crop_y, self._crop_w, self._crop_h
        )
        handles = self._get_handles(rect)

        # 1. Check 4 corners (10px tolerance radius)
        for h_name in ("TL", "TR", "BL", "BR"):
            pt = handles[h_name]
            dist = ((pos.x() - pt.x()) ** 2 + (pos.y() - pt.y()) ** 2) ** 0.5
            if dist <= 10.0:
                cursor = (
                    Qt.CursorShape.SizeFDiagCursor
                    if h_name in ("TL", "BR")
                    else Qt.CursorShape.SizeBDiagCursor
                )
                return h_name, cursor

        # 2. Check Top and Bottom edge handles / borders
        for h_name in ("T", "B"):
            pt = handles[h_name]
            dist = ((pos.x() - pt.x()) ** 2 + (pos.y() - pt.y()) ** 2) ** 0.5
            is_edge = (
                abs(pos.y() - pt.y()) <= 6.0
                and rect.left() <= pos.x() <= rect.right()
            )
            if dist <= 8.0 or is_edge:
                return h_name, Qt.CursorShape.SizeVerCursor

        # 3. Check Left and Right edge handles / borders
        for h_name in ("L", "R"):
            pt = handles[h_name]
            dist = ((pos.x() - pt.x()) ** 2 + (pos.y() - pt.y()) ** 2) ** 0.5
            is_edge = (
                abs(pos.x() - pt.x()) <= 6.0
                and rect.top() <= pos.y() <= rect.bottom()
            )
            if dist <= 8.0 or is_edge:
                return h_name, Qt.CursorShape.SizeHorCursor

        # 4. Check inside crop box (move)
        if rect.contains(pos):
            return "MOVE", Qt.CursorShape.SizeAllCursor

        # 5. Outside
        return None, Qt.CursorShape.ArrowCursor

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        drect = self._get_display_rect()

        # 1. Draw source image
        if not self._pixmap.isNull():
            painter.drawPixmap(drect.toRect(), self._pixmap)

        crop_drect = self._img_to_disp(
            self._crop_x, self._crop_y, self._crop_w, self._crop_h
        )

        # 2. Semi-transparent dark mask (rgba(0, 0, 0, 120)) over areas OUTSIDE the crop box
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 120))
        # Top
        top_h = max(0.0, crop_drect.y() - drect.y())
        if top_h > 0:
            painter.drawRect(QRectF(drect.x(), drect.y(), drect.width(), top_h))
        # Bottom
        bottom_h = max(0.0, drect.bottom() - crop_drect.bottom())
        if bottom_h > 0:
            painter.drawRect(QRectF(drect.x(), crop_drect.bottom(), drect.width(), bottom_h))
        # Left
        left_w = max(0.0, crop_drect.x() - drect.x())
        if left_w > 0:
            painter.drawRect(QRectF(drect.x(), crop_drect.y(), left_w, crop_drect.height()))
        # Right
        right_w = max(0.0, drect.right() - crop_drect.right())
        if right_w > 0:
            painter.drawRect(QRectF(crop_drect.right(), crop_drect.y(), right_w, crop_drect.height()))

        # 3. Rule-of-thirds subtle dashed guide grid inside crop box
        grid_pen = QPen(QColor(255, 255, 255, 65), 1.0, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        w3 = crop_drect.width() / 3.0
        h3 = crop_drect.height() / 3.0
        painter.drawLine(QPointF(crop_drect.x() + w3, crop_drect.y()), QPointF(crop_drect.x() + w3, crop_drect.bottom()))
        painter.drawLine(QPointF(crop_drect.x() + 2 * w3, crop_drect.y()), QPointF(crop_drect.x() + 2 * w3, crop_drect.bottom()))
        painter.drawLine(QPointF(crop_drect.x(), crop_drect.y() + h3), QPointF(crop_drect.right(), crop_drect.y() + h3))
        painter.drawLine(QPointF(crop_drect.x(), crop_drect.y() + 2 * h3), QPointF(crop_drect.right(), crop_drect.y() + 2 * h3))

        # 4. Red rectangular border representing the crop area
        red_pen = QPen(QColor("#ff2d55"), 2.0, Qt.PenStyle.SolidLine)
        painter.setPen(red_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(crop_drect)

        # 5. Visible handle squares (corners & edge centers)
        handle_pen = QPen(QColor("#ff2d55"), 1.5, Qt.PenStyle.SolidLine)
        painter.setPen(handle_pen)
        painter.setBrush(QBrush(QColor("#ffffff")))
        handle_size = 7.0
        half_h = handle_size / 2.0

        handles = self._get_handles(crop_drect)
        for pt in handles.values():
            painter.drawRect(
                QRectF(
                    pt.x() - half_h,
                    pt.y() - half_h,
                    handle_size,
                    handle_size,
                )
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle, cursor = self._hit_test(event.position())
            if handle is not None:
                self._active_handle = handle
                self._mouse_press_pos = event.position()
                self._start_crop_rect = (
                    self._crop_x,
                    self._crop_y,
                    self._crop_w,
                    self._crop_h,
                )
                self.setCursor(cursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._active_handle and self._mouse_press_pos and self._start_crop_rect:
            drect = self._get_display_rect()
            scale = drect.width() / float(self._img_w)
            delta = event.position() - self._mouse_press_pos
            dx_img = delta.x() / scale
            dy_img = delta.y() / scale

            start_x, start_y, start_w, start_h = self._start_crop_rect
            min_size = 15.0

            if self._active_handle == "MOVE":
                new_x = max(0.0, min(float(self._img_w - start_w), start_x + dx_img))
                new_y = max(0.0, min(float(self._img_h - start_h), start_y + dy_img))
                self._crop_x = new_x
                self._crop_y = new_y
            elif self._active_handle == "TL":
                new_x = max(0.0, min(start_x + start_w - min_size, start_x + dx_img))
                new_y = max(0.0, min(start_y + start_h - min_size, start_y + dy_img))
                self._crop_w = start_x + start_w - new_x
                self._crop_h = start_y + start_h - new_y
                self._crop_x = new_x
                self._crop_y = new_y
            elif self._active_handle == "TR":
                new_y = max(0.0, min(start_y + start_h - min_size, start_y + dy_img))
                new_w = max(min_size, min(float(self._img_w) - start_x, start_w + dx_img))
                self._crop_h = start_y + start_h - new_y
                self._crop_y = new_y
                self._crop_w = new_w
            elif self._active_handle == "BL":
                new_x = max(0.0, min(start_x + start_w - min_size, start_x + dx_img))
                new_h = max(min_size, min(float(self._img_h) - start_y, start_h + dy_img))
                self._crop_w = start_x + start_w - new_x
                self._crop_x = new_x
                self._crop_h = new_h
            elif self._active_handle == "BR":
                self._crop_w = max(min_size, min(float(self._img_w) - start_x, start_w + dx_img))
                self._crop_h = max(min_size, min(float(self._img_h) - start_y, start_h + dy_img))
            elif self._active_handle == "T":
                new_y = max(0.0, min(start_y + start_h - min_size, start_y + dy_img))
                self._crop_h = start_y + start_h - new_y
                self._crop_y = new_y
            elif self._active_handle == "B":
                self._crop_h = max(min_size, min(float(self._img_h) - start_y, start_h + dy_img))
            elif self._active_handle == "L":
                new_x = max(0.0, min(start_x + start_w - min_size, start_x + dx_img))
                self._crop_w = start_x + start_w - new_x
                self._crop_x = new_x
            elif self._active_handle == "R":
                self._crop_w = max(min_size, min(float(self._img_w) - start_x, start_w + dx_img))

            self.update()
            self.crop_changed.emit(int(round(self._crop_w)), int(round(self._crop_h)))
            event.accept()
            return

        # Passive hover: update mouse cursor based on hit testing
        handle, cursor = self._hit_test(event.position())
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._active_handle:
            self._active_handle = None
            self._mouse_press_pos = None
            self._start_crop_rect = None
            handle, cursor = self._hit_test(event.position())
            self.setCursor(cursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def set_aspect_ratio(self, ratio_str: str) -> None:
        """Adjust crop rect to match chosen aspect ratio, centered in image."""
        ratios = {
            "1:1": 1.0,
            "16:9": 16.0 / 9.0,
            "9:16": 9.0 / 16.0,
            "4:3": 4.0 / 3.0,
            "3:4": 3.0 / 4.0,
        }
        if ratio_str == "Free" or ratio_str not in ratios:
            return

        target_ratio = ratios[ratio_str]
        img_aspect = self._img_w / float(self._img_h)

        if img_aspect > target_ratio:
            new_h = float(self._img_h)
            new_w = new_h * target_ratio
        else:
            new_w = float(self._img_w)
            new_h = new_w / target_ratio

        self._crop_w = max(15.0, min(float(self._img_w), new_w))
        self._crop_h = max(15.0, min(float(self._img_h), new_h))
        self._crop_x = (self._img_w - self._crop_w) / 2.0
        self._crop_y = (self._img_h - self._crop_h) / 2.0

        self.update()
        self.crop_changed.emit(int(round(self._crop_w)), int(round(self._crop_h)))

    def set_dimensions(self, w: int, h: int) -> None:
        """Update crop size from input boxes."""
        new_w = float(max(15, min(self._img_w, w)))
        new_h = float(max(15, min(self._img_h, h)))
        self._crop_w = new_w
        self._crop_h = new_h
        self._crop_x = max(0.0, min(float(self._img_w - new_w), self._crop_x))
        self._crop_y = max(0.0, min(float(self._img_h - new_h), self._crop_y))
        self.update()

    def reset_crop(self) -> None:
        """Reset crop region to encompass full image."""
        self._crop_x = 0.0
        self._crop_y = 0.0
        self._crop_w = float(self._img_w)
        self._crop_h = float(self._img_h)
        self.update()
        self.crop_changed.emit(int(self._img_w), int(self._img_h))

    def get_crop_box(self) -> tuple[int, int, int, int]:
        """Return (left, top, right, bottom) pixel coordinates for PIL crop."""
        left = int(round(max(0.0, self._crop_x)))
        top = int(round(max(0.0, self._crop_y)))
        right = int(round(min(float(self._img_w), self._crop_x + self._crop_w)))
        bottom = int(round(min(float(self._img_h), self._crop_y + self._crop_h)))
        return left, top, right, bottom


CropPreviewWidget = InteractiveCropLabel


class CropDialog(QDialog):
    """Frameless light translucent frosted glass image cropping dialog."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None
        self._updating_inputs = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(450, 480)

        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("cropContainer")
        container.setStyleSheet("""
            #cropContainer {
                background-color: #f7f7f7;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # 1. Title: "Crop Image" centered
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)
        title_label = QLabel("Crop Image")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #222222;")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(0, 0, 0, 0.35);
                font-size: 12px;
                font-weight: bold;
                border: none;
                border-radius: 11px;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 0.08);
                color: #222222;
            }
        """)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)
        layout.addLayout(header_layout)

        # 2. Image Area with interactive red rectangular crop box
        self.preview_widget = InteractiveCropLabel(self.file_path, self)
        layout.addWidget(self.preview_widget, stretch=1)

        # 3. Aspect Ratio Controls
        ratio_layout = QHBoxLayout()
        ratio_layout.setSpacing(5)

        ratio_label = QLabel("Aspect ratio")
        ratio_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #222222;")
        ratio_layout.addWidget(ratio_label)
        ratio_layout.addSpacing(4)

        self.ratio_group = QButtonGroup(self)
        self.ratio_group.setExclusive(True)

        ratios = ["Free", "1:1", "16:9", "9:16", "4:3", "3:4"]
        for r_name in ratios:
            btn = QPushButton(r_name)
            btn.setCheckable(True)
            if r_name == "Free":
                btn.setChecked(True)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 0, 0, 0.05);
                    color: #333333;
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 6px;
                    padding: 4px 7px;
                    font-size: 11px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background-color: rgba(0, 0, 0, 0.09);
                    color: #111111;
                }
                QPushButton:checked {
                    background-color: #ff5500;
                    color: #ffffff;
                    font-weight: bold;
                    border: 1px solid #ff5500;
                }
            """)
            self.ratio_group.addButton(btn)
            ratio_layout.addWidget(btn)

        self.ratio_group.buttonClicked.connect(
            lambda btn: self.preview_widget.set_aspect_ratio(btn.text())
        )
        layout.addLayout(ratio_layout)

        # 4. Dimensions: W and H in px
        dim_layout = QHBoxLayout()
        dim_layout.setSpacing(6)

        w_label = QLabel("W")
        w_label.setStyleSheet("font-size: 12px; font-weight: bold; color: #222222;")
        self.w_input = QSpinBox()
        self.w_input.setRange(1, self.preview_widget._img_w)
        self.w_input.setValue(self.preview_widget._img_w)
        self.w_input.setStyleSheet("""
            QSpinBox {
                background-color: #ffffff;
                color: #222222;
                border: 1px solid rgba(0, 0, 0, 0.16);
                border-radius: 6px;
                padding: 3px 6px;
                font-size: 12px;
                font-weight: 600;
                min-width: 65px;
            }
        """)

        px1 = QLabel("px")
        px1.setStyleSheet("font-size: 12px; color: #666666;")

        h_label = QLabel("H")
        h_label.setStyleSheet("font-size: 12px; font-weight: bold; color: #222222;")
        self.h_input = QSpinBox()
        self.h_input.setRange(1, self.preview_widget._img_h)
        self.h_input.setValue(self.preview_widget._img_h)
        self.h_input.setStyleSheet("""
            QSpinBox {
                background-color: #ffffff;
                color: #222222;
                border: 1px solid rgba(0, 0, 0, 0.16);
                border-radius: 6px;
                padding: 3px 6px;
                font-size: 12px;
                font-weight: 600;
                min-width: 65px;
            }
        """)

        px2 = QLabel("px")
        px2.setStyleSheet("font-size: 12px; color: #666666;")

        dim_layout.addWidget(w_label)
        dim_layout.addWidget(self.w_input)
        dim_layout.addWidget(px1)
        dim_layout.addSpacing(12)
        dim_layout.addWidget(h_label)
        dim_layout.addWidget(self.h_input)
        dim_layout.addWidget(px2)
        dim_layout.addStretch()

        self.preview_widget.crop_changed.connect(self._on_crop_changed)
        self.w_input.valueChanged.connect(self._on_dimensions_input)
        self.h_input.valueChanged.connect(self._on_dimensions_input)

        layout.addLayout(dim_layout)

        # 5. Bottom Bar: Reset on left, Apply on right
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        reset_btn = QPushButton("Reset")
        reset_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #444444;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #222222;
            }
        """)
        reset_btn.clicked.connect(self._on_reset)
        bottom_layout.addWidget(reset_btn)

        bottom_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #444444;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #222222;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)

        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5500;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 18px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #ff661a;
            }
            QPushButton:pressed {
                background-color: #e04b00;
            }
        """)
        apply_btn.clicked.connect(self._on_apply)
        bottom_layout.addWidget(apply_btn)

        layout.addLayout(bottom_layout)
        outer_layout.addWidget(container)

    def _on_crop_changed(self, w: int, h: int) -> None:
        self._updating_inputs = True
        self.w_input.setValue(w)
        self.h_input.setValue(h)
        self._updating_inputs = False

    def _on_dimensions_input(self) -> None:
        if self._updating_inputs:
            return
        w = self.w_input.value()
        h = self.h_input.value()
        self.preview_widget.set_dimensions(w, h)

    def _on_reset(self) -> None:
        for btn in self.ratio_group.buttons():
            if btn.text() == "Free":
                btn.setChecked(True)
                break
        self.preview_widget.reset_crop()

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def _on_apply(self) -> None:
        """Crop the image and save as [OriginalName]_cropped.[ext]."""
        try:
            dir_name = os.path.dirname(self.file_path)
            base_name, ext = os.path.splitext(os.path.basename(self.file_path))
            output_path = os.path.join(dir_name, f"{base_name}_cropped{ext}")

            if os.path.abspath(output_path) == os.path.abspath(self.file_path):
                output_path = os.path.join(dir_name, f"{base_name}_cropped_1{ext}")

            crop_box = self.preview_widget.get_crop_box()
            with Image.open(self.file_path) as img:
                left = max(0, min(img.width - 1, crop_box[0]))
                top = max(0, min(img.height - 1, crop_box[1]))
                right = max(left + 1, min(img.width, crop_box[2]))
                bottom = max(top + 1, min(img.height, crop_box[3]))

                cropped = img.crop((left, top, right, bottom))
                ext_lower = ext.lower()
                if ext_lower == ".png":
                    cropped.save(output_path, "PNG", optimize=True)
                elif ext_lower in (".jpg", ".jpeg"):
                    if cropped.mode not in ("RGB", "L"):
                        cropped = cropped.convert("RGB")
                    cropped.save(output_path, "JPEG", quality=95)
                elif ext_lower == ".webp":
                    cropped.save(output_path, "WEBP", quality=95)
                else:
                    fmt = cropped.format or "PNG"
                    if cropped.mode in ("RGBA", "LA") and fmt.upper() in ("JPEG", "BMP"):
                        cropped = cropped.convert("RGB")
                    cropped.save(output_path, format=fmt)

            print(f"[QuickDrop] Cropped '{self.file_path}' -> '{output_path}'")
            self.accept()
        except Exception as e:
            print(f"[QuickDrop ERROR] Failed to crop '{self.file_path}': {e}")
            self.reject()

    # Window drag support for frameless window
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
        ):
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.reject()
        else:
            super().keyPressEvent(event)


class TransparentWebDropZone(QWebEngineView):
    """Frameless translucent WebEngine radial drop zone with macOS glassmorphism."""

    def __init__(self) -> None:
        super().__init__()

        # Frameless and stays on top
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(500, 500)

        # 1. True Transparency (CRITICAL)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.page().setBackgroundColor(Qt.GlobalColor.transparent)

        # Enable Drops, Keyboard Focus & Mouse Tracking for real-time hover
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        # State tracking
        self._drag_start_pos = None
        self._current_modifier = "DEFAULT"
        self._workers: list[ConversionWorker] = []
        self._dialogs: list[QDialog] = []
        self._is_processing = False

        # Load local index.html using absolute path and QUrl.fromLocalFile()
        html_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "index.html")
        )
        self.load(QUrl.fromLocalFile(html_path))

        # Center on primary screen
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    # ── Modifier & Dynamic Menu Switching ──────────────────────────

    def _sync_modifier(self, modifier_name: str) -> None:
        """Inform WebEngine of the active modifier menu if it has changed."""
        if self._current_modifier != modifier_name:
            self._current_modifier = modifier_name
            self.page().runJavaScript(f"window.switchMenu('{modifier_name}');")

    def _resolve_modifier(self, event, is_release: bool = False) -> str:
        """Resolve the effective modifier name based on event modifiers and key."""
        mods = event.modifiers()
        key = event.key() if hasattr(event, "key") else None

        if is_release:
            if key == Qt.Key.Key_Control:
                mods &= ~Qt.KeyboardModifier.ControlModifier
            elif key == Qt.Key.Key_Shift:
                mods &= ~Qt.KeyboardModifier.ShiftModifier
            elif key == Qt.Key.Key_Alt:
                mods &= ~Qt.KeyboardModifier.AltModifier
        else:
            if key == Qt.Key.Key_Control:
                mods |= Qt.KeyboardModifier.ControlModifier
            elif key == Qt.Key.Key_Shift:
                mods |= Qt.KeyboardModifier.ShiftModifier
            elif key == Qt.Key.Key_Alt:
                mods |= Qt.KeyboardModifier.AltModifier

        if mods & Qt.KeyboardModifier.ControlModifier:
            return "CTRL"
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            return "SHIFT"
        elif mods & Qt.KeyboardModifier.AltModifier:
            return "ALT"
        return "DEFAULT"

    # ── Native Drag & Drop Events ──────────────────────────────────

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            mod_name = self._resolve_modifier(event, is_release=False)
            self._sync_modifier(mod_name)
            pos = event.position()
            self.page().runJavaScript(
                f"window.updateHover({pos.x()}, {pos.y()});"
            )
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.page().runJavaScript("window.clearHover();")
        self._sync_modifier("DEFAULT")
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            file_paths = [
                url.toLocalFile()
                for url in event.mimeData().urls()
                if url.isLocalFile() or url.toLocalFile()
            ]

            def on_action_received(action: str | None) -> None:
                action_name = action.strip() if action else "None"
                for path in file_paths:
                    print(f"ACTION: {action_name} | FILE: {path}")
                self.page().runJavaScript("window.clearHover();")
                self._sync_modifier("DEFAULT")

                if action_name == "COMPRESS":
                    for path in file_paths:
                        dialog = CompressDialog(path, self)
                        dialog.show()
                        self._dialogs.append(dialog)
                        dialog.finished.connect(
                            lambda _, d=dialog: self._dialogs.remove(d)
                            if d in self._dialogs
                            else None
                        )
                elif action_name == "CROP":
                    for path in file_paths:
                        dialog = CropDialog(path, self)
                        dialog.show()
                        self._dialogs.append(dialog)
                        dialog.finished.connect(
                            lambda _, d=dialog: self._dialogs.remove(d)
                            if d in self._dialogs
                            else None
                        )
                elif (
                    action_name.strip().upper() == "ADD BG"
                    or action_name.strip().upper() in ConversionWorker.FORMAT_MAP
                ):
                    self._start_conversion(file_paths, action_name.strip().upper())

            self.page().runJavaScript(
                "window.currentHoveredAction", on_action_received
            )
        else:
            event.ignore()

    def _start_conversion(self, file_paths: list[str], target_format: str) -> None:
        """Spawn a background thread to convert dropped files."""
        self._is_processing = True

        # Provide visual loading/processing feedback in the center pill
        action_label = (
            "Removing BG..."
            if target_format == "ADD BG"
            else f"Converting {target_format}..."
        )
        self.page().runJavaScript(
            f"""
            window.isProcessing = true;
            (function() {{
                const cat = document.getElementById('tooltip-category');
                const txt = document.getElementById('tooltip-text');
                const tip = document.getElementById('tooltip');
                if (cat) cat.textContent = 'Processing';
                if (txt) txt.textContent = '{action_label}';
                if (tip) tip.classList.add('visible');
            }})();
            """
        )

        # Trigger: Show LoadingDialog immediately when the worker begins
        subtitle = (
            "Removing background with AI..."
            if target_format == "ADD BG"
            else f"Converting to {target_format}..."
        )
        loading_dialog = LoadingDialog(
            title="Processing... Please wait",
            subtitle=subtitle,
            parent=self,
        )
        loading_dialog.show()

        worker = ConversionWorker(file_paths, target_format, self)
        worker.file_converted.connect(
            lambda src, dst: print(f"[QuickDrop] Processed '{src}' -> '{dst}'")
        )
        # Close: Connect the worker's finished/failure signal to close LoadingDialog automatically
        worker.conversion_failed.connect(
            lambda src, err: self._on_conversion_failed(src, err, loading_dialog)
        )
        worker.finished.connect(
            lambda: self._on_conversion_finished(worker, target_format, loading_dialog)
        )
        self._workers.append(worker)
        worker.start()

    def _on_conversion_finished(
        self,
        worker: ConversionWorker,
        target_format: str,
        dialog: LoadingDialog | None = None,
    ) -> None:
        if dialog is not None:
            dialog.close()
        self._cleanup_worker(worker)
        self._is_processing = False
        self.page().runJavaScript(
            """
            (function() {
                window.isProcessing = false;
                const cat = document.getElementById('tooltip-category');
                const txt = document.getElementById('tooltip-text');
                if (cat) cat.textContent = 'Completed';
                if (txt) txt.textContent = '✓ Done';
                setTimeout(() => {
                    if (window.clearHover && !window.isProcessing) window.clearHover();
                }, 1200);
            })();
            """
        )

    def _on_conversion_failed(
        self,
        src: str,
        err: str,
        dialog: LoadingDialog | None = None,
    ) -> None:
        if dialog is not None:
            dialog.close()
        self._is_processing = False
        print(f"[QuickDrop ERROR] Failed to process '{src}': {err}")
        self.page().runJavaScript(
            """
            (function() {
                window.isProcessing = false;
                const cat = document.getElementById('tooltip-category');
                const txt = document.getElementById('tooltip-text');
                if (cat) cat.textContent = 'Error';
                if (txt) txt.textContent = 'Failed';
                setTimeout(() => {
                    if (window.clearHover && !window.isProcessing) window.clearHover();
                }, 1500);
            })();
            """
        )

    def _cleanup_worker(self, worker: ConversionWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)

    # ── Desktop Interactivity & Mouse Hover Tracking ───────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
        ):
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()
        else:
            if not getattr(self, "_is_processing", False):
                pos = event.position()
                self.page().runJavaScript(
                    f"window.updateHover({pos.x()}, {pos.y()});"
                )
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not getattr(self, "_is_processing", False):
            self.page().runJavaScript("window.clearHover();")
        super().leaveEvent(event)

    # ── Keyboard Tracking: Dynamic Switching ───────────────────────

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
            return

        mod_name = self._resolve_modifier(event, is_release=False)
        self._sync_modifier(mod_name)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        mod_name = self._resolve_modifier(event, is_release=True)
        self._sync_modifier(mod_name)
        super().keyReleaseEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    drop_zone = TransparentWebDropZone()
    drop_zone.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
