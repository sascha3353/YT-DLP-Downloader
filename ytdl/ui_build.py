"""Widget construction (init_ui), styling, dialogs, UI enable state.

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import html
import logging
import os
import re
import shutil
import threading
import requests
from PyQt6.QtCore import QSize, QTimer, Qt
from PyQt6.QtGui import QAction, QIcon, QKeyEvent, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QRadioButton, QSizePolicy, QStyle, QTextBrowser, QToolButton, QVBoxLayout, QWidget
from ytdl.config import DEFAULT_OUTPUT_DIR
from ytdl.progress import DownloadProgressManager
from ytdl.sites import SUPPORTED_SITES_LABEL
from ytdl.utils import get_log_dir, resource_path, version_header
from ytdl.widgets import BusySpinner, CustomTextEdit, SB_CATEGORY_COLORS, SB_DISPLAY_NAMES, SponsorBlockBar

logger = logging.getLogger(__name__)


def _thumbnail_candidate_urls(thumbnail_url, video_id):
    """Build thumbnail URL candidates, primary (yt-dlp's value) first.

    yt-dlp now hands out localized YouTube thumbnails
    (``https://i.ytimg.com/vi_lc/<id>/maxresdefault_en-US.jpg``) that 404 for
    some videos, while the classic
    ``https://img.youtube.com/vi/<id>/maxresdefault.jpg`` still works for them.
    The yt-dlp URL is tried first so unchanged videos keep using it; fallbacks
    are only added for YouTube-hosted thumbnails, leaving other sites untouched.
    """
    candidates = []
    if thumbnail_url:
        candidates.append(thumbnail_url)
    if video_id and ("youtube" in thumbnail_url or "ytimg" in thumbnail_url):
        candidates.append(f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg")
        candidates.append(f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg")
    # De-duplicate while preserving order, so a primary that already equals a
    # fallback pattern is only tried once.
    seen = set()
    unique = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


class UiBuildMixin:
    def check_dependencies(self):
        missing = []

        if not os.path.isfile(self.yt_dlp_bin) and not shutil.which(self.yt_dlp_bin):
            missing.append("yt-dlp")

        if not os.path.isfile(self.ffmpeg_bin) and not shutil.which(self.ffmpeg_bin):
            missing.append("ffmpeg")

        if not os.path.isfile(self.ffprobe_bin) and not shutil.which(self.ffprobe_bin):
            missing.append("ffprobe")

        # deno is optional — yt-dlp works without it; only YouTube JS-challenge
        # extraction benefits from it. No startup warning needed.

        if missing:
            missing_str = ", ".join(missing)
            self.signals.append_output.emit(
                f"🚩 Error: Missing Dependencies: {missing_str}"
            )

    def _create_radio_group(self, title, attr, group_attr, buttons, enabled=True):
        # Create the top-level GroupBox and Layout
        group_box = QGroupBox(title)
        vertical_layout = QVBoxLayout(group_box)
        vertical_layout.setContentsMargins(15, 0, 20, 0)
        group_box.setLayout(vertical_layout)

        # Create the ButtonGroup instance
        button_group = QButtonGroup(self)
        setattr(self, group_attr, button_group)

        # Prepare data for the generic handler
        button_map = {}
        button_configs = []
        for i, btn_config in enumerate(buttons):
            radio_button = QRadioButton(btn_config["text"])
            setattr(self, btn_config["attr"], radio_button)
            button_group.addButton(radio_button, i)
            vertical_layout.addWidget(radio_button)

            # Set individual button enabled state
            btn_enabled = btn_config.get("enabled", True)
            radio_button.setEnabled(enabled and btn_enabled)
            # The value travels with the button entry itself: the first-word
            # map below collapses "Video + Audio" and "Video only" onto the
            # key "Video", so the change handler resolves by button identity
            # (the map stays as the fallback for text-only lookups).
            button_configs.append({
                "button": radio_button,
                "enabled": btn_enabled,
                "value": btn_config["value"],
            })

            # Optional per-button tooltip (e.g. explaining the M4A container)
            if btn_config.get("tooltip"):
                radio_button.setToolTip(btn_config["tooltip"])

            # Populate map for the generic change handler
            button_map[radio_button.text().split(" ")[0].strip()] = btn_config["value"]

            if btn_config.get("is_default"):
                radio_button.setChecked(True)
                self.video_state[attr] = btn_config["value"]

        # Store the config for the generic change handler
        self.option_group_map.append(
            {
                "group": button_group,
                "attr": attr,
                "map": button_map,
                "enabled": enabled,
                "buttons": button_configs,
            }
        )
        # Connect to the generic handler
        button_group.buttonClicked.connect(self._on_option_group_change)

        # Set enabled state
        group_box.setEnabled(enabled)

        return group_box

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        outer_layout = QHBoxLayout(central_widget)
        outer_layout.setSpacing(10)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        # Left sidebar toolbar (real vertical toolbar with icons + labels)
        sidebar_frame = QFrame()
        sidebar_frame.setObjectName("sidebarFrame")
        sidebar_frame.setFixedWidth(80)
        sidebar_frame.setStyleSheet("""
            QFrame#sidebarFrame {
                background-color: #222222;
            }
            QToolButton#sidebarButton {
                color: #bbbbbb;
                background-color: transparent;
                border: none;
                padding: 2px 0;
                icon-size: 24px;
                font-size: 11px;
                font-weight: 400;
            }
            QToolButton#sidebarButton:hover {
                background-color: #3d3d3d;
                border-radius: 5px;
            }
            QToolButton#sidebarButton:pressed {
                background-color: #4d4d4d;
            }
            QToolButton#sidebarButton:disabled {
                color: #666666;
            }
        """)
        sidebar = QVBoxLayout(sidebar_frame)
        sidebar.setContentsMargins(4, 10, 4, 10)
        sidebar.setSpacing(4)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(15, 0, 15, 0)

        # URL Button
        url_layout = QHBoxLayout()
        url_layout.setContentsMargins(0,0,0,0)
        self.paste_button = QToolButton()
        self.paste_button.setObjectName("sidebarButton")
        self.paste_button.setText("Paste URL")
        self.paste_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.paste_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.paste_button.setToolTip("Paste Clipboard Content")
        self.paste_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.paste_button.setFixedWidth(72)
        self.paste_button.clicked.connect(self.on_paste_button_click)
        # (moved to sidebar)
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Paste video URL here...")
        self.url_entry.textChanged.connect(self.on_url_text_change)
        self.url_entry.returnPressed.connect(self.on_reload_button_click)
        url_layout.addWidget(self.url_entry)
        # Reload Button (right side): re-fetches video info for the current URL
        self.reload_button = QToolButton()
        self.reload_button.setObjectName("sidebarButton")
        self.reload_button.setText("Reload Info")
        self.reload_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.reload_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.reload_button.setToolTip("Re-Fetch Video Info")
        self.reload_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reload_button.setFixedWidth(72)
        self.reload_button.clicked.connect(self.on_reload_button_click)
        # (moved to sidebar)
        main_layout.addLayout(url_layout)
        main_layout.addSpacing(10)

        # Video Title Input
        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0,0,0,0)
        self.nice_button = QToolButton()
        self.nice_button.setObjectName("sidebarButton")
        self.nice_button.setText("Nice Title")
        self.nice_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        self.nice_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.nice_button.setToolTip("Make Nice Video Title")
        self.nice_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.nice_button.setFixedWidth(72)
        self.nice_button.clicked.connect(self.nice_title)
        # (moved to sidebar)
        self.title_entry = QLineEdit()
        self.title_entry.setPlaceholderText("...")
        title_layout.addWidget(self.title_entry)
        main_layout.addLayout(title_layout)
        main_layout.addSpacing(15)

        # Options layout
        group_boxes_hlayout = QHBoxLayout()
        group_boxes_hlayout.setContentsMargins(0,0,0,0)
        group_boxes_hlayout.setSpacing(15)
        # Info and Log buttons stacked vertically
        info_log_layout = QVBoxLayout()
        info_log_layout.setSpacing(5)

        self.thumbnail_button = QToolButton()
        self.thumbnail_button.setObjectName("sidebarButton")
        self.thumbnail_button.setText("Show Info")
        self.thumbnail_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView))
        self.thumbnail_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.thumbnail_button.setToolTip("Show Video Description")
        self.thumbnail_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumbnail_button.setFixedWidth(72)
        self.thumbnail_button.clicked.connect(self.open_thumbnail_dialog)
        self.thumbnail_button.setEnabled(False)
        info_log_layout.addWidget(self.thumbnail_button)

        self.log_button = QToolButton()
        self.log_button.setObjectName("sidebarButton")
        self.log_button.setText("Show Log")
        self.log_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.log_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.log_button.setToolTip("Show Download Log")
        self.log_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_button.setFixedWidth(72)
        self.log_button.clicked.connect(self.open_log_dialog)
        info_log_layout.addWidget(self.log_button)

        # Subs Button (standalone SRT processing) - below Log button
        self.subs_button = QToolButton()
        self.subs_button.setObjectName("sidebarButton")
        self.subs_button.setText("Nice SRT")
        self.subs_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        self.subs_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.subs_button.setToolTip("Remove Rolling from Youtube ASR")
        self.subs_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.subs_button.setFixedWidth(72)
        self.subs_button.clicked.connect(self.open_subtitle_file_dialog)
        info_log_layout.addWidget(self.subs_button)

        # (moved to sidebar)

        # Configuration for all Radio Button Groups
        RADIO_GROUP_CONFIGS = [
            {
                "title": "Quality",
                "attr": "quality",
                "group_attr": "quality_button_group",
                "buttons": [
                    {"text": "1440p", "value": "1440", "attr": "q1440_radio"},
                    {
                        "text": "1080p",
                        "value": "1080",
                        "is_default": True,
                        "attr": "q1080_radio",
                    },
                    {"text": "720p", "value": "720", "attr": "q720_radio"},
                    {"text": "Best", "value": "best", "attr": "qbest_radio"},
                ],
            },
            {
                "title": "Video",
                "attr": "video_codec",
                "group_attr": "codec_button_group",
                "buttons": [
                    {
                        "text": "Best",
                        "value": "best",
                        "is_default": True,
                        "attr": "codec_best_radio",
                    },
                    {"text": "AV1", "value": "av1", "attr": "av1_radio"},
                    {"text": "VP9", "value": "vp9", "attr": "vp9_radio"},
                    {"text": "H264", "value": "h264", "attr": "h264_radio"},
                ],
            },
            {
                "title": "Audio",
                "attr": "audio_format",
                "group_attr": "audio_format_button_group",
                "buttons": [
                    {
                        "text": "Best",
                        "value": "best",
                        "is_default": True,
                        "attr": "abest_radio",
                    },
                    {
                        "text": "Opus",
                        "value": "opus",
                        "attr": "opus_radio",
                    },
                    {
                        "text": "M4A",
                        "value": "m4a",
                        "attr": "m4a_radio",
                        "tooltip": "AAC audio in an M4A (MP4) container. "
                        "Sources already in AAC (YouTube, Rumble) are remuxed "
                        "losslessly without re-encoding.",
                    },
                    {"text": "MP3", "value": "mp3", "attr": "mp3_radio"},
                ],
            },
            {
                "title": "Container",
                "attr": "video_format",
                "group_attr": "video_format_button_group",
                "buttons": [
                    {
                        "text": "Best",
                        "value": "best",
                        "is_default": True,
                        "attr": "vbest_radio",
                    },
                    {"text": "MP4", "value": "mp4", "attr": "mp4_radio"},
                    {"text": "MKV", "value": "mkv", "attr": "mkv_radio"},
                    {"text": "WEBM", "value": "webm", "attr": "webm_radio"},
                ],
            },
        ]
        # group_boxes_hlayout.addStretch(1)

        # --------------------------------------------------------------------
        # Type group: checkboxes instead of radios (video, audio, subtitles,
        # .edl only). Built manually because checkboxes are independent, not
        # mutually exclusive like the radio groups below.
        # --------------------------------------------------------------------
        self.type_group_box = QGroupBox("Type")
        type_group_box = self.type_group_box
        type_layout = QVBoxLayout()
        type_layout.setContentsMargins(15, 0, 20, 0)
        type_layout.setSpacing(6)

        self.video_checkbox = QCheckBox("Video")
        self.video_checkbox.setChecked(self.video_state.get("wants_video", True))
        self.video_checkbox.setToolTip("Download the video stream.")
        self.video_checkbox.stateChanged.connect(self._on_type_checkbox_changed)
        type_layout.addWidget(self.video_checkbox)

        self.audio_checkbox = QCheckBox("Audio")
        self.audio_checkbox.setChecked(self.video_state.get("wants_audio", True))
        self.audio_checkbox.setToolTip("Download the audio stream.")
        self.audio_checkbox.stateChanged.connect(self._on_type_checkbox_changed)
        type_layout.addWidget(self.audio_checkbox)

        self.subtitles_checkbox = QCheckBox("Subtitles")
        self.subtitles_checkbox.setChecked(
            self.video_state.get("wants_subtitles", False)
        )
        self.subtitles_checkbox.setToolTip("Download subtitle tracks only (with --skip-download).")
        self.subtitles_checkbox.stateChanged.connect(self._on_type_checkbox_changed)
        type_layout.addWidget(self.subtitles_checkbox)

        self.edl_checkbox = QCheckBox(".edl only")
        self.edl_checkbox.setChecked(self.video_state.get("wants_edl", False))
        self.edl_checkbox.setToolTip(
            "Write only a SponsorBlock .edl skip file for this video. "
            "No yt-dlp command is started."
        )
        self.edl_checkbox.stateChanged.connect(self._on_type_checkbox_changed)
        type_layout.addWidget(self.edl_checkbox)

        type_group_box.setLayout(type_layout)
        group_boxes_hlayout.addWidget(type_group_box)

        # Loop and create the option groups dynamically
        for config in RADIO_GROUP_CONFIGS:
            group_box = self._create_radio_group(
                config["title"],
                config["attr"],
                config["group_attr"],
                config["buttons"],
                enabled=config.get("enabled", True),
            )
            group_boxes_hlayout.addWidget(group_box)

        # Subtitles Group
        self.subtitle_group_box = QGroupBox("Subtitles")
        subtitle_group_box = self.subtitle_group_box
        subtitle_layout = QVBoxLayout()
        subtitle_layout.setContentsMargins(15, 0, 20, 0)
        # Language Checkboxes
        languages = [("English", "en"), ("German", "de"), ("Spanish", "es")]
        self.subtitle_checkboxes = {}
        # Languages whose last info fetch reported "(none)" availability;
        # their checkboxes stay disabled even when the UI is re-enabled
        self.subtitle_unavailable = set()
        for label, code in languages:
            cb = QCheckBox(f"{label} ({code})")
            # Start unchecked — subtitle selection is manual; an info fetch
            # only updates labels/availability (see _update_subtitle_checkboxes)
            cb.setChecked(False)
            cb.stateChanged.connect(self._update_subtitle_warning)
            subtitle_layout.addWidget(cb)
            self.subtitle_checkboxes[code] = cb
        subtitle_group_box.setLayout(subtitle_layout)
        group_boxes_hlayout.addWidget(subtitle_group_box)
        group_boxes_hlayout.addStretch(1)
        main_layout.addLayout(group_boxes_hlayout)
        main_layout.addSpacing(10)

        # SponsorBlock Checkboxes
        sponsorblock_group_box = QGroupBox("SponsorBlock")
        sponsorblock_group_box.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred
        )
        sponsorblock_layout = QVBoxLayout()
        sponsorblock_layout.setSpacing(8)
        sponsorblock_layout.setContentsMargins(15, 2, 25, 4)
        sponsorblock_row = QHBoxLayout()
        sponsorblock_row.setSpacing(21)

        self.sb_sponsor_checkbox = QCheckBox(SB_DISPLAY_NAMES["sponsor"])
        self.sb_sponsor_checkbox.setToolTip("Paid promotion and direct advertisements.")
        self.sb_selfpromo_checkbox = QCheckBox(SB_DISPLAY_NAMES["selfpromo"])
        self.sb_selfpromo_checkbox.setToolTip(
            "Unpaid or self promotion, merchandise and donations."
        )
        self.sb_interaction_checkbox = QCheckBox(SB_DISPLAY_NAMES["interaction"])
        self.sb_interaction_checkbox.setToolTip("Reminder to like, subscribe or follow.")
        self.sb_intro_checkbox = QCheckBox(SB_DISPLAY_NAMES["intro"])
        self.sb_intro_checkbox.setToolTip(
            "Intro, intermission, animation or pause without content."
        )
        self.sb_ending_checkbox = QCheckBox(SB_DISPLAY_NAMES["ending"])
        self.sb_ending_checkbox.setToolTip("Outro, endcards or credits.")
        self.sb_preview_checkbox = QCheckBox(SB_DISPLAY_NAMES["preview"])
        self.sb_preview_checkbox.setToolTip("Preview (coming up) or recap.")
        self.sb_hook_checkbox = QCheckBox(SB_DISPLAY_NAMES["hook"])
        self.sb_hook_checkbox.setToolTip("Greetings or trailer for upcoming video.")
        self.sb_tangents_checkbox = QCheckBox(SB_DISPLAY_NAMES["tangents"])
        self.sb_tangents_checkbox.setToolTip("Tangential scene, filler or jokes.")

        # Hide "All" checkbox for now
        self.sb_all_checkbox = QCheckBox("All")
        self.sb_all_checkbox.hide()

        # Create mapping
        self.sb_checkbox_map = {
            self.sb_sponsor_checkbox: "sponsor",
            self.sb_selfpromo_checkbox: "selfpromo",
            self.sb_interaction_checkbox: "interaction",
            self.sb_intro_checkbox: "intro",
            self.sb_ending_checkbox: "ending",
            self.sb_preview_checkbox: "preview",
            self.sb_hook_checkbox: "hook",
            self.sb_tangents_checkbox: "tangents",
        }
        self.sb_all_checkbox.stateChanged.connect(self.toggle_sb_categories)

        # Default checked
        self.sb_sponsor_checkbox.setChecked(True)
        self.sb_selfpromo_checkbox.setChecked(True)

        # Style the checkboxes: labels in default color, indicator in category color
        for cb, cat in self.sb_checkbox_map.items():
            color = SB_CATEGORY_COLORS.get(cat, "#888888")
            cb.setStyleSheet(f"""
                QCheckBox {{
                    spacing: 4px;
                }}
                QCheckBox::indicator {{
                    width: 12px;
                    height: 12px;
                }}
                QCheckBox::indicator:unchecked {{
                    border: 2px solid {color};
                    background: transparent;
                    border-radius: 2px;
                }}
                QCheckBox::indicator:checked {{
                    border: 2px solid {color};
                    background: {color};
                    border-radius: 2px;
                }}
            """)

        # Layout into a single row
        for cb in [
            self.sb_sponsor_checkbox,
            self.sb_selfpromo_checkbox,
            self.sb_interaction_checkbox,
            self.sb_intro_checkbox,
            self.sb_ending_checkbox,
            self.sb_preview_checkbox,
            self.sb_hook_checkbox,
            self.sb_tangents_checkbox,
        ]:
            sponsorblock_row.addWidget(cb)

        sponsorblock_row.addStretch()
        sponsorblock_layout.addLayout(sponsorblock_row)
        self.sb_bar = SponsorBlockBar()
        sponsorblock_layout.addWidget(self.sb_bar)
        sponsorblock_group_box.setLayout(sponsorblock_layout)

        sb_centering_layout = QHBoxLayout()
        # (removed left spacing)
        sb_centering_layout.addWidget(sponsorblock_group_box)
        sb_centering_layout.addStretch(1)
        main_layout.addLayout(sb_centering_layout)

        # SponsorBlock Visual Bar
        sb_bar_layout = QHBoxLayout()
        sb_bar_layout.setContentsMargins(15, 0, 20, 0)
        main_layout.addSpacing(5)

        # Output Directory (layout intentionally not added to main_layout;
        # widgets kept because get_output_dir / start_download / _set_ui_enabled_state use them)
        self.output_dir_entry = QLineEdit(DEFAULT_OUTPUT_DIR)
        self.browse_button = QPushButton("📂")
        self.browse_button.clicked.connect(self.on_browse_directory)

        # Download Button
        self.download_button = QPushButton("Download")
        self.download_button.setToolTip("Start downloading (Cmd+D)")
        self.download_button.setObjectName("downloadButton")
        self.download_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_button.clicked.connect(self.start_download)
        # Cancel is deliberately NOT part of _set_ui_enabled_state()'s control
        # list, so it stays clickable while the rest of the UI is disabled.
        # It lives in the sidebar toolbar and is disabled (greyed out) when no
        # download is running via set_cancel_button_visible().
        self.cancel_button = QToolButton()
        self.cancel_button.setObjectName("sidebarButton")
        self.cancel_button.setText("Cancel")
        self.cancel_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton)
        )
        self.cancel_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.cancel_button.setToolTip("Cancel Download")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setFixedWidth(72)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_download)

        # Reset UI button (restores all controls to default state)
        self.reset_button = QToolButton()
        self.reset_button.setObjectName("sidebarButton")
        self.reset_button.setText("Reset UI")
        self.reset_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton))
        self.reset_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.reset_button.setToolTip("Reset UI to Default")
        self.reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_button.setFixedWidth(72)
        self.reset_button.clicked.connect(self.reset_ui)

        download_hlayout = QHBoxLayout()
        download_hlayout.setSpacing(8)
        self.download_busy_indicator = BusySpinner(size=24)
        sp = self.download_busy_indicator.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.download_busy_indicator.setSizePolicy(sp)
        self.download_busy_indicator.hide()
        download_hlayout.addWidget(self.download_button, 0)
        download_hlayout.addWidget(
            self.download_busy_indicator, 0, Qt.AlignmentFlag.AlignVCenter
        )
        download_hlayout.setContentsMargins(0, 10, 35, 15)
        main_layout.addLayout(download_hlayout)

        # Area above Output Text
        output_header_layout = QHBoxLayout()

        # Clear Button
        # self.clear_link = QPushButton("Output:")
        # self.clear_link.setObjectName("clearLink")
        # self.clear_link.setToolTip("Click to clear output area")
        # self.clear_link.clicked.connect(self.clear_output)
        # output_header_layout.addWidget(self.clear_link)

        # Video + audio progress (one row, minimal gap)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(1)
        progress_row.setContentsMargins(0, 0, 0, 0)

        bar_policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.video_progress_bar = QProgressBar()
        self.video_progress_bar.setObjectName("videoProgressBar")
        self.video_progress_bar.setRange(0, DownloadProgressManager.PROGRESS_MAX)
        self.video_progress_bar.setTextVisible(False)
        self.video_progress_bar.setSizePolicy(bar_policy)
        self.video_progress_bar.setValue(0)
        self.video_progress_bar.setToolTip("Video download")
        progress_row.addWidget(
            self.video_progress_bar, DownloadProgressManager.VIDEO_BAR_STRETCH
        )

        self.audio_progress_bar = QProgressBar()
        self.audio_progress_bar.setObjectName("audioProgressBar")
        self.audio_progress_bar.setRange(0, DownloadProgressManager.PROGRESS_MAX)
        self.audio_progress_bar.setTextVisible(False)
        self.audio_progress_bar.setSizePolicy(bar_policy)
        self.audio_progress_bar.setValue(0)
        self.audio_progress_bar.setToolTip("Audio download")
        progress_row.addWidget(
            self.audio_progress_bar, DownloadProgressManager.AUDIO_BAR_STRETCH
        )
        self._last_progress_values = (0, 0)
        self._set_progress_bars_visible(False)

        output_header_layout.addLayout(progress_row)
        main_layout.addLayout(output_header_layout)
        main_layout.addSpacing(5)

        # Output Text Area
        self.output_text = CustomTextEdit(self)
        self.output_text.setReadOnly(True)
        self.output_text.append(version_header())
        self.output_text.append("Supported: " + SUPPORTED_SITES_LABEL)
        main_layout.addWidget(self.output_text)

        # Sidebar toolbar buttons (left)
        for btn in (self.paste_button, self.reload_button, self.nice_button, self.thumbnail_button, self.subs_button, self.log_button, self.cancel_button, self.reset_button):
            btn.setFixedHeight(56)
            sidebar.addWidget(btn)
        sidebar.addStretch(1)

        outer_layout.addWidget(sidebar_frame, 0)
        outer_layout.addLayout(main_layout, 1)

    def apply_styles(self):
        qss_path = resource_path("assets/styles.qss")
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                stylesheet = f.read()
            self.setStyleSheet(stylesheet)
        except FileNotFoundError:
            logger.error(f"Stylesheet not found at: {qss_path}")
            self.signals.append_output.emit(f"🚩 Stylesheet not found: {qss_path}")

    def setup_menu_bar(self):
        menu_bar = self.menuBar()

        # App menu (YT-DLP Downloader)
        app_menu = menu_bar.addMenu("YT-DLP Downloader")

        prefs_action = QAction("Preferences...", self)
        prefs_action.setShortcut(QKeySequence("Ctrl+,"))
        prefs_action.triggered.connect(self.open_preferences_dialog)
        app_menu.addAction(prefs_action)

        app_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        app_menu.addAction(quit_action)

    def _set_ui_enabled_state(self, enabled: bool):
        self._ui_enabled = enabled

        # Primary Controls
        controls = [
            self.url_entry,
            self.paste_button,
            self.reload_button,
            self.title_entry,
            self.nice_button,
            self.output_dir_entry,
            self.browse_button,
            self.download_button,
            self.subs_button,
            self.sb_all_checkbox,
        ]
        for control in controls:
            control.setEnabled(enabled)

        # Radio Button Option Groups
        for config in self.option_group_map:
            group_enabled = enabled and config.get("enabled", True)
            if "buttons" in config:
                for btn_info in config["buttons"]:
                    btn_info["button"].setEnabled(
                        group_enabled and btn_info.get("enabled", True)
                    )
            else:
                for button in config["group"].buttons():
                    button.setEnabled(group_enabled)

        # Type checkboxes — dynamic enabling handled by
        # _update_type_checkbox_enable_state(); here we only set the base
        # enabled state (all disabled when the UI is locked during a download).
        for name in ("video_checkbox", "audio_checkbox", "subtitles_checkbox", "edl_checkbox"):
            cb = getattr(self, name, None)
            if cb is not None:
                cb.setEnabled(enabled)
        if enabled:
            self._update_type_checkbox_enable_state()

        # Subtitle Checkboxes (using the new dictionary); languages whose
        # last info fetch reported "(none)" availability stay disabled
        for code, cb in self.subtitle_checkboxes.items():
            cb.setEnabled(enabled and code not in self.subtitle_unavailable)

        # SponsorBlock Category Checkboxes
        sb_categories_enabled = enabled and not self.sb_all_checkbox.isChecked()

        for cb in self.sb_checkbox_map:
            cb.setEnabled(sb_categories_enabled)

    def _on_option_group_change(self, button):
        group = button.group()
        # Find the matching configuration in the map
        config = next(
            (item for item in self.option_group_map if item["group"] == group), None
        )
        if not config:
            return

        # Resolve the new value by button identity. The first-word text map
        # collapses "Video + Audio" and "Video only" onto the key "Video",
        # so a text lookup stored whichever button was registered last —
        # clicking "Video + Audio" set media_type to "video_only". The text
        # map remains the fallback for buttons not in the config list.
        entry = next(
            (b for b in config.get("buttons", []) if b["button"] is button),
            None,
        )
        if entry is not None and "value" in entry:
            new_value = entry["value"]
        else:
            # Get the clean button text (e.g., "1080" from "1080p")
            button_text = button.text().split(" ")[0].strip()
            # Determine the selected value using the map
            new_value = config["map"].get(button_text, button_text.lower())
        # save state
        self.video_state[config["attr"]] = new_value

        # If "Subtitles only" is selected, auto-check subtitles
        if config["attr"] == "media_type" and new_value == "subtitles":
            self._auto_select_subtitles()

    def _on_type_checkbox_changed(self):
        """Handle changes to the Type checkboxes (video, audio, subtitles,
        .edl only). Updates video_state and re-applies dynamic enabling."""
        cb = self.sender()
        if cb is self.video_checkbox:
            self.video_state["wants_video"] = cb.isChecked()
        elif cb is self.audio_checkbox:
            self.video_state["wants_audio"] = cb.isChecked()
        elif cb is self.subtitles_checkbox:
            self.video_state["wants_subtitles"] = cb.isChecked()
        elif cb is self.edl_checkbox:
            self.video_state["wants_edl"] = cb.isChecked()

        # Re-apply dynamic enable/disable rules
        self._update_type_checkbox_enable_state()

        # Subtitles-only mode: auto-select subtitle languages (same rule the
        # old "Subtitles only" radio button applied via _on_option_group_change)
        if (
            self.video_state.get("wants_subtitles")
            and not self.video_state.get("wants_video")
            and not self.video_state.get("wants_audio")
        ):
            self._auto_select_subtitles()

        # When the user checks the Type "Subtitles" checkbox without having
        # picked a language yet, pre-select the first selectable language so
        # the subtitle download actually has a target. Only auto-checks: if
        # at least one language is already chosen we leave it alone, and we
        # never auto-uncheck. The language checkbox's stateChanged then runs
        # _update_subtitle_warning, which sees the type checkbox already
        # checked and does nothing, so there is no feedback loop.
        if (
            cb is self.subtitles_checkbox
            and cb.isChecked()
            and not any(c.isChecked() for c in self.subtitle_checkboxes.values())
        ):
            for lang_cb in self.subtitle_checkboxes.values():
                if lang_cb.isEnabled():
                    lang_cb.setChecked(True)
                    break

    def _update_type_checkbox_enable_state(self):
        """Dynamic enabling/disabling of Type checkboxes based on each other's
        state.

        Rules:
          - EDL checked  → grey out Video, Audio, Subtitles (EDL is exclusive)
          - Subtitles checked AND neither Video nor Audio → grey out Video,
            Audio (subtitles-only mode)
          - Otherwise → all follow the base UI enabled state
        """
        if not getattr(self, "_ui_enabled", True):
            # UI is locked (download running): everything already disabled by
            # _set_ui_enabled_state; nothing more to do.
            return

        edl = self.edl_checkbox.isChecked() if hasattr(self, "edl_checkbox") else False
        subtitles = self.subtitles_checkbox.isChecked() if hasattr(self, "subtitles_checkbox") else False
        video = self.video_checkbox.isChecked() if hasattr(self, "video_checkbox") else False
        audio = self.audio_checkbox.isChecked() if hasattr(self, "audio_checkbox") else False

        if edl:
            # EDL only: grey out everything else
            for name in ("video_checkbox", "audio_checkbox", "subtitles_checkbox"):
                cb = getattr(self, name, None)
                if cb is not None:
                    cb.setEnabled(False)
        elif subtitles and not video and not audio:
            # Subtitles only: grey out Video + Audio
            for name in ("video_checkbox", "audio_checkbox"):
                cb = getattr(self, name, None)
                if cb is not None:
                    cb.setEnabled(False)
        else:
            # Normal mode: all enabled
            for name in ("video_checkbox", "audio_checkbox", "subtitles_checkbox"):
                cb = getattr(self, name, None)
                if cb is not None:
                    cb.setEnabled(True)

    def toggle_sb_categories(self, state):
        # Disable/enable all other SponsorBlock checkboxes depending on "All"
        disable = state == Qt.CheckState.Checked.value
        for cb in [
            self.sb_sponsor_checkbox,
            self.sb_selfpromo_checkbox,
            self.sb_interaction_checkbox,
            self.sb_intro_checkbox,
            self.sb_ending_checkbox,
            self.sb_preview_checkbox,
            self.sb_hook_checkbox,
            self.sb_tangents_checkbox,
        ]:
            cb.setDisabled(disable)

    def on_browse_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Choose Output Directory", self.output_dir_entry.text()
        )
        if directory:
            self.output_dir_entry.setText(directory)

    def nice_title(self):
        title = self.title_entry.text()

        # Replacements
        title = title.replace(":", " - ")
        title = re.sub(r"[|–/]+", "-", title)

        # Remove all characters except: word characters (a-z, A-Z, 0-9, _), spaces, hyphens, and parentheses
        title = re.sub(r"[^\w\s\-\(\)]", "", title)

        # Clean up multiple spaces
        title = re.sub(r"\s+", " ", title).strip()

        # Convert to Title Case, while preserving already-lowercase words
        def to_title_case(text):
            parts = re.split(r"([ \-\(\)])", text)
            delimiters = [" ", "-", "(", ")"]
            capitalized_parts = []
            for p in parts:
                if p in delimiters or not p:
                    capitalized_parts.append(p)
                elif p.islower():
                    capitalized_parts.append(p)
                else:
                    capitalized_parts.append(p.capitalize())
            return "".join(capitalized_parts)

        # Check if the title starts with a season/episode code like S01E02 or S26E0110
        ep_match = re.match(r"^(S\d+E\d+)(.*)", title)
        if ep_match:
            episode_prefix = ep_match.group(1)  # e.g. "S01E02"
            rest = ep_match.group(2)            # e.g. " - my awesome video title"
            title = episode_prefix + to_title_case(rest)
        else:
            # No episode prefix, Title Case the entire title
            title = to_title_case(title)

        self.title_entry.setText(title)

    def _apply_thumbnail_icon(self, image_data):
        """Set the Info toolbar icon from fetched thumbnail bytes, or reset to standard.

        The thumbnail is scaled to 24px height with aspect ratio preserved, so
        a 1280x720 thumbnail becomes roughly 43px wide — non-square, matching
        the video's true proportions instead of being forced into a square.
        """
        button = self.thumbnail_button
        if image_data and hasattr(button, "setIcon"):
            try:
                pixmap = QPixmap()
                if pixmap.loadFromData(image_data):
                    # Scale to 24px height, keep aspect ratio (non-square)
                    pixmap = pixmap.scaledToHeight(
                        24, Qt.TransformationMode.SmoothTransformation
                    )
                    button.setIcon(QIcon(pixmap))
                    # Tell QToolButton the exact icon size so it doesn't
                    # recalculate layout (which could shift height by 1-2px
                    # vs the standard style icon).
                    button.setIconSize(QSize(pixmap.width(), pixmap.height()))
                    return
            except Exception:
                pass
        self._reset_thumbnail_icon()

    def _reset_thumbnail_icon(self):
        """Reset the Info button to the standard grayscale icon."""
        button = self.thumbnail_button
        if hasattr(button, "setIcon"):
            button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView)
            )
            button.setIconSize(QSize(24, 24))

    def _set_thumbnail_icon(self, image_data, description, snapshot_url, token):
        """Slot for signals.thumbnail_icon_ready: set the Info button icon.

        Token and URL staleness guards ensure a response for a superseded
        video never repaints the icon.
        """
        if token != self._thumbnail_request_token:
            return
        if snapshot_url != self.video_state.get("thumbnail_url", ""):
            return
        self._apply_thumbnail_icon(image_data)

    def _fetch_thumbnail_for_icon(self):
        """Fetch the current video's thumbnail and set it as the Info icon.

        Uses a separate signal (thumbnail_icon_ready) from the dialog flow so
        an automatic icon update never pops the thumbnail dialog.
        """
        thumbnail_url = self.video_state.get("thumbnail_url", "")
        if not thumbnail_url:
            self._reset_thumbnail_icon()
            return

        self._thumbnail_request_token += 1
        snapshot_token = self._thumbnail_request_token
        snapshot_url = thumbnail_url

        def _fetch():
            image_data = None
            last_error = None
            # Try the yt-dlp URL first, then fall back to unlocalized YouTube
            # patterns for videos whose localized thumbnail 404s.
            for url in _thumbnail_candidate_urls(thumbnail_url, self.video_state.get("video_id", "")):
                try:
                    response = requests.get(url, timeout=10)
                    response.raise_for_status()
                    image_data = response.content
                    break
                except Exception as e:
                    last_error = e
            if image_data is None:
                logger.debug(
                    "Thumbnail fetch failed for %s: %s",
                    thumbnail_url,
                    last_error,
                )
            self._emit(
                "thumbnail_icon_ready",
                image_data,
                "",
                snapshot_url,
                snapshot_token,
            )

        threading.Thread(target=_fetch, daemon=True).start()

    def open_thumbnail_dialog(self):
        thumbnail_url = self.video_state.get("thumbnail_url", "")
        if not thumbnail_url:
            # Main thread: the SignalEmitter is still alive here.
            self.signals.append_output.emit("👉 No thumbnail available")
            return

        # Single-flight guard: ignore extra clicks while a fetch is in progress
        if self._fetching_thumbnail:
            return
        self._fetching_thumbnail = True

        # Capture the description alongside the URL at click time. The worker
        # fetches the image on a daemon thread, so by the time it arrives the
        # user may have switched videos; reading the description from live
        # video_state in the receiver would pair an old image with a new
        # description. Two staleness guards, because a response can arrive at
        # two different times:
        #
        # 1. The request token below is bumped on each click and again on a
        #    URL change. It gates the flight-guard reset and the error log on
        #    both sides, so a stale response cannot clear a newer request's
        #    guard or be logged over the new video's output panel.
        #
        # 2. The URL is re-checked in the receiver against the current
        #    thumbnail_url, so a queued delivery for a superseded video is
        #    dropped there too. This is the second line of defence: the URL
        #    check lives in the receiver, not the worker.
        snapshot_url = thumbnail_url
        snapshot_description = self.video_state.get("description", "")
        # Request token. Each click bumps it, and on_url_text_change() bumps it
        # again — so a stale response — success or failure — cannot clear a
        # newer request's in-flight guard: both the worker's finally and the
        # receiver only act when the token still matches the current request.
        self._thumbnail_request_token += 1
        snapshot_token = self._thumbnail_request_token

        def _fetch_and_show():
            try:
                image_data = None
                last_error = None
                # Try the yt-dlp URL first, then fall back to unlocalized YouTube
                # patterns for videos whose localized thumbnail 404s.
                for url in _thumbnail_candidate_urls(thumbnail_url, self.video_state.get("video_id", "")):
                    try:
                        response = requests.get(url, timeout=10)
                        response.raise_for_status()
                        image_data = response.content
                        break
                    except Exception as e:
                        last_error = e
                if image_data is None:
                    # Worker thread: route through _emit() so a close in flight
                    # cannot emit into a destroyed QObject. The token and the
                    # primary URL travel with the message so a failure for an old
                    # request cannot be logged over the new video.
                    self._emit(
                        "thumbnail_error",
                        f"🚩 Error loading thumbnail: {last_error}",
                        snapshot_url,
                        snapshot_token,
                    )
                    return
                # Deliver bytes to the main thread via a queued signal — the only
                # safe way to trigger Qt widget construction from a worker
                # thread. (QTimer.singleShot from a plain threading.Thread has
                # no event loop and is silently dropped by Qt 6.) _emit()
                # guards it too, so a close in flight cannot deliver into a
                # destroyed window. The description and the primary URL travel
                # with the image so the dialog cannot mix metadata from two
                # different videos, and the staleness guards in the receivers
                # keep comparing against video_state["thumbnail_url"].
                self._emit(
                    "thumbnail_ready", image_data, snapshot_description, snapshot_url, snapshot_token
                )
            finally:
                # Reset the flight guard only for this request. A stale
                # response must not clear a newer request's guard, so the
                # comparison happens here, on the worker, before the token is
                # handed to the main thread.
                if snapshot_token == self._thumbnail_request_token:
                    self._fetching_thumbnail = False

        threading.Thread(target=_fetch_and_show, daemon=True).start()

    def _show_thumbnail_dialog(self, image_data: bytes, description: str, snapshot_url: str, token: int):
        # Reset the flight guard only for this request. A stale response must
        # not clear a newer request's guard — if the token no longer matches,
        # the guard stays live and the dialog is not shown.
        if token == self._thumbnail_request_token:
            self._fetching_thumbnail = False
        try:
            # The user may have switched videos while the image was in flight.
            # The URL was captured with the image, so compare against the
            # current one: a response for a superseded video must not be shown
            # over the new video's metadata. This is the second line of
            # defence — the first is the request token, checked above.
            if snapshot_url != self.video_state.get("thumbnail_url", ""):
                return
            # Update the Info toolbar icon from the fetched thumbnail
            self._apply_thumbnail_icon(image_data)
            # Create dialog
            dialog = QDialog(self)
            dialog.setWindowTitle("Video Description")
            layout = QVBoxLayout(dialog)

            # Add Command+Q shortcut to the dialog
            quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), dialog)
            app_instance = QApplication.instance()
            if app_instance:
                quit_shortcut.activated.connect(app_instance.quit)

            # Create label for image
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)

            # Scale image if too large
            max_width = 420
            max_height = 240
            if pixmap.width() > max_width or pixmap.height() > max_height:
                pixmap = pixmap.scaled(
                    max_width,
                    max_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

            image_label.setPixmap(pixmap)
            layout.addWidget(image_label)

            # Add description if available — the one captured with the URL, not
            # the live video_state, so a video change in between cannot mix
            # metadata.
            if description:
                description_text = QTextBrowser()
                description_text.setReadOnly(True)
                description_text.setOpenExternalLinks(True)

                # Escape HTML characters and convert URLs to clickable links
                escaped_desc = html.escape(description)
                url_pattern = r'(https?://[^\s<>"]+|www\.[^\s<>"]+)'

                def make_link(match):
                    url = match.group(1)
                    full_url = url if url.startswith("http") else "http://" + url
                    return f'<a href="{full_url}">{url}</a>'

                html_description = re.sub(url_pattern, make_link, escaped_desc)
                description_text.setHtml(html_description.replace("\n", "<br>"))
                layout.addWidget(description_text)

            dialog.resize(
                730,
                pixmap.height() + (400 if description else 0),
            )
            dialog.exec()

        except Exception as e:
            self.signals.append_output.emit(f"🚩 Error showing thumbnail dialog: {e}")

    def open_log_dialog(self):
        log_path = os.path.join(get_log_dir(), "app.log")
        dialog = QDialog(self)
        dialog.setWindowTitle("Download Log")
        dialog.resize(750, 500)

        layout = QVBoxLayout(dialog)

        text_browser = QTextBrowser()
        text_browser.setReadOnly(True)
        text_browser.setOpenExternalLinks(False)
        text_browser.setStyleSheet("""
            QTextBrowser {
                font-family: "Menlo", "Courier New";
                font-size: 12px;
            }
        """)

        MAX_LOG_LINES = 500
        log_has_content = False
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Keep only the tail so the dialog stays fast on long-lived installs
            if len(lines) > MAX_LOG_LINES:
                omitted = len(lines) - MAX_LOG_LINES
                lines = [f"[ {omitted} earlier lines omitted ]\n"] + lines[-MAX_LOG_LINES:]

            if any(ln.strip() for ln in lines):
                log_has_content = True
                # Color result lines: red for failed, green for succeeded
                colored_lines = []
                for line in lines:
                    line = line.rstrip("\n")
                    if "Download failed" in line:
                        colored_lines.append(f'<span style="color: #ff6b6b;">{html.escape(line)}</span>')
                    elif "Download succeeded" in line:
                        colored_lines.append(f'<span style="color: #4ade80;">{html.escape(line)}</span>')
                    else:
                        colored_lines.append(html.escape(line))
                text_browser.setHtml("<br>".join(colored_lines))
                # Auto-scroll to bottom so latest entries are visible (use timer to ensure content is rendered)
                def scroll_to_bottom():
                    scrollbar = text_browser.verticalScrollBar()
                    if scrollbar:
                        scrollbar.setValue(scrollbar.maximum())
                QTimer.singleShot(0, scroll_to_bottom)
            else:
                text_browser.setText("Log is empty.")
        except FileNotFoundError:
            text_browser.setText(f"Log file not found at:\n{log_path}")
        except OSError as e:
            text_browser.setText(f"Could not read log file:\n{e}")

        layout.addWidget(text_browser)

        # Buttons: Clear (left) / Close (right)
        button_layout = QHBoxLayout()

        clear_button = QPushButton("Clear")
        clear_button.setToolTip("Delete all entries from the log file")
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Nothing to delete when the log is missing/empty
        clear_button.setEnabled(log_has_content)
        clear_button.clicked.connect(
            lambda: self._clear_log_dialog(dialog, text_browser, clear_button, log_path)
        )
        button_layout.addWidget(clear_button)

        # Dialog-local shortcut (the window is modal, so no clash with the main
        # window bindings); QPushButton.click() is a no-op while disabled.
        clear_shortcut = QShortcut(QKeySequence("Ctrl+K"), dialog)
        clear_shortcut.activated.connect(clear_button.click)

        button_layout.addStretch(1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)

        dialog.exec()

    def _clear_log_dialog(self, dialog, text_browser, clear_button, log_path):
        """Confirm, clear the app log on disk and update the dialog in place."""
        confirm = QMessageBox.question(
            dialog,
            "Clear Log",
            f"Delete all entries from\n{log_path}\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            self._truncate_log_file(log_path)
        except OSError as e:
            text_browser.setText(f"Could not clear log file:\n{e}")
            self.signals.append_output.emit(f"🚩 Could not clear log file: {e}")
            return

        text_browser.setPlainText(f"Log cleared.\n\n{log_path}")
        clear_button.setEnabled(False)
        self.signals.append_output.emit("🧹 Log cleared")

    def _truncate_log_file(self, log_path):
        """Empty the log file, keeping the logging FileHandler in sync.

        Truncates through the handler's own stream while holding its lock, so
        the stream offset stays consistent (logging opens files in append mode
        by default, where a plain truncate behind the handler's back would also
        work — but seeking/truncating on the handler's stream stays correct for
        any file mode) and the truncation can never interleave with an in-flight
        emit from a worker thread. Qt-free, so it is unit-testable.
        """
        truncated = False
        for handler in logging.getLogger().handlers:
            if not isinstance(handler, logging.FileHandler):
                continue
            if os.path.abspath(handler.baseFilename) != os.path.abspath(log_path):
                continue
            handler.acquire()
            try:
                handler.flush()
                stream = getattr(handler, "stream", None)
                if stream is None:
                    # Handler is closed; FileHandler.emit() reopens at offset 0,
                    # so truncating the file from here is safe.
                    with open(log_path, "w", encoding="utf-8"):
                        pass
                else:
                    stream.seek(0)
                    stream.truncate()
            finally:
                handler.release()
            truncated = True

        if not truncated:
            # No handler owns this file (e.g. tests) — plain truncate
            with open(log_path, "w", encoding="utf-8"):
                pass

    def closeEvent(self, event):
        # Closing the window cancels an active download, so yt-dlp (and any
        # ffmpeg child) is not left running as an orphan. A no-op otherwise.
        # The flag tells the download worker to stop emitting UI signals: the
        # SignalEmitter they belong to is destroyed along with this window, and
        # emitting into it mid-teardown can raise or crash the process.
        self._shutting_down = True
        self.cancel_download()
        super().closeEvent(event)

    def keyPressEvent(self, a0):
        if not isinstance(a0, QKeyEvent):
            return

        # Cancel an active download with Escape. A no-op when nothing is
        # running, so it is safe to bind globally.
        if a0.key() == Qt.Key.Key_Escape and self.video_state.get("is_download_running"):
            self.cancel_download()
            a0.accept()
            return

        # Handle keyboard shortcuts
        modifiers = a0.modifiers()
        key = a0.key()

        # Handle Cmd/Ctrl + Q (Quit)
        if key == Qt.Key.Key_Q and (
            modifiers & Qt.KeyboardModifier.ControlModifier
            or modifiers & Qt.KeyboardModifier.MetaModifier
        ):
            self.close()
            a0.accept()
            return

        # Handle Cmd/Ctrl + L (Focus URL input and select all text)
        if key == Qt.Key.Key_L and (
            modifiers & Qt.KeyboardModifier.ControlModifier
            or modifiers & Qt.KeyboardModifier.MetaModifier
        ):
            self.url_entry.setFocus()
            self.url_entry.selectAll()
            a0.accept()
            return

        # Handle Cmd/Ctrl + D (Start Download)
        if key == Qt.Key.Key_D and (
            modifiers & Qt.KeyboardModifier.ControlModifier
            or modifiers & Qt.KeyboardModifier.MetaModifier
        ):
            if self.download_button.isEnabled():
                self.start_download()
            a0.accept()
            return

        super().keyPressEvent(a0)

    def get_selected_sb_categories(self):
        if self.sb_all_checkbox.isChecked():
            # Return all category values from the map
            return list(self.sb_checkbox_map.values())

        # Use the cached mapping
        return [
            category
            for checkbox, category in self.sb_checkbox_map.items()
            if checkbox.isChecked()
        ]
