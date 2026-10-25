import os
import sys

# Force X11 compatibility on Linux to bypass Wayland capture block and guarantee StaysOnTop & cursor tracking
if sys.platform.startswith('linux'):
    os.environ["QT_QPA_PLATFORM"] = "xcb"

import ast
import operator as _py_op
import shutil
import subprocess
import time

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
from PyQt6.QtGui import QBrush, QClipboard, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

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


class AnnotateCanvasWidget(QWidget):
    """Interactive canvas allowing freehand drawing of red marker annotations directly onto the image."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self.original_pixmap = QPixmap(file_path)
        self._img_w = max(1, self.original_pixmap.width() if not self.original_pixmap.isNull() else 100)
        self._img_h = max(1, self.original_pixmap.height() if not self.original_pixmap.isNull() else 100)

        self.drawing_layer = QPixmap(self._img_w, self._img_h)
        self.drawing_layer.fill(Qt.GlobalColor.transparent)

        self._last_point: QPointF | None = None
        self.setMinimumSize(360, 240)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

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

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        drect = self._get_display_rect()

        # Subtle card backdrop for transparent images
        painter.fillRect(drect.toRect(), QColor(245, 245, 247))

        if not self.original_pixmap.isNull():
            painter.drawPixmap(drect.toRect(), self.original_pixmap)

        if not self.drawing_layer.isNull():
            painter.drawPixmap(drect.toRect(), self.drawing_layer)

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

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            img_pt = self._widget_to_image_coords(event.position())
            if img_pt:
                self._last_point = img_pt
                drect = self._get_display_rect()
                scale = float(self._img_w) / max(1.0, drect.width())
                pen_width = max(3.0, 4.0 * scale)

                painter = QPainter(self.drawing_layer)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                pen = QPen(
                    QColor(255, 59, 48),
                    pen_width,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
                painter.setPen(pen)
                painter.drawPoint(img_pt)
                painter.end()
                self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._last_point is not None:
            img_pt = self._widget_to_image_coords(event.position())
            if img_pt:
                drect = self._get_display_rect()
                scale = float(self._img_w) / max(1.0, drect.width())
                pen_width = max(3.0, 4.0 * scale)

                painter = QPainter(self.drawing_layer)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                pen = QPen(
                    QColor(255, 59, 48),
                    pen_width,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
                painter.setPen(pen)
                painter.drawLine(self._last_point, img_pt)
                painter.end()
                self._last_point = img_pt
                self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_point = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def clear(self) -> None:
        self.drawing_layer.fill(Qt.GlobalColor.transparent)
        self.update()


class AnnotateDialog(QDialog):
    """Frameless light-themed dialog for freehand marker annotations on images."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._drag_start_pos = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(520, 540)

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

        # Header: Centered title with subtle close button
        header_layout = QHBoxLayout()
        header_layout.addSpacing(22)

        title_label = QLabel("Annotate Image")
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

        info_label = QLabel("Click and drag on the image to draw freehand marker annotations:")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setStyleSheet("color: #666666; font-size: 11px; margin-top: -4px;")
        layout.addWidget(info_label)

        # Canvas
        self.canvas = AnnotateCanvasWidget(self.file_path, self)
        layout.addWidget(self.canvas, stretch=1)

        # Bottom Bar
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
        reset_btn.clicked.connect(self.canvas.clear)
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

        apply_btn = QPushButton("Apply Annotations")
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


class RecordDialog(QDialog):
    """Frameless light-themed floating screen recorder dialog with threaded capture and live timer."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_start_pos = None

        self._is_recording = False
        self._elapsed_seconds = 0
        self._recorder_thread = None
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

    def _stop_recording(self) -> None:
        if not self._is_recording:
            return
        self._set_mini_mode(False)
        self.stop_btn.setEnabled(False)
        self.status_pill.setText("● Finalizing video...")
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
        self._ui_timer.stop()
        if self._recorder_thread and self._recorder_thread.isRunning():
            self._recorder_thread.stop()

    def _on_started(self) -> None:
        pass

    def _on_finished(self, saved_path: str) -> None:
        self._set_mini_mode(False)
        self._is_recording = False
        self._current_output_path = saved_path
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Start New")
        self.stop_btn.setEnabled(False)

        filename = os.path.basename(saved_path)
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

    def _on_error(self, err_msg: str) -> None:
        self._set_mini_mode(False)
        self._is_recording = False
        self._ui_timer.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
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
        self._ui_timer.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
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
        super().closeEvent(event)

    def reject(self) -> None:
        if self._is_recording and self._recorder_thread and self._recorder_thread.isRunning():
            self._recorder_thread.stop()
            self._recorder_thread.wait(4000)
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
        self.window.close()
        QApplication.quit()

    @QtCore.pyqtSlot(str)
    def trigger_action(self, action_name: str) -> None:
        now = time.time()
        if now - getattr(self, "_last_trigger_time", 0.0) < 0.35:
            return
        self._last_trigger_time = now

        print(f"DEBUG: trigger_action called with: {action_name}")
        act_upper = action_name.strip().upper()
        if act_upper in ("WATERMARK", "REDACT", "EDIT", "ANNOTATE"):
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
        self.window.open_file_dialog(action_name)


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
            pos = event.position()
            self.page().runJavaScript(
                f"window.updateHover({pos.x()}, {pos.y()});"
            )
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
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

        act_upper = action_name.strip().upper()

        if act_upper == "COMPRESS":
            for path in file_paths:
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

        file_filter = (
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif *.heic *.avif);;All Files (*.*);;All Files (*)"
            if act_upper in ("WATERMARK", "REDACT", "EDIT", "ANNOTATE", "COMPRESS", "CROP", "METADATA", "COLOR", "OCR")
            else "All Files (*.*);;All Files (*)"
        )

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
        elif act in ("WATERMARK", "REDACT", "EDIT", "ANNOTATE"):
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

        # Control toggles between CTRL and DEFAULT
        if event.key() == Qt.Key.Key_Control:
            new_mod = "DEFAULT" if self._current_modifier == "CTRL" else "CTRL"
            self._sync_modifier(new_mod, force=True)
            event.accept()
            return

        # Shift toggles between SHIFT and DEFAULT
        if event.key() == Qt.Key.Key_Shift:
            new_mod = "DEFAULT" if self._current_modifier == "SHIFT" else "SHIFT"
            self._sync_modifier(new_mod, force=True)
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
        # Toggles persist on release; do not reset to DEFAULT
        super().keyReleaseEvent(event)


def main() -> None:
    if sys.platform.startswith('linux'):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication(sys.argv)
    drop_zone = TransparentWebDropZone()
    drop_zone.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
