import os
import shutil
import subprocess
import sys
import time

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
from PIL.ExifTags import GPSTAGS, TAGS
import pillow_heif

pillow_heif.register_heif_opener()
import pillow_avif
from rembg import remove

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QClipboard, QColor, QPainter, QPen, QPixmap
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
    QTextEdit,
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


class OCRWorker(QThread):
    """Background worker thread for asynchronous EasyOCR text extraction."""

    extracted = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, file_paths: list[str] | str, parent=None) -> None:
        super().__init__(parent)
        if isinstance(file_paths, str):
            self.file_paths = [file_paths]
        else:
            self.file_paths = list(file_paths)

    def run(self) -> None:
        try:
            import cv2
            import easyocr
            import numpy as np

            reader = easyocr.Reader(["en"])
            extracted_text = ""
            for path in self.file_paths:
                # Load image for processing with OpenCV
                img_cv = cv2.imread(path)
                if img_cv is None:
                    try:
                        img_cv = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                    except Exception:
                        img_cv = None

                if img_cv is not None:
                    # 1. Upscale for clarity
                    img_cv = cv2.resize(img_cv, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                    # 2. Sharpen to make symbols like '=' and quotes pop out
                    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
                    img_cv = cv2.filter2D(img_cv, -1, kernel)
                    results = reader.readtext(img_cv, detail=1, decoder="beamsearch")
                else:
                    results = reader.readtext(path, detail=1, decoder="beamsearch")

                if results:
                    # Extract bounding box metrics
                    items = []
                    for item in results:
                        box, text_chunk = item[0], item[1]
                        xs = [pt[0] for pt in box]
                        ys = [pt[1] for pt in box]
                        x_min, x_max = min(xs), max(xs)
                        y_min, y_max = min(ys), max(ys)
                        items.append({
                            "text": text_chunk,
                            "x_min": x_min,
                            "x_max": x_max,
                            "y_min": y_min,
                            "y_max": y_max,
                            "y_center": (y_min + y_max) / 2.0,
                            "h": max(1.0, y_max - y_min),
                        })

                    # Calculate document-wide metrics for spacing and indentation
                    heights = [it["h"] for it in items]
                    avg_h = (sum(heights) / len(heights)) if heights else 20.0
                    char_w = max(5.0, avg_h * 0.55)
                    global_min_x = min(it["x_min"] for it in items)

                    # Sort boxes primarily by vertical center
                    items.sort(key=lambda b: (b["y_center"], b["x_min"]))

                    # Group bounding boxes into exact lines using vertical overlap and center distance
                    lines = []
                    for b in items:
                        matched_line = None
                        best_overlap = 0.0

                        for line in lines:
                            line_h = max(1.0, line["y_max"] - line["y_min"])
                            overlap = max(0.0, min(b["y_max"], line["y_max"]) - max(b["y_min"], line["y_min"]))
                            min_h = min(b["h"], line_h)
                            overlap_ratio = overlap / min_h if min_h > 0 else 0.0
                            center_inside = line["y_min"] <= b["y_center"] <= line["y_max"]
                            center_dist = abs(b["y_center"] - line["y_center"])

                            if overlap_ratio >= 0.3 or center_inside or center_dist <= 0.45 * min_h:
                                if overlap_ratio > best_overlap:
                                    best_overlap = overlap_ratio
                                    matched_line = line

                        if matched_line is not None:
                            matched_line["boxes"].append(b)
                            matched_line["y_min"] = min(matched_line["y_min"], b["y_min"])
                            matched_line["y_max"] = max(matched_line["y_max"], b["y_max"])
                            matched_line["y_center"] = sum(x["y_center"] for x in matched_line["boxes"]) / len(matched_line["boxes"])
                        else:
                            lines.append({
                                "y_min": b["y_min"],
                                "y_max": b["y_max"],
                                "y_center": b["y_center"],
                                "boxes": [b],
                            })

                    # Sort lines strictly top-to-bottom
                    lines.sort(key=lambda l: l["y_center"])

                    # Reconstruct lines: sort left-to-right, preserve indentation and token spacing
                    formatted_lines = []
                    for line in lines:
                        line["boxes"].sort(key=lambda b: b["x_min"])

                        first_box = line["boxes"][0]
                        line_str = ""

                        indent_px = first_box["x_min"] - global_min_x
                        if indent_px >= char_w * 0.75:
                            num_indent = int(round(indent_px / char_w))
                            line_str += " " * num_indent

                        prev_x_max = None
                        for b in line["boxes"]:
                            if prev_x_max is not None:
                                gap = b["x_min"] - prev_x_max
                                if gap >= char_w * 1.5:
                                    num_spaces = int(round(gap / char_w))
                                    line_str += (" " * num_spaces) + b["text"]
                                elif gap >= char_w * 0.35:
                                    line_str += " " + b["text"]
                                else:
                                    line_str += b["text"]
                            else:
                                line_str += b["text"]
                            prev_x_max = b["x_max"]

                        formatted_lines.append(line_str)

                    text = "\n".join(formatted_lines)
                else:
                    text = ""
                if len(self.file_paths) > 1:
                    extracted_text += f"--- {os.path.basename(path)} ---\n{text}\n\n"
                else:
                    extracted_text += f"{text}\n\n"

            extracted_text = extracted_text.strip()
            if not extracted_text:
                extracted_text = "No text found in the image."
            self.extracted.emit(extracted_text)
        except ImportError:
            self.failed.emit("Missing library. Please run: pip install easyocr")
        except Exception as e:
            self.failed.emit(f"OCR Error: {e}")


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


class MetadataDialog(QDialog):
    """Frameless light translucent frosted glass dialog to view and clean image metadata."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None

        # Frameless window, light frosted glass theme matching CompressDialog
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(420, 460)

        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("metadataContainer")
        container.setStyleSheet("""
            #metadataContainer {
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
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        # 1. Header with centered bold Title "Image Metadata" and close button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)  # Symmetry for the close button

        title_label = QLabel("Image Metadata")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #222222;")
        header_layout.addWidget(title_label)

        close_x_btn = QPushButton("✕")
        close_x_btn.setFixedSize(22, 22)
        close_x_btn.setStyleSheet("""
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
                color: #111111;
            }
        """)
        close_x_btn.clicked.connect(self.close)
        header_layout.addWidget(close_x_btn)

        layout.addLayout(header_layout)

        # Subtitle: original file name
        filename_label = QLabel(os.path.basename(self.file_path))
        filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        filename_label.setWordWrap(True)
        filename_label.setStyleSheet(
            "color: #222222; font-size: 11px; margin-top: 2px; margin-bottom: 4px; padding: 0 8px;"
        )
        layout.addWidget(filename_label)

        # 2. Content: Read-only QTextEdit (semi-transparent background and no border)
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: rgba(0, 0, 0, 0.03);
                border: none;
                border-radius: 8px;
                padding: 10px;
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "SF Mono", Menlo, Consolas, "Courier New", monospace;
                font-size: 12px;
                line-height: 1.4;
            }
        """)
        metadata_text, has_metadata = self._extract_metadata()
        self.text_edit.setPlainText(metadata_text)
        layout.addWidget(self.text_edit)

        # 3. Bottom Bar: "Close" button on left, red "Clean Metadata" (#e63946) on right
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #444444;
                border: none;
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #222222;
            }
            QPushButton:pressed {
                background-color: rgba(0, 0, 0, 0.14);
            }
        """)
        close_btn.clicked.connect(self.close)
        bottom_layout.addWidget(close_btn)

        bottom_layout.addStretch()

        self.clean_btn = QPushButton("Clean Metadata")
        self.clean_btn.setStyleSheet("""
            QPushButton {
                background-color: #e63946;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #d62839;
            }
            QPushButton:pressed {
                background-color: #ba182b;
            }
            QPushButton:disabled {
                background-color: rgba(0, 0, 0, 0.08);
                color: rgba(0, 0, 0, 0.28);
            }
        """)
        self.clean_btn.setEnabled(has_metadata)
        self.clean_btn.clicked.connect(self._on_clean_metadata)
        bottom_layout.addWidget(self.clean_btn)

        layout.addLayout(bottom_layout)
        outer_layout.addWidget(container)

    def _extract_metadata(self) -> tuple[str, bool]:
        """Extract EXIF data using img.getexif() and TAGS, returning (text, has_metadata)."""
        fallback_msg = (
            "No EXIF metadata found. This file format may not be supported for "
            "metadata extraction (try JPG, PNG, TIFF)."
        )
        if not os.path.isfile(self.file_path):
            return fallback_msg, False

        try:
            with Image.open(self.file_path) as img:
                lines = []
                exif = img.getexif()
                if exif:
                    for tag_id, value in exif.items():
                        tag_name = TAGS.get(tag_id, str(tag_id))
                        if tag_id == 0x8825:
                            try:
                                gps_ifd = exif.get_ifd(0x8825)
                                for gps_id, gps_val in gps_ifd.items():
                                    gps_name = GPSTAGS.get(gps_id, str(gps_id))
                                    lines.append(f"GPS {gps_name}: {gps_val}")
                                continue
                            except Exception:
                                pass
                        elif tag_id == 0x8769:
                            try:
                                sub_ifd = exif.get_ifd(0x8769)
                                for sub_id, sub_val in sub_ifd.items():
                                    sub_name = TAGS.get(sub_id, str(sub_id))
                                    lines.append(f"{sub_name}: {sub_val}")
                                continue
                            except Exception:
                                pass

                        if isinstance(value, bytes):
                            try:
                                val_str = value.decode("utf-8", errors="replace").strip("\x00")
                            except Exception:
                                val_str = f"<{len(value)} bytes>"
                        else:
                            val_str = str(value)
                        lines.append(f"{tag_name}: {val_str}")

                if lines:
                    return "\n".join(lines), True
                return "No EXIF metadata found in this image.", False
        except (UnidentifiedImageError, Exception):
            return fallback_msg, False

    def _on_clean_metadata(self) -> None:
        """Strip metadata and save image as [OriginalName]_cleaned.[ext] in the same directory."""
        try:
            with Image.open(self.file_path) as img:
                dir_name = os.path.dirname(os.path.abspath(self.file_path))
                base_name, ext = os.path.splitext(os.path.basename(self.file_path))
                output_path = os.path.join(dir_name, f"{base_name}_cleaned{ext}")

                # Strip metadata by pasting into fresh image without exif parameter
                clean_img = Image.new(img.mode, img.size)
                clean_img.paste(img)
                if ext.lower() in (".jpg", ".jpeg"):
                    clean_img.save(output_path, "JPEG", quality=95)
                else:
                    clean_img.save(output_path)

            print(f"[QuickDrop] Cleaned metadata: '{self.file_path}' -> '{output_path}'")
        except Exception as e:
            print(f"[QuickDrop ERROR] Failed to clean metadata for '{self.file_path}': {e}")

        self.close()

    def _center_on_screen(self) -> None:
        """Center the dialog on the primary display."""
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

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
        else:
            super().keyPressEvent(event)


class ColorDialog(QDialog):
    """Frameless light translucent frosted glass dialog displaying extracted 5-color palette."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None

        # Frameless window, light frosted glass theme matching other dialogs
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(440, 260)

        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("colorContainer")
        container.setStyleSheet("""
            #colorContainer {
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
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        # 1. Header with centered bold Title "Color Palette" and close button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)  # Symmetry for the close button

        title_label = QLabel("Color Palette")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #222222;")
        header_layout.addWidget(title_label)

        close_x_btn = QPushButton("✕")
        close_x_btn.setFixedSize(22, 22)
        close_x_btn.setStyleSheet("""
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
                color: #111111;
            }
        """)
        close_x_btn.clicked.connect(self.close)
        header_layout.addWidget(close_x_btn)

        layout.addLayout(header_layout)

        # Subtitle: original file name
        filename_label = QLabel(os.path.basename(self.file_path))
        filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        filename_label.setWordWrap(True)
        filename_label.setStyleSheet(
            "color: #444444; font-size: 12px; padding-bottom: 10px;"
        )
        layout.addWidget(filename_label)

        # 2. Palette Display: Horizontal layout containing 5 color swatches
        swatches_layout = QHBoxLayout()
        swatches_layout.setSpacing(12)
        swatches_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        colors = self._extract_colors()
        for hex_code in colors:
            swatch_item = QVBoxLayout()
            swatch_item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            swatch_item.setSpacing(6)

            # 60x60 rounded swatch
            swatch = QFrame()
            swatch.setFixedSize(60, 60)
            swatch.setStyleSheet(f"""
                background-color: {hex_code};
                border-radius: 12px;
                border: 1px solid rgba(0, 0, 0, 0.12);
            """)

            # Selectable QLabel showing HEX code
            hex_label = QLabel(hex_code)
            hex_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hex_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            hex_label.setStyleSheet("""
                font-size: 11px;
                font-weight: 600;
                color: #333333;
                font-family: -apple-system, BlinkMacSystemFont, "SF Mono", Menlo, Consolas, monospace;
            """)

            swatch_item.addWidget(swatch)
            swatch_item.addWidget(hex_label)
            swatches_layout.addLayout(swatch_item)

        layout.addLayout(swatches_layout)

        # 3. Bottom Bar: "Close" button
        bottom_layout = QHBoxLayout()
        bottom_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #444444;
                border: none;
                border-radius: 6px;
                padding: 7px 28px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #222222;
            }
            QPushButton:pressed {
                background-color: rgba(0, 0, 0, 0.14);
            }
        """)
        close_btn.clicked.connect(self.close)
        bottom_layout.addWidget(close_btn)

        layout.addLayout(bottom_layout)
        outer_layout.addWidget(container)

    def _extract_colors(self) -> list[str]:
        """Extract 5 dominant colors using PIL.Image adaptive palette."""
        if not os.path.isfile(self.file_path):
            return ["#CCCCCC"] * 5

        try:
            with Image.open(self.file_path) as img:
                # Resize to small thumbnail (150x150) for fast processing
                thumb = img.convert("RGB")
                thumb = thumb.resize((150, 150))

                # Extract 5 dominant colors using adaptive palette
                pal_img = thumb.convert("P", palette=Image.ADAPTIVE, colors=5)
                palette = pal_img.getpalette() or []

                color_counts = pal_img.getcolors()
                hex_colors = []
                if color_counts:
                    color_counts.sort(key=lambda x: x[0], reverse=True)
                    for _, idx in color_counts[:5]:
                        r = palette[idx * 3]
                        g = palette[idx * 3 + 1]
                        b = palette[idx * 3 + 2]
                        hex_colors.append(f"#{r:02X}{g:02X}{b:02X}")

                # If fewer than 5 unique colors in pixel counts, check palette entries
                idx = 0
                while len(hex_colors) < 5 and (idx * 3 + 2) < len(palette):
                    r = palette[idx * 3]
                    g = palette[idx * 3 + 1]
                    b = palette[idx * 3 + 2]
                    c_hex = f"#{r:02X}{g:02X}{b:02X}"
                    if c_hex not in hex_colors:
                        hex_colors.append(c_hex)
                    idx += 1

                # If still fewer than 5, repeat last color or default
                while len(hex_colors) < 5:
                    hex_colors.append(hex_colors[-1] if hex_colors else "#888888")

                return hex_colors[:5]
        except Exception as e:
            print(f"[QuickDrop ERROR] Failed to extract colors from '{self.file_path}': {e}")
            return ["#CCCCCC"] * 5

    def _center_on_screen(self) -> None:
        """Center the dialog on the primary display."""
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

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
        else:
            super().keyPressEvent(event)


class OCRDialog(QDialog):
    """Frameless light translucent frosted glass dialog displaying extracted OCR text with editing and copy capabilities."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._initial_text = text
        self._drag_start_pos = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(460, 480)

        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("ocrContainer")
        container.setStyleSheet("""
            #ocrContainer {
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
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        # Header with title and close button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)

        title_label = QLabel("Extracted Text (OCR)")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #222222;")
        header_layout.addWidget(title_label)

        close_x_btn = QPushButton("✕")
        close_x_btn.setFixedSize(22, 22)
        close_x_btn.setStyleSheet("""
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
                color: #111111;
            }
        """)
        close_x_btn.clicked.connect(self.close)
        header_layout.addWidget(close_x_btn)
        layout.addLayout(header_layout)

        subtitle_label = QLabel("Review or edit the extracted text before copying:")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_label.setStyleSheet("color: #666666; font-size: 11px; margin-bottom: 4px;")
        layout.addWidget(subtitle_label)

        # Editable QTextEdit
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(self._initial_text)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: rgba(0, 0, 0, 0.03);
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 8px;
                padding: 10px;
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "SF Mono", Menlo, Consolas, "Courier New", monospace;
                font-size: 12px;
                line-height: 1.45;
            }
            QTextEdit:focus {
                border: 1px solid #ff5500;
                background-color: #ffffff;
            }
        """)
        layout.addWidget(self.text_edit)

        # Bottom buttons
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #444444;
                border: none;
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #222222;
            }
            QPushButton:pressed {
                background-color: rgba(0, 0, 0, 0.14);
            }
        """)
        close_btn.clicked.connect(self.close)
        bottom_layout.addWidget(close_btn)

        bottom_layout.addStretch()

        self.copy_btn = QPushButton("Copy to Clipboard")
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5500;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #e64200;
            }
            QPushButton:pressed {
                background-color: #cc3b00;
            }
        """)
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        bottom_layout.addWidget(self.copy_btn)

        layout.addLayout(bottom_layout)
        outer_layout.addWidget(container)

    def _copy_to_clipboard(self) -> None:
        """Copy current text from QTextEdit using universal PyQt6 and OS-native fallback clipboard logic."""
        text = self.text_edit.toPlainText().strip()
        if not text:
            return

        # 1. Standard PyQt6 clipboard
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        if sys.platform == "linux":
            clipboard.setText(text, QClipboard.Mode.Selection)
        QApplication.processEvents()

        # 2. OS-Native Hardcore Fallback
        try:
            if sys.platform == "linux":
                if shutil.which("wl-copy"):
                    subprocess.run(["wl-copy"], input=text.encode(), check=True)
                elif shutil.which("xclip"):
                    subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text.encode(), check=True)
            elif sys.platform == "win32":
                subprocess.run(["clip"], input=text.encode(), check=True)
        except Exception:
            pass

        # Momentary visual feedback on button
        self.copy_btn.setText("✓ Copied!")
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a9d8f;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: bold;
                font-size: 13px;
            }
        """)
        QTimer.singleShot(1500, self._reset_copy_btn)

    def _reset_copy_btn(self) -> None:
        self.copy_btn.setText("Copy to Clipboard")
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5500;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #e64200;
            }
            QPushButton:pressed {
                background-color: #cc3b00;
            }
        """)

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

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
        else:
            super().keyPressEvent(event)


class ZoomLockedWebEngineView(QWebEngineView):
    """Custom QWebEngineView subclass that definitively blocks trackpad gestures, Ctrl+Wheel zooming, and enables window dragging."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setZoomFactor(1.0)
        self.loadFinished.connect(lambda ok: self.setZoomFactor(1.0))
        self._drag_start_pos = None
        self._press_global_pos = None
        self._is_dragging_window = False

    def event(self, e) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.NativeGesture or e.type() == QEvent.Type.Gesture:
            return True
        return super().event(e)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier or (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            event.ignore()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """Record drag start position and pass event to super() so regular web clicks still work."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
            self._press_global_pos = event.globalPosition().toPoint()
            self._is_dragging_window = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        """Move top-level parent window on drag and pass event to super() for web interactions."""
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
        ):
            if not getattr(self, "_is_dragging_window", False):
                press_pos = getattr(self, "_press_global_pos", event.globalPosition().toPoint())
                if (event.globalPosition().toPoint() - press_pos).manhattanLength() > 5:
                    self._is_dragging_window = True
            if getattr(self, "_is_dragging_window", False):
                self.window().move(event.globalPosition().toPoint() - self._drag_start_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start_pos = None
        self._is_dragging_window = False
        super().mouseReleaseEvent(event)


class TransparentWebDropZone(ZoomLockedWebEngineView):
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
        self._press_global_pos = None
        self._is_dragging_window = False
        self._last_click_time = 0.0
        self._current_modifier = "DEFAULT"
        self._workers: list[ConversionWorker] = []
        self._ocr_workers: list[OCRWorker] = []
        self._dialogs: list[QDialog] = []
        self._is_processing = False

        # Lock UI zoom strictly to 1.0 and hook title changes for click-to-execute
        self.setZoomFactor(1.0)
        self.loadFinished.connect(self._on_page_loaded)
        self.titleChanged.connect(self._on_title_changed)
        self._install_event_filters()

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
                elif action_name.strip().upper() == "METADATA":
                    for path in file_paths:
                        dialog = MetadataDialog(path, self)
                        dialog.show()
                        self._dialogs.append(dialog)
                        dialog.finished.connect(
                            lambda _, d=dialog: self._dialogs.remove(d)
                            if d in self._dialogs
                            else None
                        )
                elif action_name.strip().upper() == "COLOR":
                    for path in file_paths:
                        dialog = ColorDialog(path, self)
                        dialog.show()
                        self._dialogs.append(dialog)
                        dialog.finished.connect(
                            lambda _, d=dialog: self._dialogs.remove(d)
                            if d in self._dialogs
                            else None
                        )
                elif action_name == "SCREENSHOT":
                    self._execute_screenshot()
                elif action_name == "TERMINAL":
                    if file_paths:
                        for path in file_paths:
                            self._execute_terminal(path)
                    else:
                        self._execute_terminal()
                elif action_name in ["COPY PATH", "COPY_PATH"]:
                    paths_string = "\n".join([os.path.abspath(p) for p in file_paths])

                    # 1. Standard PyQt6 approach
                    clipboard = QApplication.clipboard()
                    clipboard.setText(paths_string)
                    QApplication.processEvents()

                    # 2. OS-Native Hardcore Fallback (Bypasses GUI clipboard issues completely)
                    try:
                        if sys.platform == "linux":
                            if shutil.which("wl-copy"):
                                subprocess.run(["wl-copy"], input=paths_string.encode(), check=True)
                            elif shutil.which("xclip"):
                                subprocess.run(["xclip", "-selection", "clipboard"], input=paths_string.encode(), check=True)
                        elif sys.platform == "darwin":
                            subprocess.run(["pbcopy"], input=paths_string.encode(), check=True)
                        elif sys.platform == "win32":
                            subprocess.run(["clip"], input=paths_string.encode(), check=True)
                    except Exception as e:
                        print(f"[QuickDrop] Native clipboard fallback error: {e}")

                    print(f"[QuickDrop] Copied path(s) to system clipboard:\n{paths_string}")
                    self.page().runJavaScript(
                        """
                        (function() {
                            const cat = document.getElementById('tooltip-category');
                            const txt = document.getElementById('tooltip-text');
                            const tip = document.getElementById('tooltip');
                            if (cat) cat.textContent = 'Clipboard';
                            if (txt) txt.textContent = 'Copied!';
                            if (tip) tip.classList.add('visible', 'active');
                            setTimeout(() => {
                                if (window.clearHover && !window.isProcessing) window.clearHover();
                            }, 1200);
                        })();
                        """
                    )
                elif action_name == "OCR":
                    self._start_ocr(file_paths)
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

    def _start_ocr(self, file_paths: list[str]) -> None:
        """Asynchronously extract text using EasyOCR in a background QThread."""
        self._is_processing = True

        # Provide visual loading feedback in the center pill
        self.page().runJavaScript(
            """
            window.isProcessing = true;
            (function() {
                const cat = document.getElementById('tooltip-category');
                const txt = document.getElementById('tooltip-text');
                const tip = document.getElementById('tooltip');
                if (cat) cat.textContent = 'Processing';
                if (txt) txt.textContent = 'Extracting OCR...';
                if (tip) tip.classList.add('visible');
            })();
            """
        )

        # Show LoadingDialog immediately
        loading_dialog = LoadingDialog(
            title="Processing... Please wait",
            subtitle="🔍 Extracting text with AI... Please wait",
            parent=self,
        )
        loading_dialog.show()

        worker = OCRWorker(file_paths, self)

        def on_success(extracted_text: str) -> None:
            loading_dialog.close()
            self._is_processing = False
            if worker in self._ocr_workers:
                self._ocr_workers.remove(worker)

            self.page().runJavaScript(
                """
                (function() {
                    window.isProcessing = false;
                    const cat = document.getElementById('tooltip-category');
                    const txt = document.getElementById('tooltip-text');
                    const tip = document.getElementById('tooltip');
                    if (cat) cat.textContent = 'OCR';
                    if (txt) txt.textContent = 'Extracted';
                    if (tip) tip.classList.add('visible', 'active');
                    setTimeout(() => {
                        if (window.clearHover && !window.isProcessing) window.clearHover();
                    }, 1200);
                })();
                """
            )

            dialog = OCRDialog(extracted_text, parent=self)
            dialog.show()
            self._dialogs.append(dialog)
            dialog.finished.connect(
                lambda _, d=dialog: self._dialogs.remove(d)
                if d in self._dialogs
                else None
            )

        def on_failure(err_msg: str) -> None:
            loading_dialog.close()
            self._is_processing = False
            if worker in self._ocr_workers:
                self._ocr_workers.remove(worker)

            self.page().runJavaScript(
                """
                (function() {
                    window.isProcessing = false;
                    const cat = document.getElementById('tooltip-category');
                    const txt = document.getElementById('tooltip-text');
                    const tip = document.getElementById('tooltip');
                    if (cat) cat.textContent = 'Error';
                    if (txt) txt.textContent = 'Failed';
                    setTimeout(() => {
                        if (window.clearHover && !window.isProcessing) window.clearHover();
                    }, 1500);
                })();
                """
            )

            dialog = OCRDialog(err_msg, parent=self)
            dialog.show()
            self._dialogs.append(dialog)
            dialog.finished.connect(
                lambda _, d=dialog: self._dialogs.remove(d)
                if d in self._dialogs
                else None
            )

        worker.extracted.connect(on_success)
        worker.failed.connect(on_failure)
        self._ocr_workers.append(worker)
        worker.start()

    # ── Click-to-Execute Utilities (SCREENSHOT / TERMINAL) ─────────

    def _on_title_changed(self, title: str) -> None:
        """Handle IPC communication from WebEngine via document.title."""
        if title.startswith("quickdrop:action:"):
            parts = title.split(":")
            if len(parts) >= 3:
                action_name = parts[2].strip().upper()
                self._handle_action_click(action_name)

    def _handle_action_click(self, action_name: str) -> None:
        """Execute click-triggered actions without requiring file drop."""
        now = time.time()
        if now - getattr(self, "_last_click_time", 0) < 0.5:
            return
        self._last_click_time = now
        act = action_name.strip().upper()
        if act == "SCREENSHOT":
            self._execute_screenshot()
        elif act == "TERMINAL":
            self._execute_terminal()

    def _execute_screenshot(self) -> None:
        """Cross-platform screenshot utility triggering with automatic window hide/restore."""
        self.window().hide()

        def capture_and_restore():
            try:
                if sys.platform == "win32":
                    # Windows 10/11 interactive snipping tool
                    subprocess.Popen("start ms-screenclip:", shell=True)
                elif sys.platform == "darwin":
                    # macOS interactive capture
                    subprocess.run(["screencapture", "-i", "-c"])
                else:
                    # Linux: Native Wayland Freedesktop Portal (Bypasses Wayland security restrictions completely)
                    try:
                        subprocess.run([
                            "dbus-send", "--session", "--print-reply",
                            "--dest=org.freedesktop.portal.Desktop",
                            "/org/freedesktop/portal/desktop",
                            "org.freedesktop.portal.Screenshot.Screenshot",
                            "string:", "dict:string:variant:interactive,boolean:true"
                        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        # Ultimate fallback for non-Wayland or older systems
                        if shutil.which("gnome-screenshot"):
                            subprocess.run(["gnome-screenshot", "-a"])
                        elif shutil.which("xfce4-screenshooter"):
                            subprocess.run(["xfce4-screenshooter", "-r"])
            except Exception as e:
                print(f"[QuickDrop] Screenshot error: {e}")
            finally:
                self.window().show()

        # Let the window fully hide (300ms) before opening the OS screenshot tool
        QTimer.singleShot(300, capture_and_restore)

    def _execute_terminal(self, dir_path: str | None = None) -> None:
        """Universal cross-platform terminal launcher defaulting to home directory if no file dropped."""
        if not dir_path:
            dir_path = os.path.expanduser("~")
        else:
            abs_path = os.path.abspath(dir_path)
            dir_path = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)

        if sys.platform == "win32":
            # Windows: Open CMD in the target directory
            subprocess.Popen(["cmd", "/K", f'cd /d "{dir_path}"'])

        elif sys.platform == "darwin":
            # macOS: Open default Terminal app in the target directory
            subprocess.Popen(["open", "-a", "Terminal", dir_path])

        else:
            # Linux: The nuclear option for stubborn daemons like gnome-terminal
            bash_cmd = f'cd "{dir_path}" && exec bash'
            if shutil.which("gnome-terminal"):
                subprocess.Popen(['gnome-terminal', '--', 'bash', '-c', bash_cmd])
            elif shutil.which("kgx"):
                subprocess.Popen(['kgx', '-e', f'bash -c \'{bash_cmd}\''], shell=True)
            elif shutil.which("konsole"):
                subprocess.Popen(['konsole', '-e', 'bash', '-c', bash_cmd])
            elif shutil.which("ptyxis"):
                subprocess.Popen(['ptyxis', '--', 'bash', '-c', bash_cmd])
            else:
                subprocess.Popen(f'x-terminal-emulator -e "bash -c \\"{bash_cmd}\\""', shell=True)

    # ── Desktop Interactivity & Mouse Hover Tracking ───────────────

    def _install_event_filters(self) -> None:
        """Install event filter on self and internal child widgets to enable window dragging and block zoom."""
        self.installEventFilter(self)
        if self.focusProxy():
            self.focusProxy().installEventFilter(self)
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

    def childEvent(self, event) -> None:  # noqa: N802
        super().childEvent(event)
        if event.type() == QEvent.Type.ChildAdded and event.child().isWidgetType():
            event.child().installEventFilter(self)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._install_event_filters()

    def _on_page_loaded(self, ok: bool) -> None:
        """Lock zoom strictly to 1.0 and prevent web-level gesture/wheel zoom."""
        self.setZoomFactor(1.0)
        self.page().runJavaScript(
            """
            window.addEventListener('wheel', (e) => {
                if (e.ctrlKey) {
                    e.preventDefault();
                }
            }, { passive: false });
            window.addEventListener('gesturestart', (e) => e.preventDefault());
            window.addEventListener('gesturechange', (e) => e.preventDefault());
            window.addEventListener('gestureend', (e) => e.preventDefault());
            """
        )

    def event(self, e) -> bool:  # noqa: N802
        """Intercept and block trackpad native pinch gestures."""
        if e.type() == QEvent.Type.NativeGesture or e.type() == QEvent.Type.Gesture:
            return True
        return super().event(e)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        """Intercept gestures, mouse, and wheel events across all child widgets (including WebEngine's render widget)."""
        if event.type() == QEvent.Type.NativeGesture or event.type() == QEvent.Type.Gesture:
            return True
        if event.type() == QEvent.Type.Wheel:
            # Block Ctrl + Mouse Wheel zooming to keep scale strictly fixed
            if event.modifiers() == Qt.KeyboardModifier.ControlModifier or (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                event.ignore()
                return True
        elif event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_start_pos = (
                    event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
                )
                self._press_global_pos = event.globalPosition().toPoint()
                self._is_dragging_window = False
        elif event.type() == QEvent.Type.MouseMove:
            if (
                event.buttons() == Qt.MouseButton.LeftButton
                and self._drag_start_pos is not None
            ):
                if not getattr(self, "_is_dragging_window", False):
                    press_pos = getattr(self, "_press_global_pos", event.globalPosition().toPoint())
                    if (event.globalPosition().toPoint() - press_pos).manhattanLength() > 5:
                        self._is_dragging_window = True
                if getattr(self, "_is_dragging_window", False):
                    self.window().move(event.globalPosition().toPoint() - self._drag_start_pos)
        elif event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                was_dragging = getattr(self, "_is_dragging_window", False)
                self._drag_start_pos = None
                self._is_dragging_window = False
                if not was_dragging:
                    def on_action(action: str | None) -> None:
                        if action:
                            act = action.strip().upper()
                            if act in ("SCREENSHOT", "TERMINAL"):
                                self._handle_action_click(act)
                    self.page().runJavaScript("window.currentHoveredAction", on_action)
        elif event.type() == QEvent.Type.KeyPress:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if event.key() in (
                    Qt.Key.Key_Plus,
                    Qt.Key.Key_Minus,
                    Qt.Key.Key_Equal,
                    Qt.Key.Key_0,
                ):
                    event.ignore()
                    return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Disable Ctrl + Mouse Wheel zoom to keep circular UI scale strictly fixed."""
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier or (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            event.ignore()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """Record drag start position and pass event to super() so regular web clicks still work."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
            self._press_global_pos = event.globalPosition().toPoint()
            self._is_dragging_window = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        """Move top-level window on drag and pass event to super() so web clicks/hovers work."""
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
        ):
            if not getattr(self, "_is_dragging_window", False):
                press_pos = getattr(self, "_press_global_pos", event.globalPosition().toPoint())
                if (event.globalPosition().toPoint() - press_pos).manhattanLength() > 5:
                    self._is_dragging_window = True
            if getattr(self, "_is_dragging_window", False):
                self.window().move(event.globalPosition().toPoint() - self._drag_start_pos)
        else:
            if not getattr(self, "_is_processing", False):
                pos = event.position()
                self.page().runJavaScript(
                    f"window.updateHover({pos.x()}, {pos.y()});"
                )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        was_dragging = getattr(self, "_is_dragging_window", False)
        self._drag_start_pos = None
        self._is_dragging_window = False
        super().mouseReleaseEvent(event)
        if not was_dragging and event.button() == Qt.MouseButton.LeftButton:
            def on_action(action: str | None) -> None:
                if action:
                    act = action.strip().upper()
                    if act in ("SCREENSHOT", "TERMINAL"):
                        self._handle_action_click(act)
            self.page().runJavaScript("window.currentHoveredAction", on_action)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not getattr(self, "_is_processing", False):
            self.page().runJavaScript("window.clearHover();")
        super().leaveEvent(event)

    # ── Keyboard Tracking: Dynamic Switching ───────────────────────

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
            return

        # Disable keyboard zoom shortcuts (Ctrl + / Ctrl - / Ctrl 0)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() in (
                Qt.Key.Key_Plus,
                Qt.Key.Key_Minus,
                Qt.Key.Key_Equal,
                Qt.Key.Key_0,
            ):
                event.ignore()
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
