"""Preferences editor dialog (loads/saves preferences.json).

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import json
import os
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
)
from ytdl import preferences as prefs

# The preference keys the dialog's form owns. Their values come from the
# checkboxes and the browser field, never from the JSON editor.
_FORM_OWNED_KEYS = frozenset({
    "cookies_from_browser",
    "cookies_from_browser_enabled",
    "strip_bracketed_soundcues",
    "show_fetchinfo_command",
    "pause_threshold_ms",
})


def _form_supplied(key, cookie_enabled, cookie_browser, strip_soundcues,
                   show_fetchinfo, pause_threshold):
    """Whether the form widget that owns `key` was passed in."""
    widgets = {
        "cookies_from_browser": cookie_browser,
        "cookies_from_browser_enabled": cookie_enabled,
        "strip_bracketed_soundcues": strip_soundcues,
        "show_fetchinfo_command": show_fetchinfo,
        "pause_threshold_ms": pause_threshold,
    }
    return widgets.get(key) is not None


class PreferencesDialogMixin:
    def open_preferences_dialog(self):
        """Open a dialog to edit the preferences.json file."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Preferences")
        dialog.resize(500, 400)

        layout = QVBoxLayout(dialog)

        # Browser cookies: an on/off switch and the browser to take them from.
        # The form owns the two keys ("cookies_from_browser_enabled" and
        # "cookies_from_browser"); the JSON editor holds only
        # channel_name_map, so there is no longer a second place these
        # values live and no sync between the two.
        # No group box / frame here: the cookies section is just the
        # checkbox and the browser field, placed directly in the dialog.
        # The help text for each key is the tooltip of the widget that owns
        # it, so the dialog needs no separate explanations section. The
        # edited file's path is in the JSON editor's tooltip, so there is
        # no path label at the top either.
        cookie_enabled = QCheckBox("cookies_from_browser")
        cookie_enabled.setObjectName("cookies_from_browser")
        cookie_enabled.setToolTip(prefs.TOOLTIP_COOKIES_FROM_BROWSER_ENABLED)

        # The remaining switches: they are booleans in the preferences file
        # and are edited as checkboxes, so the JSON editor does not have to
        # carry them.
        strip_soundcues = QCheckBox("strip_bracketed_soundcues")
        strip_soundcues.setObjectName("strip_bracketed_soundcues")
        strip_soundcues.setToolTip(prefs.TOOLTIP_STRIP_BRACKETED_SOUNDCUES)

        show_fetchinfo = QCheckBox("show_fetchinfo_command")
        show_fetchinfo.setObjectName("show_fetchinfo_command")
        show_fetchinfo.setToolTip(prefs.TOOLTIP_SHOW_FETCHINFO_COMMAND)

        cookie_browser = QLineEdit()
        cookie_browser.setPlaceholderText("firefox, chrome, …")
        cookie_browser.setToolTip(prefs.TOOLTIP_COOKIES_FROM_BROWSER)
        cookie_browser.setFixedWidth(240)
        # Flat field: no padding, no rounded corners, and the pane's regular
        # font size (taken from the checkbox) instead of the stylesheet's
        # larger QLineEdit size.
        base_size = cookie_enabled.font().pointSize()
        font_rule = (
            f"font-size: {base_size}pt;" if base_size > 0 else ""
        )
        cookie_browser.setStyleSheet(f"""
            QLineEdit {{
                padding: 0;
                border: 1px solid #525252;
                border-radius: 0;
                background-color: #383838;
                color: #fff;
                {font_rule}
            }}
            QLineEdit:focus {{
                border: 1px solid #777;
            }}
            QLineEdit:disabled {{
                color: #999;
            }}
        """)

        # The field sits right next to the checkbox, on the same line.
        cookie_row = QHBoxLayout()
        cookie_row.setContentsMargins(0, 0, 0, 0)
        cookie_row.setSpacing(6)
        cookie_row.addWidget(cookie_enabled)
        cookie_row.addWidget(cookie_browser)
        cookie_row.addStretch(1)
        layout.addLayout(cookie_row)

        # The two standalone switches, each on its own line, directly in the
        # dialog (no group frame, like the cookies row).
        layout.addWidget(strip_soundcues)
        layout.addWidget(show_fetchinfo)

        # Pause threshold: the gap (ms) between spoken phrases that breaks a
        # subtitle cue into two. A line edit so the user can type any number.
        pause_row = QHBoxLayout()
        pause_row.setContentsMargins(0, 0, 0, 0)
        pause_row.setSpacing(6)
        pause_label = QLabel("pause_threshold_ms")
        pause_label.setToolTip(prefs.TOOLTIP_PAUSE_THRESHOLD_MS)
        pause_row.addWidget(pause_label)
        pause_threshold = QLineEdit()
        pause_threshold.setPlaceholderText("2000")
        pause_threshold.setObjectName("pause_threshold_ms")
        pause_threshold.setToolTip(prefs.TOOLTIP_PAUSE_THRESHOLD_MS)
        pause_threshold.setFixedWidth(100)
        pause_threshold.setStyleSheet("""
            QLineEdit {
                padding: 0;
                border: 1px solid #525252;
                border-radius: 0;
                background-color: #383838;
                color: #fff;
                """ + font_rule + """
            }
            QLineEdit:focus {
                border: 1px solid #777;
            }
            QLineEdit:disabled {
                color: #999;
            }
        """)
        pause_row.addWidget(pause_threshold)
        pause_row.addStretch(1)
        layout.addLayout(pause_row)

        # The JSON editor carries the channel name map only. The boolean
        # switches live in the checkboxes above, so the file the user edits
        # by hand stays small and cannot break a switch by accident.
        text_edit = QPlainTextEdit()
        text_edit.setToolTip(prefs.TOOLTIP_CHANNEL_NAME_MAP_EDITOR)
        text_edit.setStyleSheet("""
            QPlainTextEdit {
                font-family: "Menlo", "Courier New";
                font-size: 12px;
            }
        """)

        # Load current preferences
        try:
            os.makedirs(prefs.PREFERENCES_DIR, exist_ok=True)
            if os.path.exists(prefs.PREFERENCES_FILE):
                with open(prefs.PREFERENCES_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
            else:
                content = ""
        except (OSError, UnicodeError):
            # UnicodeError: the file is not valid UTF-8 (a corrupted or
            # hand-edited file). Treat it as empty rather than refusing
            # to open the dialog.
            content = ""

        current = {}
        if content:
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    current = parsed
            except (json.JSONDecodeError, TypeError):
                current = {}

        channel_map = current.get("channel_name_map", {})
        if not isinstance(channel_map, dict):
            channel_map = {}
        text_edit.setPlainText(
            json.dumps({"channel_name_map": channel_map}, indent=2, ensure_ascii=False)
        )
        layout.addWidget(text_edit)

        # The form shows the current state of the switches.
        cookie_enabled.setChecked(prefs.cookies_from_browser_enabled(current))
        browser = current.get("cookies_from_browser", "")
        cookie_browser.setText(browser if isinstance(browser, str) else "")
        strip_soundcues.setChecked(prefs.strip_bracketed_soundcues(current))
        show_fetchinfo.setChecked(prefs.show_fetchinfo_command(current))
        pause_threshold.setText(str(prefs.pause_threshold_ms(current)))

        cookie_browser.setEnabled(cookie_enabled.isChecked())
        cookie_enabled.toggled.connect(cookie_browser.setEnabled)

        # Buttons
        button_layout = QHBoxLayout()

        save_button = QPushButton("Save")
        save_button.clicked.connect(
            lambda: self._save_preferences(
                dialog, text_edit, cookie_enabled, cookie_browser,
                strip_soundcues, show_fetchinfo, pause_threshold,
            )
        )
        button_layout.addWidget(save_button, 1)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_button, 1)

        layout.addLayout(button_layout)

        dialog.exec()

    def _read_preferences_file(self):
        """The preferences currently on disk, as a dict (empty on any problem).

        Used to carry over the keys the dialog's form does not own, so
        saving never drops an entry the user added by hand.
        """
        try:
            if not os.path.exists(prefs.PREFERENCES_FILE):
                return {}
            with open(prefs.PREFERENCES_FILE, "r", encoding="utf-8") as f:
                parsed = json.load(f)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            # UnicodeError: the file is not valid UTF-8 (a corrupted or
            # hand-edited file). "Empty on any problem" applies here too,
            # so a broken file cannot stop a save.
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _save_preferences(self, dialog, text_edit, cookie_enabled=None,
                          cookie_browser=None, strip_soundcues=None,
                          show_fetchinfo=None, pause_threshold=None):
        """Validate and save the edited preferences JSON.

        The form is authoritative for the switches it owns (the browser
        cookie on/off switch, the browser name, and the two booleans); the
        JSON editor is authoritative for `channel_name_map`. Any other key
        already in the file is carried over untouched, so a hand-edited
        file never loses an entry the dialog does not know about.
        """
        content = text_edit.toPlainText().strip()
        try:
            parsed = json.loads(content) if content else {}
            # Validate structure
            if not isinstance(parsed, dict):
                raise ValueError("Root must be a JSON object")

            # The form is authoritative for the keys it owns.
            if cookie_enabled is not None:
                parsed["cookies_from_browser_enabled"] = bool(cookie_enabled.isChecked())
            if cookie_browser is not None:
                parsed["cookies_from_browser"] = cookie_browser.text().strip()
            if strip_soundcues is not None:
                parsed["strip_bracketed_soundcues"] = bool(strip_soundcues.isChecked())
            if show_fetchinfo is not None:
                parsed["show_fetchinfo_command"] = bool(show_fetchinfo.isChecked())
            if pause_threshold is not None:
                text = pause_threshold.text().strip()
                if text:
                    try:
                        parsed["pause_threshold_ms"] = int(text)
                    except ValueError:
                        raise ValueError("pause_threshold_ms must be a number (milliseconds)")

            # The channel name map comes from the JSON editor. It is set
            # before the carry-over below, so an editor that omits it means
            # "empty map" rather than "keep whatever is on disk".
            if "channel_name_map" not in parsed:
                parsed["channel_name_map"] = {}
            if not isinstance(parsed["channel_name_map"], dict):
                raise ValueError("channel_name_map must be a JSON object")

            # Keys the dialog does not own are carried over from the file on
            # disk, so an entry the user added by hand is never dropped.
            # A form-owned key whose widget was not supplied keeps its value
            # from the file rather than losing it.
            for key, value in self._read_preferences_file().items():
                if key in parsed:
                    continue
                if key in _FORM_OWNED_KEYS:
                    if _form_supplied(key, cookie_enabled, cookie_browser,
                                      strip_soundcues, show_fetchinfo,
                                      pause_threshold):
                        continue
                parsed[key] = value

            if "strip_bracketed_soundcues" in parsed:
                if not isinstance(parsed["strip_bracketed_soundcues"], bool):
                    raise ValueError(
                        "strip_bracketed_soundcues must be true or false")

            # Validate cookies_from_browser
            if "cookies_from_browser" in parsed:
                valid, err = prefs.validate_cookies_from_browser(parsed["cookies_from_browser"])
                if not valid and parsed["cookies_from_browser"]:
                    raise ValueError(f"cookies_from_browser: {err}")

            # Validate the cookie on/off switch
            if "cookies_from_browser_enabled" in parsed:
                if not isinstance(parsed["cookies_from_browser_enabled"], bool):
                    raise ValueError(
                        "cookies_from_browser_enabled must be true or false")

            # Validate show_fetchinfo_command
            if "show_fetchinfo_command" in parsed:
                if not isinstance(parsed["show_fetchinfo_command"], bool):
                    raise ValueError("show_fetchinfo_command must be true or false")

            # Validate pause_threshold_ms
            if "pause_threshold_ms" in parsed:
                raw = parsed["pause_threshold_ms"]
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    raise ValueError("pause_threshold_ms must be a number (milliseconds)")
                if raw < 0:
                    raise ValueError("pause_threshold_ms must be zero or greater")

            # Write to file
            os.makedirs(prefs.PREFERENCES_DIR, exist_ok=True)
            with open(prefs.PREFERENCES_FILE, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2, ensure_ascii=False)
                f.write("\n")

            # Reload preferences globally
            prefs.apply_preferences(parsed)

            self.signals.append_output.emit("✅ Preferences saved successfully")
            dialog.accept()
        except json.JSONDecodeError as e:
            self.signals.append_output.emit(f"🚩 Invalid JSON: {e}")
        except (ValueError, OSError) as e:
            self.signals.append_output.emit(f"🚩 Error saving preferences: {e}")
