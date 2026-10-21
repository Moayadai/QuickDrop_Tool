import math
import os
import sys

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView

# Menu definitions matching the frontend datasets
MENUS = {
    "DEFAULT": ("PDF", "JPG", "PNG", "WEBP", "HEIC", "TIFF", "AVIF", "BMP"),
    "CTRL": ("COMPRESS", "METADATA", "EDIT", "REDACT", "CROP", "ADD BG", "WATERMARK", "RESIZE"),
    "SHIFT": ("ENCRYPT", "OCR TEXT", "VECTORIZE", "ENHANCE", "REMOVE BG", "SPLIT", "MERGE", "UPSCALER"),
    "ALT": ("QUICK LOOK", "RENAME", "TRASH", "DUPLICATE", "ARCHIVE", "SHARE", "TAG", "GET INFO"),
}


class TransparentWebDropZone(QWebEngineView):
    """Frameless translucent WebEngine radial drop zone with macOS glassmorphism."""

    def __init__(self) -> None:
        super().__init__()

        # 1. WebEngine Setup: Frameless, Always on Top, Size 500x500
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(500, 500)

        # Translucent Background Configuration
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.page().setBackgroundColor(Qt.GlobalColor.transparent)

        # Enable Native Drops & Custom Context Menu Policy
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        # Internal state tracking
        self._current_menu: str = "DEFAULT"
        self._drag_start_pos = None

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

    def _determine_active_segment(self, x: float, y: float, menu: str) -> tuple[int, str | None]:
        """Calculates which segment is active at (x, y) relative to center (250, 250)."""
        cx, cy = self.width() / 2.0, self.height() / 2.0
        dx = x - cx
        dy = y - cy
        dist = math.hypot(dx, dy)

        # Annular ring bounds: inner radius 65px, outer radius 180px
        if not (65.0 <= dist <= 180.0):
            return -1, None

        # Angle clockwise from 12 o'clock (North = 0 deg)
        rad = math.atan2(dy, dx)
        deg = (math.degrees(rad) + 90.0 + 360.0) % 360.0
        index = int(((deg + 22.5) % 360.0) // 45.0)

        items = MENUS.get(menu, MENUS["DEFAULT"])
        return index, items[index]

    # ── Native Drag & Drop with Modifier Keys ───────────────────────

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

            # 1. Pass cursor coordinates to JavaScript updateHover(x, y)
            pos = event.position()
            self.page().runJavaScript(
                f"window.updateHover({pos.x()}, {pos.y()});"
            )

            # 2. DETECT KEYBOARD MODIFIERS
            mods = QApplication.keyboardModifiers()
            if mods & Qt.KeyboardModifier.ShiftModifier:
                target_menu = "SHIFT"
            elif mods & Qt.KeyboardModifier.ControlModifier:
                target_menu = "CTRL"
            elif mods & Qt.KeyboardModifier.AltModifier:
                target_menu = "ALT"
            else:
                target_menu = "DEFAULT"

            if target_menu != self._current_menu:
                self._current_menu = target_menu
                self.page().runJavaScript(f"window.switchMenu('{target_menu}');")
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.page().runJavaScript("window.clearHover();")
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            urls = event.mimeData().urls()
            pos = event.position()

            # Identify which menu and segment was active at drop point
            index, action = self._determine_active_segment(
                pos.x(), pos.y(), self._current_menu
            )

            for url in urls:
                file_path = url.toLocalFile()
                if action:
                    print(
                        f"[QuickDrop] Intended Action: '{action}' on file: {file_path} "
                        f"(Menu: {self._current_menu}, Segment: {index})"
                    )
                else:
                    print(
                        f"[QuickDrop] Dropped file in empty center / outside: {file_path} "
                        f"(No active segment)"
                    )

        self.page().runJavaScript("window.clearHover();")

    # ── Interactive Window Movement & Keyboard Handling ─────────────

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
            return

        # Live modifier key switching during keyboard preview
        mods = QApplication.keyboardModifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            menu = "SHIFT"
        elif mods & Qt.KeyboardModifier.ControlModifier:
            menu = "CTRL"
        elif mods & Qt.KeyboardModifier.AltModifier:
            menu = "ALT"
        else:
            menu = "DEFAULT"

        if menu != self._current_menu:
            self._current_menu = menu
            self.page().runJavaScript(f"window.switchMenu('{menu}');")
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        mods = QApplication.keyboardModifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            menu = "SHIFT"
        elif mods & Qt.KeyboardModifier.ControlModifier:
            menu = "CTRL"
        elif mods & Qt.KeyboardModifier.AltModifier:
            menu = "ALT"
        else:
            menu = "DEFAULT"

        if menu != self._current_menu:
            self._current_menu = menu
            self.page().runJavaScript(f"window.switchMenu('{menu}');")
        super().keyReleaseEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    drop_zone = TransparentWebDropZone()
    drop_zone.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
