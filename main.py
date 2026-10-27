import atexit
import os
import sys

# Force X11 compatibility on Linux to bypass Wayland capture block and guarantee StaysOnTop & cursor tracking
if sys.platform.startswith('linux'):
    os.environ["QT_QPA_PLATFORM"] = "xcb"

import ast
import http.server
import io
import json
import math
import mimetypes
import operator as _py_op
import random
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import zipfile

import qrcode
import requests
from dotenv import load_dotenv

# Load environment variables (.env) from current directory, script directory, or repo root
_script_dir = os.path.dirname(os.path.abspath(__file__))
for _env_candidate in [
    os.path.join(os.getcwd(), ".env"),
    os.path.join(_script_dir, ".env"),
    os.path.join(os.path.dirname(_script_dir), ".env"),
]:
    if os.path.isfile(_env_candidate):
        load_dotenv(_env_candidate)
        break
else:
    load_dotenv()

try:
    from PyQt5.QtGui import QCursor
except ImportError:
    import PyQt6.QtGui as _gui
    import PyQt6.QtCore as _core
    import PyQt6.QtWidgets as _widgets
    import types
    _p5 = types.ModuleType("PyQt5")
    _p5.QtGui = _gui
    _p5.QtCore = _core
    _p5.QtWidgets = _widgets
    sys.modules["PyQt5"] = _p5
    sys.modules["PyQt5.QtGui"] = _gui
    sys.modules["PyQt5.QtCore"] = _core
    sys.modules["PyQt5.QtWidgets"] = _widgets
    from PyQt5.QtGui import QCursor

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError
from PIL.ExifTags import GPSTAGS, TAGS
import pillow_heif

pillow_heif.register_heif_opener()
import pillow_avif
from rembg import remove

from PyQt6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
import PyQt6.QtCore as QtCore
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtGui import QBrush, QClipboard, QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings

# Compatibility aliases for Qt flags in PyQt6
for _holder in (QtCore.Qt, Qt):
    if not hasattr(_holder, "WindowStaysOnTopHint"):
        setattr(_holder, "WindowStaysOnTopHint", Qt.WindowType.WindowStaysOnTopHint)
    if not hasattr(_holder, "Tool"):
        setattr(_holder, "Tool", Qt.WindowType.Tool)
    if not hasattr(_holder, "FramelessWindowHint"):
        setattr(_holder, "FramelessWindowHint", Qt.WindowType.FramelessWindowHint)


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
    """Background worker thread for asynchronous bounding-box text extraction with Max-Channel preprocessing."""

    extracted = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, file_paths: list[str] | str, parent=None) -> None:
        super().__init__(parent)
        if isinstance(file_paths, str):
            self.file_paths = [file_paths]
        else:
            self.file_paths = list(file_paths)

    @staticmethod
    def init_engines():
        """Initialize RapidOCR or EasyOCR engine safely."""
        ocr_engine = None
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr_engine = RapidOCR()
        except Exception:
            ocr_engine = None

        easyocr_reader = None
        if ocr_engine is None:
            try:
                import easyocr
                easyocr_reader = easyocr.Reader(["en"])
            except Exception:
                easyocr_reader = None

        return ocr_engine, easyocr_reader

    @staticmethod
    def extract_from_image(path: str, ocr_engine=None, easyocr_reader=None) -> str:
        """Run Max-Channel dark-mode preprocessing, line clustering, and text extraction on an image."""
        import cv2
        import numpy as np

        # 1. Load image safely
        img_cv = cv2.imread(path)
        if img_cv is None:
            try:
                img_cv = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            except Exception:
                img_cv = None

        if img_cv is None:
            try:
                from PIL import Image
                with Image.open(path) as pil_img:
                    img_cv = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2BGR)
            except Exception:
                img_cv = None

        if img_cv is None:
            return ""

        # 2. Perfect Dark-Mode Color Extraction (Max-Channel)
        if len(img_cv.shape) == 2:
            b = g = r = img_cv
        else:
            b, g, r = cv2.split(img_cv)

        max_ch = np.maximum(np.maximum(b, g), r)
        min_ch = np.minimum(np.minimum(b, g), r)

        if np.mean(max_ch) < 128:  # Dark mode detected
            fg = cv2.subtract(max_ch, min_ch // 2)
            fg = cv2.normalize(fg, None, 0, 255, cv2.NORM_MINMAX)
            gray = 255 - fg  # Invert to black text on white background
        else:
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY) if len(img_cv.shape) == 3 else img_cv

        # 3. Upscale 2x using cv2.INTER_CUBIC if height < 1200px
        h, w = gray.shape[:2]
        if h < 1200:
            gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

        # 4. Add generous 40px padding so edge symbols like '{' are never cropped
        gray = cv2.copyMakeBorder(gray, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)

        # 5. Extract bounding boxes using RapidOCR or EasyOCR
        result = None
        if ocr_engine is not None:
            try:
                res, _ = ocr_engine(gray)
                if res:
                    result = res
            except Exception:
                result = None

        if result is None and easyocr_reader is not None:
            try:
                res = easyocr_reader.readtext(gray, detail=1, width_ths=1.0)
                if res:
                    result = res
            except Exception:
                result = None

        if not result:
            return ""

        items = []
        for item in result:
            box, text_chunk = item[0], str(item[1])
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
                "w": max(1.0, x_max - x_min),
            })

        avg_box_h = (sum(it["h"] for it in items) / len(items)) if items else 20.0

        items.sort(key=lambda b: (b["y_center"], b["x_min"]))
        lines = []
        for b in items:
            matched_line = None
            best_dist = float("inf")
            for line in lines:
                center_dist = abs(b["y_center"] - line["y_center"])
                if center_dist <= 0.6 * avg_box_h:
                    if center_dist < best_dist:
                        best_dist = center_dist
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

        merged_lines = []
        for line in lines:
            merged = False
            for m in merged_lines:
                if abs(line["y_center"] - m["y_center"]) <= 0.6 * avg_box_h:
                    m["boxes"].extend(line["boxes"])
                    m["y_min"] = min(m["y_min"], line["y_min"])
                    m["y_max"] = max(m["y_max"], line["y_max"])
                    m["y_center"] = sum(x["y_center"] for x in m["boxes"]) / len(m["boxes"])
                    merged = True
                    break
            if not merged:
                merged_lines.append(line)
        lines = merged_lines

        lines.sort(key=lambda l: l["y_min"])
        for line in lines:
            line["boxes"].sort(key=lambda b: b["x_min"])

        all_boxes = [b for line in lines for b in line["boxes"]]
        global_min_x = min(b["x_min"] for b in all_boxes) if all_boxes else 0.0
        total_box_width = sum(b["w"] for b in all_boxes)
        total_chars = sum(len(b["text"]) for b in all_boxes)
        char_w = (total_box_width / total_chars) if total_chars > 0 else 10.0
        char_w = max(3.0, char_w)

        line_heights = [max(1.0, line["y_max"] - line["y_min"]) for line in lines]
        avg_line_height = (sum(line_heights) / len(line_heights)) if line_heights else 20.0

        output_parts = []
        prev_line = None
        for line in lines:
            if not line["boxes"]:
                continue

            first_box = line["boxes"][0]
            line_first_box_x = first_box["x_min"]
            leading_px = line_first_box_x - global_min_x
            indent_spaces = int(round(leading_px / char_w)) if leading_px > char_w * 0.8 else 0
            line_str = " " * indent_spaces

            prev_box_x_max = None
            prev_text = ""
            for b in line["boxes"]:
                box_text = b["text"]
                if prev_box_x_max is not None:
                    gap = b["x_min"] - prev_box_x_max
                    if (
                        prev_text.endswith("-")
                        or prev_text.endswith(".")
                        or box_text.startswith(".")
                        or gap < char_w * 0.4
                    ):
                        line_str += box_text
                    else:
                        line_str += " " + box_text
                else:
                    line_str += box_text
                prev_box_x_max = b["x_max"]
                prev_text = box_text

            line_str = line_str.rstrip()

            if prev_line is not None:
                vert_gap = line["y_min"] - prev_line["y_max"]
                if vert_gap > 1.8 * avg_line_height:
                    output_parts.append("\n\n")
                else:
                    output_parts.append("\n")

            output_parts.append(line_str)
            prev_line = line

        return "".join(output_parts)

    def run(self) -> None:
        try:
            ocr_engine, easyocr_reader = OCRWorker.init_engines()
            if ocr_engine is None and easyocr_reader is None:
                self.failed.emit("Missing library. Please run: pip install rapidocr-onnxruntime easyocr")
                return

            extracted_text = ""
            for path in self.file_paths:
                text = OCRWorker.extract_from_image(path, ocr_engine, easyocr_reader)
                if len(self.file_paths) > 1:
                    extracted_text += f"--- {os.path.basename(path)} ---\n{text}\n\n"
                else:
                    extracted_text += f"{text}\n\n"

            extracted_text = extracted_text.strip()
            if not extracted_text:
                extracted_text = "No text found in the image."
            self.extracted.emit(extracted_text)
        except ImportError as e:
            self.failed.emit(f"Missing library: {e}. Please run: pip install rapidocr-onnxruntime easyocr")
        except Exception as e:
            self.failed.emit(f"OCR Error: {e}")


class DocExportWorker(QThread):
    """Background worker thread for fast file extension change (.docx or .txt) via shutil.copy."""

    file_saved = pyqtSignal(str, str)
    finished_all = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, file_paths: list[str] | str, target_format: str, parent=None) -> None:
        super().__init__(parent)
        if isinstance(file_paths, str):
            self.file_paths = [file_paths]
        else:
            self.file_paths = list(file_paths)
        self.target_format = target_format.strip().upper()

    def run(self) -> None:
        try:
            time.sleep(0.35)  # Brief delay to allow LoadingDialog to appear smoothly
            saved_files = []
            for path in self.file_paths:
                abs_path = os.path.abspath(path)
                if not os.path.isfile(abs_path):
                    self.failed.emit(f"File not found: {path}")
                    return

                dir_name = os.path.dirname(abs_path)
                base_name = os.path.splitext(os.path.basename(abs_path))[0]
                ext = f".{self.target_format.lower()}"
                out_path = os.path.join(dir_name, f"{base_name}{ext}")

                if os.path.abspath(out_path) == abs_path:
                    out_path = os.path.join(dir_name, f"{base_name}_copy{ext}")

                shutil.copy(abs_path, out_path)
                saved_files.append(out_path)
                self.file_saved.emit(abs_path, out_path)

            self.finished_all.emit(saved_files)
        except Exception as e:
            self.failed.emit(f"File Copy Error: {e}")




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


def get_unique_filename(target_path: str) -> str:
    """
    Returns a unique file path to prevent overwriting existing files.
    If target_path exists (e.g. 'file_compressed.ext'), returns
    'file_compressed_1.ext', 'file_compressed_2.ext', etc.
    NEVER overwrites existing files.
    """
    if not os.path.exists(target_path):
        return target_path

    directory = os.path.dirname(target_path)
    base_name, ext = os.path.splitext(os.path.basename(target_path))

    counter = 1
    while True:
        candidate = os.path.join(directory, f"{base_name}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _enforce_compression_sanity_check(orig_path: str, output_path: str) -> None:
    """
    The Ultimate Sanity Check (CRITICAL FAIL-SAFE):
    Compare os.path.getsize(output_path) with original size.
    If new file >= original: delete output_path immediately via os.remove(output_path)
    and return error: 'File is already highly compressed. Further compression would increase size.'
    """
    if not os.path.exists(output_path):
        raise RuntimeError("Compression failed: Output file was not generated.")
    orig_size = os.path.getsize(orig_path) if os.path.exists(orig_path) else 0
    new_size = os.path.getsize(output_path)
    if orig_size > 0 and new_size >= orig_size:
        try:
            os.remove(output_path)
        except OSError:
            pass
        raise ValueError("File is already highly compressed. Further compression would increase size.")


class CompressionWorker(QThread):
    """Background worker thread for asynchronous media & document compression."""

    started_processing = pyqtSignal()
    compression_success = pyqtSignal(str)  # output_path
    compression_failed = pyqtSignal(str)   # error_message

    def __init__(
        self,
        file_path: str,
        output_path: str,
        category: str,
        slider_val: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self.output_path = output_path
        self.category = category
        self.slider_val = slider_val

    def run(self) -> None:
        self.started_processing.emit()
        try:
            if self.category == "document":
                out = CompressDialog._do_compress_document(self.file_path, self.output_path, self.slider_val)
            elif self.category == "video":
                res = CompressDialog._do_compress_video(self.file_path, self.output_path, self.slider_val)
                if isinstance(res, dict) and res.get("status") == "error":
                    raise RuntimeError(res.get("message", "FFmpeg crashed. Check terminal logs."))
                out = self.output_path
            elif self.category == "pdf":
                CompressDialog._do_compress_pdf(self.file_path, self.output_path, self.slider_val)
                out = self.output_path
            elif self.category == "image":
                CompressDialog._do_compress_image(self.file_path, self.output_path, self.slider_val)
                out = self.output_path
            else:
                raise ValueError("Sorry, this file type is not supported for compression.")

            # The Ultimate Sanity Check (CRITICAL FAIL-SAFE)
            _enforce_compression_sanity_check(self.file_path, out)

            self.compression_success.emit(out)
        except Exception as exc:
            self.compression_failed.emit(str(exc))


class CompressDialog(QDialog):
    """Frameless light translucent frosted glass compression dialog supporting images, videos, PDFs, and documents."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None
        self._worker = None

        # Calculate original file size in bytes
        self._original_size = (
            os.path.getsize(self.file_path) if os.path.exists(self.file_path) else 0
        )

        # Strictly categorize file type into 'image', 'video', 'pdf', 'document', or 'unsupported'
        self._mime_category, self._mime_type, self._warning_threshold = self.detect_category(
            self.file_path
        )

        # 1. UI/Design: Frameless window, rounded corners with transparent window frame
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(380, 260)

        self._setup_ui()
        self._center_on_screen()

    @staticmethod
    def detect_category(path: str) -> tuple[str, str, int]:
        """
        Strictly categorizes file type into:
          - 'image'       -> Pillow (threshold 60%)
          - 'video'       -> FFmpeg (threshold 75%)
          - 'pdf'         -> PyMuPDF / Ghostscript (threshold 50%)
          - 'document'    -> Standard zip archive (threshold 50%)
          - 'unsupported' -> Rejection error
        """
        ext = os.path.splitext(path)[1].lower()
        mime, _ = mimetypes.guess_type(path)
        mime = (mime or "").lower()

        # 1. PDF Documents
        if ext == ".pdf" or mime == "application/pdf":
            return ("pdf", "application/pdf", 50)

        # 2. Video Files
        video_exts = {
            ".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".3gp"
        }
        if ext in video_exts or mime.startswith("video/"):
            return ("video", mime or f"video/{ext.lstrip('.')}", 75)

        # 3. Image Files
        image_exts = {
            ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".heic", ".avif", ".gif", ".ico"
        }
        if ext in image_exts or mime.startswith("image/"):
            return ("image", mime or f"image/{ext.lstrip('.')}", 60)

        # 4. Standard Documents (like .docx, .txt, .xlsx, .pptx, etc.)
        doc_exts = {
            ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".csv",
            ".rtf", ".odt", ".ods", ".odp", ".json", ".xml", ".md", ".log",
            ".yaml", ".yml", ".tsv"
        }
        if ext in doc_exts or (mime and (
            "officedocument" in mime
            or "msword" in mime
            or "excel" in mime
            or "powerpoint" in mime
            or "opendocument" in mime
            or mime.startswith("text/")
        )):
            return ("document", mime or "application/zip", 50)

        # 5. Unsupported Files (apps, executables, unknown binaries/extensions)
        return ("unsupported", mime or "application/octet-stream", 0)

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        # Light/translucent milky background (#f7f7f7) with rounded corners and subtle border
        self.container = QFrame()
        self.container.setObjectName("compressContainer")
        self.container.setStyleSheet("""
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

        layout = QVBoxLayout(self.container)
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
        layout.addWidget(self.est_size_label)

        # Smart Quality Warning (Subtle Tangerine Orange text, hidden by default)
        self.warning_label = QLabel("Warning: High compression may significantly degrade quality.")
        self.warning_label.setStyleSheet("color: #FF7A00; font-size: 11px; font-weight: 600; margin-top: 1px;")
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        # Graceful UI Error Label (Red text with soft background)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("""
            color: #d32f2f;
            font-size: 11.5px;
            font-weight: 600;
            background: rgba(255, 59, 48, 0.08);
            border: 1px solid rgba(255, 59, 48, 0.22);
            border-radius: 6px;
            padding: 5px 8px;
        """)
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self._on_slider_changed(50)
        self.slider.valueChanged.connect(self._on_slider_changed)

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

        self.compress_btn = QPushButton("Compress")
        self.compress_btn.setStyleSheet("""
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
            QPushButton:disabled {
                background-color: rgba(0, 0, 0, 0.15);
                color: rgba(0, 0, 0, 0.35);
            }
        """)
        self.compress_btn.clicked.connect(self._on_compress)
        btn_row.addWidget(self.compress_btn)

        layout.addLayout(btn_row)

        # Glass-morphism loading overlay inside modal with spinner (Strictly English)
        self.loading_overlay = QFrame(self.container)
        self.loading_overlay.setObjectName("compressLoadingOverlay")
        self.loading_overlay.setStyleSheet("""
            #compressLoadingOverlay {
                background-color: rgba(247, 247, 247, 0.94);
                border-radius: 16px;
            }
            QLabel {
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                font-size: 13px;
                font-weight: 700;
            }
            QProgressBar {
                background: rgba(0, 0, 0, 0.08);
                border-radius: 3px;
                height: 6px;
                border: none;
            }
            QProgressBar::chunk {
                background: #ff5500;
                border-radius: 3px;
            }
        """)
        overlay_layout = QVBoxLayout(self.loading_overlay)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.setSpacing(12)

        loading_text = QLabel("Processing...")
        loading_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(loading_text)

        prog_bar = QProgressBar()
        prog_bar.setRange(0, 0)  # indeterminate animation
        prog_bar.setTextVisible(False)
        prog_bar.setFixedWidth(200)
        overlay_layout.addWidget(prog_bar, alignment=Qt.AlignmentFlag.AlignCenter)

        self.loading_overlay.hide()

        outer_layout.addWidget(self.container)

        # Check for unsupported file type or document category on init
        if self._mime_category == "unsupported":
            self.error_label.setText("Sorry, this file type is not supported for compression.")
            self.error_label.show()
            self.compress_btn.setEnabled(False)
            self.slider.setEnabled(False)
            self.est_size_label.setText("Unsupported file format")
            self.est_size_label.setStyleSheet("font-size: 12px; color: #d32f2f;")
        elif self._mime_category == "document":
            ext = os.path.splitext(self.file_path)[1].lower()
            if ext in (".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm"):
                self.est_size_label.setText(f"Deep Office media compression ({ext})")
            else:
                self.est_size_label.setText("Will be compressed into a standard .zip archive")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "loading_overlay") and hasattr(self, "container"):
            self.loading_overlay.setGeometry(self.container.rect())

    def _on_slider_changed(self, value: int) -> None:
        """Update Reduce by and estimated size: original_size_kb * ((100 - slider_value) / 100)."""
        self.reduce_label.setText(f"Reduce by: {value}%")
        if self._mime_category == "document":
            ext = os.path.splitext(self.file_path)[1].lower()
            if ext in (".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm"):
                orig_kb = self._original_size / 1024.0
                est_kb = orig_kb * ((100 - value) / 100.0)
                if est_kb >= 1024.0:
                    est_mb = est_kb / 1024.0
                    self.est_size_label.setText(f"Output will be at most {est_mb:.2f} MB (~{est_kb:.0f} KB)")
                else:
                    self.est_size_label.setText(f"Output will be at most {est_kb:.1f} KB")
            else:
                self.est_size_label.setText("Will be compressed into a standard .zip archive")
        else:
            orig_kb = self._original_size / 1024.0
            est_kb = orig_kb * ((100 - value) / 100.0)
            if est_kb >= 1024.0:
                est_mb = est_kb / 1024.0
                self.est_size_label.setText(f"Output will be at most {est_mb:.2f} MB (~{est_kb:.0f} KB)")
            else:
                self.est_size_label.setText(f"Output will be at most {est_kb:.1f} KB")

        # Smart Quality Warning dynamic visibility check based on MIME threshold
        if hasattr(self, "warning_label"):
            if self._mime_category != "unsupported" and value > self._warning_threshold:
                self.warning_label.show()
            else:
                self.warning_label.hide()

    def _center_on_screen(self) -> None:
        """Center the dialog on the primary display."""
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    @staticmethod
    def _do_compress_document(input_path: str, output_path: str = "", slider_val: int = 50) -> str:
        """
        Deep Office Document Compression (.docx, .pptx, .xlsx, .docm, .pptm, .xlsm):
          - Extracts into tempfile via zipfile.
          - Locates internal media folders (word/media/, ppt/media/, xl/media/).
          - Compresses internal images with Pillow mapped to reduce_by.
          - Re-archives back into the original extension using zipfile.ZIP_DEFLATED (compresslevel=9).
        Standard Documents (.txt, .csv, etc.):
          - Archives into a standard .zip archive using compresslevel=9.
        """
        dir_name = os.path.dirname(input_path)
        base_name, ext = os.path.splitext(os.path.basename(input_path))
        ext_lower = ext.lower()
        office_exts = {".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm"}

        if not output_path:
            if ext_lower in office_exts:
                target_initial = os.path.join(dir_name, f"{base_name}_compressed{ext}")
            else:
                target_initial = os.path.join(dir_name, f"{base_name}_compressed.zip")
            output_path = get_unique_filename(target_initial)
        else:
            output_path = get_unique_filename(output_path)

        if ext_lower in office_exts and zipfile.is_zipfile(input_path):
            temp_dir = tempfile.mkdtemp(prefix="quickdrop_office_")
            try:
                with zipfile.ZipFile(input_path, "r") as zip_in:
                    zip_in.extractall(temp_dir)

                target_quality = max(10, 100 - slider_val)

                # Locate internal media folders (word/media, ppt/media, xl/media)
                media_dirs = []
                for root, dirs, _ in os.walk(temp_dir):
                    if os.path.basename(root).lower() == "media":
                        media_dirs.append(root)

                image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

                for m_dir in media_dirs:
                    for img_name in os.listdir(m_dir):
                        img_path = os.path.join(m_dir, img_name)
                        if not os.path.isfile(img_path):
                            continue
                        img_ext = os.path.splitext(img_name)[1].lower()
                        if img_ext not in image_exts:
                            continue

                        try:
                            with Image.open(img_path) as pil_img:
                                if img_ext in (".jpg", ".jpeg"):
                                    if pil_img.mode not in ("RGB", "L"):
                                        pil_img = pil_img.convert("RGB")
                                    pil_img.save(img_path, format="JPEG", quality=target_quality, optimize=True)
                                elif img_ext == ".png":
                                    if slider_val >= 40 or pil_img.mode in ("RGBA", "LA", "P"):
                                        num_colors = max(16, min(256, int(256 * ((100 - slider_val) / 100.0))))
                                        quantized = pil_img.quantize(colors=num_colors)
                                        quantized.save(img_path, format="PNG", optimize=True, compress_level=9)
                                    else:
                                        pil_img.save(img_path, format="PNG", optimize=True, compress_level=9)
                                elif img_ext == ".webp":
                                    pil_img.save(img_path, format="WEBP", quality=target_quality, method=6)
                                else:
                                    pil_img.save(img_path, optimize=True)
                        except Exception as img_err:
                            print(f"[QuickDrop] Non-critical image compression error in Office doc: {img_err}")

                # Re-archive back into the original extension (.docx, .pptx, .xlsx)
                with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip_out:
                    for root, _, files in os.walk(temp_dir):
                        for file in files:
                            full_path = os.path.join(root, file)
                            arcname = os.path.relpath(full_path, temp_dir)
                            zip_out.write(full_path, arcname=arcname)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
                zipf.write(input_path, arcname=os.path.basename(input_path))

        return output_path

    @staticmethod
    def _do_compress_image(input_path: str, output_path: str, slider_val: int) -> None:
        """
        Pillow-based image compression strictly mapped to slider percentage:
          - Target quality: quality = max(10, 100 - slider_val)
        """
        ext_lower = os.path.splitext(input_path)[1].lower()
        target_quality = max(10, 100 - slider_val)

        with Image.open(input_path) as img:
            # Preserve transparency for PNG / RGBA
            if ext_lower == ".png" or img.mode in ("RGBA", "LA", "PA") or ("transparency" in img.info):
                if slider_val >= 40:
                    num_colors = max(8, min(256, int(256 * ((100 - slider_val) / 100.0))))
                    quantized = img.quantize(colors=num_colors)
                    quantized.save(output_path, "PNG", optimize=True, compress_level=9)
                else:
                    img.save(output_path, "PNG", optimize=True, compress_level=9)
            elif ext_lower in (".jpg", ".jpeg"):
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(output_path, "JPEG", quality=target_quality, optimize=True)
            elif ext_lower == ".webp":
                img.save(output_path, "WEBP", quality=target_quality, method=6)
            else:
                fmt = img.format or "PNG"
                if fmt.upper() in ("JPEG", "JPG", "BMP"):
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    img.save(output_path, format=fmt, quality=target_quality)
                else:
                    img.save(output_path, format=fmt, optimize=True)

    @staticmethod
    def _do_compress_video(input_path: str, output_path: str, slider_val: int) -> dict:
        """
        Compress video using real FFmpeg re-encoding.
        Strictly re-encodes using libx264 with dynamic CRF mapped from slider_val,
        and forces AAC audio at 128k to avoid container codec crashes.
        Prints full command and raw FFmpeg stderr logs to the terminal for debugging.
        Handles non-zero exit codes and enforces Anti-Corruption sanity check.
        """
        if not shutil.which("ffmpeg"):
            return {"status": "error", "message": "Compression failed. Ensure FFmpeg is installed."}

        # Mathematically map reduce_by percentage to crf_value:
        # Base CRF is 23. E.g., 50% -> 30, 90% -> 40. The size MUST reduce!
        if slider_val <= 50:
            crf_value = int(round(23 + (slider_val / 50.0) * 7))
        else:
            crf_value = int(round(30 + ((slider_val - 50) / 40.0) * 10))
        crf_value = max(18, min(51, int(crf_value)))

        # 4. Fix CRF Type Casting: str(int(crf_value))
        # 3. Bulletproof FFmpeg Command Array: forces AAC audio
        command = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-vcodec",
            "libx264",
            "-crf",
            str(int(crf_value)),
            "-preset",
            "fast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            output_path,
        ]

        # 1. Expose the Exact Command to terminal
        print(f"[QuickDrop DEBUG] FFMPEG COMMAND: {' '.join(command)}")

        kwargs = {
            "capture_output": True,
            "text": True,
        }
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo

        # 2. Capture and Force-Print ALL FFmpeg Logs (without check=True)
        try:
            result = subprocess.run(command, **kwargs)
        except Exception as exc:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            print(f"[QuickDrop DEBUG] SUBPROCESS EXCEPTION: {exc}")
            return {"status": "error", "message": "FFmpeg crashed. Check terminal logs."}

        # IMMEDIATELY after subprocess finishes, force print the stderr
        print(f"[QuickDrop DEBUG] FFMPEG LOGS:\n{result.stderr}")

        # 3. Handle Non-Zero Exit Codes
        if result.returncode != 0:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return {"status": "error", "message": "FFmpeg crashed. Check terminal logs."}

        # The Relaxed Anti-Corruption Check (empty / tiny file check):
        # If output file does not exist or is less than 10 KB (10240 bytes), consider it corrupted or empty.
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 10240:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return {"status": "error", "message": "Compression failed. Output file is corrupted or empty."}

        return {"status": "success", "output_path": output_path}

    @staticmethod
    def _do_compress_pdf(input_path: str, output_path: str, slider_val: int = 50) -> None:
        """
        Compress PDF natively using PyMuPDF (fitz) native stream compression.
        Do NOT rasterize PDF pages into images.
        STRICTLY use PyMuPDF (fitz) native flags: doc.save(output_path, garbage=4, deflate=True, clean=True).
        """
        try:
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz
            doc = fitz.open(input_path)
            doc.save(output_path, garbage=4, deflate=True, clean=True)
            doc.close()
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return
        except Exception as fitz_err:
            print(f"[QuickDrop] PyMuPDF native deflation failed: {fitz_err}")

        # Tier 2: Ghostscript fallback if PyMuPDF fails or is missing
        gs_bin = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
        if gs_bin:
            target_dpi = int(300 - (slider_val / 100.0) * 228)
            pdf_setting = "/screen" if slider_val >= 70 else ("/ebook" if slider_val >= 40 else "/printer")
            gs_cmd = [
                gs_bin,
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                f"-dPDFSETTINGS={pdf_setting}",
                "-dColorImageDownsampleType=/Bicubic",
                f"-dColorImageResolution={target_dpi}",
                "-dGrayImageDownsampleType=/Bicubic",
                f"-dGrayImageResolution={target_dpi}",
                "-dMonoImageDownsampleType=/Bicubic",
                f"-dMonoImageResolution={target_dpi}",
                "-dNOPAUSE",
                "-dQUIET",
                "-dBATCH",
                f"-sOutputFile={output_path}",
                input_path,
            ]
            try:
                proc = subprocess.run(
                    gs_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return
            except Exception as gs_err:
                print(f"[QuickDrop] Ghostscript execution failed: {gs_err}")

        # Tier 3: PyPDF2 fallback
        try:
            from PyPDF2 import PdfReader, PdfWriter
            reader = PdfReader(input_path)
            writer = PdfWriter()
            for page in reader.pages:
                page.compress_content_streams()
                writer.add_page(page)
            with open(output_path, "wb") as f_out:
                writer.write(f_out)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return
        except Exception as pypdf_err:
            print(f"[QuickDrop] PyPDF2 compression fallback failed: {pypdf_err}")

        raise RuntimeError("Could not compress the PDF file. No suitable compression engine succeeded.")

    # Convenience aliases
    _compress_image = _do_compress_image
    _compress_video = _do_compress_video
    _compress_pdf = _do_compress_pdf
    _compress_document = _do_compress_document

    def _on_compress(self) -> None:
        """Asynchronously compress the file according to its detected format with unique output path."""
        if self._mime_category == "unsupported":
            self.error_label.setText("Sorry, this file type is not supported for compression.")
            self.error_label.show()
            return

        slider_val = self.slider.value()
        dir_name = os.path.dirname(self.file_path)
        base_name, ext = os.path.splitext(os.path.basename(self.file_path))
        ext_lower = ext.lower()
        office_exts = {".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm"}

        if ext_lower in office_exts:
            target_initial = os.path.join(dir_name, f"{base_name}_compressed{ext}")
        elif self._mime_category == "document":
            target_initial = os.path.join(dir_name, f"{base_name}_compressed.zip")
        else:
            target_initial = os.path.join(dir_name, f"{base_name}_compressed{ext}")

        # Ensure unique filename to prevent overwriting existing files
        output_path = get_unique_filename(target_initial)

        # Show glass-morphism loading overlay immediately
        self.loading_overlay.setGeometry(self.container.rect())
        self.loading_overlay.show()
        self.loading_overlay.raise_()
        self.error_label.hide()
        self.compress_btn.setEnabled(False)

        # Launch background worker thread for large files
        self._worker = CompressionWorker(
            self.file_path, output_path, self._mime_category, slider_val, self
        )
        self._worker.compression_success.connect(self._on_compression_success)
        self._worker.compression_failed.connect(self._on_compression_failed)
        self._worker.start()

    def _on_compression_success(self, output_path: str) -> None:
        self.loading_overlay.hide()
        self.compress_btn.setEnabled(True)
        print(f"[QuickDrop] Compressed '{self.file_path}' -> '{output_path}'")
        if self.parent() and hasattr(self.parent(), "page"):
            resp = json.dumps({"status": "success", "output_path": output_path, "message": "Compressed successfully."}, ensure_ascii=False)
            self.parent().page().runJavaScript(f"if (window.handleCompressResponse) window.handleCompressResponse({resp});")
        self.accept()

    def _on_compression_failed(self, error_msg: str) -> None:
        self.loading_overlay.hide()
        self.compress_btn.setEnabled(True)
        print(f"[QuickDrop ERROR] Failed to compress '{self.file_path}': {error_msg}")
        self.error_label.setText(error_msg or "Sorry, this file type is not supported for compression.")
        self.error_label.show()
        if self.parent() and hasattr(self.parent(), "page"):
            resp = json.dumps({"status": "error", "message": error_msg or "Sorry, this file type is not supported for compression."}, ensure_ascii=False)
            self.parent().page().runJavaScript(f"if (window.handleCompressResponse) window.handleCompressResponse({resp});")

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


def get_video_duration(file_path: str) -> float:
    """Probes video duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception as e:
        print(f"[QuickDrop] ffprobe duration error for '{file_path}': {e}")
    return 0.0


def format_seconds_to_hhmmss(seconds: float) -> str:
    """Formats seconds to HH:MM:SS string."""
    try:
        s = max(0, int(round(seconds)))
        hrs = s // 3600
        mins = (s % 3600) // 60
        secs = s % 60
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    except Exception:
        return "00:00:00"


class CropDialog(QDialog):
    """Frameless light translucent frosted glass dialog for spatial image cropping and video trimming."""

    @classmethod
    def detect_crop_type(cls, path: str) -> str:
        """
        Classifies file for CROP tool:
        - 'image': png, jpg, jpeg, webp, bmp, tiff, tif, heic, avif, gif, ico
        - 'video': mp4, mov, avi, mkv, webm, flv, wmv, m4v, ts, 3gp
        - 'unsupported': all others (pdf, docx, zip, exe, etc.)
        """
        if not path or not isinstance(path, str):
            return "unsupported"
        ext = os.path.splitext(path)[1].lower()
        mime, _ = mimetypes.guess_type(path)
        mime = (mime or "").lower()

        video_exts = {
            ".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".3gp"
        }
        if ext in video_exts or mime.startswith("video/"):
            return "video"

        image_exts = {
            ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".heic", ".avif", ".gif", ".ico"
        }
        if ext in image_exts or mime.startswith("image/"):
            return "image"

        return "unsupported"

    @classmethod
    def _do_process_video(
        cls,
        input_path: str,
        start_time: str,
        end_time: str,
        output_path: str = "",
        crop_box: tuple[int, int, int, int] | None = None,
    ) -> str:
        """
        Combines Video Trimming and Visual Spatial Cropping in a single pass:
        - If crop_box is provided (x, y, width, height) with width > 0 and height > 0:
            ffmpeg -y -ss [start_time] -to [end_time] -i [input] -vf "crop=[width]:[height]:[x]:[y]" -c:v libx264 -crf 23 -c:a aac [output]
        - If crop_box is None (visual crop toggle-off):
            ffmpeg -y -ss [start_time] -to [end_time] -i [input] -c copy [output]
        Uses get_unique_filename so files like video_cropped_1.mp4 are saved uniquely without overwriting.
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input video file not found: {input_path}")

        if not output_path:
            dir_name = os.path.dirname(input_path)
            base_name, ext = os.path.splitext(os.path.basename(input_path))
            target_initial = os.path.join(dir_name, f"{base_name}_cropped{ext}")
            if os.path.abspath(target_initial) == os.path.abspath(input_path):
                target_initial = os.path.join(dir_name, f"{base_name}_cropped_1{ext}")
            output_path = get_unique_filename(target_initial)

        start_str = str(start_time).strip() or "00:00:00"
        end_str = str(end_time).strip()
        if not end_str:
            dur = get_video_duration(input_path)
            end_str = format_seconds_to_hhmmss(dur) if dur > 0 else "00:00:10"

        has_crop = False
        crop_filter = ""
        if crop_box and len(crop_box) == 4:
            x, y, w, h = crop_box
            w = int(w)
            h = int(h)
            x = max(0, int(x))
            y = max(0, int(y))
            # H.264 requires dimensions to be divisible by 2
            w = w - (w % 2)
            h = h - (h % 2)
            if w > 0 and h > 0:
                has_crop = True
                crop_filter = f"crop={w}:{h}:{x}:{y}"

        if has_crop:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                start_str,
                "-to",
                end_str,
                "-i",
                input_path,
                "-vf",
                crop_filter,
                "-c:v",
                "libx264",
                "-crf",
                "23",
                "-c:a",
                "aac",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                start_str,
                "-to",
                end_str,
                "-i",
                input_path,
                "-c",
                "copy",
                output_path,
            ]

        print(f"[QuickDrop DEBUG] FFMPEG VIDEO STUDIO COMMAND: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[QuickDrop DEBUG] FFMPEG ERROR:\n{result.stderr}")
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            err_line = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Unknown error"
            raise RuntimeError(f"FFmpeg processing failed: {err_line}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            raise RuntimeError("Processing failed: Output video was not generated or is empty.")

        print(f"[QuickDrop] Processed video '{input_path}' -> '{output_path}'")
        return output_path

    @classmethod
    def _do_trim_video(cls, input_path: str, start_time: str, end_time: str, output_path: str = "") -> str:
        """Stream copy trim video forwarding to _do_process_video."""
        return cls._do_process_video(input_path, start_time, end_time, output_path, crop_box=None)

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None
        self._updating_inputs = False
        self.crop_type = self.detect_crop_type(file_path)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        if self.crop_type == "video":
            self.setFixedSize(390, 290)
            self._setup_video_ui()
        else:
            self.setFixedSize(450, 480)
            self._setup_ui()

        self._center_on_screen()

    def _setup_video_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        self.container = QFrame()
        self.container.setObjectName("cropVideoContainer")
        self.container.setStyleSheet("""
            #cropVideoContainer {
                background-color: #f7f7f7;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self.container)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # 1. Header: "Trim Video" + Close Button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)
        title_label = QLabel("Trim Video")
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

        # 2. File Information
        dur = get_video_duration(self.file_path)
        self._video_duration = dur
        dur_str = format_seconds_to_hhmmss(dur) if dur > 0 else "00:00:10"

        file_label = QLabel(os.path.basename(self.file_path))
        file_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #222222;")
        layout.addWidget(file_label)

        duration_label = QLabel(f"Duration: {format_seconds_to_hhmmss(dur)}" if dur > 0 else "Duration: Unknown")
        duration_label.setStyleSheet("font-size: 12px; font-weight: 500; color: #666666;")
        layout.addWidget(duration_label)

        # 3. Start & End Time Inputs
        times_layout = QHBoxLayout()
        times_layout.setSpacing(12)

        start_vbox = QVBoxLayout()
        start_vbox.setSpacing(4)
        start_lbl = QLabel("Start Time (HH:MM:SS)")
        start_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #444444;")
        self.start_input = QLineEdit("00:00:00")
        self.start_input.setPlaceholderText("00:00:00")
        self.start_input.setStyleSheet("""
            QLineEdit {
                background-color: #ffffff;
                color: #222222;
                border: 1px solid rgba(0, 0, 0, 0.16);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                font-weight: 600;
            }
            QLineEdit:focus {
                border: 1px solid #ff5500;
            }
        """)
        start_vbox.addWidget(start_lbl)
        start_vbox.addWidget(self.start_input)
        times_layout.addLayout(start_vbox)

        end_vbox = QVBoxLayout()
        end_vbox.setSpacing(4)
        end_lbl = QLabel("End Time (HH:MM:SS)")
        end_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #444444;")
        self.end_input = QLineEdit(dur_str)
        self.end_input.setPlaceholderText("00:00:10")
        self.end_input.setStyleSheet("""
            QLineEdit {
                background-color: #ffffff;
                color: #222222;
                border: 1px solid rgba(0, 0, 0, 0.16);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                font-weight: 600;
            }
            QLineEdit:focus {
                border: 1px solid #ff5500;
            }
        """)
        end_vbox.addWidget(end_lbl)
        end_vbox.addWidget(self.end_input)
        times_layout.addLayout(end_vbox)

        layout.addLayout(times_layout)

        # 4. Error Message (Hidden by default)
        self.video_error_label = QLabel("")
        self.video_error_label.setStyleSheet("""
            QLabel {
                color: #d32f2f;
                font-size: 11.5px;
                font-weight: 600;
                background: rgba(255, 59, 48, 0.08);
                border: 1px solid rgba(255, 59, 48, 0.25);
                border-radius: 6px;
                padding: 5px 8px;
            }
        """)
        self.video_error_label.setWordWrap(True)
        self.video_error_label.hide()
        layout.addWidget(self.video_error_label)

        layout.addStretch()

        # 5. Buttons: Cancel and Trim
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)
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

        self.trim_btn = QPushButton("Trim")
        self.trim_btn.setStyleSheet("""
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
            QPushButton:disabled {
                background-color: rgba(0, 0, 0, 0.15);
                color: rgba(0, 0, 0, 0.35);
            }
        """)
        self.trim_btn.clicked.connect(self._on_trim_clicked)
        bottom_layout.addWidget(self.trim_btn)
        layout.addLayout(bottom_layout)

        outer_layout.addWidget(self.container)

        # 6. Loading Overlay
        self.loading_overlay = QFrame(self.container)
        self.loading_overlay.setStyleSheet("""
            background-color: rgba(255, 255, 255, 0.92);
            border-radius: 16px;
        """)
        overlay_layout = QVBoxLayout(self.loading_overlay)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.setSpacing(8)

        overlay_title = QLabel("Processing...")
        overlay_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #ff5500;")
        overlay_layout.addWidget(overlay_title)

        overlay_sub = QLabel("Trimming video duration...")
        overlay_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_sub.setStyleSheet("font-size: 12px; color: #666666;")
        overlay_layout.addWidget(overlay_sub)

        self.loading_overlay.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "loading_overlay") and hasattr(self, "container"):
            self.loading_overlay.setGeometry(0, 0, self.container.width(), self.container.height())

    def _show_video_error(self, message: str) -> None:
        self.video_error_label.setText(message)
        self.video_error_label.show()

    def _show_video_loading(self, show: bool) -> None:
        if show:
            if hasattr(self, "loading_overlay") and hasattr(self, "container"):
                self.loading_overlay.setGeometry(0, 0, self.container.width(), self.container.height())
                self.loading_overlay.show()
                self.loading_overlay.raise_()
            self.trim_btn.setEnabled(False)
        else:
            if hasattr(self, "loading_overlay"):
                self.loading_overlay.hide()
            self.trim_btn.setEnabled(True)

    def _on_trim_clicked(self) -> None:
        start_time = self.start_input.text().strip() or "00:00:00"
        end_time = self.end_input.text().strip()
        if not end_time:
            self._show_video_error("Please specify an End Time (HH:MM:SS).")
            return

        self._show_video_loading(True)
        self.video_error_label.hide()

        def run_worker():
            try:
                out = self._do_trim_video(self.file_path, start_time, end_time)
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_on_trim_finished_success",
                    Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, out)
                )
            except Exception as exc:
                err_text = str(exc)
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_on_trim_finished_error",
                    Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, err_text)
                )

        threading.Thread(target=run_worker, daemon=True).start()

    @QtCore.pyqtSlot(str)
    def _on_trim_finished_success(self, output_path: str) -> None:
        print(f"[QuickDrop] Trim complete: {output_path}")
        parent = self.parent()
        if parent and hasattr(parent, "page"):
            parent.page().runJavaScript(
                'if (window.showToast) window.showToast("Video trimmed successfully!", "success");'
            )
        self.accept()

    @QtCore.pyqtSlot(str)
    def _on_trim_finished_error(self, err_msg: str) -> None:
        self._show_video_loading(False)
        self._show_video_error(err_msg or "Video trimming failed.")

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("cropContainer")
        container.setStyleSheet("""
            #cropContainer {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

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
            target_initial = os.path.join(dir_name, f"{base_name}_cropped{ext}")

            if os.path.abspath(target_initial) == os.path.abspath(self.file_path):
                target_initial = os.path.join(dir_name, f"{base_name}_cropped_1{ext}")

            output_path = get_unique_filename(target_initial)

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
            parent = self.parent()
            if parent and hasattr(parent, "page"):
                parent.page().runJavaScript(
                    'if (window.showToast) window.showToast("Image cropped successfully!", "success");'
                )
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
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() == Qt.Key.Key_Q and getattr(self, "crop_type", "image") != "video":
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


class DocCompleteDialog(QDialog):
    """Frameless translucent frosted glass dialog displaying document export completion with open actions."""

    def __init__(self, file_paths: list[str], target_format: str, parent=None) -> None:
        super().__init__(parent)
        self.file_paths = file_paths
        self.target_format = target_format.strip().upper()
        self._drag_start_pos = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(450, 230)

        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("docCompleteContainer")
        container.setStyleSheet("""
            #docCompleteContainer {
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

        icon_str = "📄" if self.target_format == "DOCX" else "📝"
        title_label = QLabel(f"{icon_str} {self.target_format} Export Complete")
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

        # Subtitle
        sub_text = "File successfully saved to:" if len(self.file_paths) == 1 else "Files successfully saved to:"
        sub_label = QLabel(sub_text)
        sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_label.setStyleSheet("color: #666666; font-size: 11px;")
        layout.addWidget(sub_label)

        # Path display box
        path_box = QFrame()
        path_box.setStyleSheet("""
            QFrame {
                background-color: rgba(0, 0, 0, 0.04);
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 8px;
                padding: 6px;
            }
        """)
        path_layout = QVBoxLayout(path_box)
        path_layout.setContentsMargins(8, 6, 8, 6)

        first_path = self.file_paths[0] if self.file_paths else ""
        display_text = os.path.basename(first_path) if len(self.file_paths) == 1 else f"{len(self.file_paths)} documents created"
        file_label = QLabel(display_text)
        file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        file_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #ff5500;")
        path_layout.addWidget(file_label)

        if len(self.file_paths) == 1:
            dir_label = QLabel(os.path.dirname(first_path))
            dir_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dir_label.setStyleSheet("font-size: 10px; color: #888888;")
            path_layout.addWidget(dir_label)

        layout.addWidget(path_box)

        # Bottom buttons
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #444444;
                border: none;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 500;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #222222;
            }
        """)
        open_folder_btn.clicked.connect(self._open_folder)
        bottom_layout.addWidget(open_folder_btn)

        if len(self.file_paths) == 1:
            open_file_btn = QPushButton("Open File")
            open_file_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 0, 0, 0.06);
                    color: #444444;
                    border: none;
                    border-radius: 6px;
                    padding: 7px 14px;
                    font-weight: 500;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: rgba(0, 0, 0, 0.1);
                    color: #222222;
                }
            """)
            open_file_btn.clicked.connect(self._open_file)
            bottom_layout.addWidget(open_file_btn)

        bottom_layout.addStretch()

        done_btn = QPushButton("Done")
        done_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5500;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 20px;
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
        done_btn.clicked.connect(self.close)
        bottom_layout.addWidget(done_btn)

        layout.addLayout(bottom_layout)
        outer_layout.addWidget(container)

    def _open_file(self) -> None:
        if not self.file_paths:
            return
        path = os.path.abspath(self.file_paths[0])
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])
        except Exception as e:
            print(f"[QuickDrop] Error opening file: {e}")

    def _open_folder(self) -> None:
        if not self.file_paths:
            return
        folder = os.path.dirname(os.path.abspath(self.file_paths[0]))
        try:
            if sys.platform == "win32":
                subprocess.run(["explorer", folder])
            elif sys.platform == "darwin":
                subprocess.run(["open", folder])
            else:
                subprocess.run(["xdg-open", folder])
        except Exception as e:
            print(f"[QuickDrop] Error opening folder: {e}")

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            x = (screen_geo.width() - self.width()) // 2
            y = (screen_geo.height() - self.height()) // 2
            self.move(x, y)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() == Qt.MouseButton.LeftButton and getattr(self, "_drag_start_pos", None) is not None:
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


class WatermarkDialog(QDialog):
    """Frameless dark-themed dialog for applying custom text watermarks with opacity and positioning."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(500, 580)

        self._setup_ui()
        self._center_on_screen()
        self._update_preview()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("watermarkContainer")
        container.setStyleSheet("""
            #watermarkContainer {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
            QLineEdit {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.16);
                border-radius: 6px;
                color: #222222;
                padding: 6px 10px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #ff5500;
            }
            QComboBox {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.16);
                border-radius: 6px;
                color: #222222;
                padding: 4px 8px;
                font-size: 12px;
                min-width: 120px;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                color: #222222;
                selection-background-color: #ff5500;
                selection-color: #ffffff;
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
                width: 16px;
                height: 16px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 8px;
            }
        """)

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Header
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)

        title_label = QLabel("Watermark Image")
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

        filename_label = QLabel(os.path.basename(self.file_path))
        filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        filename_label.setStyleSheet("color: #666666; font-size: 11px; margin-top: -4px;")
        layout.addWidget(filename_label)

        # Preview area
        self.preview_frame = QFrame()
        self.preview_frame.setStyleSheet("""
            QFrame {
                background-color: #f5f5f7;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 10px;
            }
        """)
        preview_layout = QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(6, 6, 6, 6)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(190)
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(self.preview_frame, stretch=1)

        # Text input row
        text_row = QHBoxLayout()
        text_row.setSpacing(8)
        text_lbl = QLabel("Text:")
        text_lbl.setStyleSheet("font-size: 12px; font-weight: 600; min-width: 45px; color: #222222;")
        self.text_input = QLineEdit("CONFIDENTIAL")
        self.text_input.textChanged.connect(self._update_preview)
        text_row.addWidget(text_lbl)
        text_row.addWidget(self.text_input)
        layout.addLayout(text_row)

        # Position and color row
        pos_row = QHBoxLayout()
        pos_row.setSpacing(8)
        pos_lbl = QLabel("Position:")
        pos_lbl.setStyleSheet("font-size: 12px; font-weight: 600; min-width: 45px; color: #222222;")
        self.pos_combo = QComboBox()
        self.pos_combo.addItems(["Center", "Bottom Right", "Bottom Left", "Top Right", "Top Left", "Tiled / Diagonal"])
        self.pos_combo.currentIndexChanged.connect(self._update_preview)
        pos_row.addWidget(pos_lbl)
        pos_row.addWidget(self.pos_combo)

        pos_row.addSpacing(10)
        color_lbl = QLabel("Color:")
        color_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #222222;")
        self.color_combo = QComboBox()
        self.color_combo.addItems(["White", "Black"])
        self.color_combo.currentIndexChanged.connect(self._update_preview)
        pos_row.addWidget(color_lbl)
        pos_row.addWidget(self.color_combo)
        layout.addLayout(pos_row)

        # Opacity slider row
        op_row = QHBoxLayout()
        op_row.setSpacing(8)
        self.op_lbl = QLabel("Opacity: 45%")
        self.op_lbl.setStyleSheet("font-size: 12px; font-weight: 600; min-width: 90px; color: #222222;")
        self.op_slider = QSlider(Qt.Orientation.Horizontal)
        self.op_slider.setRange(5, 100)
        self.op_slider.setValue(45)
        self.op_slider.valueChanged.connect(self._on_opacity_changed)
        op_row.addWidget(self.op_lbl)
        op_row.addWidget(self.op_slider)
        layout.addLayout(op_row)

        # Size slider row
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        self.size_lbl = QLabel("Font Size: 36px")
        self.size_lbl.setStyleSheet("font-size: 12px; font-weight: 600; min-width: 90px; color: #222222;")
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(14, 100)
        self.size_slider.setValue(36)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        size_row.addWidget(self.size_lbl)
        size_row.addWidget(self.size_slider)
        layout.addLayout(size_row)

        # Bottom buttons
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)
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

        apply_btn = QPushButton("Apply Watermark")
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

    def _on_opacity_changed(self, val: int) -> None:
        self.op_lbl.setText(f"Opacity: {val}%")
        self._update_preview()

    def _on_size_changed(self, val: int) -> None:
        self.size_lbl.setText(f"Font Size: {val}px")
        self._update_preview()

    @staticmethod
    def _get_font(size: int):
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "C:\\Windows\\Fonts\\arial.ttf",
            "C:\\Windows\\Fonts\\segoeui.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
            "DejaVuSans.ttf",
            "arial.ttf",
        ]
        for p in font_candidates:
            if os.path.isfile(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
        try:
            return ImageFont.load_default(size=size)
        except Exception:
            return ImageFont.load_default()

    @staticmethod
    def _measure_text(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
        if hasattr(draw, "textbbox"):
            bbox = draw.textbbox((0, 0), text, font=font)
            return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])
        elif hasattr(draw, "textsize"):
            return draw.textsize(text, font=font)
        return max(1, len(text) * 10), 20

    @staticmethod
    def apply_watermark_to_pil(img: Image.Image, text: str, opacity: int, position: str, font_size: int, is_white: bool) -> Image.Image:
        base = img.convert("RGBA")
        txt_layer = Image.new("RGBA", base.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt_layer)

        font = WatermarkDialog._get_font(font_size)
        tw, th = WatermarkDialog._measure_text(draw, text, font)
        alpha = int(255 * (max(5, min(100, opacity)) / 100.0))
        rgb = (255, 255, 255) if is_white else (0, 0, 0)
        fill_color = (rgb[0], rgb[1], rgb[2], alpha)

        w, h = base.size
        pad = 20

        if position == "Tiled / Diagonal":
            stamp_w = tw + 80
            stamp_h = th + 80
            stamp = Image.new("RGBA", (stamp_w, stamp_h), (255, 255, 255, 0))
            s_draw = ImageDraw.Draw(stamp)
            s_draw.text((40, 40), text, font=font, fill=fill_color)
            rotated = stamp.rotate(30, expand=True, resample=Image.Resampling.BICUBIC)
            rw, rh = rotated.size
            for y_pos in range(-rh, h + rh, max(20, rh)):
                for x_pos in range(-rw, w + rw, max(20, rw)):
                    txt_layer.paste(rotated, (x_pos, y_pos), rotated)
        else:
            if position == "Center":
                x = (w - tw) // 2
                y = (h - th) // 2
            elif position == "Bottom Right":
                x = max(pad, w - tw - pad)
                y = max(pad, h - th - pad)
            elif position == "Bottom Left":
                x = pad
                y = max(pad, h - th - pad)
            elif position == "Top Right":
                x = max(pad, w - tw - pad)
                y = pad
            else:  # Top Left
                x = pad
                y = pad
            draw.text((x, y), text, font=font, fill=fill_color)

        return Image.alpha_composite(base, txt_layer)

    def _update_preview(self) -> None:
        if not os.path.isfile(self.file_path):
            return
        try:
            with Image.open(self.file_path) as orig:
                thumb = orig.copy()
                thumb.thumbnail((440, 200), Image.Resampling.LANCZOS)
                scale = thumb.width / float(orig.width) if orig.width > 0 else 1.0

                text = self.text_input.text() or " "
                opacity = self.op_slider.value()
                pos = self.pos_combo.currentText()
                is_white = (self.color_combo.currentText() == "White")
                scaled_font_size = max(10, int(self.size_slider.value() * scale))

                preview_img = WatermarkDialog.apply_watermark_to_pil(
                    thumb, text, opacity, pos, scaled_font_size, is_white
                )

                raw_data = preview_img.convert("RGBA").tobytes("raw", "RGBA")
                qimg = QImage(raw_data, preview_img.width, preview_img.height, QImage.Format.Format_RGBA8888)
                self.preview_label.setPixmap(QPixmap.fromImage(qimg))
        except Exception as e:
            print(f"[QuickDrop] Watermark preview error: {e}")

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def _on_apply(self) -> None:
        try:
            dir_name = os.path.dirname(os.path.abspath(self.file_path))
            base_name, ext = os.path.splitext(os.path.basename(self.file_path))
            output_path = os.path.join(dir_name, f"{base_name}_watermarked{ext}")

            if os.path.abspath(output_path) == os.path.abspath(self.file_path):
                output_path = os.path.join(dir_name, f"{base_name}_watermarked_1{ext}")

            text = self.text_input.text() or "CONFIDENTIAL"
            opacity = self.op_slider.value()
            pos = self.pos_combo.currentText()
            is_white = (self.color_combo.currentText() == "White")
            font_size = self.size_slider.value()

            with Image.open(self.file_path) as img:
                watermarked = WatermarkDialog.apply_watermark_to_pil(
                    img, text, opacity, pos, font_size, is_white
                )
                ext_lower = ext.lower()
                if ext_lower in (".jpg", ".jpeg"):
                    watermarked.convert("RGB").save(output_path, "JPEG", quality=95)
                elif ext_lower == ".png":
                    watermarked.save(output_path, "PNG", optimize=True)
                elif ext_lower == ".webp":
                    watermarked.save(output_path, "WEBP", quality=95)
                else:
                    fmt = img.format or "PNG"
                    watermarked.save(output_path, format=fmt)

            print(f"[QuickDrop] Watermarked '{self.file_path}' -> '{output_path}'")
            self.accept()
        except Exception as e:
            print(f"[QuickDrop ERROR] Watermark error: {e}")
            self.reject()

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


class RedactCanvasWidget(QWidget):
    """Interactive canvas allowing the user to actively drag a rectangle to redact with real-time live preview."""

    selection_changed = pyqtSignal()

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self.original_pixmap = QPixmap(file_path)
        self._pixmap = self.original_pixmap
        self._img_w = max(1, self.original_pixmap.width() if not self.original_pixmap.isNull() else 100)
        self._img_h = max(1, self.original_pixmap.height() if not self.original_pixmap.isNull() else 100)

        # Cache base PIL image for responsive live effect generation
        try:
            with Image.open(file_path) as pil_img:
                self._base_pil_img = ImageOps.exif_transpose(pil_img).convert("RGBA")
        except Exception as e:
            print(f"[QuickDrop] Redact image load warning: {e}")
            self._base_pil_img = None

        self.effect_pixmap = self.original_pixmap.copy()
        self.generate_effect_pixmap("Blur", 15)

        self._start_point: QPointF | None = None
        self._current_point: QPointF | None = None
        self._is_dragging = False

        # Selection rectangle in widget display coordinates (QRectF)
        self.selection_rect: QRectF | None = None
        # Redact box in original image pixel coordinates (x1, y1, x2, y2)
        self.redact_rect: tuple[int, int, int, int] | None = None

        self.setMinimumSize(360, 220)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def generate_effect_pixmap(self, effect: str = "Blur", intensity: int = 18) -> None:
        """Generate self.effect_pixmap from the original image using Pillow based on the active effect."""
        try:
            base = getattr(self, "_base_pil_img", None)
            if base is None:
                with Image.open(self.file_path) as img:
                    base = ImageOps.exif_transpose(img).convert("RGBA")
                    self._base_pil_img = base

            eff_name = (effect or "Blur").strip().capitalize()

            if eff_name == "Blur":
                radius = max(1, int(intensity))
                processed = base.filter(ImageFilter.GaussianBlur(radius=radius))
            elif eff_name == "Pixelate":
                step = max(2, int(intensity))
                pw = max(1, base.width // step)
                ph = max(1, base.height // step)
                small = base.resize((pw, ph), Image.Resampling.NEAREST)
                processed = small.resize((base.width, base.height), Image.Resampling.NEAREST)
            elif eff_name == "Blackout":
                processed = Image.new("RGBA", base.size, (0, 0, 0, 255))
            else:
                radius = max(1, int(intensity))
                processed = base.filter(ImageFilter.GaussianBlur(radius=radius))

            raw_bytes = processed.tobytes("raw", "RGBA")
            qimg = QImage(raw_bytes, processed.width, processed.height, QImage.Format.Format_RGBA8888).copy()
            self.effect_pixmap = QPixmap.fromImage(qimg)
        except Exception as e:
            print(f"[QuickDrop] Redact effect generation warning: {e}")
            self.effect_pixmap = self.original_pixmap.copy()

        self.update()

    def _get_display_rect(self) -> QRectF:
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

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        drect = self._get_display_rect()

        # Simply draw self.original_pixmap
        if not self.original_pixmap.isNull():
            painter.drawPixmap(drect.toRect(), self.original_pixmap)

        # If a selection exists, use painter.setClipRect to draw self.effect_pixmap directly over it
        sel = self.selection_rect
        if sel is None and self.redact_rect is not None:
            rx1, ry1, rx2, ry2 = self.redact_rect
            scale = drect.width() / float(self._img_w)
            bx1 = drect.x() + rx1 * scale
            by1 = drect.y() + ry1 * scale
            bw = (rx2 - rx1) * scale
            bh = (ry2 - ry1) * scale
            sel = QRectF(bx1, by1, max(1.0, bw), max(1.0, bh))

        if sel and sel.width() > 1 and sel.height() > 1:
            painter.save()
            painter.setClipRect(sel)
            if hasattr(self, "effect_pixmap") and not self.effect_pixmap.isNull():
                painter.drawPixmap(drect.toRect(), self.effect_pixmap)
            painter.restore()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            drect = self._get_display_rect()
            pos = event.position()
            clamped_x = max(drect.left(), min(drect.right(), pos.x()))
            clamped_y = max(drect.top(), min(drect.bottom(), pos.y()))
            self._start_point = QPointF(clamped_x, clamped_y)
            self._current_point = self._start_point
            self._is_dragging = True
            self.selection_rect = None
            self.redact_rect = None
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._is_dragging and (event.buttons() & Qt.MouseButton.LeftButton) and self._start_point:
            drect = self._get_display_rect()
            pos = event.position()
            clamped_x = max(drect.left(), min(drect.right(), pos.x()))
            clamped_y = max(drect.top(), min(drect.bottom(), pos.y()))
            self._current_point = QPointF(clamped_x, clamped_y)

            # Update the selection rectangle and call self.update() instantly
            x1 = min(self._start_point.x(), self._current_point.x())
            y1 = min(self._start_point.y(), self._current_point.y())
            x2 = max(self._start_point.x(), self._current_point.x())
            y2 = max(self._start_point.y(), self._current_point.y())
            self.selection_rect = QRectF(x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1))
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._is_dragging and event.button() == Qt.MouseButton.LeftButton and self._start_point:
            self._is_dragging = False
            drect = self._get_display_rect()
            pos = event.position()
            clamped_x = max(drect.left(), min(drect.right(), pos.x()))
            clamped_y = max(drect.top(), min(drect.bottom(), pos.y()))
            self._current_point = QPointF(clamped_x, clamped_y)

            x1 = min(self._start_point.x(), self._current_point.x())
            y1 = min(self._start_point.y(), self._current_point.y())
            x2 = max(self._start_point.x(), self._current_point.x())
            y2 = max(self._start_point.y(), self._current_point.y())

            if (x2 - x1) > 3 and (y2 - y1) > 3:
                self.selection_rect = QRectF(x1, y1, x2 - x1, y2 - y1)
                scale = drect.width() / float(self._img_w)
                ix1 = int(round((x1 - drect.left()) / scale))
                iy1 = int(round((y1 - drect.top()) / scale))
                ix2 = int(round((x2 - drect.left()) / scale))
                iy2 = int(round((y2 - drect.top()) / scale))
                self.redact_rect = (
                    max(0, min(self._img_w - 1, ix1)),
                    max(0, min(self._img_h - 1, iy1)),
                    max(1, min(self._img_w, ix2)),
                    max(1, min(self._img_h, iy2)),
                )
            else:
                self.selection_rect = None
                self.redact_rect = None

            self._start_point = None
            self._current_point = None
            self.update()
            self.selection_changed.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def clear_selection(self) -> None:
        """Sets the current selection rectangle to None and calls update() immediately to revert to original image."""
        self.selection_rect = None
        self.redact_rect = None
        self._start_point = None
        self._current_point = None
        self._is_dragging = False
        self.update()
        self.selection_changed.emit()

    def reset_selection(self) -> None:
        """Alias for clear_selection."""
        self.clear_selection()


class RedactDialog(QDialog):
    """Frameless dark-themed dialog for selecting sensitive regions and applying blur/pixelate redactions."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None
        self.effect_intensities = {"Blur": 15, "Pixelate": 15}

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(520, 560)

        self._setup_ui()
        self._center_on_screen()
        self.generate_effect_pixmap()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("redactContainer")
        container.setStyleSheet("""
            #redactContainer {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
            QLabel:disabled {
                color: rgba(0, 0, 0, 0.3);
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
            QSlider::sub-page:horizontal:disabled {
                background: rgba(0, 0, 0, 0.06);
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.22);
                width: 16px;
                height: 16px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:disabled {
                background: rgba(0, 0, 0, 0.15);
                border: 1px solid rgba(0, 0, 0, 0.08);
            }
        """)

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Header
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)

        title_label = QLabel("Redact Sensitive Data")
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

        info_label = QLabel("Click and drag on the image to draw a redaction box:")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setStyleSheet("color: #666666; font-size: 11px; margin-top: -4px;")
        layout.addWidget(info_label)

        # Canvas preview
        self.canvas = RedactCanvasWidget(self.file_path, self)
        layout.addWidget(self.canvas, stretch=1)

        # Effect selection buttons
        effect_layout = QHBoxLayout()
        effect_layout.setSpacing(8)

        eff_lbl = QLabel("Effect:")
        eff_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #222222;")
        effect_layout.addWidget(eff_lbl)

        self.effect_group = QButtonGroup(self)
        self.effect_group.setExclusive(True)

        effects = ["Blur", "Pixelate", "Blackout"]
        for eff in effects:
            btn = QPushButton(eff)
            btn.setCheckable(True)
            if eff == "Blur":
                btn.setChecked(True)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 0, 0, 0.05);
                    color: #333333;
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-size: 12px;
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
            btn.toggled.connect(lambda checked, b=btn: self._on_effect_toggled(b, checked))
            self.effect_group.addButton(btn)
            effect_layout.addWidget(btn)

        effect_layout.addStretch()
        layout.addLayout(effect_layout)

        # Intensity slider
        slider_row = QHBoxLayout()
        slider_row.setSpacing(8)
        self.intensity_lbl = QLabel(f"Intensity: {self.effect_intensities['Blur']}px")
        self.intensity_lbl.setStyleSheet("font-size: 12px; font-weight: 600; min-width: 90px; color: #222222;")
        self.intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self.intensity_slider.setRange(5, 50)
        self.intensity_slider.setValue(self.effect_intensities["Blur"])
        self.intensity_slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(self.intensity_lbl)
        slider_row.addWidget(self.intensity_slider)
        layout.addLayout(slider_row)

        # Bottom Bar: Reset on left, Cancel and Apply on right
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        reset_btn = QPushButton("Reset / Clear")
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

        apply_btn = QPushButton("Apply Redaction")
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

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    @property
    def effect_pixmap(self) -> QPixmap:
        if hasattr(self, "canvas") and hasattr(self.canvas, "effect_pixmap"):
            return self.canvas.effect_pixmap
        return QPixmap(self.file_path)

    def _get_active_effect(self) -> str:
        for b in self.effect_group.buttons():
            if b.isChecked():
                return b.text()
        return "Blur"

    def _on_effect_toggled(self, button, checked: bool) -> None:
        if not checked:
            return
        effect = button.text()
        if effect == "Blackout":
            self.intensity_slider.setEnabled(False)
            self.intensity_lbl.setEnabled(False)
            self.intensity_lbl.setText("Intensity: N/A")
            self.canvas.generate_effect_pixmap("Blackout", 0)
        else:
            self.intensity_slider.setEnabled(True)
            self.intensity_lbl.setEnabled(True)
            val = self.effect_intensities.get(effect, 15)
            self.intensity_slider.blockSignals(True)
            self.intensity_slider.setValue(val)
            self.intensity_slider.blockSignals(False)
            self.intensity_lbl.setText(f"Intensity: {val}px")
            self.canvas.generate_effect_pixmap(effect, val)
        self.update()

    def _on_slider_changed(self, val: int) -> None:
        effect = self._get_active_effect()
        if effect in self.effect_intensities:
            self.effect_intensities[effect] = val
        self.intensity_lbl.setText(f"Intensity: {val}px")
        self.canvas.generate_effect_pixmap(effect, val)
        self.update()

    def generate_effect_pixmap(self, *args) -> None:
        """Regenerate effect pixmap using current active effect and its saved intensity."""
        effect = self._get_active_effect()
        if effect == "Blackout":
            self.intensity_slider.setEnabled(False)
            self.intensity_lbl.setEnabled(False)
            self.intensity_lbl.setText("Intensity: N/A")
            self.canvas.generate_effect_pixmap("Blackout", 0)
        else:
            self.intensity_slider.setEnabled(True)
            self.intensity_lbl.setEnabled(True)
            val = self.effect_intensities.get(effect, self.intensity_slider.value())
            self.intensity_slider.blockSignals(True)
            self.intensity_slider.setValue(val)
            self.intensity_slider.blockSignals(False)
            self.intensity_lbl.setText(f"Intensity: {val}px")
            self.canvas.generate_effect_pixmap(effect, val)
        self.update()

    def _on_reset(self) -> None:
        """Reset the redaction box to None and instantly revert live preview back to clean original image."""
        if hasattr(self, "canvas") and self.canvas is not None:
            self.canvas.clear_selection()

    def _on_apply(self) -> None:
        try:
            dir_name = os.path.dirname(os.path.abspath(self.file_path))
            base_name, ext = os.path.splitext(os.path.basename(self.file_path))
            output_path = os.path.join(dir_name, f"{base_name}_redacted{ext}")

            if os.path.abspath(output_path) == os.path.abspath(self.file_path):
                output_path = os.path.join(dir_name, f"{base_name}_redacted_1{ext}")

            effect = self._get_active_effect()
            intensity = self.effect_intensities.get(effect, self.intensity_slider.value())

            with Image.open(self.file_path) as img:
                box = self.canvas.redact_rect
                if box is None:
                    print("[QuickDrop] No redaction box selected; keeping original clean image.")
                    self.accept()
                    return

                x1, y1, x2, y2 = box
                x1 = max(0, min(img.width - 1, x1))
                y1 = max(0, min(img.height - 1, y1))
                x2 = max(x1 + 1, min(img.width, x2))
                y2 = max(y1 + 1, min(img.height, y2))

                region = img.crop((x1, y1, x2, y2))
                if effect == "Blur":
                    redacted = region.filter(ImageFilter.GaussianBlur(radius=max(1, intensity)))
                elif effect == "Pixelate":
                    step = max(2, intensity)
                    pw = max(1, region.width // step)
                    ph = max(1, region.height // step)
                    small = region.resize((pw, ph), Image.Resampling.NEAREST)
                    redacted = small.resize((region.width, region.height), Image.Resampling.NEAREST)
                else:  # Blackout
                    redacted = Image.new(region.mode, region.size, (0, 0, 0))

                img.paste(redacted, (x1, y1))

                ext_lower = ext.lower()
                if ext_lower in (".jpg", ".jpeg"):
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    img.save(output_path, "JPEG", quality=95)
                elif ext_lower == ".png":
                    img.save(output_path, "PNG", optimize=True)
                elif ext_lower == ".webp":
                    img.save(output_path, "WEBP", quality=95)
                else:
                    fmt = img.format or "PNG"
                    img.save(output_path, format=fmt)

            print(f"[QuickDrop] Redacted '{self.file_path}' -> '{output_path}'")
            self.accept()
        except Exception as e:
            print(f"[QuickDrop ERROR] Redact error: {e}")
            self.reject()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is not None and (child is self.canvas or self.canvas.isAncestorOf(child)):
                return
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


class EditDialog(QDialog):
    """Frameless dark-themed dialog for adjusting image Brightness, Contrast, and Saturation in real time."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None
        self._preview_base: Image.Image | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(500, 560)

        self._load_base_image()
        self._setup_ui()
        self._center_on_screen()
        self._update_preview()

    def _load_base_image(self) -> None:
        try:
            with Image.open(self.file_path) as orig:
                self._preview_base = orig.convert("RGBA")
                self._preview_base.thumbnail((440, 220), Image.Resampling.LANCZOS)
        except Exception as e:
            print(f"[QuickDrop] Edit base image load error: {e}")
            self._preview_base = None

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("editContainer")
        container.setStyleSheet("""
            #editContainer {
                background-color: #ffffff;
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
                width: 16px;
                height: 16px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 8px;
            }
        """)

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Header
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)

        title_label = QLabel("Edit Image")
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

        filename_label = QLabel(os.path.basename(self.file_path))
        filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        filename_label.setStyleSheet("color: #666666; font-size: 11px; margin-top: -4px;")
        layout.addWidget(filename_label)

        # Preview area
        preview_frame = QFrame()
        preview_frame.setStyleSheet("""
            QFrame {
                background-color: #f5f5f7;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 10px;
            }
        """)
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(6, 6, 6, 6)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(200)
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(preview_frame, stretch=1)

        # Sliders container
        sliders_layout = QVBoxLayout()
        sliders_layout.setSpacing(8)

        # 1. Brightness
        b_row = QHBoxLayout()
        self.bright_lbl = QLabel("Brightness: 100%")
        self.bright_lbl.setStyleSheet("font-size: 12px; font-weight: 600; min-width: 120px; color: #222222;")
        self.bright_slider = QSlider(Qt.Orientation.Horizontal)
        self.bright_slider.setRange(0, 200)
        self.bright_slider.setValue(100)
        self.bright_slider.valueChanged.connect(self._on_slider_change)
        b_row.addWidget(self.bright_lbl)
        b_row.addWidget(self.bright_slider)
        sliders_layout.addLayout(b_row)

        # 2. Contrast
        c_row = QHBoxLayout()
        self.contrast_lbl = QLabel("Contrast: 100%")
        self.contrast_lbl.setStyleSheet("font-size: 12px; font-weight: 600; min-width: 120px; color: #222222;")
        self.contrast_slider = QSlider(Qt.Orientation.Horizontal)
        self.contrast_slider.setRange(0, 200)
        self.contrast_slider.setValue(100)
        self.contrast_slider.valueChanged.connect(self._on_slider_change)
        c_row.addWidget(self.contrast_lbl)
        c_row.addWidget(self.contrast_slider)
        sliders_layout.addLayout(c_row)

        # 3. Saturation
        s_row = QHBoxLayout()
        self.sat_lbl = QLabel("Saturation: 100%")
        self.sat_lbl.setStyleSheet("font-size: 12px; font-weight: 600; min-width: 120px; color: #222222;")
        self.sat_slider = QSlider(Qt.Orientation.Horizontal)
        self.sat_slider.setRange(0, 200)
        self.sat_slider.setValue(100)
        self.sat_slider.valueChanged.connect(self._on_slider_change)
        s_row.addWidget(self.sat_lbl)
        s_row.addWidget(self.sat_slider)
        sliders_layout.addLayout(s_row)

        layout.addLayout(sliders_layout)

        # Bottom buttons row
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

        apply_btn = QPushButton("Apply Adjustments")
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

    def _on_slider_change(self) -> None:
        self.bright_lbl.setText(f"Brightness: {self.bright_slider.value()}%")
        self.contrast_lbl.setText(f"Contrast: {self.contrast_slider.value()}%")
        self.sat_lbl.setText(f"Saturation: {self.sat_slider.value()}%")
        self._update_preview()

    def _on_reset(self) -> None:
        self.bright_slider.setValue(100)
        self.contrast_slider.setValue(100)
        self.sat_slider.setValue(100)
        self._on_slider_change()

    def _update_preview(self) -> None:
        if self._preview_base is None:
            return
        b_factor = self.bright_slider.value() / 100.0
        c_factor = self.contrast_slider.value() / 100.0
        s_factor = self.sat_slider.value() / 100.0

        try:
            img = self._preview_base.copy()
            if b_factor != 1.0:
                img = ImageEnhance.Brightness(img).enhance(b_factor)
            if c_factor != 1.0:
                img = ImageEnhance.Contrast(img).enhance(c_factor)
            if s_factor != 1.0:
                img = ImageEnhance.Color(img).enhance(s_factor)

            rgba = img.convert("RGBA")
            raw_data = rgba.tobytes("raw", "RGBA")
            qimg = QImage(raw_data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
            self.preview_label.setPixmap(QPixmap.fromImage(qimg))
        except Exception as e:
            print(f"[QuickDrop] Edit preview error: {e}")

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def _on_apply(self) -> None:
        try:
            dir_name = os.path.dirname(os.path.abspath(self.file_path))
            base_name, ext = os.path.splitext(os.path.basename(self.file_path))
            output_path = os.path.join(dir_name, f"{base_name}_edited{ext}")

            if os.path.abspath(output_path) == os.path.abspath(self.file_path):
                output_path = os.path.join(dir_name, f"{base_name}_edited_1{ext}")

            b_factor = self.bright_slider.value() / 100.0
            c_factor = self.contrast_slider.value() / 100.0
            s_factor = self.sat_slider.value() / 100.0

            with Image.open(self.file_path) as orig_img:
                orig_format = orig_img.format
                has_alpha = orig_img.mode in ("RGBA", "LA") or (
                    orig_img.mode == "P" and "transparency" in orig_img.info
                )
                target = orig_img.convert("RGBA") if has_alpha else orig_img.convert("RGB")

                if b_factor != 1.0:
                    target = ImageEnhance.Brightness(target).enhance(b_factor)
                if c_factor != 1.0:
                    target = ImageEnhance.Contrast(target).enhance(c_factor)
                if s_factor != 1.0:
                    target = ImageEnhance.Color(target).enhance(s_factor)

                ext_lower = ext.lower()
                if ext_lower in (".jpg", ".jpeg"):
                    target.convert("RGB").save(output_path, "JPEG", quality=95)
                elif ext_lower == ".png":
                    target.save(output_path, "PNG", optimize=True)
                elif ext_lower == ".webp":
                    target.save(output_path, "WEBP", quality=95)
                else:
                    fmt = orig_format or "PNG"
                    if fmt.upper() in ("JPEG", "JPG"):
                        target.convert("RGB").save(output_path, format=fmt, quality=95)
                    else:
                        target.save(output_path, format=fmt)

            print(f"[QuickDrop] Edited '{self.file_path}' -> '{output_path}'")
            self.accept()
        except Exception as e:
            print(f"[QuickDrop ERROR] Edit error: {e}")
            self.reject()

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


class AnnotationItem:
    """Base class for interactive, draggable annotation elements."""

    def copy(self) -> "AnnotationItem":
        raise NotImplementedError

    def translate(self, dx: float, dy: float) -> None:
        raise NotImplementedError

    def get_bounding_rect(self) -> QRectF:
        raise NotImplementedError

    def contains(self, pt: QPointF, scale: float = 1.0) -> bool:
        raise NotImplementedError

    def draw(self, painter: QPainter) -> None:
        raise NotImplementedError


class RectangleAnnotationItem(AnnotationItem):
    def __init__(self, rect: QRectF, color: QColor, stroke_width: float) -> None:
        self.rect = QRectF(rect.normalized())
        self.color = QColor(color)
        self.stroke_width = float(stroke_width)

    def copy(self) -> "RectangleAnnotationItem":
        return RectangleAnnotationItem(QRectF(self.rect), QColor(self.color), self.stroke_width)

    def translate(self, dx: float, dy: float) -> None:
        self.rect.translate(dx, dy)

    def get_bounding_rect(self) -> QRectF:
        pad = self.stroke_width / 2.0 + 4.0
        return self.rect.adjusted(-pad, -pad, pad, pad)

    def contains(self, pt: QPointF, scale: float = 1.0) -> bool:
        tol = max(self.stroke_width / 2.0, 10.0 * scale)
        return self.rect.adjusted(-tol, -tol, tol, tol).contains(pt)

    def draw(self, painter: QPainter) -> None:
        pen = QPen(
            self.color,
            self.stroke_width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect)


class EllipseAnnotationItem(AnnotationItem):
    def __init__(self, rect: QRectF, color: QColor, stroke_width: float) -> None:
        self.rect = QRectF(rect.normalized())
        self.color = QColor(color)
        self.stroke_width = float(stroke_width)

    def copy(self) -> "EllipseAnnotationItem":
        return EllipseAnnotationItem(QRectF(self.rect), QColor(self.color), self.stroke_width)

    def translate(self, dx: float, dy: float) -> None:
        self.rect.translate(dx, dy)

    def get_bounding_rect(self) -> QRectF:
        pad = self.stroke_width / 2.0 + 4.0
        return self.rect.adjusted(-pad, -pad, pad, pad)

    def contains(self, pt: QPointF, scale: float = 1.0) -> bool:
        rx = self.rect.width() / 2.0
        ry = self.rect.height() / 2.0
        if rx <= 0.0 or ry <= 0.0:
            return False
        cx = self.rect.center().x()
        cy = self.rect.center().y()
        tol = max(self.stroke_width / 2.0, 10.0 * scale)
        val = ((pt.x() - cx) / (rx + tol)) ** 2 + ((pt.y() - cy) / (ry + tol)) ** 2
        return val <= 1.0

    def draw(self, painter: QPainter) -> None:
        pen = QPen(
            self.color,
            self.stroke_width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(self.rect)


class ArrowAnnotationItem(AnnotationItem):
    def __init__(self, p1: QPointF, p2: QPointF, color: QColor, stroke_width: float) -> None:
        self.p1 = QPointF(p1)
        self.p2 = QPointF(p2)
        self.color = QColor(color)
        self.stroke_width = float(stroke_width)

    def copy(self) -> "ArrowAnnotationItem":
        return ArrowAnnotationItem(QPointF(self.p1), QPointF(self.p2), QColor(self.color), self.stroke_width)

    def translate(self, dx: float, dy: float) -> None:
        self.p1 += QPointF(dx, dy)
        self.p2 += QPointF(dx, dy)

    def get_bounding_rect(self) -> QRectF:
        rect = QRectF(
            min(self.p1.x(), self.p2.x()),
            min(self.p1.y(), self.p2.y()),
            abs(self.p2.x() - self.p1.x()),
            abs(self.p2.y() - self.p1.y()),
        )
        pad = max(self.stroke_width * 4.0, 16.0)
        return rect.adjusted(-pad, -pad, pad, pad)

    def contains(self, pt: QPointF, scale: float = 1.0) -> bool:
        dx = self.p2.x() - self.p1.x()
        dy = self.p2.y() - self.p1.y()
        l2 = dx * dx + dy * dy
        tol = max(self.stroke_width, 10.0 * scale)
        if l2 == 0.0:
            return math.hypot(pt.x() - self.p1.x(), pt.y() - self.p1.y()) <= tol
        t = max(0.0, min(1.0, ((pt.x() - self.p1.x()) * dx + (pt.y() - self.p1.y()) * dy) / l2))
        proj_x = self.p1.x() + t * dx
        proj_y = self.p1.y() + t * dy
        dist = math.hypot(pt.x() - proj_x, pt.y() - proj_y)
        return dist <= tol

    def draw(self, painter: QPainter) -> None:
        pen = QPen(
            self.color,
            self.stroke_width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(self.p1, self.p2)

        dx = self.p2.x() - self.p1.x()
        dy = self.p2.y() - self.p1.y()
        dist = math.hypot(dx, dy)
        if dist > 3.0:
            angle = math.atan2(dy, dx)
            arrow_len = max(12.0, self.stroke_width * 3.5)
            arrow_len = min(arrow_len, dist * 0.45)
            arrow_angle = math.pi / 6.0

            p_left = QPointF(
                self.p2.x() - arrow_len * math.cos(angle - arrow_angle),
                self.p2.y() - arrow_len * math.sin(angle - arrow_angle),
            )
            p_right = QPointF(
                self.p2.x() - arrow_len * math.cos(angle + arrow_angle),
                self.p2.y() - arrow_len * math.sin(angle + arrow_angle),
            )
            painter.setBrush(QBrush(self.color))
            painter.drawPolygon(QPolygonF([self.p2, p_left, p_right]))


class TextAnnotationItem(AnnotationItem):
    def __init__(self, pos: QPointF, text: str, font: QFont, color: QColor) -> None:
        self.pos = QPointF(pos)
        self.text = text
        self.font = QFont(font)
        self.color = QColor(color)

    def copy(self) -> "TextAnnotationItem":
        return TextAnnotationItem(QPointF(self.pos), str(self.text), QFont(self.font), QColor(self.color))

    def translate(self, dx: float, dy: float) -> None:
        self.pos += QPointF(dx, dy)

    def get_bounding_rect(self) -> QRectF:
        fm = QFontMetrics(self.font)
        w = float(fm.horizontalAdvance(self.text))
        h = float(fm.height())
        ascent = float(fm.ascent())
        pad = 6.0
        return QRectF(self.pos.x() - pad, self.pos.y() - ascent - pad, w + 2.0 * pad, h + 2.0 * pad)

    def contains(self, pt: QPointF, scale: float = 1.0) -> bool:
        return self.get_bounding_rect().contains(pt)

    def draw(self, painter: QPainter) -> None:
        painter.setFont(self.font)
        painter.setPen(QPen(self.color))
        painter.drawText(self.pos, self.text)


class PenStrokeAnnotationItem(AnnotationItem):
    def __init__(self, points: list[QPointF], color: QColor, stroke_width: float) -> None:
        self.points = [QPointF(p) for p in points]
        self.color = QColor(color)
        self.stroke_width = float(stroke_width)

    def copy(self) -> "PenStrokeAnnotationItem":
        return PenStrokeAnnotationItem([QPointF(p) for p in self.points], QColor(self.color), self.stroke_width)

    def translate(self, dx: float, dy: float) -> None:
        offset = QPointF(dx, dy)
        for i in range(len(self.points)):
            self.points[i] += offset

    def get_bounding_rect(self) -> QRectF:
        if not self.points:
            return QRectF()
        min_x = min(p.x() for p in self.points)
        max_x = max(p.x() for p in self.points)
        min_y = min(p.y() for p in self.points)
        max_y = max(p.y() for p in self.points)
        pad = self.stroke_width / 2.0 + 4.0
        return QRectF(min_x - pad, min_y - pad, (max_x - min_x) + 2.0 * pad, (max_y - min_y) + 2.0 * pad)

    def contains(self, pt: QPointF, scale: float = 1.0) -> bool:
        tol = max(self.stroke_width / 2.0, 8.0 * scale)
        if not self.points:
            return False
        if len(self.points) == 1:
            return math.hypot(pt.x() - self.points[0].x(), pt.y() - self.points[0].y()) <= tol
        for i in range(len(self.points) - 1):
            p1 = self.points[i]
            p2 = self.points[i + 1]
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            l2 = dx * dx + dy * dy
            if l2 == 0.0:
                dist = math.hypot(pt.x() - p1.x(), pt.y() - p1.y())
            else:
                t = max(0.0, min(1.0, ((pt.x() - p1.x()) * dx + (pt.y() - self.p1.y()) * dy) / l2))
                proj_x = p1.x() + t * dx
                proj_y = p1.y() + t * dy
                dist = math.hypot(pt.x() - proj_x, pt.y() - proj_y)
            if dist <= tol:
                return True
        return False

    def draw(self, painter: QPainter) -> None:
        if not self.points:
            return
        pen = QPen(
            self.color,
            self.stroke_width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(self.points) == 1:
            painter.drawPoint(self.points[0])
        else:
            for i in range(len(self.points) - 1):
                painter.drawLine(self.points[i], self.points[i + 1])


class AnnotateCanvasWidget(QWidget):
    """Interactive canvas supporting rich annotations (Select/Move, Pen, Rectangle, Ellipse, Arrow, Text) with live previews."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self.original_pixmap = QPixmap(file_path)
        self._img_w = max(1, self.original_pixmap.width() if not self.original_pixmap.isNull() else 100)
        self._img_h = max(1, self.original_pixmap.height() if not self.original_pixmap.isNull() else 100)

        self.drawing_layer = QPixmap(self._img_w, self._img_h)
        self.drawing_layer.fill(Qt.GlobalColor.transparent)

        self.items: list[AnnotationItem] = []
        self.selected_item: AnnotationItem | None = None
        self._is_dragging_item: bool = False
        self._drag_last_img_pt: QPointF | None = None
        self._item_has_moved: bool = False

        self.current_tool = "Pen"
        self.current_color = QColor(255, 59, 48)  # Vibrant marker red
        self.thickness = 4
        self.undo_stack: list[list[AnnotationItem]] = []

        self._is_drawing = False
        self._start_point: QPointF | None = None
        self._current_point: QPointF | None = None
        self._current_pen_points: list[QPointF] = []
        self._current_pen_item: PenStrokeAnnotationItem | None = None

        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_tool(self, tool: str) -> None:
        self.current_tool = tool
        self._is_drawing = False
        self._is_dragging_item = False
        if tool == "Select":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif tool == "Text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
            self.selected_item = None
            self.update()
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.selected_item = None
            self.update()

    def set_color(self, color: QColor) -> None:
        self.current_color = color
        if self.current_tool == "Select" and self.selected_item:
            self._save_undo_state()
            self.selected_item.color = QColor(color)
            self._render_all_items()
            self.update()

    def set_thickness(self, thickness: int) -> None:
        self.thickness = max(1, min(50, int(thickness)))
        if self.current_tool == "Select" and self.selected_item and hasattr(self.selected_item, "stroke_width"):
            self._save_undo_state()
            self.selected_item.stroke_width = max(1.0, float(self.thickness) * self._get_image_scale())
            self._render_all_items()
            self.update()

    def _save_undo_state(self) -> None:
        state = [item.copy() for item in self.items]
        self.undo_stack.append(state)
        if len(self.undo_stack) > 35:
            self.undo_stack.pop(0)

    def undo(self) -> None:
        if self.undo_stack:
            self.items = self.undo_stack.pop()
            self.selected_item = None
            self._render_all_items()
            self.update()

    def clear(self) -> None:
        if self.items:
            self._save_undo_state()
            self.items = []
            self.selected_item = None
            self._render_all_items()
            self.update()

    def _render_all_items(self) -> None:
        self.drawing_layer.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self.drawing_layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for item in self.items:
            item.draw(painter)
        painter.end()

    def _get_display_rect(self) -> QRectF:
        w_avail = float(self.width())
        h_avail = float(self.height())
        aspect_widget = w_avail / max(1.0, h_avail)
        aspect_img = float(self._img_w) / float(self._img_h)

        if aspect_img > aspect_widget:
            draw_w = w_avail
            draw_h = draw_w / aspect_img
            draw_x = 0.0
            draw_y = (h_avail - draw_h) / 2.0
        else:
            draw_h = h_avail
            draw_w = draw_h * aspect_img
            draw_x = (w_avail - draw_w) / 2.0
            draw_y = 0.0

        return QRectF(draw_x, draw_y, draw_w, draw_h)

    def _get_image_scale(self) -> float:
        drect = self._get_display_rect()
        return float(self._img_w) / max(1.0, drect.width())

    def _widget_to_image_coords(self, pos: QPointF) -> QPointF | None:
        drect = self._get_display_rect()
        if drect.width() <= 0 or drect.height() <= 0:
            return None
        scale_x = float(self._img_w) / drect.width()
        scale_y = float(self._img_h) / drect.height()
        ix = (pos.x() - drect.x()) * scale_x
        iy = (pos.y() - drect.y()) * scale_y
        ix = max(0.0, min(float(self._img_w), ix))
        iy = max(0.0, min(float(self._img_h), iy))
        return QPointF(ix, iy)

    def _draw_shape(
        self,
        painter: QPainter,
        tool: str,
        p1: QPointF,
        p2: QPointF,
        color: QColor,
        stroke_width: float,
    ) -> None:
        pen = QPen(
            color,
            stroke_width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if tool == "Rectangle":
            rect = QRectF(
                min(p1.x(), p2.x()),
                min(p1.y(), p2.y()),
                abs(p2.x() - p1.x()),
                abs(p2.y() - p1.y()),
            )
            painter.drawRect(rect)

        elif tool == "Ellipse":
            rect = QRectF(
                min(p1.x(), p2.x()),
                min(p1.y(), p2.y()),
                abs(p2.x() - p1.x()),
                abs(p2.y() - p1.y()),
            )
            painter.drawEllipse(rect)

        elif tool == "Arrow":
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            dist = math.hypot(dx, dy)
            painter.drawLine(p1, p2)
            if dist > 3.0:
                angle = math.atan2(dy, dx)
                arrow_len = max(12.0, stroke_width * 3.5)
                arrow_len = min(arrow_len, dist * 0.45)
                arrow_angle = math.pi / 6.0

                p_left = QPointF(
                    p2.x() - arrow_len * math.cos(angle - arrow_angle),
                    p2.y() - arrow_len * math.sin(angle - arrow_angle),
                )
                p_right = QPointF(
                    p2.x() - arrow_len * math.cos(angle + arrow_angle),
                    p2.y() - arrow_len * math.sin(angle + arrow_angle),
                )
                painter.setBrush(QBrush(color))
                painter.drawPolygon(QPolygonF([p2, p_left, p_right]))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        drect = self._get_display_rect()

        # Canvas card backdrop
        painter.fillRect(drect.toRect(), QColor(245, 245, 247))

        if not self.original_pixmap.isNull():
            painter.drawPixmap(drect.toRect(), self.original_pixmap)

        if not self.drawing_layer.isNull():
            painter.drawPixmap(drect.toRect(), self.drawing_layer)

        # Live shape preview while dragging
        if (
            self._is_drawing
            and self._start_point
            and self._current_point
            and self.current_tool in ("Rectangle", "Ellipse", "Arrow")
        ):
            painter.save()
            painter.setClipRect(drect)
            scale_x = drect.width() / float(self._img_w)
            scale_y = drect.height() / float(self._img_h)
            painter.translate(drect.x(), drect.y())
            painter.scale(scale_x, scale_y)
            stroke = max(1.0, float(self.thickness) * self._get_image_scale())
            self._draw_shape(
                painter,
                self.current_tool,
                self._start_point,
                self._current_point,
                self.current_color,
                stroke,
            )
            painter.restore()

        # Selection bounding box and corner handles for "Select" tool
        if self.current_tool == "Select" and self.selected_item and (self.selected_item in self.items):
            brect = self.selected_item.get_bounding_rect()
            scale_x = drect.width() / float(self._img_w)
            scale_y = drect.height() / float(self._img_h)
            w_rect = QRectF(
                drect.x() + brect.x() * scale_x,
                drect.y() + brect.y() * scale_y,
                brect.width() * scale_x,
                brect.height() * scale_y,
            )
            painter.save()
            sel_pen = QPen(QColor(0, 122, 255), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(sel_pen)
            painter.setBrush(QBrush(QColor(0, 122, 255, 18)))
            painter.drawRect(w_rect)

            # Draw corner handles
            handle_size = 6.0
            handle_half = handle_size / 2.0
            handle_pen = QPen(QColor(0, 122, 255), 1.0)
            handle_brush = QBrush(QColor(255, 255, 255))
            painter.setPen(handle_pen)
            painter.setBrush(handle_brush)
            for pt in [
                w_rect.topLeft(),
                w_rect.topRight(),
                w_rect.bottomLeft(),
                w_rect.bottomRight(),
            ]:
                painter.drawRect(QRectF(pt.x() - handle_half, pt.y() - handle_half, handle_size, handle_size))
            painter.restore()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            img_pt = self._widget_to_image_coords(event.position())
            if img_pt is None:
                event.accept()
                return

            if self.current_tool == "Select":
                scale = self._get_image_scale()
                clicked_item = None
                for item in reversed(self.items):
                    if item.contains(img_pt, scale):
                        clicked_item = item
                        break

                if clicked_item is not None:
                    self.selected_item = clicked_item
                    self._is_dragging_item = True
                    self._drag_last_img_pt = img_pt
                    self._item_has_moved = False
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                else:
                    self.selected_item = None
                    self._is_dragging_item = False
                    self.setCursor(Qt.CursorShape.ArrowCursor)

                self.update()
                event.accept()
                return

            if self.current_tool == "Text":
                text, ok = QInputDialog.getText(
                    self,
                    "Add Text Annotation",
                    "Enter annotation text:",
                    QLineEdit.EchoMode.Normal,
                    "",
                )
                if ok and text.strip():
                    self._save_undo_state()
                    scale = self._get_image_scale()
                    base_font_size = max(12, int(self.thickness * 1.5 + 8))
                    effective_font = max(10, int(base_font_size * scale))

                    font = QFont("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif", effective_font)
                    font.setBold(True)
                    fm = QFontMetrics(font)
                    draw_y = max(float(fm.ascent()), img_pt.y())
                    text_item = TextAnnotationItem(QPointF(img_pt.x(), draw_y), text.strip(), font, self.current_color)
                    self.items.append(text_item)
                    self._render_all_items()
                    self.update()
                event.accept()
                return

            self._is_drawing = True
            self._start_point = img_pt
            self._current_point = img_pt

            if self.current_tool == "Pen":
                self._save_undo_state()
                stroke = max(1.0, float(self.thickness) * self._get_image_scale())
                self._current_pen_points = [img_pt]
                self._current_pen_item = PenStrokeAnnotationItem(self._current_pen_points, self.current_color, stroke)
                self.items.append(self._current_pen_item)
                self.selected_item = None
                self._render_all_items()
                self.update()
            else:
                self.selected_item = None
                self.update()

            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        img_pt = self._widget_to_image_coords(event.position())

        if self.current_tool == "Select":
            if img_pt is None:
                event.accept()
                return

            if (event.buttons() & Qt.MouseButton.LeftButton) and self._is_dragging_item and self.selected_item:
                dx = img_pt.x() - self._drag_last_img_pt.x()
                dy = img_pt.y() - self._drag_last_img_pt.y()
                if dx != 0.0 or dy != 0.0:
                    if not self._item_has_moved:
                        self._save_undo_state()
                        self._item_has_moved = True
                    self.selected_item.translate(dx, dy)
                    self._drag_last_img_pt = img_pt
                    self._render_all_items()
                    self.update()
            else:
                scale = self._get_image_scale()
                hovered = None
                for item in reversed(self.items):
                    if item.contains(img_pt, scale):
                        hovered = item
                        break
                if hovered is not None:
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)

            event.accept()
            return

        if (event.buttons() & Qt.MouseButton.LeftButton) and self._is_drawing:
            if img_pt is None:
                event.accept()
                return

            if self.current_tool == "Pen":
                if hasattr(self, "_current_pen_points") and self._current_pen_points:
                    self._current_pen_points.append(img_pt)
                    self._current_pen_item.points = self._current_pen_points
                    painter = QPainter(self.drawing_layer)
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    stroke = max(1.0, float(self.thickness) * self._get_image_scale())
                    pen = QPen(
                        self.current_color,
                        stroke,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    )
                    painter.setPen(pen)
                    painter.drawLine(self._current_pen_points[-2], img_pt)
                    painter.end()
                    self.update()
            else:
                self._current_point = img_pt
                self.update()

            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self.current_tool == "Select":
                if self._is_dragging_item:
                    self._is_dragging_item = False
                    self._render_all_items()
                    self.update()
                img_pt = self._widget_to_image_coords(event.position())
                if img_pt is not None:
                    scale = self._get_image_scale()
                    hovered = any(item.contains(img_pt, scale) for item in self.items)
                    self.setCursor(Qt.CursorShape.SizeAllCursor if hovered else Qt.CursorShape.ArrowCursor)
                event.accept()
                return

            if self._is_drawing:
                img_pt = self._widget_to_image_coords(event.position())
                if img_pt is not None:
                    self._current_point = img_pt

                if self.current_tool in ("Rectangle", "Ellipse", "Arrow"):
                    if self._start_point and self._current_point:
                        if math.hypot(self._current_point.x() - self._start_point.x(), self._current_point.y() - self._start_point.y()) > 2.0:
                            self._save_undo_state()
                            stroke = max(1.0, float(self.thickness) * self._get_image_scale())
                            if self.current_tool == "Rectangle":
                                rect = QRectF(
                                    min(self._start_point.x(), self._current_point.x()),
                                    min(self._start_point.y(), self._current_point.y()),
                                    abs(self._current_point.x() - self._start_point.x()),
                                    abs(self._current_point.y() - self._start_point.y()),
                                )
                                new_item = RectangleAnnotationItem(rect, self.current_color, stroke)
                            elif self.current_tool == "Ellipse":
                                rect = QRectF(
                                    min(self._start_point.x(), self._current_point.x()),
                                    min(self._start_point.y(), self._current_point.y()),
                                    abs(self._current_point.x() - self._start_point.x()),
                                    abs(self._current_point.y() - self._start_point.y()),
                                )
                                new_item = EllipseAnnotationItem(rect, self.current_color, stroke)
                            else:  # Arrow
                                new_item = ArrowAnnotationItem(self._start_point, self._current_point, self.current_color, stroke)

                            self.items.append(new_item)
                            self._render_all_items()

                self._is_drawing = False
                self._start_point = None
                self._current_point = None
                self._current_pen_points = []
                self._current_pen_item = None
                self.update()
                event.accept()
                return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.current_tool == "Select" and self.selected_item and (self.selected_item in self.items):
                self._save_undo_state()
                self.items.remove(self.selected_item)
                self.selected_item = None
                self._render_all_items()
                self.update()
                event.accept()
                return
        super().keyPressEvent(event)


class AnnotateDialog(QDialog):
    """Frameless modern light-themed dialog for rich image annotations."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(680, 640)
        self.setMinimumSize(600, 560)

        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("annotateContainer")
        container.setStyleSheet("""
            #annotateContainer {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #1e293b;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Header: Centered title with subtle close button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(24)

        title_label = QLabel("Annotation Studio")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e293b;")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(0, 0, 0, 0.4);
                font-size: 13px;
                font-weight: bold;
                border: none;
                border-radius: 12px;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 0.08);
                color: #1e293b;
            }
        """)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)
        layout.addLayout(header_layout)

        # 1. Instruction label with proper margins, word wrap, and no clipping
        info_label = QLabel(
            "Select a tool to annotate, or choose 'Select' to click & drag existing shapes to reposition them."
        )
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #64748b; font-size: 12px; margin: 0px 4px 2px 4px;")
        layout.addWidget(info_label)

        # 2. Rich Toolbar
        self.toolbar_frame = QFrame()
        self.toolbar_frame.setObjectName("annotateToolbar")
        self.toolbar_frame.setStyleSheet("""
            #annotateToolbar {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                padding: 4px;
            }
            QLabel {
                font-size: 12px;
                font-weight: 600;
                color: #475569;
            }
            QComboBox {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 500;
                color: #1e293b;
                min-width: 115px;
            }
            QComboBox:hover {
                border-color: #94a3b8;
            }
            QComboBox::drop-down {
                border: none;
                width: 18px;
            }
            QSpinBox {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 500;
                color: #1e293b;
                min-width: 80px;
            }
            QSpinBox:hover {
                border-color: #94a3b8;
            }
        """)
        toolbar_layout = QHBoxLayout(self.toolbar_frame)
        toolbar_layout.setContentsMargins(10, 6, 10, 6)
        toolbar_layout.setSpacing(10)

        # Tool selector
        toolbar_layout.addWidget(QLabel("Tool:"))
        self.tool_combo = QComboBox()
        self.tool_combo.addItem("👆 Select", "Select")
        self.tool_combo.addItem("✏️ Pen", "Pen")
        self.tool_combo.addItem("⬜ Rectangle", "Rectangle")
        self.tool_combo.addItem("⭕ Ellipse", "Ellipse")
        self.tool_combo.addItem("↗️ Arrow", "Arrow")
        self.tool_combo.addItem("🔤 Text", "Text")
        self.tool_combo.setCurrentIndex(1)
        self.tool_combo.currentIndexChanged.connect(self._on_tool_changed)
        toolbar_layout.addWidget(self.tool_combo)

        toolbar_layout.addSpacing(6)

        # Color picker button
        self.color_btn = QPushButton("🎨 Color")
        self.color_btn.clicked.connect(self._choose_color)
        toolbar_layout.addWidget(self.color_btn)

        toolbar_layout.addSpacing(6)

        # Thickness / Size control
        toolbar_layout.addWidget(QLabel("Size:"))
        self.thickness_spin = QSpinBox()
        self.thickness_spin.setRange(1, 50)
        self.thickness_spin.setValue(4)
        self.thickness_spin.setSuffix(" px")
        self.thickness_spin.valueChanged.connect(self._on_thickness_changed)
        toolbar_layout.addWidget(self.thickness_spin)

        toolbar_layout.addStretch()

        # Undo button
        self.undo_btn = QPushButton("↩ Undo")
        self.undo_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
                color: #1e293b;
            }
            QPushButton:hover {
                background-color: #f1f5f9;
                border-color: #94a3b8;
            }
            QPushButton:pressed {
                background-color: #e2e8f0;
            }
        """)
        self.undo_btn.clicked.connect(self._on_undo)
        toolbar_layout.addWidget(self.undo_btn)

        # Clear button
        self.clear_btn = QPushButton("🗑 Clear")
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
                color: #dc2626;
            }
            QPushButton:hover {
                background-color: #fee2e2;
                border-color: #fca5a5;
            }
            QPushButton:pressed {
                background-color: #fecaca;
            }
        """)
        self.clear_btn.clicked.connect(self._on_clear)
        toolbar_layout.addWidget(self.clear_btn)

        layout.addWidget(self.toolbar_frame)

        # 3. Canvas
        self.canvas = AnnotateCanvasWidget(self.file_path, self)
        layout.addWidget(self.canvas, stretch=1)

        # Update initial color swatch on button
        self._update_color_btn(self.canvas.current_color)

        # 4. Bottom action buttons
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        bottom_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #475569;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #1e293b;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)

        apply_btn = QPushButton("Apply Annotations")
        apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5500;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 22px;
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

    def _on_tool_changed(self, index: int) -> None:
        tool = self.tool_combo.itemData(index) or "Pen"
        self.canvas.set_tool(tool)

    def _on_thickness_changed(self, val: int) -> None:
        self.canvas.set_thickness(val)

    def _on_undo(self) -> None:
        self.canvas.undo()

    def _on_clear(self) -> None:
        self.canvas.clear()

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(self.canvas.current_color, self, "Select Annotation Color")
        if color.isValid():
            self.canvas.set_color(color)
            self._update_color_btn(color)

    def _update_color_btn(self, color: QColor) -> None:
        text_color = "#ffffff" if color.lightness() < 135 else "#1e293b"
        self.color_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {color.name()};
                color: {text_color};
                border: 1px solid rgba(0, 0, 0, 0.2);
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                border: 2px solid rgba(0, 0, 0, 0.4);
            }}
        """)

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def _on_apply(self) -> None:
        try:
            dir_name = os.path.dirname(os.path.abspath(self.file_path))
            base_name, ext = os.path.splitext(os.path.basename(self.file_path))
            output_path = os.path.join(dir_name, f"{base_name}_annotated{ext}")

            if os.path.abspath(output_path) == os.path.abspath(self.file_path):
                output_path = os.path.join(dir_name, f"{base_name}_annotated_1{ext}")

            full_pixmap = QPixmap(self.file_path)
            painter = QPainter(full_pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.canvas._render_all_items()
            painter.drawPixmap(0, 0, self.canvas.drawing_layer)
            painter.end()

            ext_lower = ext.lower()
            if ext_lower in (".jpg", ".jpeg"):
                full_pixmap.save(output_path, "JPEG", 95)
            elif ext_lower == ".png":
                full_pixmap.save(output_path, "PNG")
            elif ext_lower == ".webp":
                full_pixmap.save(output_path, "WEBP", 95)
            else:
                full_pixmap.save(output_path)

            print(f"[QuickDrop] Annotated '{self.file_path}' -> '{output_path}'")
            self.accept()
        except Exception as e:
            print(f"[QuickDrop ERROR] Annotate error: {e}")
            self.reject()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is not None and (
                child is self.canvas
                or self.canvas.isAncestorOf(child)
                or child is self.toolbar_frame
                or self.toolbar_frame.isAncestorOf(child)
            ):
                return
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


_CALC_OPERATORS = {
    ast.Add: _py_op.add,
    ast.Sub: _py_op.sub,
    ast.Mult: _py_op.mul,
    ast.Div: _py_op.truediv,
    ast.USub: _py_op.neg,
    ast.UAdd: _py_op.pos,
}


def _eval_arithmetic_ast(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Non-numeric constant")
    elif isinstance(node, ast.BinOp):
        left = _eval_arithmetic_ast(node.left)
        right = _eval_arithmetic_ast(node.right)
        op_type = type(node.op)
        if op_type in _CALC_OPERATORS:
            return _CALC_OPERATORS[op_type](left, right)
        raise ValueError("Unsupported operation")
    elif isinstance(node, ast.UnaryOp):
        operand = _eval_arithmetic_ast(node.operand)
        op_type = type(node.op)
        if op_type in _CALC_OPERATORS:
            return _CALC_OPERATORS[op_type](operand)
        raise ValueError("Unsupported unary operator")
    raise ValueError("Invalid expression element")


def _safe_calc_eval(expr_str: str) -> float:
    cleaned = expr_str.strip().replace("×", "*").replace("÷", "/")
    if not cleaned:
        return 0.0
    parsed = ast.parse(cleaned, mode="eval")
    return _eval_arithmetic_ast(parsed.body)


class CalcDialog(QDialog):
    """Frameless modern light-themed mini calculator dialog with keyboard and button support."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_start_pos = None

        self._expression = "0"
        self._reset_input = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(300, 420)

        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("calcContainer")
        container.setStyleSheet("""
            #calcContainer {
                background-color: #FFFFFF;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #111827;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Header: Centered title with subtle close button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)

        title_label = QLabel("Calculator")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #111827;")
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
                color: #111827;
            }
        """)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)
        layout.addLayout(header_layout)

        # Display screen
        self.display_label = QLabel("0")
        self.display_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.display_label.setStyleSheet("""
            QLabel {
                background-color: #F3F4F6;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 10px;
                color: #111827;
                font-size: 28px;
                font-weight: bold;
                padding: 10px 14px;
            }
        """)
        self.display_label.setFixedHeight(54)
        layout.addWidget(self.display_label)

        # Grid: 5 rows x 4 columns
        grid = QGridLayout()
        grid.setSpacing(8)

        num_style = """
            QPushButton {
                background-color: #F3F4F6;
                color: #111827;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                min-height: 44px;
            }
            QPushButton:hover {
                background-color: #E5E7EB;
            }
            QPushButton:pressed {
                background-color: #D1D5DB;
            }
        """

        op_style = """
            QPushButton {
                background-color: #ff5500;
                color: #ffffff;
                border: none;
                border-radius: 10px;
                font-size: 18px;
                font-weight: bold;
                min-height: 44px;
            }
            QPushButton:hover {
                background-color: #ff661a;
            }
            QPushButton:pressed {
                background-color: #e04b00;
            }
        """

        # Row 1: C, (, ), /
        btn_c = QPushButton("C")
        btn_c.setStyleSheet(op_style)
        btn_c.clicked.connect(self._on_clear)
        grid.addWidget(btn_c, 0, 0)

        btn_lpar = QPushButton("(")
        btn_lpar.setStyleSheet(num_style)
        btn_lpar.clicked.connect(lambda: self._on_input("("))
        grid.addWidget(btn_lpar, 0, 1)

        btn_rpar = QPushButton(")")
        btn_rpar.setStyleSheet(num_style)
        btn_rpar.clicked.connect(lambda: self._on_input(")"))
        grid.addWidget(btn_rpar, 0, 2)

        btn_div = QPushButton("/")
        btn_div.setStyleSheet(op_style)
        btn_div.clicked.connect(lambda: self._on_operator("/"))
        grid.addWidget(btn_div, 0, 3)

        # Row 2: 7, 8, 9, *
        for c, text in enumerate(["7", "8", "9"]):
            btn = QPushButton(text)
            btn.setStyleSheet(num_style)
            btn.clicked.connect(lambda _, t=text: self._on_input(t))
            grid.addWidget(btn, 1, c)

        btn_mul = QPushButton("*")
        btn_mul.setStyleSheet(op_style)
        btn_mul.clicked.connect(lambda: self._on_operator("*"))
        grid.addWidget(btn_mul, 1, 3)

        # Row 3: 4, 5, 6, -
        for c, text in enumerate(["4", "5", "6"]):
            btn = QPushButton(text)
            btn.setStyleSheet(num_style)
            btn.clicked.connect(lambda _, t=text: self._on_input(t))
            grid.addWidget(btn, 2, c)

        btn_sub = QPushButton("-")
        btn_sub.setStyleSheet(op_style)
        btn_sub.clicked.connect(lambda: self._on_operator("-"))
        grid.addWidget(btn_sub, 2, 3)

        # Row 4: 1, 2, 3, +
        for c, text in enumerate(["1", "2", "3"]):
            btn = QPushButton(text)
            btn.setStyleSheet(num_style)
            btn.clicked.connect(lambda _, t=text: self._on_input(t))
            grid.addWidget(btn, 3, c)

        btn_add = QPushButton("+")
        btn_add.setStyleSheet(op_style)
        btn_add.clicked.connect(lambda: self._on_operator("+"))
        grid.addWidget(btn_add, 3, 3)

        # Row 5: 0 (span 2 cols), ., =
        btn_0 = QPushButton("0")
        btn_0.setStyleSheet(num_style)
        btn_0.clicked.connect(lambda: self._on_input("0"))
        grid.addWidget(btn_0, 4, 0, 1, 2)

        btn_dot = QPushButton(".")
        btn_dot.setStyleSheet(num_style)
        btn_dot.clicked.connect(lambda: self._on_input("."))
        grid.addWidget(btn_dot, 4, 2)

        btn_eq = QPushButton("=")
        btn_eq.setStyleSheet(op_style)
        btn_eq.clicked.connect(self._on_equals)
        grid.addWidget(btn_eq, 4, 3)

        layout.addLayout(grid)
        outer_layout.addWidget(container)

    def _update_display(self, text: str) -> None:
        if len(text) > 16:
            text = text[-16:]
        self.display_label.setText(text)

    def _on_input(self, char: str) -> None:
        if self._reset_input:
            if char in "0123456789.()":
                self._expression = ""
            self._reset_input = False

        if self._expression in ("0", "Error") and char != ".":
            self._expression = char
        else:
            self._expression += char

        self._update_display(self._expression)

    def _on_operator(self, op: str) -> None:
        if self._reset_input:
            if self._expression == "Error":
                self._expression = "0"
            self._reset_input = False

        if self._expression and self._expression[-1] in "+-*/":
            self._expression = self._expression[:-1] + op
        else:
            self._expression += op

        self._update_display(self._expression)

    def _on_clear(self) -> None:
        self._expression = "0"
        self._reset_input = False
        self._update_display("0")

    def _on_backspace(self) -> None:
        if self._reset_input or self._expression in ("Error", "0") or len(self._expression) <= 1:
            self._expression = "0"
            self._reset_input = False
        else:
            self._expression = self._expression[:-1]
        self._update_display(self._expression)

    def _on_equals(self) -> None:
        if not self._expression or self._expression == "Error":
            return
        try:
            val = _safe_calc_eval(self._expression)
            if abs(val - round(val)) < 1e-10:
                result_str = str(int(round(val)))
            else:
                result_str = f"{val:.8g}"
            self._expression = result_str
            self._reset_input = True
            self._update_display(self._expression)
        except ZeroDivisionError:
            self._expression = "Error"
            self._reset_input = True
            self._update_display("Error")
        except Exception:
            self._expression = "Error"
            self._reset_input = True
            self._update_display("Error")

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
        key = event.key()
        text = event.text()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.reject()
        elif text in "0123456789.()":
            self._on_input(text)
        elif text in ("+", "-", "*", "/"):
            self._on_operator(text)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or text == "=":
            self._on_equals()
        elif key == Qt.Key.Key_Backspace:
            self._on_backspace()
        elif text in ("c", "C"):
            self._on_clear()
        else:
            super().keyPressEvent(event)


class TimerDialog(QDialog):
    """Frameless light-themed countdown timer dialog with customizable minutes/seconds and audio-visual alert."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_start_pos = None

        self._total_seconds = 300
        self._remaining_seconds = 300
        self._is_running = False

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(320, 360)

        self._setup_ui()
        self._center_on_screen()
        self._update_display()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("timerContainer")
        container.setStyleSheet("""
            #timerContainer {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Header: Centered title with subtle close button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)

        title_label = QLabel("Timer")
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

        # Large Digital Countdown Display
        self.time_display = QLabel("05:00")
        self.time_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_display.setStyleSheet("""
            QLabel {
                background-color: #f5f5f7;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 12px;
                color: #1c1c1e;
                font-size: 44px;
                font-weight: bold;
                padding: 12px 10px;
                letter-spacing: 2px;
            }
        """)
        layout.addWidget(self.time_display)

        # Inputs: Minutes & Seconds spinboxes
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        m_lbl = QLabel("Minutes:")
        m_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #222222;")
        self.m_input = QSpinBox()
        self.m_input.setRange(0, 999)
        self.m_input.setValue(5)
        self.m_input.setStyleSheet("""
            QSpinBox {
                background-color: #ffffff;
                color: #222222;
                border: 1px solid rgba(0, 0, 0, 0.16);
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
                font-weight: 600;
                min-width: 55px;
            }
        """)
        self.m_input.valueChanged.connect(self._on_input_changed)

        s_lbl = QLabel("Seconds:")
        s_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #222222;")
        self.s_input = QSpinBox()
        self.s_input.setRange(0, 59)
        self.s_input.setValue(0)
        self.s_input.setStyleSheet("""
            QSpinBox {
                background-color: #ffffff;
                color: #222222;
                border: 1px solid rgba(0, 0, 0, 0.16);
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
                font-weight: 600;
                min-width: 55px;
            }
        """)
        self.s_input.valueChanged.connect(self._on_input_changed)

        input_row.addWidget(m_lbl)
        input_row.addWidget(self.m_input)
        input_row.addSpacing(10)
        input_row.addWidget(s_lbl)
        input_row.addWidget(self.s_input)
        layout.addLayout(input_row)

        # Quick preset chips
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        presets = [("1m", 1), ("5m", 5), ("10m", 10), ("25m", 25)]
        for label, mins in presets:
            btn = QPushButton(label)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 0, 0, 0.05);
                    color: #333333;
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 6px;
                    padding: 4px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: rgba(0, 0, 0, 0.09);
                    color: #111111;
                }
            """)
            btn.clicked.connect(lambda _, m=mins: self._set_preset(m))
            preset_row.addWidget(btn)
        layout.addLayout(preset_row)

        # Bottom Buttons: Reset on left, Start/Pause on right
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #444444;
                border: none;
                border-radius: 8px;
                padding: 8px 18px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #222222;
            }
        """)
        self.reset_btn.clicked.connect(self._on_reset)
        btn_layout.addWidget(self.reset_btn)

        btn_layout.addStretch()

        self.toggle_btn = QPushButton("Start")
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5500;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 22px;
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
        self.toggle_btn.clicked.connect(self._toggle_timer)
        btn_layout.addWidget(self.toggle_btn)

        layout.addLayout(btn_layout)
        outer_layout.addWidget(container)

    def _set_preset(self, minutes: int) -> None:
        self._timer.stop()
        self._is_running = False
        self.toggle_btn.setText("Start")
        self.m_input.setValue(minutes)
        self.s_input.setValue(0)
        self._total_seconds = minutes * 60
        self._remaining_seconds = self._total_seconds
        self._update_display()

    def _on_input_changed(self) -> None:
        if not self._is_running:
            self._total_seconds = self.m_input.value() * 60 + self.s_input.value()
            self._remaining_seconds = self._total_seconds
            self._update_display()

    def _update_display(self) -> None:
        mins = self._remaining_seconds // 60
        secs = self._remaining_seconds % 60
        self.time_display.setText(f"{mins:02d}:{secs:02d}")

    def _toggle_timer(self) -> None:
        if self._is_running:
            self._timer.stop()
            self._is_running = False
            self.toggle_btn.setText("Resume")
        else:
            if self._remaining_seconds <= 0:
                self._remaining_seconds = self.m_input.value() * 60 + self.s_input.value()
            if self._remaining_seconds > 0:
                self._timer.start()
                self._is_running = True
                self.toggle_btn.setText("Pause")

    def _on_tick(self) -> None:
        if self._remaining_seconds > 0:
            self._remaining_seconds -= 1
            self._update_display()

        if self._remaining_seconds <= 0:
            self._timer.stop()
            self._is_running = False
            self.toggle_btn.setText("Start")
            self.time_display.setText("00:00")
            QApplication.beep()

    def _on_reset(self) -> None:
        self._timer.stop()
        self._is_running = False
        self.toggle_btn.setText("Start")
        self._remaining_seconds = self.m_input.value() * 60 + self.s_input.value()
        self._update_display()

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
            self.reject()
        elif event.key() == Qt.Key.Key_Space:
            self._toggle_timer()
        else:
            super().keyPressEvent(event)


class RecorderThread(QThread):
    """Background worker thread for continuous screen recording (ffmpeg on Linux, mss+cv2 on Windows/macOS)."""

    recording_started = pyqtSignal()
    recording_finished = pyqtSignal(str)
    recording_error = pyqtSignal(str)

    def __init__(self, output_path: str, fps: int = 25, parent=None) -> None:
        super().__init__(parent)
        self.output_path = output_path
        self.fps = fps
        self._is_running = True
        self.process = None
        self._terminated = False

    def run(self) -> None:
        if sys.platform.startswith("linux"):
            self._run_linux()
        else:
            self._run_windows_macos()

    def _run_linux(self) -> None:
        try:
            self._recorded_path = ""
            # Native GNOME D-Bus Screencast for Wayland on Linux
            try:
                from PyQt6.QtDBus import QDBusConnection, QDBusInterface
                self._dbus_iface = QDBusInterface(
                    "org.gnome.Shell.Screencast",
                    "/org/gnome/Shell/Screencast",
                    "org.gnome.Shell.Screencast",
                    QDBusConnection.sessionBus(),
                )
            except Exception:
                self._dbus_iface = None

            if self._dbus_iface and self._dbus_iface.isValid():
                reply = self._dbus_iface.call("Screencast", "quickdrop_%d.webm", {})
                args = reply.arguments()
                if args and args[0] and len(args) > 1:
                    self._recorded_path = str(args[1])
            else:
                self.process = subprocess.Popen(
                    [
                        'gdbus', 'call', '--session',
                        '--dest', 'org.gnome.Shell.Screencast',
                        '--object-path', '/org/gnome/Shell/Screencast',
                        '--method', 'org.gnome.Shell.Screencast.Screencast',
                        'quickdrop_%d.webm', '{}'
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            self.recording_started.emit()

            while self._is_running:
                time.sleep(0.1)

        except Exception as exc:
            self.recording_error.emit(str(exc))
            return
        finally:
            if hasattr(self, "_dbus_iface") and self._dbus_iface and self._dbus_iface.isValid():
                try:
                    self._dbus_iface.call("StopScreencast")
                except Exception:
                    pass
            try:
                subprocess.Popen(
                    [
                        'gdbus', 'call', '--session',
                        '--dest', 'org.gnome.Shell.Screencast',
                        '--object-path', '/org/gnome/Shell/Screencast',
                        '--method', 'org.gnome.Shell.Screencast.StopScreencast'
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass

        time.sleep(0.5)
        if not self._recorded_path:
            import glob
            files = sorted(glob.glob(os.path.expanduser("~/Videos/quickdrop*")), key=os.path.getmtime)
            if files:
                self._recorded_path = files[-1]

        final_path = self._recorded_path if self._recorded_path and os.path.exists(self._recorded_path) else self.output_path
        self.recording_finished.emit(final_path)

    def _run_windows_macos(self) -> None:
        try:
            import mss
            import cv2
            import numpy as np
        except ImportError as exc:
            self.recording_error.emit(f"Missing dependency: {exc}")
            return

        writer = None
        try:
            sct_factory = getattr(mss, "MSS", None) or getattr(mss, "mss", None)
            with sct_factory() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                width = int(mon["width"])
                height = int(mon["height"])

                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(self.output_path, fourcc, float(self.fps), (width, height))
                if not writer.isOpened():
                    self.recording_error.emit("Failed to initialize video encoder.")
                    return

                self.recording_started.emit()
                frame_interval = 1.0 / max(1, self.fps)

                while self._is_running:
                    t_start = time.perf_counter()
                    raw_frame = np.array(sct.grab(mon))
                    bgr_frame = cv2.cvtColor(raw_frame, cv2.COLOR_BGRA2BGR)
                    writer.write(bgr_frame)

                    elapsed = time.perf_counter() - t_start
                    sleep_time = frame_interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)

        except Exception as exc:
            self.recording_error.emit(str(exc))
            return
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass

        self.recording_finished.emit(self.output_path)

    def stop(self) -> None:
        """Signal recording loop to stop."""
        self._is_running = False
        if sys.platform.startswith("linux"):
            if hasattr(self, "_dbus_iface") and self._dbus_iface and self._dbus_iface.isValid():
                try:
                    self._dbus_iface.call("StopScreencast")
                except Exception:
                    pass
            try:
                subprocess.Popen(
                    [
                        'gdbus', 'call', '--session',
                        '--dest', 'org.gnome.Shell.Screencast',
                        '--object-path', '/org/gnome/Shell/Screencast',
                        '--method', 'org.gnome.Shell.Screencast.StopScreencast'
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass


class StopRecordingWorker(QThread):
    """Background worker thread that stops recording and finalizes DBus/processes without blocking UI."""

    def __init__(self, recorder_thread: RecorderThread | None, parent=None) -> None:
        super().__init__(parent)
        self.recorder_thread = recorder_thread

    def run(self) -> None:
        if self.recorder_thread and self.recorder_thread.isRunning():
            self.recorder_thread.stop()
            self.recorder_thread.wait()


class RecordDialog(QDialog):
    """Frameless light-themed floating screen recorder dialog with threaded capture and live timer."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_start_pos = None

        self._is_recording = False
        self._elapsed_seconds = 0
        self._recorder_thread: RecorderThread | None = None
        self._stop_worker: StopRecordingWorker | None = None
        self._current_output_path = ""

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(1000)
        self._ui_timer.timeout.connect(self._on_tick)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(340, 360)

        self._setup_ui()
        self._center_on_screen()

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @is_recording.setter
    def is_recording(self, val: bool) -> None:
        self._is_recording = bool(val)

    def _setup_ui(self) -> None:
        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(10, 10, 10, 10)

        self.container = QFrame()
        self.container.setObjectName("recordContainer")
        self.container.setStyleSheet("""
            #recordContainer {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 16px;
            }
            QLabel {
                color: #222222;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self.container)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)

        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(20, 16, 20, 16)
        self.container_layout.setSpacing(12)

        # Header: Centered title with close button
        self.header_widget = QWidget()
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addSpacing(22)

        self.title_label = QLabel("Screen Recorder")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #222222;")
        header_layout.addWidget(self.title_label)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setStyleSheet("""
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
        self.close_btn.clicked.connect(self.reject)
        header_layout.addWidget(self.close_btn)
        self.container_layout.addWidget(self.header_widget)

        # Status badge pill
        self.status_pill = QLabel("● Standby")
        self.status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_pill.setStyleSheet("""
            QLabel {
                background-color: #f3f4f6;
                color: #6b7280;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
                border-radius: 10px;
            }
        """)
        self.container_layout.addWidget(self.status_pill)

        # Large Digital Timer Display ("00:00:00")
        self.timer_display = QLabel("00:00:00")
        self.timer_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer_display.setStyleSheet("""
            QLabel {
                background-color: #f5f5f7;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 12px;
                color: #1c1c1e;
                font-size: 38px;
                font-weight: bold;
                padding: 12px 8px;
                letter-spacing: 2px;
            }
        """)
        self.container_layout.addWidget(self.timer_display)

        # Status / Destination text
        self.info_label = QLabel("Ready to record primary screen (25 FPS)")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("font-size: 11px; color: #666666;")
        self.container_layout.addWidget(self.info_label)

        # Open file button (initially hidden)
        self.open_folder_btn = QPushButton("Open Recording Folder")
        self.open_folder_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 87, 34, 0.08);
                color: #ff5500;
                border: 1px solid rgba(255, 87, 34, 0.3);
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(255, 87, 34, 0.16);
            }
        """)
        self.open_folder_btn.hide()
        self.open_folder_btn.clicked.connect(self._open_recording_folder)
        self.container_layout.addWidget(self.open_folder_btn)

        # Action Buttons: Stop (Left) and Start (Right)
        self.btn_layout = QHBoxLayout()
        self.btn_layout.setSpacing(10)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #f3f4f6;
                color: #4b5563;
                border: none;
                border-radius: 8px;
                padding: 8px 18px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #e5e7eb;
                color: #111827;
            }
            QPushButton:disabled {
                background-color: rgba(0, 0, 0, 0.04);
                color: rgba(0, 0, 0, 0.25);
            }
        """)
        self.stop_btn.clicked.connect(self._stop_recording)
        self.btn_layout.addWidget(self.stop_btn)

        self.btn_layout.addStretch()

        self.start_btn = QPushButton("Start")
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5500;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 22px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #ff661a;
            }
            QPushButton:pressed {
                background-color: #e04b00;
            }
            QPushButton:disabled {
                background-color: rgba(255, 85, 0, 0.35);
                color: rgba(255, 255, 255, 0.7);
            }
        """)
        self.start_btn.clicked.connect(self._start_recording)
        self.btn_layout.addWidget(self.start_btn)

        self.container_layout.addLayout(self.btn_layout)
        self.outer_layout.addWidget(self.container)

    def _set_mini_mode(self, mini: bool) -> None:
        """Switch between standard dialog and compact bottom-right floating stop control."""
        if mini:
            self.header_widget.hide()
            self.status_pill.hide()
            self.timer_display.hide()
            self.info_label.hide()
            self.open_folder_btn.hide()
            self.start_btn.hide()

            self.stop_btn.setText("■ Stop")
            self.stop_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ef4444;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 8px 14px;
                    font-weight: bold;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #dc2626;
                }
                QPushButton:disabled {
                    background-color: rgba(239, 68, 68, 0.4);
                    color: rgba(255, 255, 255, 0.7);
                }
            """)

            self.container_layout.setContentsMargins(8, 6, 8, 6)
            self.outer_layout.setContentsMargins(4, 4, 4, 4)
            self.setFixedSize(140, 52)

            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                x = geo.right() - self.width() - 24
                y = geo.bottom() - self.height() - 24
                self.move(x, y)
        else:
            self.header_widget.show()
            self.status_pill.show()
            self.timer_display.show()
            self.info_label.show()
            self.start_btn.show()

            self.stop_btn.setText("Stop")
            self.stop_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f3f4f6;
                    color: #4b5563;
                    border: none;
                    border-radius: 8px;
                    padding: 8px 18px;
                    font-weight: 600;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background-color: #e5e7eb;
                    color: #111827;
                }
                QPushButton:disabled {
                    background-color: rgba(0, 0, 0, 0.04);
                    color: rgba(0, 0, 0, 0.25);
                }
            """)

            self.container_layout.setContentsMargins(20, 16, 20, 16)
            self.outer_layout.setContentsMargins(10, 10, 10, 10)
            self.setFixedSize(340, 360)
            self._center_on_screen()
            self.activateWindow()
            self.raise_()

    def _start_recording(self) -> None:
        if self._is_recording:
            return

        videos_dir = os.path.expanduser("~/Videos")
        if not os.path.isdir(videos_dir):
            try:
                os.makedirs(videos_dir, exist_ok=True)
            except Exception:
                videos_dir = os.path.expanduser("~")
        if not os.path.isdir(videos_dir):
            videos_dir = os.path.expanduser("~")

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"QuickDrop_Recording_{timestamp}.mp4"
        self._current_output_path = os.path.join(videos_dir, filename)

        self._recorder_thread = RecorderThread(self._current_output_path, fps=25, parent=self)
        self._recorder_thread.recording_started.connect(self._on_started)
        self._recorder_thread.recording_finished.connect(self._on_finished)
        self._recorder_thread.recording_error.connect(self._on_error)

        self._elapsed_seconds = 0
        self._update_timer_label()
        self.open_folder_btn.hide()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_pill.setText("● Recording...")
        self.status_pill.setStyleSheet("""
            QLabel {
                background-color: #fee2e2;
                color: #ef4444;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
                border-radius: 10px;
            }
        """)
        self.info_label.setText(f"Recording to: {filename}")

        self._is_recording = True
        self._recorder_thread.start()
        self._ui_timer.start()
        self._set_mini_mode(True)
        if self.parent():
            self.parent().hide()

    def _stop_recording(self) -> None:
        if not self._is_recording:
            return

        self._is_recording = False

        # Immediately disable buttons and show "Saving..." text/state
        self.stop_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setText("Saving...")
        self.status_pill.setText("● Saving...")
        self.status_pill.setStyleSheet("""
            QLabel {
                background-color: #fef3c7;
                color: #d97706;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
                border-radius: 10px;
            }
        """)
        self.info_label.setText("Saving video...")
        self._ui_timer.stop()

        # Run the blocking stop / DBus finalization in the background worker thread
        self._stop_worker = StopRecordingWorker(self._recorder_thread, parent=self)
        self._stop_worker.finished.connect(self._on_stop_finished)
        self._stop_worker.start()

    def _on_stop_finished(self) -> None:
        self._set_mini_mode(False)
        if self.parent():
            self.parent().showNormal()
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Start New")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stop")

        if not self._current_output_path and self._recorder_thread:
            rec_path = getattr(self._recorder_thread, "_recorded_path", "")
            if rec_path:
                self._current_output_path = rec_path

        filename = os.path.basename(self._current_output_path) if self._current_output_path else "video.webm"
        self.status_pill.setText("✓ Saved")
        self.status_pill.setStyleSheet("""
            QLabel {
                background-color: #dcfce7;
                color: #16a34a;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
                border-radius: 10px;
            }
        """)
        self.info_label.setText(f"Saved! {filename}")
        self.open_folder_btn.show()
        self._stop_worker = None

    def _on_started(self) -> None:
        pass

    def _on_finished(self, saved_path: str) -> None:
        if saved_path:
            self._current_output_path = saved_path
        if not self._stop_worker:
            self._on_stop_finished()

    def _on_error(self, err_msg: str) -> None:
        self._set_mini_mode(False)
        if self.parent():
            self.parent().showNormal()
        self._is_recording = False
        self._ui_timer.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stop")
        self.status_pill.setText("✕ Error")
        self.status_pill.setStyleSheet("""
            QLabel {
                background-color: #fee2e2;
                color: #dc2626;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
                border-radius: 10px;
            }
        """)
        self.info_label.setText(f"Error: {err_msg}")
        self._stop_worker = None

    def _on_tick(self) -> None:
        self._elapsed_seconds += 1
        self._update_timer_label()

    def _update_timer_label(self) -> None:
        hrs = self._elapsed_seconds // 3600
        mins = (self._elapsed_seconds % 3600) // 60
        secs = self._elapsed_seconds % 60
        self.timer_display.setText(f"{hrs:02d}:{mins:02d}:{secs:02d}")

    def _open_recording_folder(self) -> None:
        if not self._current_output_path:
            return
        folder = os.path.dirname(os.path.abspath(self._current_output_path))
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

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
            self.reject()
        elif event.key() == Qt.Key.Key_Space:
            if self._is_recording:
                self._stop_recording()
            else:
                self._start_recording()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._is_recording and self._recorder_thread and self._recorder_thread.isRunning():
            self._recorder_thread.stop()
            self._recorder_thread.wait(4000)
        if self.parent():
            self.parent().showNormal()
        super().closeEvent(event)

    def reject(self) -> None:
        if self._is_recording and self._recorder_thread and self._recorder_thread.isRunning():
            self._recorder_thread.stop()
            self._recorder_thread.wait(4000)
        if self.parent():
            self.parent().showNormal()
        super().reject()


def _get_local_lan_ip() -> str:
    """Dynamically determine the active outbound LAN IP address without external network calls."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class _SingleFileHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler to serve a single shared file with proper download headers."""

    file_path: str = ""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if not self.file_path or not os.path.isfile(self.file_path):
            self.send_error(404, "File Not Found")
            return

        filename = os.path.basename(self.file_path)
        encoded_filename = urllib.parse.quote(filename)
        mime_type, _ = mimetypes.guess_type(self.file_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        try:
            file_size = os.path.getsize(self.file_path)
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}',
            )
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            with open(self.file_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except Exception:
            pass

    def do_HEAD(self):
        if not self.file_path or not os.path.isfile(self.file_path):
            self.send_error(404, "File Not Found")
            return
        filename = os.path.basename(self.file_path)
        encoded_filename = urllib.parse.quote(filename)
        mime_type, _ = mimetypes.guess_type(self.file_path)
        if not mime_type:
            mime_type = "application/octet-stream"
        file_size = os.path.getsize(self.file_path)
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}',
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()


class LocalShareWorker(QThread):
    """Background worker hosting a local HTTP server on an available port to share a specific file."""

    server_started = pyqtSignal(str, int, str)
    server_error = pyqtSignal(str)

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = os.path.abspath(file_path)
        self.local_ip = _get_local_lan_ip()
        self.port: int = 0
        self.share_url: str = ""
        self.server: http.server.ThreadingHTTPServer | None = None

        try:
            handler_cls = type(
                "_FileHandler",
                (_SingleFileHTTPHandler,),
                {"file_path": self.file_path},
            )
            self.server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), handler_cls)
            self.port = self.server.server_address[1]
            filename = os.path.basename(self.file_path)
            encoded_name = urllib.parse.quote(filename)
            self.share_url = f"http://{self.local_ip}:{self.port}/{encoded_name}"
        except Exception as exc:
            print(f"[QuickDrop AirDrop] Init error: {exc}")

    def run(self) -> None:
        if not self.server:
            self.server_error.emit("Failed to initialize HTTP server socket")
            return

        try:
            print(f"[QuickDrop AirDrop] Serving on {self.share_url}")
            self.server_started.emit(self.local_ip, self.port, self.share_url)
            self.server.serve_forever()
        except Exception as exc:
            print(f"[QuickDrop AirDrop] Server loop exception: {exc}")
            self.server_error.emit(str(exc))
        finally:
            if self.server:
                try:
                    self.server.server_close()
                except Exception:
                    pass

    def stop(self) -> None:
        """Gracefully shut down the HTTP server and release port."""
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None


def _generate_qr_pixmap(data_text: str, size: int = 200) -> QPixmap:
    """Generate a high-contrast QR code QPixmap for the given URL/text."""
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(data_text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#1e293b", back_color="#ffffff")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qimg = QImage.fromData(buf.getvalue())
        pixmap = QPixmap.fromImage(qimg)
        return pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception as exc:
        print(f"[QuickDrop AirDrop] QR code generation error: {exc}")
        fallback = QPixmap(size, size)
        fallback.fill(QColor("#f1f5f9"))
        return fallback


class ShareDialog(QDialog):
    """Frameless modern light-themed dialog for AirDrop / Local LAN file sharing via QR code and URL."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = os.path.abspath(file_path)
        self._drag_start_pos = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(410)

        # Initialize local file share worker
        self.worker = LocalShareWorker(self.file_path, parent=self)
        self.worker.start()

        self._setup_ui()
        self.adjustSize()
        self._center_on_screen()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("shareContainer")
        container.setStyleSheet("""
            #shareContainer {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 18px;
            }
            QLabel {
                color: #1e293b;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        # Header: Title + Close Button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(24)

        title_label = QLabel("📡 AirDrop / Local Share")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e293b;")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(0, 0, 0, 0.4);
                font-size: 13px;
                font-weight: bold;
                border: none;
                border-radius: 12px;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 0.08);
                color: #1e293b;
            }
        """)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)
        layout.addLayout(header_layout)

        # File info banner
        file_size_bytes = os.path.getsize(self.file_path) if os.path.isfile(self.file_path) else 0
        formatted_size = self._format_size(file_size_bytes)
        filename = os.path.basename(self.file_path)

        file_banner = QFrame()
        file_banner.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                padding: 6px;
            }
        """)
        banner_layout = QHBoxLayout(file_banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)

        file_icon_label = QLabel("📦")
        file_icon_label.setStyleSheet("font-size: 18px;")
        banner_layout.addWidget(file_icon_label)

        file_details = QVBoxLayout()
        file_details.setSpacing(2)
        name_lbl = QLabel(filename)
        name_lbl.setStyleSheet("font-weight: 600; font-size: 13px; color: #0f172a;")
        size_lbl = QLabel(formatted_size)
        size_lbl.setStyleSheet("font-size: 11px; color: #64748b;")
        file_details.addWidget(name_lbl)
        file_details.addWidget(size_lbl)
        banner_layout.addLayout(file_details)
        banner_layout.addStretch()

        layout.addWidget(file_banner)

        # QR Code Display
        qr_container = QFrame()
        qr_container.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 14px;
            }
        """)
        qr_layout = QVBoxLayout(qr_container)
        qr_layout.setContentsMargins(10, 10, 10, 10)
        qr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        qr_pix = _generate_qr_pixmap(self.worker.share_url, size=175)
        self.qr_label = QLabel()
        self.qr_label.setPixmap(qr_pix)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(self.qr_label)
        layout.addWidget(qr_container)

        # Instructions & Connection Guide
        instructions_frame = QFrame()
        instructions_frame.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
            }
        """)
        instructions_layout = QVBoxLayout(instructions_frame)
        instructions_layout.setContentsMargins(12, 10, 12, 10)
        instructions_layout.setSpacing(4)

        how_to_label = QLabel(
            "<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif;'>"
            "<div style='font-weight: 700; font-size: 11.5px; color: #1e293b; margin-bottom: 4px;'>💡 How to connect:</div>"
            "<ol style='margin: 0; padding-left: 18px; font-size: 11px; color: #475569; line-height: 1.5;'>"
            "<li style='margin-bottom: 2px;'>Connect both devices to the same Wi-Fi, <b>OR</b> turn on this PC's Wi-Fi Hotspot.</li>"
            "<li style='margin-bottom: 2px;'>Connect your phone to that Wi-Fi/Hotspot.</li>"
            "<li style='margin-bottom: 2px;'>Turn <b>OFF</b> mobile data (4G/5G) on your phone.</li>"
            "<li>Scan the QR code to download.</li>"
            "</ol>"
            "</div>"
        )
        how_to_label.setTextFormat(Qt.TextFormat.RichText)
        how_to_label.setWordWrap(True)
        instructions_layout.addWidget(how_to_label)
        layout.addWidget(instructions_frame)

        # Direct URL & Copy row
        url_layout = QHBoxLayout()
        url_layout.setSpacing(8)

        self.url_edit = QLineEdit(self.worker.share_url)
        self.url_edit.setReadOnly(True)
        self.url_edit.setStyleSheet("""
            QLineEdit {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                color: #334155;
            }
        """)
        url_layout.addWidget(self.url_edit)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
            QPushButton:pressed {
                background-color: #075985;
            }
        """)
        self.copy_btn.clicked.connect(self._copy_url)
        url_layout.addWidget(self.copy_btn)
        layout.addLayout(url_layout)

        # Stop Sharing button
        self.stop_btn = QPushButton("Stop Sharing")
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #fee2e2;
                color: #dc2626;
                border: 1px solid #fca5a5;
                border-radius: 8px;
                padding: 8px;
                font-weight: 600;
                font-size: 13px;
                margin-top: 4px;
            }
            QPushButton:hover {
                background-color: #fecaca;
            }
            QPushButton:pressed {
                background-color: #fca5a5;
            }
        """)
        self.stop_btn.clicked.connect(self.reject)
        layout.addWidget(self.stop_btn)

        outer_layout.addWidget(container)

    def _copy_url(self) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(self.worker.share_url)
        self.copy_btn.setText("Copied! ✓")
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #16a34a;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }
        """)
        QTimer.singleShot(1500, self._reset_copy_btn)

    def _reset_copy_btn(self) -> None:
        self.copy_btn.setText("Copy")
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
            QPushButton:pressed {
                background-color: #075985;
            }
        """)

    def _format_size(self, num_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if num_bytes < 1024.0:
                return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} TB"

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is not None and isinstance(child, (QPushButton, QLineEdit)):
                return
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

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1000)
        super().closeEvent(event)

    def reject(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1000)
        super().reject()


class AIDetectorWorker(QThread):
    """Background worker sending an image to Hugging Face AI-image-detector online API."""

    API_URL = "https://api-inference.huggingface.co/models/umm-maybe/AI-image-detector"

    progress_updated = pyqtSignal(int, str)
    scan_finished = pyqtSignal(dict)
    scan_error = pyqtSignal(str)

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = os.path.abspath(file_path)
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        if not os.path.isfile(self.file_path):
            self.scan_error.emit(f"File not found: {self.file_path}")
            return

        api_key = (os.getenv("HF_API_KEY") or os.getenv("HUGGINGFACE_API_KEY") or "").strip()
        if not api_key or api_key == "your_huggingface_token_here":
            self.scan_error.emit("API Key Missing: Please set your HF_API_KEY in the .env file.")
            return

        try:
            self.progress_updated.emit(20, "Reading image bytes...")
            with open(self.file_path, "rb") as f:
                image_data = f.read()

            if self._is_cancelled or self.isInterruptionRequested():
                return

            self.progress_updated.emit(45, "Contacting Hugging Face AI detector...")

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/octet-stream",
            }

            self.progress_updated.emit(70, "Running deep neural classification...")
            response = requests.post(
                self.API_URL,
                headers=headers,
                data=image_data,
                timeout=25,
            )

            if self._is_cancelled or self.isInterruptionRequested():
                return

            if response.status_code != 200:
                error_msg = ""
                try:
                    err_payload = response.json()
                    if isinstance(err_payload, dict):
                        error_msg = str(err_payload.get("error", response.text))
                    else:
                        error_msg = str(err_payload)
                except Exception:
                    error_msg = response.text or f"HTTP {response.status_code}: {response.reason}"

                print(f"AI API ERROR: HTTP {response.status_code} - {error_msg}")

                if response.status_code == 503 or "loading" in error_msg.lower() or "warm" in error_msg.lower():
                    self.scan_error.emit("Model is warming up, please wait 20 seconds and try again.")
                elif response.status_code == 401:
                    self.scan_error.emit("API Error: Unauthorized (Invalid Hugging Face API key).")
                elif response.status_code == 403:
                    self.scan_error.emit("API Error: Forbidden (Check Hugging Face token permissions).")
                elif response.status_code == 429:
                    self.scan_error.emit("API Error: Rate Limit Reached. Please wait a moment and try again.")
                else:
                    self.scan_error.emit(f"API Error ({response.status_code}): {error_msg}")
                return

            data = response.json()

            # Handle possible error dictionary inside 200 response
            if isinstance(data, dict) and "error" in data:
                err_text = str(data["error"])
                print(f"AI API ERROR: {err_text}")
                if "loading" in err_text.lower() or "warm" in err_text.lower():
                    self.scan_error.emit("Model is warming up, please wait 20 seconds and try again.")
                else:
                    self.scan_error.emit(f"API Error: {err_text}")
                return

            # Extract list of predictions
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                items = data[0]
            elif isinstance(data, list):
                items = data
            else:
                items = []

            if not items:
                err_msg = "Empty response received from AI model."
                print(f"AI API ERROR: {err_msg}")
                self.scan_error.emit(f"API Error: {err_msg}")
                return

            self.progress_updated.emit(95, "Evaluating prediction scores...")

            artificial_score = 0.0
            human_score = 0.0

            for item in items:
                if not isinstance(item, dict):
                    continue
                lbl = str(item.get("label", "")).lower().strip()
                score = float(item.get("score", 0.0))
                if any(k in lbl for k in ("artificial", "ai", "fake", "synthetic", "generated")):
                    artificial_score = max(artificial_score, score)
                elif any(k in lbl for k in ("human", "real", "authentic", "natural")):
                    human_score = max(human_score, score)

            # Fallback if specific labels not matched
            if artificial_score == 0.0 and human_score == 0.0:
                top_item = max(items, key=lambda x: x.get("score", 0.0) if isinstance(x, dict) else 0.0)
                lbl = str(top_item.get("label", "")).lower().strip()
                score = float(top_item.get("score", 0.0))
                if any(k in lbl for k in ("art", "ai", "synth", "fake")):
                    artificial_score = score
                else:
                    human_score = score

            is_ai = artificial_score >= human_score
            if is_ai:
                confidence = int(round(artificial_score * 100))
                result_str = f"⚠️ AI Generated: {confidence}% Confidence"
                detail = f"Hugging Face neural classifier detected synthetic image patterns ({confidence}% confidence)."
            else:
                confidence = int(round(human_score * 100)) if human_score > 0 else int(round((1.0 - artificial_score) * 100))
                result_str = f"✅ Authentic: {confidence}%"
                detail = f"Hugging Face neural classifier verified natural photographic sensor patterns ({confidence}% confidence)."

            self.progress_updated.emit(100, "Analysis complete!")
            self.scan_finished.emit({
                "file_path": self.file_path,
                "is_ai": is_ai,
                "confidence": confidence,
                "result_str": result_str,
                "detail": detail,
            })

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            print(f"AI API ERROR: {e}")
            self.scan_error.emit("Internet not available: Please check your network connection.")
        except requests.exceptions.RequestException as e:
            print(f"AI API ERROR: {e}")
            self.scan_error.emit("Internet not available: Please check your network connection.")
        except Exception as e:
            print(f"AI API ERROR: {e}")
            self.scan_error.emit(f"Error: {e}")


class AIDetectDialog(QDialog):
    """Frameless modern light-themed dialog for deep AI image detection."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = os.path.abspath(file_path)
        self._drag_start_pos = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(410)

        self._setup_ui()
        self.adjustSize()
        self._center_on_screen()

        # Initialize and launch background detector
        self.worker = AIDetectorWorker(self.file_path, parent=self)
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.scan_finished.connect(self._on_finished)
        self.worker.scan_error.connect(self._on_error)
        self.worker.start()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("aiDetectContainer")
        container.setStyleSheet("""
            #aiDetectContainer {
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: 18px;
            }
            QLabel {
                color: #1e293b;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        # Header: Title + Close Button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(24)

        title_label = QLabel("🧠 AI Image Detector")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e293b;")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(0, 0, 0, 0.4);
                font-size: 13px;
                font-weight: bold;
                border: none;
                border-radius: 12px;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 0.08);
                color: #1e293b;
            }
        """)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)
        layout.addLayout(header_layout)

        # File info banner with thumbnail
        file_banner = QFrame()
        file_banner.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                padding: 6px;
            }
        """)
        banner_layout = QHBoxLayout(file_banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)

        thumb_lbl = QLabel()
        pix = QPixmap(self.file_path)
        if not pix.isNull():
            thumb = pix.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            thumb_lbl.setPixmap(thumb)
            thumb_lbl.setStyleSheet("border-radius: 6px; border: 1px solid #cbd5e1;")
        else:
            thumb_lbl.setText("🖼️")
            thumb_lbl.setStyleSheet("font-size: 22px;")
        banner_layout.addWidget(thumb_lbl)

        file_details = QVBoxLayout()
        file_details.setSpacing(2)
        filename = os.path.basename(self.file_path)
        name_lbl = QLabel(filename)
        name_lbl.setStyleSheet("font-weight: 600; font-size: 13px; color: #0f172a;")
        size_bytes = os.path.getsize(self.file_path) if os.path.isfile(self.file_path) else 0
        meta_str = f"{pix.width()}×{pix.height()} • {self._format_size(size_bytes)}" if not pix.isNull() else self._format_size(size_bytes)
        meta_lbl = QLabel(meta_str)
        meta_lbl.setStyleSheet("font-size: 11px; color: #64748b;")
        file_details.addWidget(name_lbl)
        file_details.addWidget(meta_lbl)
        banner_layout.addLayout(file_details)
        banner_layout.addStretch()

        layout.addWidget(file_banner)

        # Scanning Card (Active during scan)
        self.scan_card = QFrame()
        self.scan_card.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
        """)
        scan_card_layout = QVBoxLayout(self.scan_card)
        scan_card_layout.setContentsMargins(16, 14, 16, 14)
        scan_card_layout.setSpacing(10)

        self.status_title = QLabel("Scanning pixels...")
        self.status_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #1e293b;")
        scan_card_layout.addWidget(self.status_title)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(10)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #e2e8f0;
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff7300, stop:1 #e04b00);
                border-radius: 4px;
            }
        """)
        scan_card_layout.addWidget(self.progress_bar)

        self.status_detail = QLabel("Initializing neural inspection...")
        self.status_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_detail.setWordWrap(True)
        self.status_detail.setStyleSheet("font-size: 11px; color: #64748b;")
        scan_card_layout.addWidget(self.status_detail)

        layout.addWidget(self.scan_card)

        # Result Card (Hidden until finished)
        self.result_card = QFrame()
        self.result_card.hide()
        result_card_layout = QVBoxLayout(self.result_card)
        result_card_layout.setContentsMargins(16, 14, 16, 14)
        result_card_layout.setSpacing(10)

        self.result_pill = QLabel()
        self.result_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_pill.setWordWrap(True)
        result_card_layout.addWidget(self.result_pill)

        self.confidence_bar = QProgressBar()
        self.confidence_bar.setRange(0, 100)
        self.confidence_bar.setFixedHeight(8)
        self.confidence_bar.setTextVisible(False)
        result_card_layout.addWidget(self.confidence_bar)

        self.result_detail = QLabel()
        self.result_detail.setWordWrap(True)
        self.result_detail.setStyleSheet("font-size: 11.5px; color: #475569; line-height: 1.4;")
        result_card_layout.addWidget(self.result_detail)

        layout.addWidget(self.result_card)

        # Bottom Button
        self.action_btn = QPushButton("Cancel")
        self.action_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.06);
                color: #475569;
                border: none;
                border-radius: 8px;
                padding: 9px;
                font-weight: 600;
                font-size: 13px;
                margin-top: 4px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                color: #1e293b;
            }
        """)
        self.action_btn.clicked.connect(self.reject)
        layout.addWidget(self.action_btn)

        outer_layout.addWidget(container)

    def _on_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.status_detail.setText(msg)

    def _on_finished(self, data: dict) -> None:
        self.scan_card.hide()

        is_ai = data.get("is_ai", False)
        confidence = data.get("confidence", 90)
        result_str = data.get("result_str", "")
        detail = data.get("detail", "")

        self.result_pill.setText(result_str)
        if is_ai:
            self.result_card.setStyleSheet("""
                QFrame {
                    background-color: #fef2f2;
                    border: 1px solid #fecaca;
                    border-radius: 12px;
                }
            """)
            self.result_pill.setStyleSheet("""
                font-size: 15px;
                font-weight: bold;
                color: #dc2626;
                padding: 4px;
            """)
            self.confidence_bar.setStyleSheet("""
                QProgressBar {
                    background-color: #fee2e2;
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: #dc2626;
                    border-radius: 4px;
                }
            """)
        else:
            self.result_card.setStyleSheet("""
                QFrame {
                    background-color: #f0fdf4;
                    border: 1px solid #bbf7d0;
                    border-radius: 12px;
                }
            """)
            self.result_pill.setStyleSheet("""
                font-size: 15px;
                font-weight: bold;
                color: #16a34a;
                padding: 4px;
            """)
            self.confidence_bar.setStyleSheet("""
                QProgressBar {
                    background-color: #dcfce7;
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: #16a34a;
                    border-radius: 4px;
                }
            """)

        self.confidence_bar.setValue(confidence)
        self.confidence_bar.show()
        self.result_detail.setText(detail)
        self.result_card.show()

        # Update button to Done
        self.action_btn.setText("Done")
        self.action_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5500;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 9px;
                font-weight: bold;
                font-size: 13px;
                margin-top: 4px;
            }
            QPushButton:hover {
                background-color: #ff661a;
            }
            QPushButton:pressed {
                background-color: #e04b00;
            }
        """)
        try:
            self.action_btn.clicked.disconnect()
        except Exception:
            pass
        self.action_btn.clicked.connect(self.accept)

        self.adjustSize()

    def _on_error(self, err_msg: str) -> None:
        self.scan_card.hide()
        self.result_card.setStyleSheet("""
            QFrame {
                background-color: #fef2f2;
                border: 1px solid #fecaca;
                border-radius: 12px;
            }
        """)
        self.result_pill.setText("⚠️ Scan Error")
        self.result_pill.setStyleSheet("""
            font-size: 15px;
            font-weight: bold;
            color: #dc2626;
            padding: 4px;
        """)
        self.confidence_bar.hide()
        self.result_detail.setText(err_msg)
        self.result_detail.setStyleSheet("font-size: 11.5px; color: #b91c1c; line-height: 1.4;")
        self.result_card.show()

        self.action_btn.setText("Close")
        self.action_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 0.08);
                color: #334155;
                border: none;
                border-radius: 8px;
                padding: 9px;
                font-weight: 600;
                font-size: 13px;
                margin-top: 4px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.12);
                color: #0f172a;
            }
        """)
        try:
            self.action_btn.clicked.disconnect()
        except Exception:
            pass
        self.action_btn.clicked.connect(self.reject)
        self.adjustSize()

    def _format_size(self, num_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if num_bytes < 1024.0:
                return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} TB"

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is not None and isinstance(child, QPushButton):
                return
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

    def closeEvent(self, event) -> None:  # noqa: N802
        if hasattr(self, "worker") and self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.requestInterruption()
            self.worker.wait(1000)
        super().closeEvent(event)

    def reject(self) -> None:
        if hasattr(self, "worker") and self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.requestInterruption()
            self.worker.wait(1000)
        super().reject()


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


class KeepAwakeManager:
    """Manages cross-platform sleep/idle inhibition to keep screen and system awake."""

    def __init__(self) -> None:
        self.is_active = False
        self.process: subprocess.Popen | None = None
        atexit.register(self.stop)

    def toggle(self) -> bool:
        """Toggle the keep-awake state and return the new active status."""
        if self.is_active:
            self.stop()
        else:
            self.start()
        return self.is_active

    def start(self) -> None:
        """Activate system sleep inhibition."""
        if self.is_active:
            return

        try:
            if sys.platform == "win32":
                import ctypes
                # 0x80000003 = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
                self.is_active = True
            elif sys.platform == "darwin":
                # macOS: caffeinate -d prevents display from sleeping
                self.process = subprocess.Popen(
                    ["caffeinate", "-d"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.is_active = True
            else:
                # Linux: spawn gnome-session-inhibit (or systemd-inhibit)
                inhibit_cmd = None
                if shutil.which("gnome-session-inhibit"):
                    inhibit_cmd = ["gnome-session-inhibit", "--inhibit", "suspend:idle", "sleep", "infinity"]
                elif shutil.which("systemd-inhibit"):
                    inhibit_cmd = ["systemd-inhibit", "--what=idle:sleep", "--why=QuickDrop Keep Awake", "sleep", "infinity"]
                else:
                    inhibit_cmd = ["gnome-session-inhibit", "--inhibit", "suspend:idle", "sleep", "infinity"]

                self.process = subprocess.Popen(
                    inhibit_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.is_active = True
        except Exception as exc:
            print(f"[QuickDrop] Error starting keep-awake: {exc}")
            self.is_active = False

    def stop(self) -> None:
        """Deactivate system sleep inhibition and restore default power management."""
        if not self.is_active and not self.process:
            return

        try:
            if sys.platform == "win32":
                import ctypes
                # 0x80000000 = ES_CONTINUOUS resets execution state
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            elif self.process:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=1)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                self.process = None
        except Exception as exc:
            print(f"[QuickDrop] Error stopping keep-awake: {exc}")
        finally:
            self.is_active = False
            self.process = None


class QuickDropBridge(QObject):
    """Bridge object exposed via QWebChannel for direct JavaScript-to-Python communication."""

    def __init__(self, main_window: "TransparentWebDropZone") -> None:
        super().__init__()
        self.window = main_window
        self._last_trigger_time = 0.0

    @QtCore.pyqtSlot(bool)
    def toggle_float_on_top(self, enable: bool):
        print(f"--> [BACKEND] Float Toggle: {enable}")
        self.window.hide()
        flags = self.window.windowFlags()
        if enable:
            flags |= QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
        else:
            flags &= ~(QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.window.setWindowFlags(flags)
        self.window.show()
        is_float_js = "true" if enable else "false"
        self.window.page().runJavaScript(
            f"if (window.setFloatState) window.setFloatState({is_float_js});"
        )

    @QtCore.pyqtSlot(result=bool)
    def toggleKeepAwake(self) -> bool:
        new_state = self.window.toggle_keep_awake()
        print(f"--> [BACKEND] Keep Awake Toggle: {new_state}")
        return new_state

    @QtCore.pyqtSlot(result=bool)
    def toggle_keep_awake(self) -> bool:
        return self.toggleKeepAwake()

    @QtCore.pyqtSlot()
    def start_drag(self):
        self.drag_offset = self.window.pos() - QCursor.pos()

    @QtCore.pyqtSlot()
    def do_drag(self):
        if hasattr(self, 'drag_offset'):
            self.window.move(QCursor.pos() + self.drag_offset)

    @QtCore.pyqtSlot()
    def stop_drag(self) -> None:
        if hasattr(self, "drag_offset"):
            delattr(self, "drag_offset")

    @QtCore.pyqtSlot()
    def close_app(self) -> None:
        print("DEBUG: close_app called")
        if hasattr(self.window, "keep_awake_manager"):
            self.window.keep_awake_manager.stop()
        if hasattr(self.window, "_stop_hotkey_listener"):
            self.window._stop_hotkey_listener()
        if hasattr(self.window, "_tray_icon") and self.window._tray_icon:
            self.window._tray_icon.hide()
        QApplication.quit()

    @QtCore.pyqtSlot(str, int, result=str)
    @QtCore.pyqtSlot(str, result=str)
    def compress_file(self, file_path: str, slider_val: int = 50) -> str:
        """Exposes file compression to web interface with validation and async handling."""
        print(f"[QuickDrop] compress_file requested via bridge for: {file_path}")
        category, mime, _ = CompressDialog.detect_category(file_path)
        if category == "unsupported":
            resp = {
                "status": "error",
                "message": "Sorry, this file type is not supported for compression."
            }
            resp_json = json.dumps(resp, ensure_ascii=False)
            self.window.page().runJavaScript(
                f"if (window.handleCompressResponse) window.handleCompressResponse({resp_json});"
            )
            return resp_json

        dir_name = os.path.dirname(file_path)
        base_name, ext = os.path.splitext(os.path.basename(file_path))
        ext_lower = ext.lower()
        office_exts = {".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm"}
        if ext_lower in office_exts:
            target_initial = os.path.join(dir_name, f"{base_name}_compressed{ext}")
        elif category == "document":
            target_initial = os.path.join(dir_name, f"{base_name}_compressed.zip")
        else:
            target_initial = os.path.join(dir_name, f"{base_name}_compressed{ext}")

        # Unique filename ensures multiple compressions never overwrite previous versions
        output_path = get_unique_filename(target_initial)

        def run_worker():
            try:
                if category == "document":
                    out = CompressDialog._do_compress_document(file_path, output_path, slider_val)
                elif category == "video":
                    res = CompressDialog._do_compress_video(file_path, output_path, slider_val)
                    if isinstance(res, dict) and res.get("status") == "error":
                        raise RuntimeError(res.get("message", "FFmpeg crashed. Check terminal logs."))
                    out = output_path
                elif category == "pdf":
                    CompressDialog._do_compress_pdf(file_path, output_path, slider_val)
                    out = output_path
                else:
                    CompressDialog._do_compress_image(file_path, output_path, slider_val)
                    out = output_path

                # Enforce Ultimate Sanity Check fail-safe
                _enforce_compression_sanity_check(file_path, out)

                resp = {
                    "status": "success",
                    "output_path": out,
                    "message": "Compressed successfully."
                }
            except Exception as e:
                resp = {
                    "status": "error",
                    "message": str(e) or "Sorry, this file type is not supported for compression."
                }
            resp_json = json.dumps(resp, ensure_ascii=False)
            self.window.page().runJavaScript(
                f"if (window.handleCompressResponse) window.handleCompressResponse({resp_json});"
            )

        threading.Thread(target=run_worker, daemon=True).start()
        return json.dumps({"status": "processing"})

    @QtCore.pyqtSlot(str, str, str, int, int, int, int, bool, result=str)
    def trim_crop_video(
        self,
        file_path: str,
        start_time: str = "00:00:00",
        end_time: str = "",
        x: int = 0,
        y: int = 0,
        width: int = 0,
        height: int = 0,
        crop_enabled: bool = False
    ) -> str:
        """Processes video trim and spatial crop together via bridge."""
        crop_box = (x, y, width, height) if crop_enabled and width > 0 and height > 0 else None
        return self._execute_video_pipeline(file_path, start_time, end_time, crop_box)

    @QtCore.pyqtSlot(str, str, str, str, result=str)
    @QtCore.pyqtSlot(str, str, str, result=str)
    @QtCore.pyqtSlot(str, result=str)
    def trim_video(self, file_path: str, start_time: str = "00:00:00", end_time: str = "", crop_data: str = "") -> str:
        """Exposes video trimming and optional crop to web interface with validation and async handling."""
        crop_box = None
        if crop_data:
            try:
                cd = json.loads(crop_data) if isinstance(crop_data, str) else crop_data
                if isinstance(cd, dict):
                    if cd.get("crop_enabled", cd.get("enabled", True)):
                        c_dict = cd.get("crop", cd)
                        w = int(c_dict.get("width", 0))
                        h = int(c_dict.get("height", 0))
                        x = int(c_dict.get("x", 0))
                        y = int(c_dict.get("y", 0))
                        if w > 0 and h > 0:
                            crop_box = (x, y, w, h)
            except Exception as e:
                print(f"[QuickDrop] Error parsing crop_data: {e}")
        return self._execute_video_pipeline(file_path, start_time, end_time, crop_box)

    def _execute_video_pipeline(
        self,
        file_path: str,
        start_time: str,
        end_time: str,
        crop_box: tuple[int, int, int, int] | None = None
    ) -> str:
        print(f"[QuickDrop] Video studio request: {file_path} | Trim: {start_time}->{end_time} | Crop: {crop_box}")
        crop_type = CropDialog.detect_crop_type(file_path)
        if crop_type != "video":
            resp = {
                "status": "error",
                "message": "Unsupported file. Only images and videos can be cropped or trimmed."
            }
            resp_json = json.dumps(resp, ensure_ascii=False)
            self.window.page().runJavaScript(
                f"if (window.handleCropResponse) window.handleCropResponse({resp_json}); else if (window.showToast) window.showToast({resp_json}.message, 'error');"
            )
            return resp_json

        if not end_time:
            dur = get_video_duration(file_path)
            end_time = format_seconds_to_hhmmss(dur) if dur > 0 else "00:00:10"

        dir_name = os.path.dirname(file_path)
        base_name, ext = os.path.splitext(os.path.basename(file_path))
        target_initial = os.path.join(dir_name, f"{base_name}_cropped{ext}")
        if os.path.abspath(target_initial) == os.path.abspath(file_path):
            target_initial = os.path.join(dir_name, f"{base_name}_cropped_1{ext}")
        output_path = get_unique_filename(target_initial)

        def run_worker():
            try:
                out = CropDialog._do_process_video(file_path, start_time, end_time, output_path, crop_box)
                resp = {
                    "status": "success",
                    "output_path": out,
                    "message": "Video processed successfully."
                }
            except Exception as e:
                resp = {
                    "status": "error",
                    "message": str(e) or "Video processing failed. Please check your time inputs."
                }
            resp_json = json.dumps(resp, ensure_ascii=False)
            self.window.page().runJavaScript(
                f"if (window.handleCropResponse) window.handleCropResponse({resp_json}); else if (window.showToast) window.showToast({resp_json}.message, 'error');"
            )

        threading.Thread(target=run_worker, daemon=True).start()
        return json.dumps({"status": "processing"})

    @QtCore.pyqtSlot(str)
    def open_crop_dialog(self, file_path: str) -> None:
        """Opens CropDialog for given file from WebChannel."""
        crop_type = CropDialog.detect_crop_type(file_path)
        if crop_type == "unsupported":
            resp = {
                "status": "error",
                "message": "Unsupported file. Only images and videos can be cropped or trimmed."
            }
            resp_json = json.dumps(resp, ensure_ascii=False)
            self.window.page().runJavaScript(
                f"if (window.handleCropResponse) window.handleCropResponse({resp_json}); else if (window.showToast) window.showToast({resp_json}.message, 'error');"
            )
            return

        dialog = CropDialog(file_path, self.window)
        dialog.show()
        self.window._dialogs.append(dialog)
        dialog.finished.connect(
            lambda _, d=dialog: self.window._dialogs.remove(d)
            if d in self.window._dialogs
            else None
        )

    @QtCore.pyqtSlot(str, result=str)
    def validate_crop_file(self, file_path: str) -> str:
        """Validates if file can be cropped/trimmed and returns metadata JSON."""
        crop_type = CropDialog.detect_crop_type(file_path)
        if crop_type == "unsupported":
            return json.dumps({
                "status": "error",
                "message": "Unsupported file. Only images and videos can be cropped or trimmed."
            })
        duration = 0.0
        dur_str = ""
        if crop_type == "video":
            duration = get_video_duration(file_path)
            dur_str = format_seconds_to_hhmmss(duration) if duration > 0 else "00:00:10"
        return json.dumps({
            "status": "success",
            "category": crop_type,
            "duration": duration,
            "duration_str": dur_str
        })

    @QtCore.pyqtSlot(list, str)
    def drop_files_action(self, file_paths: list, action_name: str) -> None:
        """Handle dropped files with a specific target action from WebChannel."""
        print(f"--> [BACKEND] drop_files_action: {action_name} | Files: {file_paths}")
        self.window.process_dropped_files(action_name, [str(p) for p in file_paths])

    @QtCore.pyqtSlot(str)
    def trigger_action(self, action_name: str) -> None:
        now = time.time()
        if now - getattr(self, "_last_trigger_time", 0.0) < 0.35:
            return
        self._last_trigger_time = now

        print(f"DEBUG: trigger_action called with: {action_name}")
        act_upper = action_name.strip().upper()
        if act_upper in ("KEEP AWAKE", "KEEP_AWAKE", "KEEPAWAKE", "AWAKE"):
            self.window.toggle_keep_awake()
        elif act_upper in ("WATERMARK", "REDACT", "EDIT", "ANNOTATE", "AIRDROP", "SHARE", "AI DETECT", "AI_DETECT", "AIDETECT"):
            self.window.open_file_dialog(act_upper)
        else:
            self.window._handle_action_click(action_name)

    @QtCore.pyqtSlot(str)
    def open_file_dialog(self, action_name: str) -> None:
        now = time.time()
        if now - getattr(self, "_last_trigger_time", 0.0) < 0.35:
            return
        self._last_trigger_time = now

        print(f"DEBUG: open_file_dialog called with: {action_name}")
        act_upper = action_name.strip().upper()
        if act_upper in ("KEEP AWAKE", "KEEP_AWAKE", "KEEPAWAKE", "AWAKE"):
            self.window.toggle_keep_awake()
            return
        self.window.open_file_dialog(action_name)


class GlobalHotkeyEmitter(QObject):
    """Thread-safe signal dispatcher bridging background pynput callbacks to the Qt main thread."""

    hotkey_triggered = pyqtSignal()


class TransparentWebDropZone(ZoomLockedWebEngineView):
    """Frameless translucent WebEngine radial drop zone with macOS glassmorphism."""

    def __init__(self) -> None:
        super().__init__()

        # Frameless and stays on top (using Tool flag for Wayland/GNOME compatibility)
        self.setWindowTitle("QuickDrop")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(500, 500)

        # 1. True Transparency (CRITICAL)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.page().setBackgroundColor(Qt.GlobalColor.transparent)

        # Configure WebEngine for HTML5 video playback with local file access
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
        )

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
        self._is_floating = True
        self._workers: list[ConversionWorker] = []
        self._ocr_workers: list[OCRWorker] = []
        self._doc_workers: list[DocExportWorker] = []
        self._dialogs: list[QDialog] = []
        self._is_processing = False
        self.process_file_action = self.process_dropped_files
        self.keep_awake_manager = KeepAwakeManager()

        # Lock UI zoom strictly to 1.0 and hook title changes for click-to-execute
        self.setZoomFactor(1.0)
        self.loadFinished.connect(self._on_page_loaded)
        self.titleChanged.connect(self._on_title_changed)
        self._install_event_filters()

        # Setup QWebChannel bridge for robust JS-to-Python IPC
        self._bridge = QuickDropBridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("backend", self._bridge)
        self._channel.registerObject("bridge", self._bridge)
        self.page().setWebChannel(self._channel)

        # Load local index.html using absolute path and QUrl.fromLocalFile()
        html_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "index.html")
        )
        self.load(QUrl.fromLocalFile(html_path))

        # Center on primary screen
        self._center_on_screen()

        # Initialize system tray icon
        self._setup_tray_icon()

        # Initialize global summon hotkey listener (<ctrl>+<shift>+<space>)
        self._setup_global_hotkey()

    def toggle_keep_awake(self) -> bool:
        """Toggle system keep awake state and synchronize with frontend."""
        new_state = self.keep_awake_manager.toggle()
        is_on_str = "true" if new_state else "false"
        self.page().runJavaScript(
            f"if (window.setKeepAwakeState) window.setKeepAwakeState({is_on_str});"
        )
        return new_state

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    # ── Modifier & Dynamic Menu Switching ──────────────────────────

    def _sync_modifier(self, modifier_name: str, force: bool = False) -> None:
        """Inform WebEngine of the active modifier menu if it has changed."""
        if self._current_modifier != modifier_name or force:
            self._current_modifier = modifier_name
            force_arg = "true" if force else "false"
            self.page().runJavaScript(f"window.switchMenu('{modifier_name}', {force_arg});")

    def _resolve_modifier(self, event=None, is_release: bool = False) -> str:
        """Resolve the effective modifier name based on event modifiers, app modifiers, and key."""
        mods = QApplication.keyboardModifiers()
        if event is not None and hasattr(event, "modifiers"):
            mods |= event.modifiers()
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
            mods = QApplication.keyboardModifiers()
            if mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
                self._sync_modifier("MODIFIER")
            else:
                self._sync_modifier("DEFAULT")
            pos = event.position()
            self.page().runJavaScript(
                f"window.updateHover({pos.x()}, {pos.y()});"
            )
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._sync_modifier("DEFAULT")
        self.page().runJavaScript("window.clearHover();")
        event.accept()

    def process_dropped_files(self, action_name: str, file_paths: list[str]) -> None:
        """Route file paths and action to the processing/conversion pipeline."""
        action_name = action_name.strip() if action_name else "None"
        if isinstance(file_paths, str):
            file_paths = [file_paths]
        for path in file_paths:
            print(f"ACTION: {action_name} | FILE: {path}")
        self.page().runJavaScript("window.clearHover();")
        self._sync_modifier("DEFAULT")

        act_upper = action_name.strip().upper()

        if act_upper == "COMPRESS":
            for path in file_paths:
                category, mime, _ = CompressDialog.detect_category(path)
                if category == "unsupported":
                    err_payload = {
                        "status": "error",
                        "message": "Sorry, this file type is not supported for compression."
                    }
                    print(f"[QuickDrop] Unsupported file for compression: {path}")
                    err_json = json.dumps(err_payload, ensure_ascii=False)
                    self.page().runJavaScript(
                        f"if (window.handleCompressResponse) window.handleCompressResponse({err_json}); else if (window.showToast) window.showToast({err_json}.message, 'error');"
                    )
                    continue
                dialog = CompressDialog(path, self)
                dialog.show()
                self._dialogs.append(dialog)
                dialog.finished.connect(
                    lambda _, d=dialog: self._dialogs.remove(d)
                    if d in self._dialogs
                    else None
                )
        elif act_upper == "CROP":
            for path in file_paths:
                crop_type = CropDialog.detect_crop_type(path)
                if crop_type == "unsupported":
                    err_payload = {
                        "status": "error",
                        "message": "Unsupported file. Only images and videos can be cropped or trimmed."
                    }
                    print(f"[QuickDrop] Unsupported file for crop: {path}")
                    err_json = json.dumps(err_payload, ensure_ascii=False)
                    self.page().runJavaScript(
                        f"if (window.handleCropResponse) window.handleCropResponse({err_json}); else if (window.showToast) window.showToast({err_json}.message, 'error');"
                    )
                    continue
                if crop_type == "video":
                    path_json = json.dumps(path)
                    dur = get_video_duration(path)
                    dur_str = json.dumps(format_seconds_to_hhmmss(dur) if dur > 0 else "00:00:10")
                    self.page().runJavaScript(
                        f"if (window.openCropModal) window.openCropModal({path_json}, {dur_str});"
                    )
                else:
                    dialog = CropDialog(path, self)
                    dialog.show()
                    self._dialogs.append(dialog)
                    dialog.finished.connect(
                        lambda _, d=dialog: self._dialogs.remove(d)
                        if d in self._dialogs
                        else None
                    )
        elif act_upper == "METADATA":
            for path in file_paths:
                dialog = MetadataDialog(path, self)
                dialog.show()
                self._dialogs.append(dialog)
                dialog.finished.connect(
                    lambda _, d=dialog: self._dialogs.remove(d)
                    if d in self._dialogs
                    else None
                )
        elif act_upper == "COLOR":
            for path in file_paths:
                dialog = ColorDialog(path, self)
                dialog.show()
                self._dialogs.append(dialog)
                dialog.finished.connect(
                    lambda _, d=dialog: self._dialogs.remove(d)
                    if d in self._dialogs
                    else None
                )
        elif act_upper == "SCREENSHOT":
            self._execute_screenshot()
        elif act_upper == "TERMINAL":
            if file_paths:
                for path in file_paths:
                    self._execute_terminal(path)
            else:
                self._execute_terminal()
        elif act_upper in ("CLOSE", "QUIT", "EXIT"):
            self.close_app()
        elif act_upper in ("FLOAT", "PIN") or act_upper.startswith("FLOAT"):
            self.toggle_float_on_top()
        elif act_upper in ["COPY PATH", "COPY_PATH"]:
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
        elif act_upper == "OCR":
            self._start_ocr(file_paths)
        elif act_upper in ("DOCX", "TXT"):
            self._start_doc_conversion(file_paths, act_upper)
        elif act_upper == "WATERMARK":
            for path in file_paths:
                dialog = WatermarkDialog(path, self)
                dialog.show()
                self._dialogs.append(dialog)
                dialog.finished.connect(
                    lambda _, d=dialog: self._dialogs.remove(d)
                    if d in self._dialogs
                    else None
                )
        elif act_upper == "REDACT":
            for path in file_paths:
                dialog = RedactDialog(path, self)
                dialog.show()
                self._dialogs.append(dialog)
                dialog.finished.connect(
                    lambda _, d=dialog: self._dialogs.remove(d)
                    if d in self._dialogs
                    else None
                )
        elif act_upper == "EDIT":
            for path in file_paths:
                dialog = EditDialog(path, self)
                dialog.show()
                self._dialogs.append(dialog)
                dialog.finished.connect(
                    lambda _, d=dialog: self._dialogs.remove(d)
                    if d in self._dialogs
                    else None
                )
        elif act_upper == "ANNOTATE":
            for path in file_paths:
                dialog = AnnotateDialog(path, self)
                dialog.show()
                self._dialogs.append(dialog)
                dialog.finished.connect(
                    lambda _, d=dialog: self._dialogs.remove(d)
                    if d in self._dialogs
                    else None
                )
        elif act_upper == "CALC":
            self._open_calc()
        elif act_upper == "TIMER":
            self._open_timer()
        elif act_upper == "RECORD":
            self._open_record()
        elif act_upper in ("AIRDROP", "SHARE", "LOCAL SHARE", "LOCAL_SHARE"):
            for path in file_paths:
                self._open_share(path)
        elif act_upper in ("AI DETECT", "AI_DETECT", "AIDETECT"):
            for path in file_paths:
                self._open_ai_detect(path)
        elif act_upper in ("KEEP AWAKE", "KEEP_AWAKE", "KEEPAWAKE", "AWAKE"):
            self.toggle_keep_awake()
        elif (
            act_upper == "ADD BG"
            or act_upper in ConversionWorker.FORMAT_MAP
        ):
            self._start_conversion(file_paths, act_upper)

    def open_file_dialog(self, action_name: str) -> None:
        """Open native QFileDialog to select a file and route to the action pipeline."""
        now = time.time()
        if now - getattr(self, "_last_dialog_time", 0) < 0.3:
            return
        self._last_dialog_time = now

        act_upper = action_name.strip().upper()
        if act_upper in ("CLOSE", "QUIT", "EXIT"):
            self.close_app()
            return
        if act_upper in ("FLOAT", "PIN") or act_upper.startswith("FLOAT"):
            self.toggle_float_on_top()
            return
        if act_upper in ("KEEP AWAKE", "KEEP_AWAKE", "KEEPAWAKE", "AWAKE"):
            self.toggle_keep_awake()
            return
        if act_upper == "SCREENSHOT":
            self._execute_screenshot()
            return
        if act_upper == "CALC":
            self._open_calc()
            return
        if act_upper == "TIMER":
            self._open_timer()
            return
        if act_upper == "RECORD":
            self._open_record()
            return

        if act_upper == "COMPRESS":
            file_filter = (
                "All Supported (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif *.heic *.avif *.mp4 *.mov *.avi *.mkv *.webm *.pdf *.docx *.pptx *.xlsx *.txt *.csv);;"
                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif *.heic *.avif);;"
                "Videos (*.mp4 *.mov *.avi *.mkv *.webm);;"
                "PDF Documents (*.pdf);;"
                "Office Documents (*.docx *.pptx *.xlsx *.docm *.pptm *.xlsm);;"
                "Standard Documents (*.txt *.csv *.rtf *.json *.xml *.log *.md);;"
                "All Files (*.*);;All Files (*)"
            )
        elif act_upper == "CROP":
            file_filter = (
                "Images & Videos (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif *.heic *.avif *.mp4 *.mov *.avi *.mkv *.webm *.flv *.wmv *.m4v *.ts *.3gp);;"
                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif *.heic *.avif);;"
                "Videos (*.mp4 *.mov *.avi *.mkv *.webm *.flv *.wmv *.m4v *.ts *.3gp);;"
                "All Files (*.*);;All Files (*)"
            )
        elif act_upper in ("WATERMARK", "REDACT", "EDIT", "ANNOTATE", "METADATA", "COLOR", "OCR", "AI DETECT", "AI_DETECT"):
            file_filter = (
                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif *.heic *.avif);;All Files (*.*);;All Files (*)"
            )
        else:
            file_filter = "All Files (*.*);;All Files (*)"

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select File for {action_name.strip()}",
            os.path.expanduser("~"),
            file_filter,
        )
        if file_path:
            self.process_dropped_files(action_name, [file_path])

    def dropEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            file_paths = [
                url.toLocalFile()
                for url in event.mimeData().urls()
                if url.isLocalFile() or url.toLocalFile()
            ]

            def on_action_received(action: str | None) -> None:
                self.process_dropped_files(action or "None", file_paths)

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

    def _start_doc_conversion(self, file_paths: list[str], target_format: str) -> None:
        """Asynchronously copy file with .docx or .txt extension via background worker."""
        self._is_processing = True
        fmt = target_format.strip().upper()

        self.page().runJavaScript(
            f"""
            window.isProcessing = true;
            (function() {{
                const cat = document.getElementById('tooltip-category');
                const txt = document.getElementById('tooltip-text');
                const tip = document.getElementById('tooltip');
                if (cat) cat.textContent = 'Copying';
                if (txt) txt.textContent = 'Creating {fmt}...';
                if (tip) tip.classList.add('visible');
            }})();
            """
        )

        icon_str = "📄" if fmt == "DOCX" else "📝"
        loading_dialog = LoadingDialog(
            title="Processing... Please wait",
            subtitle=f"{icon_str} Creating {fmt}... Please wait",
            parent=self,
        )
        loading_dialog.show()

        worker = DocExportWorker(file_paths, fmt, self)
        worker.file_saved.connect(
            lambda src, dst: print(f"[QuickDrop] Copied '{src}' -> '{dst}'")
        )

        def on_finished(saved_files: list[str]) -> None:
            loading_dialog.close()
            self._is_processing = False
            if worker in self._doc_workers:
                self._doc_workers.remove(worker)

            self.page().runJavaScript(
                f"""
                (function() {{
                    window.isProcessing = false;
                    const cat = document.getElementById('tooltip-category');
                    const txt = document.getElementById('tooltip-text');
                    const tip = document.getElementById('tooltip');
                    if (cat) cat.textContent = '{fmt}';
                    if (txt) txt.textContent = '✓ Saved';
                    if (tip) tip.classList.add('visible', 'active');
                    setTimeout(() => {{
                        if (window.clearHover && !window.isProcessing) window.clearHover();
                    }}, 1500);
                }})();
                """
            )

            dialog = DocCompleteDialog(saved_files, fmt, parent=self)
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
            if worker in self._doc_workers:
                self._doc_workers.remove(worker)

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

            dialog = OCRDialog(f"File Copy Failed:\n{err_msg}", parent=self)
            dialog.show()
            self._dialogs.append(dialog)
            dialog.finished.connect(
                lambda _, d=dialog: self._dialogs.remove(d)
                if d in self._dialogs
                else None
            )

        worker.finished_all.connect(on_finished)
        worker.failed.connect(on_failure)
        self._doc_workers.append(worker)
        worker.start()

    # ── Click-to-Execute Utilities (SCREENSHOT / TERMINAL) ─────────

    def _on_title_changed(self, title: str) -> None:
        """Handle IPC communication from WebEngine via document.title."""
        if title.startswith("quickdrop:action:"):
            parts = title.split(":")
            if len(parts) >= 3:
                action_name = parts[2].strip().upper()
                target_state = None
                if len(parts) >= 4 and parts[3] in ("0", "1"):
                    target_state = (parts[3] == "1")
                self._handle_action_click(action_name, target_state)
        elif title.startswith("quickdrop:compress:"):
            parts = title.split(":")
            if len(parts) >= 4:
                file_path = parts[2]
                try:
                    slider_val = int(parts[3])
                except (ValueError, TypeError):
                    slider_val = 50
                self._bridge.compress_file(file_path, slider_val)
        elif title.startswith("quickdrop:trim:"):
            parts = title.split(":")
            if len(parts) >= 5:
                file_path = parts[2]
                start_time = parts[3]
                end_time = parts[4]
                crop_data = parts[5] if len(parts) >= 6 else ""
                self._bridge.trim_video(file_path, start_time, end_time, crop_data)
        elif title.startswith("quickdrop:video_studio:"):
            parts = title.split(":")
            if len(parts) >= 3:
                try:
                    import urllib.parse
                    raw_json = urllib.parse.unquote(parts[2])
                    data = json.loads(raw_json)
                    fp = data.get("file_path", "")
                    st = data.get("start_time", "00:00:00")
                    et = data.get("end_time", "")
                    ce = data.get("crop_enabled", False)
                    cb = None
                    if ce and data.get("crop"):
                        c = data["crop"]
                        cb = (c.get("x", 0), c.get("y", 0), c.get("width", 0), c.get("height", 0))
                    self._bridge._execute_video_pipeline(fp, st, et, cb)
                except Exception as e:
                    print(f"[QuickDrop] Error parsing quickdrop:video_studio: {e}")

    def _handle_action_click(self, action_name: str, target_state: bool | None = None) -> None:
        """Execute click-triggered actions without requiring file drop."""
        now = time.time()
        if now - getattr(self, "_last_click_time", 0.0) < 0.35:
            return
        self._last_click_time = now
        act = action_name.strip().upper()
        if act in ("CLOSE", "QUIT", "EXIT"):
            self.close_app()
        elif act == "SCREENSHOT":
            self._execute_screenshot()
        elif act == "TERMINAL":
            self._execute_terminal()
        elif act == "CALC":
            self._open_calc()
        elif act == "TIMER":
            self._open_timer()
        elif act == "RECORD":
            self._open_record()
        elif act in ("FLOAT", "PIN") or act.startswith("FLOAT"):
            self.toggle_float_on_top(target_state)
        elif act in ("KEEP AWAKE", "KEEP_AWAKE", "KEEPAWAKE", "AWAKE"):
            self.toggle_keep_awake()
        elif act in ("WATERMARK", "REDACT", "EDIT", "ANNOTATE", "AIRDROP", "SHARE", "AI DETECT", "AI_DETECT"):
            self.open_file_dialog(act)
        else:
            self.open_file_dialog(action_name)

    def toggle_float_on_top(self, enable: bool | None = None) -> None:
        """Safely toggle window float state forwarding to the bridge."""
        if enable is None:
            enable = not getattr(self, "_is_floating", True)
        self._bridge.toggle_float_on_top(enable)

    def start_drag(self, *args, **kwargs) -> None:
        """Forward manual drag start to the bridge."""
        self._bridge.start_drag()

    def do_drag(self, *args, **kwargs) -> None:
        """Forward manual dragging to the bridge."""
        self._bridge.do_drag()

    def stop_drag(self) -> None:
        """Forward manual drag release from JS."""
        self._bridge.stop_drag()

    def close_app(self) -> None:
        """Gracefully close and exit the application."""
        self._bridge.close_app()

    def trigger_action(self, action_name: str) -> None:
        """Handle generic action dispatches from JavaScript."""
        self._bridge.trigger_action(action_name)

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

    def _open_calc(self) -> None:
        """Open the standalone mini-calculator dialog."""
        dialog = CalcDialog(self)
        dialog.show()
        self._dialogs.append(dialog)
        dialog.finished.connect(
            lambda _, d=dialog: self._dialogs.remove(d)
            if d in self._dialogs
            else None
        )

    def _open_timer(self) -> None:
        """Open the standalone countdown timer dialog."""
        dialog = TimerDialog(self)
        dialog.show()
        self._dialogs.append(dialog)
        dialog.finished.connect(
            lambda _, d=dialog: self._dialogs.remove(d)
            if d in self._dialogs
            else None
        )

    def _open_record(self) -> None:
        """Open the standalone screen recorder dialog."""
        dialog = RecordDialog(self)
        dialog.show()
        self._dialogs.append(dialog)
        dialog.finished.connect(
            lambda _, d=dialog: self._dialogs.remove(d)
            if d in self._dialogs
            else None
        )

    def _open_share(self, file_path: str) -> None:
        """Open the AirDrop / Local Share dialog with live HTTP server and QR Code."""
        if not os.path.isfile(file_path):
            print(f"[QuickDrop AirDrop] File not found: '{file_path}'")
            return
        dialog = ShareDialog(file_path, self)
        dialog.show()
        self._dialogs.append(dialog)
        dialog.finished.connect(
            lambda _, d=dialog: self._dialogs.remove(d)
            if d in self._dialogs
            else None
        )

    def _open_ai_detect(self, file_path: str) -> None:
        """Open the AI Image Detector dialog with simulated deep neural analysis."""
        if not os.path.isfile(file_path):
            print(f"[QuickDrop AI Detect] File not found: '{file_path}'")
            return
        dialog = AIDetectDialog(file_path, self)
        dialog.show()
        self._dialogs.append(dialog)
        dialog.finished.connect(
            lambda _, d=dialog: self._dialogs.remove(d)
            if d in self._dialogs
            else None
        )

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
        """Lock zoom strictly to 1.0, sync float state, and prevent web-level gesture/wheel zoom."""
        self.setZoomFactor(1.0)
        is_float_js = "true" if getattr(self, "_is_floating", True) else "false"
        self.page().runJavaScript(
            f"""
            if (window.setFloatState) window.setFloatState({is_float_js});
            window.addEventListener('wheel', (e) => {{
                if (e.ctrlKey) {{
                    e.preventDefault();
                }}
            }}, {{ passive: false }});
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
                self._drag_start_pos = None
                self._is_dragging_window = False
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
        self._drag_start_pos = None
        self._is_dragging_window = False
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

        if event.isAutoRepeat():
            event.ignore()
            return

        # Space resets to Default main menu
        if event.key() == Qt.Key.Key_Space:
            self._sync_modifier("DEFAULT", force=True)
            event.accept()
            return

        # Modifier keys: Hold Shift for tools, Control/Alt/Meta for utilities
        if event.key() == Qt.Key.Key_Shift:
            self._sync_modifier("SHIFT", force=True)
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            self._sync_modifier("CTRL", force=True)
            event.accept()
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

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.isAutoRepeat():
            event.ignore()
            return

        if event.key() in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            mods = QApplication.keyboardModifiers()
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self._sync_modifier("SHIFT", force=True)
            elif mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
                self._sync_modifier("CTRL", force=True)
            else:
                self._sync_modifier("DEFAULT", force=True)
            event.accept()
            return

        super().keyReleaseEvent(event)

    # ── System Tray Integration ────────────────────────────────────

    def _setup_tray_icon(self) -> None:
        """Initialize the persistent system tray icon and context menu."""
        self._tray_icon = QSystemTrayIcon(self)
        default_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._tray_icon.setIcon(default_icon)
        self._tray_icon.setToolTip("QuickDrop")

        tray_menu = QMenu()
        toggle_action = tray_menu.addAction("Show/Hide QuickDrop")
        toggle_action.triggered.connect(self._toggle_window_visibility)

        tray_menu.addSeparator()

        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self._quit_application)

        self._tray_icon.setContextMenu(tray_menu)
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()

    def _toggle_window_visibility(self) -> None:
        """Toggle main window visibility from system tray."""
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()
            self.raise_()

    def _quit_application(self) -> None:
        """Safely quit the application from the tray menu."""
        if hasattr(self, "keep_awake_manager"):
            self.keep_awake_manager.stop()
        self._stop_hotkey_listener()
        if hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.hide()
        QApplication.quit()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation events, triggering showNormal on double-click."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()
            self.activateWindow()

    # ── Global Hotkey Integration ──────────────────────────────────

    def _setup_global_hotkey(self) -> None:
        """Initialize and start background pynput global hotkey listener for <ctrl>+<shift>+<space>."""
        self._hotkey_emitter = GlobalHotkeyEmitter(self)
        self._hotkey_emitter.hotkey_triggered.connect(self._toggle_window_visibility)
        self._hotkey_listener = None

        try:
            from pynput import keyboard

            def on_hotkey_activated():
                self._hotkey_emitter.hotkey_triggered.emit()

            self._hotkey_listener = keyboard.GlobalHotKeys({
                "<ctrl>+<shift>+<space>": on_hotkey_activated
            })
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
        except Exception as exc:
            print(f"[QuickDrop] Warning: Unable to start global hotkey listener: {exc}")

    def _stop_hotkey_listener(self) -> None:
        """Safely stop the background pynput global hotkey listener."""
        if hasattr(self, "_hotkey_listener") and self._hotkey_listener:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None

    def closeEvent(self, event) -> None:  # noqa: N802
        """Override close event to minimize/hide to system tray instead of exiting."""
        event.ignore()
        self.hide()


def main() -> None:
    if sys.platform.startswith('linux'):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    drop_zone = TransparentWebDropZone()
    app.aboutToQuit.connect(drop_zone.keep_awake_manager.stop)
    app.aboutToQuit.connect(drop_zone._stop_hotkey_listener)
    drop_zone.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
