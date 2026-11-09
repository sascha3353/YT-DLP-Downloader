"""Download orchestration: command building, yt-dlp run, progress parsing, post-processing.

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import logging
import os
import signal
import subprocess
import threading

from ytdl.command import _format_captured_output, _redact_command
from ytdl.config import DEFAULT_OUTPUT_DIR
from ytdl.progress import DownloadProgressManager
from ytdl.sites import DEFAULT_SITE, site_supports_sponsorblock
from ytdl.sponsorblock import (
    EDL_ONLY_MEDIA_TYPE,
    _extract_video_id,
    build_edl_content,
    fetch_sponsorblock_segments,
    select_segments,
    write_edl,
)
from ytdl.utils import sanitize_title

logger = logging.getLogger(__name__)


VIDEO_OUTPUT_EXTENSIONS = (".mp4", ".mkv", ".webm")
AUDIO_OUTPUT_EXTENSIONS = (".mp3", ".m4a", ".wav", ".opus", ".webm")


# How many lines of yt-dlp output are kept for the failure log. yt-dlp can
# emit thousands of lines on a long download, and the failure path dumps the
# whole capture into one log message - that is unbounded memory and an
# unreadable log. A bounded tail keeps the lines that actually matter (the
# error, the last few retries, the final state) and says so out loud.
MAX_CAPTURED_OUTPUT_LINES = 200

# yt-dlp passes every argument on the command line, and the failure log dumps
# the whole command. The arguments are not the app's secrets, but they are the
# user's, so the command is redacted before it is logged.
_REDACTED_ARG = "***"
# yt-dlp accepts a value-taking flag in two shapes: "--flag value" (separate
# arguments, the common form) and "--flag=value" (the equals form, used for
# long headers and extractor args). Both must be handled or a token slips
# through.
#
# Membership here is what marks a flag as value-taking. A flag that is absent
# consumes no argument - --netrc, for instance, only tells yt-dlp to read
# ~/.netrc - so its successor is left alone instead of being masked.
#
# build_command() emits only --cookies-from-browser today; the rest are listed
# because _redact_command() sanitizes whatever argv it is handed and cannot
# know which flags the caller chose to pass. Add an entry when a new
# credentialed flag reaches a command.
_VALUE_FLAGS = {
    "--cookies",
    "--cookies-from-browser",
    "--cookie",
    "--header",
    "--extractor-args",
    "--username",
    "--password",
    "--video-password",
}




class DownloadMixin:
    def _emit(self, name, *args):
        """Emit a UI signal, unless the window is closing.

        self.signals is a QObject owned by the main window, so it is destroyed
        along with it. A download worker that is still winding down while the
        window closes would otherwise emit into a dying object: that raises
        "wrapped C/C++ object has been deleted", and during interpreter
        shutdown it can take the process down without a traceback.

        Qt-free work (writing EDL/NFO, subtitle files, logging) is unaffected —
        only the UI notification is dropped, and nobody can see it anyway once
        the window is gone.

        getattr(self, "_shutting_down", False) rather than a plain attribute
        access: the minimal test harnesses inherit a single mixin (e.g.
        InfoFetchMixin alone) and have no _shutting_down, so a worker routed
        through _emit() would raise AttributeError on a harness that has no
        window to close at all. The real window always sets it in __init__,
        so the guard is fully active there.
        """
        if getattr(self, "_shutting_down", False):
            return
        getattr(self.signals, name).emit(*args)

    def _effective_media_type(self):
        """Compute the effective media type from the type checkboxes.

        Returns one of: "edl_only", "video", "video_only", "audio",
        "subtitles", or None when nothing is selected.

        Falls back to the legacy ``video_state["media_type"]`` string for
        callers (e.g. test harnesses) that still set it directly.
        """
        vs = self.video_state
        if vs.get("wants_edl"):
            return "edl_only"
        if vs.get("wants_video") and vs.get("wants_audio"):
            return "video"
        if vs.get("wants_video"):
            return "video_only"
        if vs.get("wants_audio"):
            return "audio"
        if vs.get("wants_subtitles"):
            return "subtitles"
        # Legacy fallback: some test harnesses set "media_type" directly.
        legacy = vs.get("media_type")
        if legacy in ("edl_only", "video", "video_only", "audio", "subtitles"):
            return legacy
        return None

    def _terminate_process_tree(self, process):
        """SIGTERM the download's whole process group (yt-dlp + ffmpeg).

        yt-dlp spawns ffmpeg as a child for merging/conversion, so terminating
        yt-dlp alone can leave ffmpeg writing into a directory we are about to
        tear down. The Popen is started with start_new_session=True, so it is
        its own group leader and killpg reaches the children too.

        Falls back to a plain terminate() if the group is already gone.
        """
        if process is None:
            return
        try:
            if process.poll() is not None:
                return
        except (AttributeError, OSError):
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except OSError:
            # ProcessLookupError / PermissionError are both OSError subclasses;
            # the group may already be gone. Fall back to a plain terminate().
            try:
                process.terminate()
            except (AttributeError, OSError):
                pass

    def cancel_download(self):
        """Terminate the running download. Safe to call from the main thread.

        Two deliberate no-ops, both returning False without touching the run:
          - No process yet: the intent is recorded so run_download()'s post-Popen
            check terminates immediately (the Cancel/Popen race).
          - Process already exited (post-processing): nothing left to kill, and
            honouring the click would relabel a *finished* download as
            "Cancelled" and zero its progress bars.

        The emits below are intentionally direct, NOT routed through _emit():
        this method is main-thread-only (button click, Esc, closeEvent), so the
        SignalEmitter is always still alive here — including inside closeEvent,
        which sets _shutting_down before calling us. Converting them for
        "consistency" would make the Cancel feedback silently disappear on
        window close. _emit() guards the download *worker* thread, not this one.
        """
        process = self._proc
        if process is None:
            # Pre-Popen race: record the intent so the worker's post-Popen
            # check acts on it. This is the one case that must set the flag
            # even though nothing is killed here.
            self._cancelled = True
            return False
        try:
            already_exited = process.poll() is not None
        except (AttributeError, OSError):
            return False
        if already_exited:
            self.signals.append_output.emit(
                "Finishing up (subtitles/EDL/NFO) — nothing left to cancel."
            )
            return False
        self._cancelled = True
        self._terminate_process_tree(process)
        self.signals.append_output.emit("Cancelling download…")
        # Hide immediately so a second click (e.g. during post-processing)
        # cannot fire again.
        self.signals.set_cancel_button_visible.emit(False)
        return True

    def start_download(self):
        # Reset label when a new download is initiated
        self.download_button.setText("Download")
        self.set_download_button_status("")
        # Get base filename/folder from title
        base_name = self.title_entry.text().strip()
        if not base_name:
            self.signals.append_output.emit("🚩 Error: No video title available")
            return

        # Base directory from settings
        root_output_dir = self.output_dir_entry.text().strip() or DEFAULT_OUTPUT_DIR

        # Create folder structure: Root / Channel / SxxEyyyy - Video Title
        # Files inside: "SxxEyyyy - Video Title"
        channel = self.video_state.get("channel", "")
        sanitized_base_name = sanitize_title(base_name)
        if not sanitized_base_name:
            # A title made only of illegal characters sanitizes to an empty
            # string, which would collapse video_dir to the output root and
            # dump the media straight into it. Use a defined fallback so the
            # directory and base_filename always agree.
            sanitized_base_name = "downloaded_video"

        if channel:
            sanitized_channel = sanitize_title(channel) or "Unknown Channel"
            # Create nested folder structure: {root}/{channel}/{base_name}
            video_dir = os.path.join(root_output_dir, sanitized_channel, sanitized_base_name)
        else:
            video_dir = os.path.join(root_output_dir, sanitized_base_name)

        if not os.path.exists(video_dir):
            try:
                os.makedirs(video_dir, exist_ok=True)
                rel_path = os.path.relpath(video_dir, root_output_dir)
                self.signals.append_output.emit(f"📂 Created directory: {rel_path}")
            except Exception as e:
                self.signals.append_output.emit(f"🚩 Error creating directory: {e}")
                return

        # Update video state — store sanitized filename so get_full_path() and
        # get_filename_template() agree with the directory name we just created.
        self.update_video_state(title=sanitized_base_name, output_dir=video_dir)
        self.video_state["is_download_running"] = True

        # Derive boolean flags from the legacy "media_type" string when the
        # checkboxes haven't been set explicitly (e.g. test harnesses that
        # still use video_state["media_type"]). Must run before the .edl_only
        # branch below, which reads video_state["wants_edl"].
        if not any(
            k in self.video_state
            for k in ("wants_video", "wants_audio", "wants_subtitles", "wants_edl")
        ):
            _mt = self._effective_media_type()
            self.video_state["wants_video"] = _mt in ("video", "video_only")
            self.video_state["wants_audio"] = _mt in ("video", "audio")
            self.video_state["wants_subtitles"] = _mt == "subtitles"
            self.video_state["wants_edl"] = _mt == "edl_only"

        # .edl only: the SponsorBlock skip file, without downloading anything.
        # Branches right after this shared prelude (title gate, directory
        # creation, update_video_state) so the .edl lands exactly where the
        # media file would have — and before the existence check and
        # build_command(), neither of which means anything for a mode that
        # never spawns yt-dlp.
        if self.video_state.get("wants_edl"):
            self.start_edl_only(channel, base_name)
            return

        # Detailed Existence Check
        media_type = self._effective_media_type()
        selected_langs = self.get_selected_subtitle_codes()
        selected_langs = self.get_selected_subtitle_codes()
        # Subtitles are only downloaded when the Type "Subtitles" checkbox is
        # checked. If it's unchecked, language selections are ignored: no
        # --write-subs is passed to yt-dlp, no subtitle existence check, no
        # subtitle post-processing, and no subtitle cleanup.
        if not self.video_state.get("wants_subtitles", False):
            selected_langs = []
        base_path = self.get_full_path()

        # 1. Check Media (Video/Audio)
        media_exists = False
        media_file = ""
        if media_type in ["video", "video_only"]:
            for ext in VIDEO_OUTPUT_EXTENSIONS:
                if os.path.exists(base_path + ext):
                    media_exists = True
                    media_file = os.path.basename(base_path + ext)
                    break
        elif media_type == "audio":
            for ext in AUDIO_OUTPUT_EXTENSIONS:
                if os.path.exists(base_path + ext):
                    media_exists = True
                    media_file = os.path.basename(base_path + ext)
                    break

        # 2. Check Subtitles
        missing_subs = []
        existing_subs = []
        existing_subtitle_paths = {}
        # A video whose only subtitle is stored without a language code
        # ("Title.srt") must still be recognized as downloaded - otherwise
        # re-running it would fetch the media file all over again. That name
        # carries no language, so it is only credited to the one request it can
        # be attributed to (SubtitleMixin._bare_subtitle_credit).
        bare_credit = self._bare_subtitle_credit(selected_langs)
        for code in selected_langs:
            # Manual/auto subtitles may be SRT or VTT when conversion was
            # unavailable; keep the pre-check aligned with subtitle discovery.
            candidates = [
                self.get_full_path(f".{code}.srt"),
                self.get_full_path(f".a.{code}.srt"),
                self.get_full_path(f".{code}.vtt"),
                self.get_full_path(f".a.{code}.vtt"),
            ]
            if code == bare_credit:
                candidates += [self.get_full_path(".srt"), self.get_full_path(".vtt")]
            existing_path = next(
                (path for path in candidates if os.path.isfile(path)), None
            )
            if existing_path:
                existing_subs.append(code)
                existing_subtitle_paths[code] = existing_path
            else:
                missing_subs.append(code)

        # 3. Output Detailed Status
        if media_file:
            self.signals.append_output.emit(f"✓ Media exists: {media_file}")

        for code in existing_subs:
            path = existing_subtitle_paths[code]
            self.signals.append_output.emit(f"✓ Subtitle exists: {os.path.basename(path)}")

        # 4. Decide if we skip
        should_skip = False
        skip_reason = ""

        wants_video = self.video_state.get("wants_video", False)
        wants_audio = self.video_state.get("wants_audio", False)
        wants_subtitles = self.video_state.get("wants_subtitles", False)

        # Validate at least one thing is selected
        if not wants_video and not wants_audio and not wants_subtitles:
            self.signals.append_output.emit(
                "👉 Please select at least one of: Video, Audio, Subtitles"
            )
            self.video_state["is_download_running"] = False
            return

        # 4. Decide if we skip
        should_skip = False
        skip_reason = ""

        if wants_subtitles and not wants_video and not wants_audio:
            # Subtitles only
            if not selected_langs:
                self.signals.append_output.emit(
                    "👉 No subtitle languages selected. Please check at least one language."
                )
                self.video_state["is_download_running"] = False
                return

            if not missing_subs:
                should_skip = True
                skip_reason = "All requested subtitles already exist"
        else:
            # Video or Audio requested
            # Skip only if the media file exists AND all requested subtitles are already there
            if media_exists and not missing_subs:
                should_skip = True
                skip_reason = "All requested files (media and subtitles) already exist"

        if should_skip:
            self.signals.append_output.emit(f"👉 {skip_reason} - Download skipped")
            self.video_state["is_download_running"] = False
            return

        cmd = self.build_command(selected_langs)
        if not cmd:
            self.signals.append_output.emit("👉 Please enter a valid video URL")
            self.video_state["is_download_running"] = False
            self.url_entry.setFocus()
            return

        # Log the download start. The URL is deliberately not logged here:
        # the result line in run_download()'s finally already records it
        # alongside the outcome, so logging it twice put an extra line into
        # app.log for every successful download while saying nothing new.
        logger.info(f"{channel}")
        logger.info(f"{base_name}")

        # Clear cached metadata at start of new download
        self.cached_video_metadata = None

        self.signals.update_dock_tile.emit("")
        self.clearDockProgress()
        self._set_ui_enabled_state(False)
        self.download_button.setEnabled(False)
        self._reset_download_progress_bars()
        self._set_download_busy(True)

        self.signals.append_output.emit(
            f"\n🖥️ {' '.join(str(item) for item in _redact_command(cmd) if item)}"
        )

        # Shown only here, after every early return above (missing title, failed
        # directory creation, "already downloaded" skip, invalid URL): the button
        # must not linger with no download behind it.
        self._cancelled = False
        self.signals.set_cancel_button_visible.emit(True)

        thread = threading.Thread(target=self.run_download, args=(cmd, selected_langs))
        thread.daemon = True
        thread.start()

    # ----------------------------------------------------------------------------------------------------
    # ".edl only" mode (EDL_ONLY_MEDIA_TYPE): the SponsorBlock skip file on
    # its own, without downloading anything and without starting yt-dlp.
    # ----------------------------------------------------------------------------------------------------

    def start_edl_only(self, channel, base_name):
        """Main-thread entry for the ".edl only" media type.

        Mirrors the pre-thread part of the download path (log, dock clear,
        UI disable, spinner) and hands the work to a worker thread, because
        the segment source may make one HTTP request. Unlike a download
        there is no process to kill, so the Cancel button stays hidden —
        the whole run is one bounded request plus one file write.
        """
        logger.info(f"{channel}")
        logger.info(f"{base_name}")

        # Clear cached metadata at start of new work (like start_download)
        self.cached_video_metadata = None

        self.signals.update_dock_tile.emit("")
        self.clearDockProgress()
        self._set_ui_enabled_state(False)
        self.download_button.setEnabled(False)
        self._reset_download_progress_bars()
        self._set_download_busy(True)
        self._cancelled = False
        self.signals.append_output.emit(
            "\n📟 .edl only — no download; writing SponsorBlock skip file..."
        )

        thread = threading.Thread(target=self.run_edl_only)
        thread.daemon = True
        thread.start()

    def run_edl_only(self):
        """Worker for the ".edl only" media type: write <title>.edl, no yt-dlp.

        Same UI contract as run_download()'s finally: everything goes through
        _emit() so a window closing mid-run cannot be touched from this
        thread, and the UI is always re-enabled with a verdict on the button.
        create_edl_only() returns the applied segments, [] when there was
        deliberately nothing to write, or None on a failure.
        """
        segments = None
        try:
            segments = self.create_edl_only()
        except Exception as e:  # pylint: disable=broad-except
            # UI/network boundary convention (see run_download's except
            # branch): any surprise here must still leave the window usable.
            self._emit("append_output", f"🚩 An unexpected error occurred: {e}")
            logger.error(f".edl only mode failed with exception: {e}")

        url = self.get_clean_url() if hasattr(self, "get_clean_url") else "unknown"
        if segments:
            status = "succeeded"
        elif segments is None:
            status = "failed"
        else:
            status = "wrote nothing"
        logger.info(f".edl only {status}: {url}")

        # Clean up state and UI in a single batch to avoid cascading updates.
        self.video_state["is_download_running"] = False
        self._emit("set_indeterminate", False)
        if segments:
            button_text, button_status = "EDL Created ✅", "success"
        elif segments is None:
            button_text, button_status = "EDL Error 🚨", "error"
        else:
            button_text, button_status = "No EDL Written", ""
        self._emit("set_download_button_label", button_text)
        self._emit("set_download_button_status", button_status)
        self._emit("enable_button")
        self._emit("update_dock_tile", "✓" if segments else "")
        self._emit("clear_dock_progress")

    def create_edl_only(self, fetch=fetch_sponsorblock_segments):
        """Write <title>.edl from SponsorBlock data — the ".edl only" body.

        Runs on the worker started by start_edl_only(), i.e. after
        start_download()'s prelude has set base_filename/output_dir and after
        the info fetch has filled video_id/site/url. `fetch` exists for
        tests: video_id -> (segments, error).

        Multiple early returns are deliberate guard clauses: site not
        supported, missing video ID, missing title, no matching segments,
        fetch failure, write failure — each returns a distinct tri-state.

        Segment source:
          1. sb_bar.segments — check_sponsorblock() fetched ALL categories
             for the visual bar during the info fetch, so normally zero HTTP
             here.
          2. A fresh API fetch when the bar is empty (bar cleared, or the
             info fetch's lookup failed).

        Returns the applied segments (like create_edl_file()), [] when there
        is deliberately nothing to write (no matching segments), or None on a
        failure — in every no-file case a line says why on the output panel.
        """
        # pylint: disable=too-many-return-statements
        state = self.video_state

        def emit(text):
            self._emit("append_output", text)

        site = state.get("site", DEFAULT_SITE)
        if not site_supports_sponsorblock(site):
            emit("SponsorBlock: Not available for this site (YouTube only) — skipping")
            return None

        video_id = state.get("video_id") or _extract_video_id(
            state.get("url") or state.get("clean_url") or ""
        )
        if not video_id:
            emit("SponsorBlock: Could not extract video ID from URL")
            return None

        # Bar segments first: they were fetched (all categories) by the info fetch.
        sponsor_bar = getattr(self, "sb_bar", None)
        segments = list(getattr(sponsor_bar, "segments", []) or [])
        if not segments:
            segments, error = fetch(video_id)
            if error:
                emit(f"🚩 SponsorBlock lookup failed: {error}")
                return None

        categories = self.get_selected_sb_categories()
        emit(f"📟 Selected SB categories: {categories}")
        selected = select_segments(segments, categories)
        # Drop zero-length markers (the API's whole-video flag) before the
        # emptiness check: a selection of only those must say "nothing to
        # skip" instead of writing an empty .edl.
        selected = [s for s in selected if s["segment"][0] != s["segment"][1]]
        if not selected:
            emit("📟 No segments to skip found in selected categories")
            return []

        base_path = self.get_full_path()
        if not base_path:
            emit("🚩 Error: No video title available")
            return None
        try:
            # Append, never splitext(): base_path is extensionless by
            # construction, and a title like "Lecture 1.5" must keep its
            # suffix so the EDL sits under the media's exact basename.
            edl_path = write_edl(base_path + ".edl", selected)
        except OSError as exc:
            emit(f"🚩 Error creating EDL file: {exc}")
            return None

        emit(f"🎬 Created EDL file: {os.path.basename(edl_path)}")
        for line in build_edl_content(selected).splitlines():
            emit(f"   {line}")
        return selected

    def run_download(self, cmd, selected_langs):
        success = False
        had_download_progress = False
        progress_manager = None
        completed_progress = None
        removed_sb_segments = []
        # Capture output lines for logging on failure
        captured_output = []
        # Lines are stored exactly as they were printed, because the point of
        # this capture is to show what yt-dlp actually said. Only the command
        # line is redacted, separately, through _redact_command() - that is the
        # one place a credential of ours (a browser cookie path) can appear.
        # Set when the bounded tail drops earlier lines, so the failure log
        # can say so instead of presenting a truncated capture as complete.
        captured_output_truncated = False
        # Track whether the failure-sidecar step already ran, so a Python
        # exception raised by the sidecar attempt itself is not retried
        # (duplicate .nfo/.edl writes). Both the nonzero-exit branch below
        # and the except branch share this flag.
        sidecars_attempted = False
        # True once the media file is confirmed on disk, so the except branch
        # can tell "the download actually landed" apart from "it failed".
        # An exception during post-download work (subtitle resync, EDL/NFO,
        # analysis) must not be reported as a media failure or rewrite the
        # sidecars the success path already wrote.
        media_downloaded = False
        # True once the requested subtitle file(s) are confirmed on disk, so
        # the except branch can tell "the SRT landed" apart from "it failed".
        # media_downloaded is only set inside the non-subtitle path, so a
        # subtitle-only job needs its own flag or a post-download exception
        # after the SRT was written is still reported as a failure.
        subtitles_downloaded = False
        try:
            if self.simulate_download_error:
                raise RuntimeError("Simulated download error (testing flag enabled)")
            # Say what is being requested before anything is fetched: the
            # media type decides whether audio comes along, and that decision
            # was previously invisible.
            format_line = self.describe_format_choice(
                cmd, self._effective_media_type() or "video"
            )
            self._emit("append_output", format_line)
            captured_output.append(format_line)
            # Capture the command for error logging, redacting any argument
            # that could carry a secret (cookies, headers, password, ...).
            cmd_line = "🖥️ " + " ".join(
                str(item) for item in _redact_command(cmd) if item
            )
            captured_output.append(cmd_line)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
                # Own process group, so cancel_download() can SIGTERM yt-dlp
                # and its ffmpeg children together via killpg().
                start_new_session=True,
            )
            self._proc = process
            # Cancel may have arrived between thread start and Popen (a few ms);
            # the button is reachable that early, so honour it here.
            if self._cancelled:
                self._terminate_process_tree(process)

            progress_manager = DownloadProgressManager(
                media_type=self._effective_media_type() or "video"
            )
            state = {
                "last_line_was_progress": False,
                "merged_filename": None,
                "downloading_subtitles": False,
                "progress_manager": progress_manager,
            }

            if process.stdout is None:
                return

            self._emit("set_indeterminate", True)

            # Main Output Loop
            for line in process.stdout:
                # yt-dlp separates progress updates with carriage returns;
                # universal newlines already splits them, but strip any
                # leading "\r" so the line reliably starts with "[download]".
                line = line.lstrip("\r").rstrip()
                # Capture error-relevant lines for the failure log. yt-dlp
                # prints thousands of progress lines on a long download, so
                # only lines that actually say something about the failure are
                # kept - and the list is bounded below so a runaway process
                # cannot grow memory without limit.
                if any(
                    keyword in line.lower()
                    for keyword in (
                        "error", "warning", "http", "403", "404",
                        "sign in", "geo", "unavailable", "private", "members",
                    )
                ):
                    captured_output.append(line)
                    if len(captured_output) > MAX_CAPTURED_OUTPUT_LINES:
                        captured_output = captured_output[
                            -MAX_CAPTURED_OUTPUT_LINES:
                        ]
                        captured_output_truncated = True
                state = self._parse_download_output(line, state)

            process.wait()
            exit_code = process.returncode
            had_download_progress = progress_manager.is_started()
            if had_download_progress and exit_code == 0:
                completed_progress = progress_manager.mark_complete()
                self._emit("update_download_progress", *completed_progress)
                # Push the terminal dock fraction explicitly. During the run the
                # overlay only reaches the video's share of the bar (an audio
                # transfer may still follow), so a single muxed download would
                # otherwise sit at 85% forever - should_redraw_dock() always
                # repaints the 1.0 endpoint, so this lands the final frame.
                self._emit("update_dock_progress",
                    progress_manager.get_combined_fraction()
                )

            if exit_code == 0:
                # Post-processing phase: keep the busy spinner visible while we
                # finalize files (subtitles, EDL/NFO, cleanup, analysis, etc.).
                self._emit("set_indeterminate", True)

                media_type = self._effective_media_type()

                # Resolve final filename before all post-processing.
                # Fallback discovery runs here so EDL/NFO/metadata all see the
                # correct path — not after cleanup has already run.
                if media_type != "subtitles":
                    captured_filename = state["merged_filename"]
                    if captured_filename and not os.path.isfile(captured_filename):
                        logger.debug(
                            "Captured output file is missing: %s", captured_filename
                        )
                        state["merged_filename"] = None

                    if not state["merged_filename"]:
                        if media_type in ("video", "video_only"):
                            extensions = VIDEO_OUTPUT_EXTENSIONS
                        elif media_type == "audio":
                            extensions = AUDIO_OUTPUT_EXTENSIONS
                        else:
                            extensions = ()

                        base_path = self.get_full_path()
                        if base_path:
                            for ext in extensions:
                                candidate = base_path + ext
                                if os.path.isfile(candidate):
                                    state["merged_filename"] = candidate
                                    logger.debug("Fallback found file: %s", candidate)
                                    break

                    # For non-subtitle downloads, a missing output file means
                    # the download effectively failed even though yt-dlp exited 0.
                    if (
                        not state["merged_filename"]
                        or not os.path.isfile(state["merged_filename"])
                    ):
                        self._emit("append_output",
                            "🚩 yt-dlp exited successfully but no output file was found"
                        )
                        exit_code = -1
                    else:
                        # The media file is on disk: a later exception in
                        # post-download work is not a media failure, and the
                        # success-path sidecars must not be rewritten.
                        media_downloaded = True

            if exit_code == 0:
                if media_type == "subtitles":
                    self._emit("append_output", "Subtitle download complete.")
                else:
                    self._emit("append_output", "Video/Audio downloaded.")

                # Update video state with final filename
                if state["merged_filename"]:
                    self.video_state["full_path"] = state["merged_filename"]

                # Cache metadata immediately after download
                if state["merged_filename"]:
                    self.cached_video_metadata = self.get_file_metadata(
                        state["merged_filename"]
                    )

                # Process subtitles (now downloaded together with video)
                downloaded_subs = []
                bare_name = False
                if selected_langs:
                    self._emit("append_output", "\nProcessing subtitles...")
                    found_subs = self._find_downloaded_subtitles(selected_langs)
                    # One subtitle for the whole video carries no information
                    # in its name: store it as "<title>.srt" instead of
                    # "<title>.en.srt". Two or more files keep their code -
                    # that is what tells them apart.
                    bare_name = self._uses_bare_subtitle_name(len(found_subs))
                    downloaded_subs = self._normalize_subtitle_names(
                        found_subs, bare=bare_name
                    )

                    if downloaded_subs:
                        report_langs = [f"{item[0]} ({item[2]})" for item in downloaded_subs]
                        self.video_state["downloaded_subtitles"] = [item[0] for item in downloaded_subs]

                        self._emit("append_output",
                            f"💬 Subtitles identified: {', '.join(report_langs)}"
                        )

                        if bare_name:
                            final_name = os.path.basename(
                                self._subtitle_output_path(downloaded_subs[0][0], bare=True)
                            )
                            self._emit("append_output",
                                f"  → single subtitle, stored as: {final_name}"
                            )

                        # Process subtitles: merge auto-generated ones for 2-line
                        # display where the site needs it (YouTube), but leave
                        # subs untouched that are already well-formatted (real
                        # subs anywhere, auto subs on e.g. Rumble).
                        for lang, srt_path, sub_type in downloaded_subs:
                            if not self._subtitle_needs_resync(sub_type):
                                self._emit("append_output", f"  → {lang} ({sub_type}): keeping original format")
                            else:
                                self._emit("append_output", f"  → {lang} (auto): merging into 2-line format")
                                self._resync_subtitle_for_language(
                                    lang, srt_path, bare=bare_name
                                )
                    else:
                        self._emit("append_output", "👉 No subtitles were downloaded.")

                    # The requested SRT(s) are on disk: a later exception
                    # (cleanup, superseded-bare removal, analysis) is not a
                    # subtitle failure. media_downloaded is only set on the
                    # non-subtitle path, so a subtitle-only job needs its own
                    # flag — without it a post-download exception after the
                    # SRT was written is still reported as "Subtitles only
                    # failed".
                    if downloaded_subs and all(
                        os.path.isfile(path) for _lang, path, _t in downloaded_subs
                    ):
                        subtitles_downloaded = True

                # Create EDL and NFO files BEFORE cleanup deletes the .info.json
                # Skip if we only downloaded subtitles
                if state["merged_filename"] and self._effective_media_type() != "subtitles":
                    removed_sb_segments = self.create_edl_file(state["merged_filename"])
                    self.create_nfo_file(state["merged_filename"])
                    # The success path wrote the sidecars; a later exception
                    # (subtitle resync, analysis, ...) must not rewrite them.
                    sidecars_attempted = True

                # Cleanup (deletes .info.json and original subtitles)
                self.cleanup_files(selected_langs)

                # A language-less subtitle from an earlier single-language run
                # is superseded once this run stored language-tagged files.
                if selected_langs and downloaded_subs and not bare_name:
                    removed_bare = self._remove_superseded_bare_subtitle()
                    if removed_bare:
                        self._emit("append_output",
                            f"  → removed superseded subtitle: {os.path.basename(removed_bare)}"
                        )

                # Post Download Analysis (uses cached metadata)
                # Skip if we only downloaded subtitles (as no new media was created)
                if state["merged_filename"] and self._effective_media_type() != "subtitles":
                    self._emit("append_output", f"🔍 Analyzing: {os.path.basename(state['merged_filename'])}")
                    self._analyze_downloaded_file(state["merged_filename"])
                elif not state["merged_filename"] and self._effective_media_type() != "subtitles":
                    self._emit("append_output", "🚩 Could not find filename for analysis")
                # all done
                self._emit("append_output", "\n✅ Download finished.")
                success = True
            else:
                if self._cancelled:
                    self._emit("append_output", "Download cancelled.")
                else:
                    self._emit("append_output",
                        f"🚩 yt-dlp process failed with exit code {exit_code}"
                    )
                    # Log captured error-relevant output for debugging
                    if captured_output:
                        logger.error(
                            _format_captured_output(
                                captured_output, captured_output_truncated
                            )
                        )
                    # Attempt to create metadata sidecar files (.edl, .nfo)
                    # even when the media download failed: yt-dlp writes
                    # .info.json and the info-fetch already retrieved all
                    # metadata (title, description, SponsorBlock segments).
                    # Sidecars are video-oriented (an .edl jumps a media
                    # timeline, an .nfo describes an episode), so they only
                    # make sense for a job that was supposed to produce
                    # video — a subtitles-only or audio-only failure is not
                    # one, and naming it "media download failed" is wrong.
                    # Shared with the except branch via
                    # _finalize_failure_sidecars(); cancellation is excluded
                    # there and sidecars_attempted prevents a double attempt.
                    # The nonzero-exit branch is a genuine media failure, so
                    # media_downloaded is False here.
                    self._finalize_failure_sidecars(cmd, media_downloaded=False)
                    sidecars_attempted = True

        except Exception as e:
            self._emit("append_output", f"🚩 An unexpected error occurred: {e}")
            logger.error(f"Download failed with exception: {e}")
            if captured_output:
                logger.error(
                    _format_captured_output(
                        captured_output, captured_output_truncated
                    )
                )
            # A Python exception can land here from two very different places:
            # before the media file exists (a real download failure, where
            # yt-dlp still wrote .info.json and sidecars are useful) or after
            # it does (subtitle resync, EDL/NFO, analysis - the download
            # itself succeeded). Only the first case is a media failure, and
            # only it should attempt sidecars: the success path already wrote
            # them, so a post-download exception must not rewrite them or
            # report the media as failed. Cancellation is excluded (nothing to
            # finalize for a user stop), and sidecars_attempted prevents the
            # sidecar attempt itself from being retried.
            if (
                not self._cancelled
                and not sidecars_attempted
            ):
                self._finalize_failure_sidecars(
                    cmd, media_downloaded, subtitles_downloaded
                )
        finally:
            # Drop the process handle so a later cancel cannot reach a stale
            # PID, and capture the verdict before the flag is reset.
            self._proc = None
            cancelled = self._cancelled
            self._cancelled = False

            # Log SponsorBlock segments separately if available. Debug level:
            # the ranges are already on screen (the overlay bar + sb_log) and
            # written into the .edl the player reads, so a line per download in
            # app.log restated them for no one. Kept for when a segment lookup
            # actually needs chasing.
            if success and removed_sb_segments:
                segment_info = []
                for seg in removed_sb_segments:
                    category = seg.get('category', 'unknown')
                    start = seg.get('start', 0)
                    end = seg.get('end', 0)
                    segment_info.append(f"{category} ({start:.1f}s-{end:.1f}s)")
                logger.debug("SponsorBlock marked: %s", ", ".join(segment_info))

            # Log the result. The URL is logged whole, like every other log
            # path: it is the only thing that identifies which job this was,
            # and no supported site's URL carries a credential. Do not "fix"
            # this back to masking the query string - see _redact_command().
            url = self.get_clean_url() if hasattr(self, "get_clean_url") else "unknown"
            logger.info(
                f"Download {'succeeded' if success else 'cancelled' if cancelled else 'failed'}: {url}"
            )

            # Clean up state and UI in a single batch to avoid cascading updates.
            # _emit() drops the UI half of this while the window is closing.
            self.video_state["is_download_running"] = False
            if success:
                if self._effective_media_type() == "subtitles":
                    self._emit("update_download_progress",
                        DownloadProgressManager.PROGRESS_MAX,
                        DownloadProgressManager.PROGRESS_MAX,
                    )
                elif completed_progress is not None:
                    self._emit("update_download_progress", *completed_progress)
            elif cancelled or (
                progress_manager is not None
                and progress_manager.is_started()
            ):
                # A failed or cancelled operation must not leave a 100%
                # transfer bar on screen, even if yt-dlp emitted 100% first.
                self._emit("update_download_progress", 0, 0)
            self._emit("set_indeterminate", False)
            self._emit("set_cancel_button_visible", False)
            if cancelled:
                button_text, button_status = "Download Cancelled", ""
            elif success:
                button_text, button_status = "Download Successful ✅", "success"
            else:
                button_text, button_status = "Download Error 🚨", "error"
            self._emit("set_download_button_label", button_text)
            self._emit("set_download_button_status", button_status)
            self._emit("enable_button")
            self._emit("update_dock_tile",
                "✓" if success else "!" if not cancelled else ""
            )
            self._emit("clear_dock_progress")

    def cleanup_files(self, selected_langs):
        # Clean up JSON
        json_file = self.get_full_path(".info.json")
        if os.path.exists(json_file):
            try:
                os.remove(json_file)
            except OSError:
                logger.warning(f"Could not remove info JSON file: {json_file}")

        # Clean up original/auto-generated files
        for code in selected_langs:
            # We always output to Title.[lang].srt (or Title.srt for a video
            # with a single subtitle), so we only need to clean up the
            # Title.[lang].a.srt that it replaced, if it is still there.
            auto_srt = self.get_full_path(f".a.{code}.srt")
            if os.path.exists(auto_srt):
                try:
                    os.remove(auto_srt)
                except OSError:
                    pass

            # Clean up JSON3 source files after resync (auto and manual)
            for suffix in (f".a.{code}.json3", f".{code}.json3"):
                json3_file = self.get_full_path(suffix)
                if os.path.exists(json3_file):
                    try:
                        os.remove(json3_file)
                    except OSError:
                        pass

            # Also clean up any lingering .resynced or .merged files from previous versions
            for suffix in [".resynced", ".merged"]:
                old_path = self.get_full_path(f".{code}{suffix}.srt")
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass

    def get_output_dir(self):
        # Use cached value when running in a thread or during download to avoid UI access
        if (
            self.video_state.get("is_download_running")
            or threading.current_thread() is not threading.main_thread()
        ):
            return self.video_state.get("output_dir", DEFAULT_OUTPUT_DIR)
        return self.output_dir_entry.text().strip() or DEFAULT_OUTPUT_DIR

    def get_full_path(self, extension="", lang=None):
        base = self.video_state["base_filename"]
        output_dir = self.get_output_dir()

        if not base:
            return ""

        # If a language is provided, inject it before the extension
        if lang:
            base = f"{base}.{lang}"

        if extension:
            if not extension.startswith("."):
                extension = "." + extension
            return os.path.join(output_dir, base + extension)

        return os.path.join(output_dir, base)

    def get_filename_template(self):
        base = self.video_state["base_filename"]
        output_dir = self.get_output_dir()

        if not base:
            return ""

        return f"{output_dir}/{base}.%(ext)s"
