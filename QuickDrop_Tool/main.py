import os
import sys

from PIL import Image
import pillow_heif

pillow_heif.register_heif_opener()
import pillow_avif

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView


class ConversionWorker(QThread):
    """Background worker thread for asynchronous image conversions."""

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

                if action_name.upper() in ConversionWorker.FORMAT_MAP:
                    self._start_conversion(file_paths, action_name)

            self.page().runJavaScript(
                "window.currentHoveredAction", on_action_received
            )
        else:
            event.ignore()

    def _start_conversion(self, file_paths: list[str], target_format: str) -> None:
        """Spawn a background thread to convert dropped files."""
        worker = ConversionWorker(file_paths, target_format, self)
        worker.file_converted.connect(
            lambda src, dst: print(f"[QuickDrop] Converted '{src}' -> '{dst}'")
        )
        worker.conversion_failed.connect(
            lambda src, err: print(f"[QuickDrop ERROR] Failed to convert '{src}': {err}")
        )
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._workers.append(worker)
        worker.start()

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
            # Forward live cursor hover coordinates to WebEngine
            pos = event.position()
            self.page().runJavaScript(
                f"window.updateHover({pos.x()}, {pos.y()});"
            )
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
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
