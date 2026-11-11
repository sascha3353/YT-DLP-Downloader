"""Small custom Qt widgets for the main window."""

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal, QRectF
from PyQt6.QtGui import QAction, QColor, QContextMenuEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QMenu, QTextEdit, QWidget


# SponsorBlock category display names
SB_DISPLAY_NAMES = {
    "sponsor": "Sponsor",
    "selfpromo": "Selfpromo",
    "interaction": "Interaction",
    "intro": "Intro",
    "ending": "Ending",
    "preview": "Preview",
    "hook": "Hook",
    "tangents": "Tangents",
    "highlight": "Highlight",
    "music_offtopic": "Music/Offtopic",
}

# Mapping from SponsorBlock API internal names to our display/internal names
SB_API_MAP = {
    "outro": "ending",
    "filler": "tangents",
    "poi_highlight": "highlight",
}

SB_CATEGORY_COLORS = {
    "sponsor": "#00cc00",
    "selfpromo": "#d4ac0d",
    "interaction": "#8e44ad",
    "intro": "#16bbcc",
    "ending": "#0202ed",
    "preview": "#008fd6",
    "hook": "#395699",
    "tangents": "#7300ff",
    "music_offtopic": "#888888",
    "highlight": "#9b044c",
    "exclusive_access": "#888888",
}


# ====================================================================================================
# Signal Emitter Class for Thread-Safe GUI Updates
# ====================================================================================================
class SignalEmitter(QObject):
    update_title = pyqtSignal(str)
    append_output = pyqtSignal(str)
    update_last_line = pyqtSignal(str)
    enable_button = pyqtSignal()
    update_download_progress = pyqtSignal(int, int)
    title_fetch_complete = pyqtSignal(dict)
    set_indeterminate = pyqtSignal(bool)
    update_subtitle_checkboxes = pyqtSignal(str)
    update_dock_tile = pyqtSignal(str)
    update_dock_progress = pyqtSignal(float)
    clear_dock_progress = pyqtSignal()
    update_sb_bar = pyqtSignal(list, float, int)
    # SponsorBlock log lines carry their lookup generation so a stale line
    # queued before a URL change can be dropped in the receiver, the same way
    # the bar update is gated. Scoped rather than tacked onto append_output,
    # because the download worker also writes to append_output legitimately.
    sb_log = pyqtSignal(str, int)
    set_download_button_label = pyqtSignal(str)
    set_download_button_status = pyqtSignal(str)
    set_cancel_button_visible = pyqtSignal(bool)
    thumbnail_ready = pyqtSignal(bytes, str, str, int)
    # The four args are the image bytes, the description captured alongside
    # the URL at click time, the URL itself, and a request token. The worker
    # fetches the image on a daemon thread, so by the time it arrives the user
    # may have switched videos; reading the description from live video_state
    # in the receiver would pair an old image with a new video's description.
    # Capturing all four together, and only acting when the token still matches
    # the current request, keeps the dialog's metadata consistent — and keeps
    # a stale request from clearing a newer request's in-flight guard.
    # Scoped rather than tacked onto append_output, because the download
    # worker also writes to append_output legitimately.
    thumbnail_error = pyqtSignal(str, str, int)
    # The three args are the error message, the snapshot URL captured at click
    # time, and the request token. Scoped rather than tacked onto
    # append_output, because the download worker also writes to
    # append_output legitimately. The URL is checked in the receiver too, so a
    # failure for an old video is dropped even when the token has not been
    # bumped again — the URL change clears thumbnail_url, and only the
    # receiver's comparison against the current value catches a queued error
    # that arrived after the change but before the next Info click.
    thumbnail_icon_ready = pyqtSignal(bytes, str, str, int)
    # Channel dialog: banner bytes, logo bytes, channel name, channel
    # description, snapshot URL captured at click time, and a request token.
    channel_ready = pyqtSignal(bytes, bytes, str, str, str, int)
    # Same shape as thumbnail_ready but used exclusively for updating the
    # "Show Info" toolbar icon. Kept as a separate signal so the icon fetch
    # (triggered automatically after info fetch) does not also pop the
    # thumbnail dialog.
    # Standalone SRT conversion: the worker emits this when it is done, so the
    # main thread can re-enable the controls without the handler having to
    # know whether the worker succeeded, failed or was interrupted.
    subtitle_processing_complete = pyqtSignal(bool)


# ----------------------------------------------------------------------------------------------------
# Custom SponsorBlock Visualization Bar
# ----------------------------------------------------------------------------------------------------
class SponsorBlockBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments = []
        self.duration = 0
        self.setFixedHeight(7)
        self.setToolTip("SponsorBlock segments visualization")
        # Colors based on categories
        self.category_colors = {
            cat: QColor(hex_val) for cat, hex_val in SB_CATEGORY_COLORS.items()
        }
        self.default_color = QColor("#888888")   # Grey

    def set_segments(self, segments, duration):
        self.segments = segments if segments else []
        self.duration = duration

        if self.segments and self.duration > 0:
            lines = []
            # Sort segments by start time
            sorted_segments = sorted(
                self.segments, key=lambda x: x.get("segment", [0, 0])[0]
            )
            for seg in sorted_segments:
                cat_key = seg.get("category", "unknown")
                # Translate from API name if needed
                cat_key = SB_API_MAP.get(cat_key, cat_key)
                category = SB_DISPLAY_NAMES.get(cat_key, cat_key.capitalize())
                times = seg.get("segment", [0, 0])
                start_m, start_s = divmod(int(times[0]), 60)
                end_m, end_s = divmod(int(times[1]), 60)
                lines.append(
                    f"{category}: {start_m}:{start_s:02d} - {end_m}:{end_s:02d}"
                )
            self.setToolTip("\n".join(lines))
        else:
            self.setToolTip("SponsorBlock segments visualization")

        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Create rounded rect path for clipping
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 3, 3)
        painter.setClipPath(path)

        # Draw background
        painter.setBrush(QColor("#444444"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())

        if not self.segments or self.duration <= 0:
            return

        width = self.width()
        for seg in self.segments:
            cat_key = seg.get("category", "unknown")
            # Translate from API name if needed
            cat_key = SB_API_MAP.get(cat_key, cat_key)

            times = seg.get("segment", [0, 0])
            start = float(times[0])
            end = float(times[1])

            x_start = (start / self.duration) * width
            x_end = (end / self.duration) * width
            seg_width = x_end - x_start

            color = self.category_colors.get(cat_key, self.default_color)
            painter.setBrush(color)
            painter.drawRect(int(x_start), 0, max(1, int(seg_width)), self.height())


# ----------------------------------------------------------------------------------------------------
# Small busy spinner (QProgressIndicator is not available in all PyQt6 builds)
# ----------------------------------------------------------------------------------------------------
class BusySpinner(QWidget):
    SPINNER_COLOR = QColor("#259")
    INTERVAL_MS = 80

    def __init__(self, parent=None, size: int = 18):
        super().__init__(parent)
        self._angle = 0
        self.setFixedSize(size, size)
        self.setToolTip("Working…")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)

    def start(self):
        self._angle = 0
        self._timer.start(self.INTERVAL_MS)
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _rotate(self):
        self._angle = (self._angle + 36) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        margin = 3
        rect = QRectF(margin, margin, side - 2 * margin, side - 2 * margin)
        pen = QPen(self.SPINNER_COLOR)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(rect, -self._angle * 16, -270 * 16)

# ====================================================================================================
# Custom QTextEdit Class
# ====================================================================================================
class CustomTextEdit(QTextEdit):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window

    # add context menu
    def contextMenuEvent(self, e: QContextMenuEvent | None):
        if e is None:
            return

        menu = self.createStandardContextMenu()
        if menu is None:
            menu = QMenu(self)

        menu.addSeparator()
        # Check the state of the main window
        is_busy = (
            self.main_window.video_state["is_fetching_info"]
            or self.main_window.video_state["is_download_running"]
        )
        clear_action = QAction("Clear Output", self)
        # Set the enabled state based on the check
        clear_action.setEnabled(not is_busy)
        # Keep the app header line when clearing output
        clear_action.triggered.connect(self.main_window.clear_output)
        menu.addAction(clear_action)
        menu.exec(e.globalPos())
