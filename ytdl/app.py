# ==================================================================================================================================
# YT-DLP Downloader
# a GUI (PyQt6) application for downloading videos from YouTube, Rumble and
# Odysee (see SUPPORTED_SITES in ytdl/sites.py; more sites can be added there)
# with advanced options for format, quality, codec, subtitles, and SponsorBlock removal
# also optional subtitles resyncing to removed chapters
# Requirements: yt-dlp, ffmpeg
# Info: originaly we used "--sponsorblock-remove" to cut out all sponsor chapters,
# but now we create ".edl" files for Kodi to skip those chapters. This is smoother.
# And so the subtitles resyncing is not really needed anymore.
# ----------------------------------------------------------------------------------------------------------------------------------
# pyinstaller --name "YT-DLP Downloader" --windowed --icon assets/AppIcon.icns --add-data "assets:assets" main.py --clean --noconfirm
# ----------------------------------------------------------------------------------------------------------------------------------
# python3 main.py --simulate-download-error
# ==================================================================================================================================
#
# pylint: disable=unused-import,wrong-import-position
#   unused-import: the ytdl.* block below is the deliberate re-export surface for
#     main.py and the tests (they patch main.subprocess / main.shutil and read
#     e.g. main.RUMBLE_ID_REGEX).
#   wrong-import-position: the Qt/ytdl imports follow the IS_MACOS/AppKit
#     conditional and the logger setup on purpose (see the comments below).
import logging
import os
import shutil      # re-exported public surface (main.shutil used by tests)
import subprocess  # re-exported public surface (main.subprocess used by tests)
import sys
import threading

# macOS-only framework bindings (dock tile badge + progress overlay).
# Optional dependency: on Linux (and other platforms) AppKit does not exist,
# so we import conditionally and degrade gracefully — every AppKit use below
# is either gated on IS_MACOS or no-ops when self.dockTile is None.
IS_MACOS = sys.platform == "darwin"
if IS_MACOS:
    from AppKit import (
        NSApplication,
        NSImage,
        NSImageView,
        NSColor,
        NSBezierPath,
    )  # type: ignore
    from Foundation import NSMakeRect  # type: ignore
else:
    NSApplication = NSImage = NSImageView = NSColor = NSBezierPath = None
    NSMakeRect = None
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QFileDialog, QMainWindow

# ----------------------------------------------------------------------------------------------------
# Support modules (extracted from this file; see ytdl/sites.py, description.py,
# progress.py, utils.py, widgets.py, preferences.py, config.py - re-exported
# here so `main.X` keeps working as the single import surface for tests and
# tooling). The per-concern mixins (info_fetch/download/subtitles/ui_build/
# preferences_dialog) decompose the GUI class; assembled below.
# ----------------------------------------------------------------------------------------------------
from ytdl import APP_VERSION  # noqa: F401
from ytdl.config import (  # noqa: F401
    DEFAULT_OUTPUT_DIR,
    INFO_FETCH_TIMEOUT_SECONDS,
    TITLE_FETCH_DELAY_MS,
)
from ytdl.sites import (  # noqa: F401
    ARD_ID_REGEX,
    DEFAULT_SITE,
    ODYSEE_ID_REGEX,
    RTVE_ID_REGEX,
    RUMBLE_ID_REGEX,
    SUPPORTED_SITES,
    SUPPORTED_SITES_LABEL,
    YOUTUBE_ID_REGEX,
    ZDF_ID_REGEX,
    detect_site,
    is_channel_url,
    is_known_site,
    is_plausible_url,
    normalize_url,
    site_info_timeout,
    site_resyncs_auto_subs,
    site_slow_hint,
    site_supports_sponsorblock,
    site_wants_js_runtime,
)
from ytdl.progress import DownloadProgressManager  # noqa: F401
from ytdl.utils import (  # noqa: F401
    find_binary,
    get_log_dir,
    resource_path,
    version_header,
)
from ytdl.widgets import SignalEmitter  # noqa: F401
from ytdl.download import DownloadMixin  # noqa: F401
from ytdl.info_fetch import InfoFetchMixin  # noqa: F401
from ytdl.preferences_dialog import PreferencesDialogMixin  # noqa: F401
from ytdl.subtitles import SubtitleMixin  # noqa: F401
from ytdl.sidecars import SidecarMixin  # noqa: F401
from ytdl.metadata import MetadataMixin  # noqa: F401
from ytdl.command import CommandMixin  # noqa: F401
from ytdl.progress import ProgressMixin  # noqa: F401
from ytdl.ui_build import UiBuildMixin  # noqa: F401

# ----------------------------------------------------------------------------------------------------
# Logger
# ----------------------------------------------------------------------------------------------------
# NOTE: logging is deliberately NOT configured here. ytdl.app is imported by
# main.py, by the tests and by the replay helpers, and a library module must
# not call logging.basicConfig() at import time: doing so wired the root
# logger to the live ~/Library/Logs/.../app.log on every import, so simply
# running the unit tests appended dozens of synthetic entries ("Download
# failed: unknown", "sidecar boom") to the real log.
#
# Configuration belongs to the entry point, which knows it is the
# application rather than a test. See main.py's __main__ block.
logger = logging.getLogger(__name__)


# Redrawing the dock overlay is expensive: every call allocates an NSImage,
# repaints the app icon and calls -display(). yt-dlp emits a percent line far
# more often than that is visible, so redraw only once the fraction has moved
# by at least this much (0.5% of the bar width).
DOCK_PROGRESS_MIN_DELTA = 0.005


def should_redraw_dock(last_fraction: float, new_fraction: float) -> bool:
    """Decide whether the dock progress overlay needs a repaint.

    ``last_fraction`` is negative (initially -1.0) when nothing has been drawn
    for the current download yet, which always repaints. The 0.0 and 1.0
    endpoints always repaint too, so a download never appears to stall just
    short of full — a sub-threshold final step must still land.

    Pure function — no GUI state, safe to unit-test.
    """
    if last_fraction < 0.0:
        return True
    fraction = max(0.0, min(1.0, new_fraction))
    if fraction in (0.0, 1.0):
        return True
    return abs(fraction - last_fraction) >= DOCK_PROGRESS_MIN_DELTA


# ====================================================================================================
# Main Class
# ====================================================================================================
class YTDLPDownloaderGUI(
    InfoFetchMixin,
    DownloadMixin,
    SidecarMixin,
    MetadataMixin,
    CommandMixin,
    ProgressMixin,
    SubtitleMixin,
    UiBuildMixin,
    PreferencesDialogMixin,
    QMainWindow,
):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YT-DLP Downloader")
        self.setGeometry(100, 100, 900, 845)
        # self.setFixedSize(self.size())

        # Window/taskbar icon: Linux window managers take it from QIcon (macOS
        # takes it from the .app bundle), so set it whenever a PNG icon ships.
        _icon_path = resource_path("assets/AppIcon.png")
        if os.path.exists(_icon_path):
            self.setWindowIcon(QIcon(_icon_path))

        # Initialize variables
        self.video_state = {
            "url": "",
            "clean_url": "",
            "video_id": "",
            "title": "",
            "description": "",
            "thumbnail_url": "",
            "channel_id": "",
            "channel_url": "",
            "upload_date": "",
            "language": "",
            "output_dir": DEFAULT_OUTPUT_DIR,
            "base_filename": "",
            "full_path": "",
            "wants_video": True,
            "wants_audio": True,
            "wants_subtitles": False,
            "wants_edl": False,
            "video_format": "best",
            "audio_format": "best",
            "quality": "1080",
            "video_codec": "best",
            "is_fetching_info": False,
            "is_download_running": False,
            "episode_code": "",  # Set when a special-case channel overrides the episode number
            "site": "youtube",  # Site profile key from SUPPORTED_SITES
        }

        # Cache for video metadata
        self.cached_video_metadata = None

        # Cache binary paths
        self.yt_dlp_bin = find_binary("yt-dlp")
        self.ffprobe_bin = find_binary("ffprobe")
        self.ffmpeg_bin = find_binary("ffmpeg")
        self.deno_bin = find_binary("deno")

        # Test helpers (CLI flags set in __main__)
        self.simulate_download_error = False

        # Map for radio buttons
        self.option_group_map = []

        # Signal emitter for thread-safe updates
        self.signals = SignalEmitter()
        self.signals.update_title.connect(self.set_title_label)
        self.signals.append_output.connect(self.append_output_text)
        self.signals.update_last_line.connect(self.update_last_output_line)
        self.signals.enable_button.connect(self.enable_all_controls)
        self.signals.title_fetch_complete.connect(self.title_fetch_finished)
        self.signals.set_indeterminate.connect(self.handle_indeterminate_state)
        self.signals.update_download_progress.connect(self._set_download_progress)
        self.signals.update_subtitle_checkboxes.connect(
            self._update_subtitle_checkboxes
        )
        self.signals.update_dock_tile.connect(self.setDockTileCheck)
        self.signals.update_dock_progress.connect(self.setDockProgressOverlay)
        self.signals.clear_dock_progress.connect(self.clearDockProgress)
        # Deferred lookup on purpose: self.sb_bar is created by init_ui(), which
        # runs after these connections — hence a slot method, not self.sb_bar.x.
        self.signals.update_sb_bar.connect(self._set_sb_bar_segments)
        self.signals.sb_log.connect(self._append_sb_log)
        self.signals.set_download_button_label.connect(self.set_download_button_label)
        self.signals.set_download_button_status.connect(self.set_download_button_status)
        self.signals.set_cancel_button_visible.connect(self.set_cancel_button_visible)
        self.signals.thumbnail_ready.connect(self._show_thumbnail_dialog)
        self.signals.thumbnail_icon_ready.connect(self._set_thumbnail_icon)
        self.signals.thumbnail_error.connect(self._append_thumbnail_error)
        self.signals.channel_ready.connect(self._show_channel_dialog)

        # Guard: prevent concurrent thumbnail fetches from double-clicking Info
        self._fetching_thumbnail = False
        # Thumbnail request token. Each Info click bumps it, so a stale
        # response — success or failure — cannot clear a newer request's
        # in-flight guard. Both receivers only act when the token still matches
        # the current request.
        self._thumbnail_request_token = 0
        # Guard: prevent concurrent channel fetches from double-clicking Channel
        self._fetching_channel = False
        # Channel request token — same staleness pattern as thumbnails.
        self._channel_request_token = 0
        # In-memory channel cache (lifetime = app process). Maps channel_url to
        # {channel_name, channel_description, logo_url, banner_url} so repeat
        # fetches of the same channel skip the yt-dlp call. Cleared on app exit.
        self._channel_cache = {}
        # Last (video, audio) progress values, kept so the bars can be restored
        self._last_progress_values = (0, 0)

        # Cancellation: _proc is the running yt-dlp Popen (set by the download
        # worker, read/terminated by the main thread); _cancelled records that
        # the user asked to stop, so the result is reported as "Cancelled"
        # rather than "Error". Both are plain attributes: a single assignment is
        # atomic and a stale read is harmless here.
        self._proc = None
        self._cancelled = False
        # SponsorBlock lookup generation. Each info fetch bumps this counter, so a
        # SponsorBlock response that was queued before a URL change (or before a
        # second fetch of the same video) can be discarded in the main-thread
        # receiver: the worker checks the video id before emitting, but Qt may
        # queue the signal while the main thread is busy, so a late delivery can
        # still repaint a bar that a newer fetch already cleared. The token is
        # bumped on the main thread and read back on the main thread, so the
        # comparison is decisive regardless of how the worker and the event loop
        # interleave. A plain int assignment is atomic; a stale read is harmless.
        self._sb_generation = 0
        # Set by closeEvent(): once the window is closing, the download worker
        # must not emit UI signals, because the SignalEmitter they belong to is
        # being destroyed with it.
        self._shutting_down = False

        # Preferences shortcut: owned by the "Preferences..." QAction in
        # setup_menu_bar() ("Ctrl+,"/"Cmd+,"). Do NOT also bind a QShortcut
        # for the same key here — two active shortcuts with the identical
        # sequence make Qt treat the keypress as ambiguous and neither fires.

        # Timer for fetching UI title entryfield
        self.fetch_title_timer = QTimer()
        self.fetch_title_timer.setSingleShot(True)
        self.fetch_title_timer.timeout.connect(self.fetch_video_info)

        # Init
        self.init_ui()
        self.setup_menu_bar()
        self.apply_styles()
        self.check_dependencies()

        # Get MacOS Dock Tile for later
        try:
            self.dockTile = NSApplication.sharedApplication().dockTile()
        except Exception:
            self.dockTile = None

        # Last fraction painted on the dock overlay; -1.0 = nothing drawn yet.
        # Only read by the throttle in setDockProgressOverlay().
        self._last_dock_fraction = -1.0

        # Check clipboard on startup
        self.check_clipboard_on_startup()

    # ----------------------------------------------------------------------------------------------------
    # Check if required binaries are available at startup
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Get the current output directory
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Construct full path to file with optional extension and language
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Get the yt-dlp filename template
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Update video state with validation
    # ----------------------------------------------------------------------------------------------------
    def update_video_state(self, **kwargs):
        self.video_state.update(kwargs)

        # Update base_filename if title changed
        if "title" in kwargs:
            self.video_state["base_filename"] = kwargs["title"]

    # ----------------------------------------------------------------------------------------------------
    # Method to get cleaned URL
    # ----------------------------------------------------------------------------------------------------
    def get_clean_url(self):
        raw_url = self.url_entry.text().strip()
        if not raw_url:
            self.video_state["url"] = ""
            self.video_state["clean_url"] = ""
            return ""

        # Use centralized normalization
        clean_url = normalize_url(raw_url)
        self.video_state["url"] = raw_url
        self.video_state["clean_url"] = clean_url
        # Remember which site profile this URL belongs to (site support is
        # decided on the normalized URL, not the raw clipboard text)
        self.video_state["site"] = detect_site(clean_url)
        return clean_url

    # ----------------------------------------------------------------------------------------------------
    # Extract and cache video ID (site-aware: YouTube and Rumble for now)
    # ----------------------------------------------------------------------------------------------------
    def extract_video_id(self, url):
        # Check if we already extracted this URL
        if url == self.video_state["url"] and self.video_state["video_id"]:
            return self.video_state["video_id"]

        site = detect_site(url)
        id_regex = SUPPORTED_SITES[site]["id_regex"]

        match = id_regex.search(url)
        if match:
            video_id = match.group(1)
            # Update state
            self.video_state["url"] = url
            self.video_state["video_id"] = video_id
            self.video_state["site"] = site
            return video_id

        # Not found - clear cache
        self.video_state["url"] = url
        self.video_state["video_id"] = ""
        self.video_state["site"] = site
        return None

    # ----------------------------------------------------------------------------------------------------
    # Returns a list of language codes for currently checked subtitle boxes
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Check clipboard on app launch
    # ----------------------------------------------------------------------------------------------------
    def check_clipboard_on_startup(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        clipboard_content = clipboard.text()

        # Use helper function in Silent_mode for clean startup experience
        self._process_and_set_url(clipboard_content, silent_mode=True)

    # ----------------------------------------------------------------------------------------------------
    # Create a radio button group and wire it up
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Main GUI
    # ----------------------------------------------------------------------------------------------------
        # End of UI Init

    # ----------------------------------------------------------------------------------------------------
    # Add CSS Styling
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Menu bar
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Enable/disable all primary controls and option groups
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Clicked Reload Button (re-fetch video info for the current URL,
    # just like when a new URL is entered)
    # ----------------------------------------------------------------------------------------------------
    def on_reload_button_click(self):
        # Stop the debounce timer so a pending URL-change fetch doesn't fire
        # after this one and spawn a second concurrent worker.
        self.fetch_title_timer.stop()
        self.fetch_video_info()

    # ----------------------------------------------------------------------------------------------------
    # Clicked Paste Button
    # ----------------------------------------------------------------------------------------------------
    def on_paste_button_click(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        clipboard_content = clipboard.text()
        self._process_and_set_url(clipboard_content, silent_mode=False)

    # ----------------------------------------------------------------------------------------------------
    # Changed URL text
    # ----------------------------------------------------------------------------------------------------
    def on_url_text_change(self):
        new_raw_url = self.url_entry.text().strip()
        # Get normalized/cleaned version
        new_clean = normalize_url(new_raw_url)

        # Update the UI field if it was a naked ID or needs cleaning (e.g. removing &list=)
        if new_clean and new_clean != new_raw_url:
            self.url_entry.blockSignals(True)
            self.url_entry.setText(new_clean)
            self.url_entry.blockSignals(False)

        # Get the previously stored clean URL from state
        old_clean = self.video_state.get("clean_url", "")

        # Only invalidate if the actual base URL has changed
        if new_clean != old_clean:
            self.video_state["clean_url"] = ""
            self.video_state["video_id"] = ""
            self.video_state["url"] = ""
            self.video_state["site"] = detect_site(new_clean) if new_clean else "youtube"
            self.video_state["episode_code"] = ""  # Clear special-case episode override
            # Clear stale metadata immediately so the old title/channel cannot
            # be used as a filename if the user clicks Download during the
            # 500 ms debounce window before the new info fetch begins.
            self.video_state["channel"] = ""
            self.video_state["channel_id"] = ""
            self.video_state["channel_url"] = ""
            self.video_state["thumbnail_url"] = ""
            self.title_entry.clear()
            self.download_button.setEnabled(False)
            # Disable thumbnail button since info is now stale
            self.thumbnail_button.setEnabled(False)
            self._reset_thumbnail_icon()
            # Clear SponsorBlock bar
            self.sb_bar.set_segments([], 0)
            # Invalidate any SponsorBlock result still in flight. The lookup
            # generation is bumped here, before the new fetch starts, so a
            # response that was queued from the previous video cannot match it:
            # if the bump only happened near the end of get_video_info(), that
            # queued delivery could land after the URL changed but before the
            # new fetch reached it, and its token would still match, briefly
            # repainting the cleared bar.
            self._sb_generation += 1
            # Invalidate any thumbnail request still in flight. The URL change
            # clears thumbnail_url, but the request token is only bumped when
            # Info is clicked again — so a failure queued from the previous
            # video could still match the token and be logged over the new
            # video's output panel. Bump it here too, alongside the
            # SponsorBlock generation, so a stale error is dropped.
            self._thumbnail_request_token += 1
            # Reset the flight guard: a request in flight is now stale, so its
            # result cannot reset the flag (the token no longer matches) and
            # the Info button would stay blocked. Resetting here is safe — the
            # token keeps the old request from clearing the state of any newer
            # one, so a result that arrives later is still dropped.
            self._fetching_thumbnail = False

        self.fetch_title_timer.stop()
        self.fetch_title_timer.start(TITLE_FETCH_DELAY_MS)

    # ----------------------------------------------------------------------------------------------------
    # Generic handler for all QButtonGroup options
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Update Subtitle Checkboxes
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Auto-select subtitles based on type (real vs auto)
    # ("Subtitles only" mode only — selection is manual in every other mode)
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Changed SponsorBlock
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Clicked Browse Button
    # ----------------------------------------------------------------------------------------------------

    def url_rejection_reason(self, url):
        """
        None when the URL may be handed to yt-dlp, otherwise a user-facing
        reason why it was rejected (single place so the fetch and paste
        paths stay in sync).
        """
        if not is_plausible_url(url):
            return "Please enter a valid video URL"
        if not is_known_site(url):
            return f"Unsupported site — supported: {SUPPORTED_SITES_LABEL}"
        if is_channel_url(url):
            return (
                "Channel/playlist URLs are not supported — "
                "please paste a link to a single video"
            )
        return None

    # ----------------------------------------------------------------------------------------------------
    # Process clipboard content (used by paste and startup)
    # ----------------------------------------------------------------------------------------------------
    def _process_and_set_url(self, clipboard_content, silent_mode=False):
        # Clean up and normalize clipboard content (now handles naked IDs too)
        content = normalize_url(clipboard_content)

        if content and self.url_rejection_reason(content) is None:
            # If valid set text - this will trigger on_url_text_change
            self.url_entry.setText(content)
            return True

        if not silent_mode:
            # Display error/info only when not in silent mode (i.e., when user clicks Paste)
            reason = self.url_rejection_reason(content) if content else None
            if reason:
                self.signals.append_output.emit(f"Clipboard: {reason}")
            else:
                self.signals.append_output.emit("Clipboard is empty")

        return False

    # ----------------------------------------------------------------------------------------------------
    # Fetch video info in sub-thread
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Get video info with yt-dlp
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Dump raw yt-dlp output into the debug panel (info-fetch failures)
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Finished Title fetch thread
    # ----------------------------------------------------------------------------------------------------
    def title_fetch_finished(self, result):
        # Reset state variable
        self.video_state["is_fetching_info"] = False
        # Restore Download button label after metadata fetch completes (success or failure)
        self.download_button.setText("Download")
        self.set_download_button_status("")
        self._reset_download_progress_bars()
        self._set_download_busy(False)
        # Re-enable all primary controls
        self._set_ui_enabled_state(True)
        # Ensure download button state is correct based on result
        if result.get("error"):
            self.download_button.setEnabled(False)
            self.thumbnail_button.setEnabled(False)
        else:
            # Only enable Info button when there is actually a thumbnail to show
            self.thumbnail_button.setEnabled(
                bool(self.video_state.get("thumbnail_url"))
            )
            # Only enable Channel button when there is a channel URL (YouTube)
            self.channel_button.setEnabled(
                bool(self.video_state.get("channel_url"))
            )
            # Fetch the thumbnail for the Info toolbar icon if available
            if self.video_state.get("thumbnail_url"):
                self._fetch_thumbnail_for_icon()
            else:
                self._reset_thumbnail_icon()

    # ----------------------------------------------------------------------------------------------------
    # Get selected SponsorBlock categories from checkboxes
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Check for SponsorBlock segments
    # ----------------------------------------------------------------------------------------------------


    # ----------------------------------------------------------------------------------------------------
    # Set Title text
    # ----------------------------------------------------------------------------------------------------
    def set_title_label(self, text):
        self.title_entry.setText(text)
        # Update video state
        self.update_video_state(title=text)

    # ----------------------------------------------------------------------------------------------------
    # Cleans up the title (mostly to avoid ALL-CAP strings)
    # If the title starts with a season/episode code like "S01E02", that prefix is kept uppercase.
    # The rest of the title is converted to Title Case, preserving already-lowercase words.
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Open thumbnail in a dialog
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Open log file in a dialog with newest entries on top
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Open Preferences Dialog (Cmd+,)
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Save preferences from the dialog
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Clear Output area
    # ----------------------------------------------------------------------------------------------------
    def clear_output(self):
        self.output_text.clear()
        # Keep the header line visible even while we are fetching video info
        self.output_text.append(version_header())
        self.output_text.append("Supported: " + SUPPORTED_SITES_LABEL)
        self._reset_download_progress_bars()

    # ----------------------------------------------------------------------------------------------------
    # Add text to Output area
    # ----------------------------------------------------------------------------------------------------
    def append_output_text(self, text):
        # Block signals temporarily to prevent cascading updates
        self.output_text.blockSignals(True)
        self.output_text.append(text)
        cursor = self.output_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.output_text.setTextCursor(cursor)
        self.output_text.blockSignals(False)

    # ----------------------------------------------------------------------------------------------------
    # Update of last line
    # ----------------------------------------------------------------------------------------------------
    def update_last_output_line(self, text):
        # Block signals to prevent recursive repaints
        self.output_text.blockSignals(True)
        cursor = self.output_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.LineUnderCursor)
        if cursor.selectedText().startswith("[download]"):
            cursor.insertText(text)
            # Scroll is already at end, no need to scroll again
        else:
            # Don't call append_output_text to avoid double scrolling
            self.output_text.append(text)
            cursor = self.output_text.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.output_text.setTextCursor(cursor)
        self.output_text.blockSignals(False)

    # ----------------------------------------------------------------------------------------------------
    # Use ffprobe to get media metadata
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Create NFO file for the downloaded video
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Download busy spinner + progress bars (no indeterminate bar animation)
    # ----------------------------------------------------------------------------------------------------
    def _set_progress_bars_visible(self, visible: bool):
        self.video_progress_bar.setVisible(visible)
        self.audio_progress_bar.setVisible(visible)

    def _set_download_busy(self, busy: bool):
        if busy:
            self.download_busy_indicator.start()
        else:
            self.download_busy_indicator.stop()

    def _reset_download_progress_bars(self):
        max_val = DownloadProgressManager.PROGRESS_MAX
        self._last_progress_values = (0, 0)
        self.video_progress_bar.setRange(0, max_val)
        self.audio_progress_bar.setRange(0, max_val)
        self.video_progress_bar.setValue(0)
        self.audio_progress_bar.setValue(0)
        self._set_progress_bars_visible(False)

    def _set_download_progress(self, video_value: int, audio_value: int):
        self._last_progress_values = (video_value, audio_value)
        if not self.video_progress_bar.isVisible():
            self._set_progress_bars_visible(True)
        self.video_progress_bar.setValue(video_value)
        self.audio_progress_bar.setValue(audio_value)

    def handle_indeterminate_state(self, is_busy: bool):
        self._set_download_busy(is_busy)
        # Do NOT force-hide progress bars here.
        # We use the spinner both:
        # 1) before the first download progress arrives, and
        # 2) during post-processing steps (after audio download), where keeping the
        #    progress bars visible is useful context.

    # ----------------------------------------------------------------------------------------------------
    # Subs Button
    # ----------------------------------------------------------------------------------------------------
    def open_subtitle_file_dialog(self):
        """Open a file dialog to select an SRT file and process it.

        Parsing, layout and writing a whole subtitle file are synchronous in
        resync_subtitles(), so calling it here used to freeze the window for the
        duration of a long file. The work now runs on a daemon thread through
        _run_subtitle_resync(); the completion is reported back through the
        queued subtitle_processing_complete signal, so the controls re-enable
        on the main thread exactly like the download worker's finally block.
        """
        if self.video_state.get("is_download_running"):
            self._emit(
                "append_output",
                "⚠️ Cannot process subtitles while a download is running",
            )
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SRT file",
            "",
            "SubRip files (*.srt);;All files (*)",
        )
        if not file_path:
            return

        srt_path = file_path
        self._emit("append_output", f"🔄 Processing subtitle file: {os.path.basename(srt_path)}")
        # Disable the primary controls while the worker runs; the completion
        # signal re-enables them. The Cancel button is deliberately NOT in the
        # controls list, so it stays clickable - but there is no cancel for a
        # subtitle job, so it is hidden here too.
        self.set_cancel_button_visible(False)
        self._set_ui_enabled_state(False)
        self._set_download_busy(True)
        self.signals.subtitle_processing_complete.connect(
            self._on_subtitle_processing_complete
        )

        thread = threading.Thread(
            target=self._run_subtitle_resync, args=(srt_path,)
        )
        # Daemon: a non-daemon thread keeps the process alive until it
        # finishes, so closing the window could leave the process running in
        # the background for the rest of a long conversion - and a 30s join
        # followed by dropping the reference would not bound it either. A
        # daemon lets the process exit on close; the atomic write still
        # protects the original SRT, and the worker's finally removes a
        # leftover .tmp on every exit path it actually reaches.
        thread.daemon = True
        thread.start()

    def _on_subtitle_processing_complete(self, ok):
        """Main-thread slot: re-enable the controls after the worker finishes."""
        try:
            self.signals.subtitle_processing_complete.disconnect(
                self._on_subtitle_processing_complete
            )
        except (TypeError, RuntimeError):
            pass
        self._set_download_busy(False)
        self._set_ui_enabled_state(True)
        if ok:
            self._emit("append_output", "✅ Subtitle processing complete")
        else:
            self._emit("append_output", "⚠️ Subtitle processing did not complete")

    # ----------------------------------------------------------------------------------------------------
    # Start download
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Build Command for yt-dlp
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Subtitle helpers (site-aware: YouTube "en"/"a.en", Rumble "en-auto", ...)
    # ----------------------------------------------------------------------------------------------------


    # ----------------------------------------------------------------------------------------------------
    # Run download process
    # ----------------------------------------------------------------------------------------------------


    # ----------------------------------------------------------------------------------------------------
    # Cleanup temporary files
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Load sponsor segments from info JSON file and verify removal
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Calculate how much time to subtract from a timestamp based on removed segments
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Build a mapping of original timestamps to adjusted timestamps
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Build a mapping of original timestamps to adjusted timestamps
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Adjust a timestamp using the pre-built time map with interpolation
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Splits a single line of text into at most two lines at the best space near the midpoint
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Remove non-spoken bracketed text (e.g. [laughter], [snorts])
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Subtitle display pipeline: merge choppy cues → pause/sentence boundaries → 2-line wrap
    # ----------------------------------------------------------------------------------------------------


    # ----------------------------------------------------------------------------------------------------
    # Resync subtitle for a single language
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Resync subtitle file based on removed segments and merge into 2-line format
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Verify removed segments match actual video duration and adjust if needed
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Detect subtitle file paths in yt-dlp output (yt-dlp downloads subtitles
    # before the media streams, so subtitle transfers must be told apart)
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Parse each line of yt-dlp output
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Update the GUI progress bar based on yt-dlp output
    # ----------------------------------------------------------------------------------------------------

    # ----------------------------------------------------------------------------------------------------
    # Analyze the downloaded file and display metadata summary
    # ----------------------------------------------------------------------------------------------------

    def reset_ui(self):
        """Restore all controls to their default state, as when the app
        just opened. Clears URL/title fields, resets all option groups,
        checkboxes, output area, progress bars, and internal state.
        """
        # Stop any pending info-fetch timer
        self.fetch_title_timer.stop()

        # Clear URL and title fields
        self.url_entry.clear()
        self.title_entry.clear()

        # Reset output directory
        self.output_dir_entry.setText(DEFAULT_OUTPUT_DIR)

        # Clear output area (also resets progress bars)
        self.clear_output()

        # Reset internal video state
        self.video_state = {
            "url": "",
            "clean_url": "",
            "video_id": "",
            "title": "",
            "description": "",
            "thumbnail_url": "",
            "channel_id": "",
            "channel_url": "",
            "upload_date": "",
            "language": "",
            "output_dir": DEFAULT_OUTPUT_DIR,
            "base_filename": "",
            "full_path": "",
            "wants_video": True,
            "wants_audio": True,
            "wants_subtitles": False,
            "wants_edl": False,
            "video_format": "best",
            "audio_format": "best",
            "quality": "1080",
            "video_codec": "best",
            "is_fetching_info": False,
            "is_download_running": False,
            "episode_code": "",
            "site": "youtube",
        }
        self.cached_video_metadata = None

        # Reset type checkboxes to defaults
        self.video_checkbox.setChecked(True)
        self.audio_checkbox.setChecked(True)
        self.subtitles_checkbox.setChecked(False)
        self.edl_checkbox.setChecked(False)

        # Reset radio button option groups to their defaults
        for config in self.option_group_map:
            group = config.get("group")
            if group is not None:
                for button in group.buttons():
                    button.setChecked(False)
                # Click the default button
                buttons = config.get("buttons", [])
                for btn_info in buttons:
                    if btn_info.get("is_default"):
                        btn_info["button"].setChecked(True)
                        # Update video_state for this group's attr
                        self.video_state[config["attr"]] = btn_info["value"]
                        break

        # Clear subtitle checkboxes and availability tracking
        self.subtitle_unavailable = set()
        for cb in self.subtitle_checkboxes.values():
            cb.setChecked(False)

        # Clear SponsorBlock category checkboxes
        if hasattr(self, "sb_all_checkbox"):
            self.sb_all_checkbox.setChecked(False)
        for cb in getattr(self, "sb_checkbox_map", []):
            cb.setChecked(False)

        # Clear SponsorBlock bar
        if hasattr(self, "sb_bar"):
            self.sb_bar.set_segments([], 0)

        # Reset thumbnail button and icon
        self.thumbnail_button.setEnabled(False)
        self._reset_thumbnail_icon()

        # Reset download button
        self.download_button.setEnabled(False)
        self.download_button.setText("Download")
        self.download_button.setProperty("status", "")
        self.download_button.style().unpolish(self.download_button)
        self.download_button.style().polish(self.download_button)
        self.download_button.update()

        # Reset cancel button (greyed out)
        self.cancel_button.setEnabled(False)

        # Reset download busy spinner
        self._set_download_busy(False)

        # Reset internal flags
        self._fetching_thumbnail = False
        self._sb_generation = 0
        self._thumbnail_request_token = 0

        # Re-enable all UI controls
        self._set_ui_enabled_state(True)

        self.signals.append_output.emit("🔄 UI reset to default state")

    # ----------------------------------------------------------------------------------------------------
    # Re-enable all controls after download
    # ----------------------------------------------------------------------------------------------------
    def enable_all_controls(self):
        self._set_ui_enabled_state(True)

    def set_download_button_label(self, text: str):
        self.download_button.setText(text)

    def set_cancel_button_visible(self, visible: bool):
        """Enable/disable the cancel control only while a download is running.

        When no download is active the button stays in the sidebar but is
        greyed out (disabled); during a download it is enabled so it can be
        clicked even though the rest of the UI is locked.
        """
        self.cancel_button.setEnabled(bool(visible))

    def _set_sb_bar_segments(self, segments, duration, generation):
        """Slot for signals.update_sb_bar (sb_bar exists only after init_ui).

        The generation token is checked here, on the main thread, because the
        worker already checked the video id before emitting — but Qt may queue
        that signal while the main thread is busy, so a late delivery can still
        repaint a bar that a newer fetch already cleared. The token is bumped
        on the main thread at the start of each info fetch and read back here,
        so the comparison is decisive regardless of how the worker and the
        event loop interleave, and it also covers a second fetch of the *same*
        video, where an id check alone would let an older request publish after
        a newer one.
        """
        if generation != self._sb_generation:
            return
        self.sb_bar.set_segments(segments, duration)

    def _append_sb_log(self, text, generation):
        """Slot for signals.sb_log (SponsorBlock output lines).

        Scoped to the SponsorBlock lookup and gated on the same generation
        token as the bar update, because log signals can be queued and
        delivered after a URL change just as easily as bar updates can. A stale
        line for a superseded video must not appear below the new video's
        metadata. The token is bumped on the main thread at the start of each
        fetch, so the comparison is decisive regardless of how the worker and
        the event loop interleave.
        """
        if generation != self._sb_generation:
            return
        self.append_output_text(text)

    def _append_thumbnail_error(self, text, snapshot_url, token):
        """Slot for signals.thumbnail_error (thumbnail fetch failures).

        Scoped to the thumbnail request and gated on both the request token
        and the snapshot URL, the same way the success path is gated: a
        failure for an old request must not be logged after the user has
        clicked Info on a new video. The URL is checked here too, because a URL
        change clears thumbnail_url without bumping the token — so a queued
        error that arrived after the change but before the next Info click
        would still match the token, and only the URL comparison catches it.
        """
        if token != self._thumbnail_request_token:
            return
        if snapshot_url != self.video_state.get("thumbnail_url", ""):
            return
        self.append_output_text(text)

    def set_download_button_status(self, status: str):
        """
        status:
          - "error" => red styling
          - "" (empty) => default (green) styling
        """
        self.download_button.setProperty("status", status or "")
        # Force Qt to re-evaluate stylesheets after changing dynamic properties
        self.download_button.style().unpolish(self.download_button)
        self.download_button.style().polish(self.download_button)
        self.download_button.update()

    # ----------------------------------------------------------------------------------------------------
    # Set or Unset the Dock tile with Checkbox
    # ----------------------------------------------------------------------------------------------------
    def setDockTileCheck(self, label):
        if self.dockTile:
            self.dockTile.setBadgeLabel_(label)
            self.dockTile.display()

    # ----------------------------------------------------------------------------------------------------
    # Draw a progress bar onto the dock icon tile
    # ----------------------------------------------------------------------------------------------------
    def setDockProgressOverlay(self, progress: float):
        """
        Draw a progress bar onto the dock icon tile.
        progress: 0.0 – 1.0

        Throttled: yt-dlp emits a progress line per update, and every redraw
        allocates an NSImage and repaints the app icon. should_redraw_dock()
        skips the calls that would not move the visible bar.
        """
        if not self.dockTile:
            return

        if not should_redraw_dock(self._last_dock_fraction, progress):
            return
        self._last_dock_fraction = max(0.0, min(1.0, progress))

        size = self.dockTile.size()  # typically 128x128

        img = NSImage.alloc().initWithSize_(size)
        img.lockFocus()

        # Draw app icon as background
        app_icon = NSApplication.sharedApplication().applicationIconImage()
        if app_icon:
            app_icon.drawInRect_(NSMakeRect(0, 0, size.width, size.height))

        # Draw progress bar background (dark, at bottom)
        bar_h = size.height * 0.12
        bar_y = size.height * 0.04
        bar_rect = NSMakeRect(4, bar_y, size.width - 8, bar_h)

        #NSColor.darkGrayColor().setFill()
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.2, 0.2, 0.2, 1.0).setFill()
        bg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bar_rect, 4, 4)
        bg_path.fill()

        # Draw progress fill
        fill_w = max(0.0, min(1.0, progress)) * (size.width - 8)
        fill_rect = NSMakeRect(4, bar_y, fill_w, bar_h)
        NSColor.systemBlueColor().setFill()
        fg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(fill_rect, 4, 4)
        fg_path.fill()

        img.unlockFocus()

        # Apply to dock tile
        image_view = NSImageView.alloc().init()
        image_view.setImage_(img)
        self.dockTile.setContentView_(image_view)
        self.dockTile.display()

    def clearDockProgress(self):
        # Forget the cached fraction so the next download always paints a
        # first frame, even if it resumes at the same value it ended on.
        self._last_dock_fraction = -1.0
        if self.dockTile:
            self.dockTile.setContentView_(None)
            self.dockTile.display()

    # ----------------------------------------------------------------------------------------------------
    # Handle Keypress Events
    # ----------------------------------------------------------------------------------------------------


# Main Class End
