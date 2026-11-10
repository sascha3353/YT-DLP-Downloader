"""Video-info fetching: yt-dlp subprocess, parsing, SponsorBlock.

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import json
import logging
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
import requests
from ytdl import preferences as prefs
from ytdl.config import (
    INFO_FETCH_HINT_AFTER_SECONDS,
    INFO_FETCH_HINT_EVERY_SECONDS,
    SPONSORBLOCK_API_URL,
    SPONSORBLOCK_TIMEOUT_SECONDS,
)
from ytdl.description import clean_youtube_description
from ytdl.download import _redact_command
from ytdl.sponsorblock import SB_ALL_API_CATEGORIES
from ytdl.sites import (
    DEFAULT_SITE,
    SUPPORTED_SITES,
    site_info_timeout,
    site_slow_hint,
    site_supports_sponsorblock,
    site_wants_js_runtime,
)
from ytdl.utils import canonical_subtitle_lang, sanitize_title
from ytdl.widgets import SB_DISPLAY_NAMES

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------
# Episode-rule table
# Each entry maps a YouTube channel name to a regex and a clean-up strategy.
# apply_episode_rules() is a pure function so it can be unit-tested without Qt.
# ----------------------------------------------------------------------------------------------------

# Each rule is a dict:
#   channel   : exact YouTube channel string
#   pattern   : compiled regex; group(1) must capture the episode number digits
#   clean     : "replace_with_dash" | "remove"  — what to do with the matched token in the title
_EPISODE_RULES = [
    {
        "channel": "PowerfulJRE",
        # Require explicit "#" so bare numbers (years, Fight Companion dates) don't match.
        "pattern": re.compile(r"#(\d+)(?: -| |$)"),
        "clean": "replace_with_dash",
    },
    {
        "channel": "Shawn Ryan Show",
        "pattern": re.compile(r"(?:[|]\s*)?SRS\s*#?\s*(\d+)"),
        "clean": "remove",
    },
    {
        "channel": "Lex Fridman",
        "pattern": re.compile(r"(?:[|]\s*)?Lex Fridman Podcast\s*#?\s*(\d+)"),
        "clean": "remove",
    },
    {
        "channel": "PBD Podcast",
        "pattern": re.compile(r"(?:[|]\s*)?PBD(?: Podcast)?\s*#?\s*(\d+)"),
        "clean": "remove",
    },
]


def apply_episode_rules(channel: str, title: str) -> tuple[str, str]:
    """
    Apply channel-specific episode-number extraction rules.

    Returns (episode_code, clean_title) where episode_code is e.g. "S01E2481"
    or "" if no rule matched, and clean_title has the episode token removed.
    Pure function — no GUI state, safe to unit-test.
    """
    for rule in _EPISODE_RULES:
        if channel != rule["channel"]:
            continue
        m = rule["pattern"].search(title)
        if not m:
            break  # channel matched but no episode token — leave title unchanged
        try:
            ep_num = int(m.group(1))
        except (ValueError, IndexError):
            break
        episode_code = f"S01E{ep_num:04d}"
        match_str = m.group(0)
        if rule["clean"] == "replace_with_dash":
            clean = title.replace(match_str, " - ")
        else:
            clean = title.replace(match_str, "")
        # Normalise whitespace, stray dashes and pipes left by the removal
        clean = re.sub(r"\s+", " ", clean)
        clean = clean.replace(" - - ", " - ")
        clean = clean.strip().strip("-").strip("|").strip()
        return episode_code, clean
    return "", title


def build_title_with_prefix(prefix: str, title: str) -> str:
    """
    Join an episode/date prefix and a title, skipping the prefix when absent.

    ``prefix`` is an episode code ("S01E2481") or a date-derived code
    ("S26E0110"); it is empty when the video has no usable upload date and no
    channel rule matched. Without this guard the concatenation produced titles
    like " - Some Video Title", and that malformed string became the on-disk
    folder/file name because sanitize_title() preserves the leading dash.

    Pure function — no GUI state, safe to unit-test.
    """
    clean_title = sanitize_title(title)
    prefix = (prefix or "").strip()
    if not prefix:
        return clean_title
    return f"{prefix} - {clean_title}"


class InfoFetchMixin:
    def _info_wait_hint_text(self, elapsed):
        """Hint shown in the output panel while the info fetch is still running."""
        site = self.video_state.get("site", DEFAULT_SITE)
        hint = site_slow_hint(site) or "the site is responding slowly"
        return f"⏳ Still fetching video info ({int(elapsed)}s) — {hint}…"

    def _info_wait_monitor(self, stop_event):
        """
        Emit progress hints while the yt-dlp info fetch is running, so long
        upstream waits (e.g. Odysee's LBRY resolve, ~40-70s) do not look like
        the app froze. Runs in a daemon thread; stops via `stop_event`.
        """
        if stop_event.wait(INFO_FETCH_HINT_AFTER_SECONDS):
            return
        elapsed = INFO_FETCH_HINT_AFTER_SECONDS
        while True:
            self._emit("append_output", self._info_wait_hint_text(elapsed))
            if stop_event.wait(INFO_FETCH_HINT_EVERY_SECONDS):
                return
            elapsed += INFO_FETCH_HINT_EVERY_SECONDS

    def _run_info_command(self, cmd, timeout):
        """Run the yt-dlp info command, with wait hints + the site's timeout."""
        stop_event = threading.Event()
        monitor = threading.Thread(
            target=self._info_wait_monitor, args=(stop_event,), daemon=True
        )
        monitor.start()
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        finally:
            stop_event.set()

    def fetch_video_info(self):
        # Guard: ignore if a fetch is already in progress (prevents concurrent
        # workers from the timer and a manual Reload/Enter firing together)
        if self.video_state.get("is_fetching_info"):
            return

        url = self.get_clean_url()
        if not url:
            self.title_entry.setText("Please enter a valid video URL")
            return

        # Single gate for both paths: rejects garbage ("nonsense"), domains
        # without a site profile (google.com) and channel/playlist URLs
        # before any yt-dlp subprocess is spawned.
        reason = self.url_rejection_reason(url)
        if reason:
            self.title_entry.setText(reason)
            return

        # Populate video_state["video_id"]: check_sponsorblock() (worker
        # thread) reads it and extract_video_id() had no other call site,
        # so every info fetch used to end in "Could not extract video ID
        # from URL". Clear first — get_clean_url() refreshes "url" but not
        # "video_id", so the cache would otherwise return the previous
        # video's ID when the raw and cleaned URL are identical.
        self.video_state["video_id"] = ""
        self.extract_video_id(url)

        # Bump the SponsorBlock lookup generation up front, before the
        # metadata worker starts. A response for the previous video may
        # already be queued in Qt's event loop — if the token is bumped only
        # near the end of get_video_info(), that queued delivery can land
        # after the URL changes but before the new fetch reaches the bump,
        # and its token still matches, so the old segments briefly repaint a
        # bar the URL change already cleared. Bumping here, before any new
        # signal is emitted, closes that window.
        self._sb_generation = getattr(self, "_sb_generation", 0) + 1

        self.title_entry.setText("Fetching video info...")
        # Reset any prior error styling as soon as we start fetching info again
        self.set_download_button_status("")
        # Temporarily update the Download button label while we fetch metadata
        self.download_button.setText("Getting Info...")
        # Set state variable
        self.video_state["is_fetching_info"] = True
        self._set_ui_enabled_state(False)
        self.download_button.setEnabled(False)
        self._reset_download_progress_bars()
        self._set_download_busy(True)
        self._emit("update_dock_tile", "")
        self.clearDockProgress()
        self.clear_output()
        thread = threading.Thread(target=self.get_video_info, args=(url,))
        thread.daemon = True
        thread.start()

    def get_video_info(self, url):
        error_status = {"error": False}
        # Reset subtitle availability up front, before any yt-dlp call. It is
        # written only on the success path further down, so without this a
        # failed fetch (nonzero exit, timeout, no audio, or a parse error)
        # would leave the PREVIOUS video's languages in place — and
        # _update_subtitle_checkboxes() never runs to correct the labels, so
        # its checkboxes would stay enabled and get_selected_subtitle_codes()
        # would ask yt-dlp for subtitles this video does not have.
        self.video_state["available_subtitles"] = {}
        # The description has the same problem: it is written only on the
        # success path (via update_video_state), but _emit_description_summary()
        # reads it on EVERY path — including the failure paths below. A failed
        # fetch therefore re-printed the PREVIOUS video's description, and the
        # stale text also leaked into the thumbnail dialog and the NFO sidecar.
        # Reset it here, next to the subtitle reset, so a failed fetch shows no
        # description instead of the last successful one.
        self.video_state["description"] = ""
        # `language` is only assigned after a successful parse, but the
        # checkbox refresh at the end of this method runs on every path —
        # default it here so the failure paths do not raise NameError.
        language = ""
        # Per-site budget: slow extractors (Odysee/LBRY resolves for ~40s)
        # would otherwise be killed by the default 15s timeout.
        info_timeout = site_info_timeout(self.video_state.get("site", DEFAULT_SITE))
        try:
            cmd = [self.yt_dlp_bin]

            # Use a JS runtime for extraction if available. Only extractors
            # that evaluate JavaScript need one (YouTube); per-site profiles
            # opt out (e.g. Rumble).
            if (
                self.deno_bin
                and shutil.which(self.deno_bin)
                and site_wants_js_runtime(self.video_state.get("site", DEFAULT_SITE))
            ):
                cmd.extend(["--js-runtimes", f"deno:{self.deno_bin}"])

            # Cookies from browser (for authenticated requests: 403, sign-in, age-restricted)
            cookie_browser = prefs.cookie_browser()
            if cookie_browser:
                cmd.extend(["--cookies-from-browser", cookie_browser])

            cmd.extend(
                [
                    "--print-json",
                    "--no-warnings",
                    "--no-playlist",
                    "--skip-download",
                ]
            )
            # Some extractors (RTVE) only expose subtitle metadata in
            # --print-json output when --write-subs is also passed. Add it
            # for those sites so the info fetch can discover available
            # languages. Writing .vtt files is a harmless side effect of
            # --skip-download; they are overwritten by the actual download.
            if SUPPORTED_SITES.get(
                self.video_state.get("site", DEFAULT_SITE), {}
            ).get("needs_write_subs_for_info", False):
                cmd.append("--write-subs")
            cmd.append(url)
            # Log the info-fetch command if enabled (disabled by default)
            if prefs.show_fetchinfo_command():
                # Same redaction as the download command log: a value-taking
                # flag (cookies, header, ...) is masked, the URL is not.
                self._emit(
                    "append_output",
                    f"\n🖥️ {' '.join(str(item) for item in _redact_command(cmd) if item)}",
                )
            result = self._run_info_command(cmd, info_timeout)
            if result.returncode == 0 and result.stdout.strip():
                json_data = json.loads(result.stdout.strip())

                # yt-dlp JSON fields can sometimes be present but null/None.
                # Guard against "NoneType has no attribute 'strip'" by normalizing values.
                def _s(key: str, default: str = "") -> str:
                    val = json_data.get(key, default)
                    return str(val).strip() if val is not None else ""

                title = _s("title")
                upload_date = _s("upload_date")
                if not upload_date and json_data.get("epoch"):
                    # Some extractors (RTVE) don't expose upload_date but do
                    # expose epoch; derive the date from it so the same
                    # S%yE%m%d prefix code path below kicks in.
                    dt_epoch = datetime.fromtimestamp(
                        json_data["epoch"], tz=timezone.utc
                    )
                    upload_date = dt_epoch.strftime("%Y%m%d")
                duration = _s("duration_string")
                duration_sec = json_data.get("duration") or 0
                youtube_channel = _s("channel")
                if not youtube_channel:
                    # Some extractors (RTVE) expose a series name instead of
                    # a channel; fall back to it so downloads land in a
                    # meaningful per-program folder rather than "Unknown Channel".
                    youtube_channel = _s("series")
                channel = prefs.CHANNEL_NAME_MAP.get(youtube_channel, youtube_channel)
                thumbnail_url = _s("thumbnail")
                language = _s("language")
                height = str(json_data.get("height", ""))
                raw_description = _s("description")
                description = clean_youtube_description(raw_description)
                ext = _s("ext")
                vcodec = _s("vcodec")
                fps = str(json_data.get("fps", ""))

                subs_dict = json_data.get("subtitles") or {}
                autos_dict = json_data.get("automatic_captions") or {}
                all_formats = json_data.get("formats") or []

                # Update consolidated state
                self.update_video_state(
                    original_title=title,
                    channel=channel,
                    description=description,
                    thumbnail_url=thumbnail_url,
                    upload_date=upload_date,
                    language=language,
                    detected_ext=ext,
                    detected_vcodec=vcodec,
                    duration_sec=duration_sec,
                )

                self._emit("append_output", f"Channel: {youtube_channel}")
                if channel != youtube_channel:
                    self._emit("append_output", f"Podcast: {channel}")

                # Format and display info
                # short_date is the title prefix: only keep it when the date
                # actually parsed, otherwise build_title_with_prefix() would
                # emit a malformed " - Title" (or a raw "not-a-date - Title").
                formatted_date = upload_date
                short_date = ""
                if upload_date:
                    try:
                        dt = datetime.strptime(upload_date, "%Y%m%d")
                        formatted_date = dt.strftime("%Y-%m-%d")  # For display: "2026-01-10"
                        short_date = dt.strftime("S%yE%m%d")      # For title: "S26E0110"
                    except ValueError:
                        # Unparseable date: report it, but never use it as a prefix.
                        logger.debug(
                            f"Unparseable upload_date {upload_date!r}; "
                            "omitting the title prefix"
                        )

                    self._emit("append_output", f"Upload date: {formatted_date}")

                self._emit("append_output", f"Duration: {duration}")

                # Resolutions
                # Exclude audio-only formats: Rumble's "audio-192p" carries a
                # bogus height (192) and would pollute the list. yt-dlp sets
                # video_ext="none" on audio-only formats generically.
                unique_heights = sorted(list(set(
                    f.get("height") for f in all_formats
                    if f.get("height") and isinstance(f.get("height"), int)
                    and (f.get("vcodec") not in (None, "none") or f.get("video_ext", "none") != "none")
                )), reverse=True)

                if unique_heights:
                    res_str = " | ".join([f"{h}p" for h in unique_heights])
                    self._emit("append_output", f"Resolutions: {res_str}")
                elif height and height.isdigit():
                    self._emit("append_output", f"Max Resolution: {height}p")

                if fps and fps not in ("NA", "None", "", "None.0"):
                    self._emit("append_output", f"FPS: {fps}")

                # Show bitrates for the selected resolution only
                if all_formats and height and height.isdigit():
                    try:
                        selected_height = int(height)
                        # Keep only video streams at the selected height with a real bitrate
                        matching_fmts = [
                            f for f in all_formats
                            if f.get("vbr")
                            and f.get("vcodec") not in (None, "none")
                            and f.get("height") == selected_height
                        ]
                        # Sort by vbr descending (premium stream will appear first)
                        matching_fmts.sort(key=lambda f: f["vbr"], reverse=True)
                        if matching_fmts:
                            bitrate_strs = [f"{round(f['vbr'])} kbps" for f in matching_fmts]
                            label_key = "Video Bitrate" if len(bitrate_strs) == 1 else "Video Bitrates"
                            self._emit("append_output", f"{label_key}: {' | '.join(bitrate_strs)}")
                    except Exception:
                        pass

                # Codecs availability
                available_codecs = set()
                for f in all_formats:
                    vc = (f.get("vcodec") or "").lower()
                    if vc and vc != "none":
                        if vc.startswith("avc1"):
                            available_codecs.add("H264")
                        elif vc.startswith("vp9") or vc.startswith("vp09"):
                            available_codecs.add("VP9")
                        elif vc.startswith("av01"):
                            available_codecs.add("AV1")

                if available_codecs:
                    codec_order = {"H264": 1, "VP9": 2, "AV1": 3}
                    sorted_codecs = sorted(list(available_codecs), key=lambda x: codec_order.get(x, 99))
                    self._emit("append_output", f"Video Codecs: {' | '.join(sorted_codecs)}")

                # Audio Codecs availability.
                # Generic across sites: Rumble reports plain "aac" (not
                # "mp4a.*") and its muxed HLS formats report no codecs at
                # all, while YouTube DASH video-only streams explicitly say
                # acodec="none". Only an explicit "none" everywhere means a
                # video truly has no audio.
                available_audio = set()
                unknown_codec = False
                for f in all_formats:
                    raw_acodec = f.get("acodec")
                    ac = (raw_acodec or "").lower()
                    if not ac or ac == "none":
                        if raw_acodec is None:
                            # Codec unknown (e.g. muxed HLS) - may carry audio
                            unknown_codec = True
                        continue
                    if ac.startswith("mp4a") or ac.startswith("aac"):
                        available_audio.add("AAC")
                    elif ac.startswith("opus"):
                        available_audio.add("Opus")
                    elif ac.startswith("vorbis"):
                        available_audio.add("Vorbis")
                    elif ac.startswith("mp3"):
                        available_audio.add("MP3")
                    else:
                        # Unknown codec family - show it as-is (e.g. FLAC, EC-3)
                        available_audio.add(ac.split(".")[0].upper())

                # A format with an unknown codec may still carry audio
                audio_capable = bool(available_audio) or unknown_codec

                if available_audio:
                    audio_order = {"AAC": 1, "Opus": 2, "MP3": 3, "Vorbis": 4}
                    sorted_audio = sorted(list(available_audio), key=lambda x: audio_order.get(x, 99))
                    self._emit("append_output", f"Audio Codecs: {' | '.join(sorted_audio)}")

                if audio_capable:

                    # Subtitles availability analysis
                    available_subs = {}
                    try:
                        # subs_dict and autos_dict are already extracted from json_data above

                        # Filter automatic captions to only include ORIGINAL ones (not auto-translations)
                        original_autos = {}
                        for lang_code, formats in autos_dict.items():
                            if not formats:
                                continue

                            # Original auto-captions don't have "tlang=" in their URL.
                            # We check the URL of the first format.
                            fmt_url = formats[0].get("url", "")
                            # Also check for lang matches if possible
                            if "tlang=" not in fmt_url:
                                # Standardize the key - sometimes YouTube provides 'en-orig' or 'en'
                                base = canonical_subtitle_lang(lang_code)
                                original_autos[base] = formats

                        # We check for our 3 target languages for the UI checkboxes.
                        # Some sites (Rumble) report generated subtitles as
                        # "<code>-auto" inside `subtitles` instead of YouTube's
                        # `automatic_captions` - classify those as "(auto)".
                        target_langs = [("English", "en"), ("German", "de"), ("Spanish", "es")]
                        for _, code in target_langs:
                            # canonical_subtitle_lang maps ISO 639-2 site keys
                            # ("deu" on ARD/ZDF) onto the UI codes ("de").
                            keys = [k for k in subs_dict.keys() if canonical_subtitle_lang(k) == code]
                            manual = [k for k in keys if "auto" not in k.lower()]
                            if manual:
                                available_subs[code] = "real"
                            elif keys or code in original_autos:
                                available_subs[code] = "auto"

                        # Build the full report for the output log as requested
                        # Use sorted union of manual keys and original auto keys
                        all_langs = sorted({canonical_subtitle_lang(k) for k in list(subs_dict.keys()) + list(original_autos.keys())})
                        report_tokens = []

                        for lang_code in all_langs:
                            lang_keys = [k for k in subs_dict.keys() if canonical_subtitle_lang(k) == lang_code]
                            # Check manual (site keys that are not generated "<...>-auto")
                            if any("auto" not in k.lower() for k in lang_keys):
                                report_tokens.append(f"{lang_code} (real)")
                            # Check auto (site-generated keys or YouTube original auto-captions)
                            if any("auto" in k.lower() for k in lang_keys) or any(
                                canonical_subtitle_lang(k) == lang_code for k in original_autos
                            ):
                                report_tokens.append(f"{lang_code} (auto)")

                        if report_tokens:
                            self._emit("append_output", f"Subtitles: {', '.join(report_tokens)}")
                        else:
                            site_key = self.video_state.get("site", DEFAULT_SITE)
                            profile = SUPPORTED_SITES.get(site_key, {})
                            if profile.get("supports_subtitles", True):
                                self._emit("append_output", "Subtitles: None available")
                            else:
                                # e.g. Odysee: the extractor exposes no
                                # subtitle tracks at all - say so instead of
                                # the generic "(none)" wording.
                                label = profile.get("label", site_key)
                                self._emit("append_output", f"Subtitles: Not available on {label}")

                        # Cache availability BEFORE the emit below, so the
                        # main-thread slot always reads the fresh data
                        self.video_state["available_subtitles"] = available_subs

                    except Exception as e:
                        # json.JSONDecodeError is not listed on purpose: the
                        # subs_dict/autos_dict inputs were already decoded from
                        # json_data above, so no JSON parsing happens here.
                        self._emit("append_output", "⚠️ Could not read subtitle information for this video")

                    # Set title (without channel name - channel is added to folder name only)
                    if title:
                        episode_code, title = apply_episode_rules(youtube_channel, title)
                        if episode_code:
                            short_date = episode_code
                            self.video_state["episode_code"] = episode_code

                        full_title = build_title_with_prefix(short_date, title)
                        self._emit("update_title", full_title)
                    else:
                        self._emit("append_output", "🚩 Could not find title")
                        error_status["error"] = True
                else:
                    # Every format explicitly says acodec="none" (no unknown-
                    # codec/muxed formats either), so the video has no audio.
                    # Kept as a hard error so the Download button stays off.
                    self._emit("append_output", "🚩 yt-dlp returned no audio formats for this video")
                    error_status["error"] = True

            else:
                # yt-dlp failed (HTTP 403, sign-in wall, geo-block, ...).
                # Surface its captured output in the output/debug panel
                # instead of failing silently.
                self._dump_ytdlp_error_output(
                    f"🚩 yt-dlp exited with code {result.returncode} while fetching video info",
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
                error_status["error"] = True

        except subprocess.TimeoutExpired as timeout_error:
            self._emit("append_output", f"👉 Timeout fetching video info (yt-dlp killed after {info_timeout}s)")
            # subprocess.run() attaches whatever it captured before the kill.
            partial_stdout = getattr(timeout_error, "stdout", None)
            if not partial_stdout:
                partial_stdout = getattr(timeout_error, "output", None)
            self._dump_ytdlp_error_output(
                "yt-dlp output captured before the timeout:",
                stdout=partial_stdout,
                stderr=getattr(timeout_error, "stderr", None),
            )
            error_status["error"] = True
        except Exception as e:
            self._emit("append_output", f"🚩 Error fetching video info: {e}")
            error_status["error"] = True

        # Refresh the subtitle checkboxes on every path. The availability dict
        # was reset at the top of this method and only re-populated when the
        # parse succeeded, so this always leaves the UI matching what we
        # actually learned — including a failed fetch, which leaves it empty
        # and disables the boxes instead of showing the previous video's.
        self._emit("update_subtitle_checkboxes", language or "")

        # Release the UI first. Everything above is local metadata state; the
        # SponsorBlock lookup below is optional enrichment over the network and
        # must never gate readiness (it used to run inline, delaying this
        # signal by up to ~49s on a failing API). The description summary moves
        # here too, so it no longer rides on the SponsorBlock code path.
        # Capture the snapshot BEFORE releasing the UI. The completion signal
        # re-enables the Download/Info buttons, so a user can paste a new URL
        # and start a second fetch while the first SponsorBlock request is
        # still in flight. The lookup thread must therefore not read
        # video_state later — it gets the id/site/duration it was started for,
        # and discards its result when the current video no longer matches.
        snapshot_id = self.video_state.get("video_id", "")
        snapshot_site = self.video_state.get("site", DEFAULT_SITE)
        snapshot_duration = self.video_state.get("duration_sec", 0)
        # The generation was already bumped at the start of this fetch, before
        # any signal was emitted, so a response queued from a previous video
        # cannot match it. Read the current value here rather than re-bumping:
        # the token is per fetch, not per emission.
        sb_generation = getattr(self, "_sb_generation", 0)

        self._emit_description_summary()
        self._emit("title_fetch_complete", error_status)

        if not error_status["error"]:
            # Pass an explicit snapshot: check_sponsorblock() hands the HTTP
            # request to its own thread, and that thread must not read
            # video_state later, when a new URL may already have replaced it.
            self.check_sponsorblock(
                video_id=snapshot_id,
                site=snapshot_site,
                duration_sec=snapshot_duration,
                sb_generation=sb_generation,
            )

    def _dump_ytdlp_error_output(self, headline, stdout=None, stderr=None, max_lines=40):
        """
        Print yt-dlp's captured process output to the output/debug panel.

        get_video_info() runs yt-dlp via subprocess.run(capture_output=True),
        which used to swallow everything yt-dlp printed on failure. Without
        this dump, errors like "HTTP Error 403: Forbidden" or "Sign in to
        confirm you're not a bot" never reached the UI and the app appeared
        to just stop. Only the last `max_lines` lines are kept (yt-dlp prints
        the actual ERROR at the end of its output).
        """
        self._emit("append_output", headline)
        showed_something = False
        for stream_name, data in (("stdout", stdout), ("stderr", stderr)):
            if data is None:
                continue
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            lines = [line.strip() for line in str(data).splitlines()]
            lines = [line for line in lines if line]
            if not lines:
                continue
            self._emit("append_output", f"--- yt-dlp {stream_name} ---")
            if len(lines) > max_lines:
                self._emit("append_output", f"[yt-dlp] ({len(lines) - max_lines} earlier lines omitted)")
            for line in lines[-max_lines:]:
                if len(line) > 1000:
                    line = line[:1000] + " …"
                self._emit("append_output", f"[yt-dlp] {line}")
            showed_something = True
        if not showed_something:
            self._emit("append_output", "[yt-dlp] (no output was captured)")

    def _get_description_summary(
        self, description: str, max_chars: int = 220, min_words: int = 10
    ) -> str:
        """
        Return a human-friendly short summary of the description.

        Previous behavior used `split(".", 1)` which breaks on abbreviations like "Dr.".
        This tries to find the first real sentence boundary, but requires at least
        `min_words` words before accepting a sentence end. Also ignores a short list of
        common abbreviations. Falls back to a soft character/word preview.
        """
        if not description:
            return ""

        text = " ".join(description.strip().split())  # collapse whitespace/newlines
        if not text:
            return ""

        # Common abbreviations that frequently appear at the start of a sentence.
        # This is intentionally small and can be extended if we see more false splits.
        abbreviations = {
            "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st",
            "vs", "etc", "e.g", "i.e",
        }

        # Scan for the first likely end-of-sentence punctuation, but only accept
        # it once we've accumulated at least `min_words` words.
        boundary_idx = None
        for i, ch in enumerate(text):
            if ch not in ".!?":
                continue

            # Require a following space (or end-of-string) to look like a sentence boundary.
            next_char = text[i + 1] if i + 1 < len(text) else ""
            if next_char not in ("", " "):
                continue

            # Get the token immediately before the punctuation (e.g. "Dr" from "Dr.")
            before = text[:i].rstrip()
            last_token = before.split(" ")[-1] if before else ""
            token_key = last_token.lower().strip("()[]{}\"'“”‘’.,:;")
            if token_key in abbreviations:
                continue

            # Minimum-words rule: don't allow extremely short "sentences" like "Dr."
            if len(before.split()) < min_words:
                continue

            boundary_idx = i + 1
            break

        if boundary_idx:
            summary = text[:boundary_idx].strip()
        else:
            # Fallback: take a preview that contains at least `min_words` words, while
            # still preferring a soft character limit.
            words = text.split()
            if len(words) <= min_words:
                summary = text
            else:
                preview = ""
                for w in words:
                    candidate = (preview + " " + w).strip()
                    # Always allow growth until we reach min_words
                    if len(candidate.split()) <= min_words:
                        preview = candidate
                        continue
                    # After min_words, keep growing only if we stay within max_chars
                    if len(candidate) <= max_chars:
                        preview = candidate
                    else:
                        break
                summary = preview.strip()

        return summary

    def _emit_description_summary(self):
        description = (self.video_state.get("description") or "").strip()
        if not description:
            return

        summary = self._get_description_summary(description)
        if summary:
            # Keep an empty line above the description and force a "..." ending.
            self._emit("append_output", f"\nDescription: {summary}...")
    def check_sponsorblock(
        self, video_id=None, site=None, duration_sec=None, sb_generation=None
    ):
        """Run the SponsorBlock gate now and schedule the segment lookup.

        The local gate (site profile flag, extracted video id) is pure local
        state, so it stays synchronous and its output line keeps its place in
        the log. Only the HTTP request is handed to a background thread: the
        API is optional enrichment, and a slow or failing endpoint must never
        delay the info fetch's title_fetch_complete hand-off.

        The four arguments default to the current video_state values (for a
        direct call), but get_video_info() passes an explicit snapshot taken on
        the metadata worker — the id/site/duration it was started for, plus the
        lookup generation it bumped. A later URL change cannot race this thread,
        because the thread never reads video_state after it is handed off.
        """
        if video_id is None:
            video_id = self.video_state.get("video_id", "")
        if site is None:
            site = self.video_state.get("site", DEFAULT_SITE)
        if duration_sec is None:
            duration_sec = self.video_state.get("duration_sec", 0)
        if sb_generation is None:
            sb_generation = getattr(self, "_sb_generation", 0)

        # Get all possible categories to show everything in the visual bar
        # (shared with the ".edl only" mode's standalone lookup, so the bar
        # and that writer always see the same segments)
        all_categories = SB_ALL_API_CATEGORIES

        if not site_supports_sponsorblock(site):
            # SponsorBlock is a YouTube-only database; skip cleanly on other sites
            self._emit(
                "sb_log",
                "SponsorBlock: Not available for this site (YouTube only) — skipping",
                sb_generation,
            )
            return
        if not video_id:
            self._emit(
                "sb_log",
                "SponsorBlock: Could not extract video ID from URL",
                sb_generation,
            )
            return

        thread = threading.Thread(
            target=self._fetch_sponsorblock_segments,
            args=(video_id, all_categories, duration_sec, video_id, sb_generation),
            name="sponsorblock-lookup",
            daemon=True,
        )
        thread.start()

    def _fetch_sponsorblock_segments(
        self, video_id, all_categories, duration, snapshot_video_id, sb_generation
    ):
        """Query the SponsorBlock API for one video id (YouTube only).

        Runs on its own daemon thread, so a slow or failing endpoint delays
        only the progress bar — never the metadata hand-off. Every result is
        published through queued signals, so the UI updates on the main thread.

Three staleness guards, because a response can arrive at three different
        times:

        1. Before emitting, the thread re-checks the *current* video id against
           the snapshot it was started for. The user can paste a new URL and
           start a second fetch while this request is still in flight, and the
           current id is read here, on the worker, not from the snapshot — so a
           fetch that *did* replace this one is what gates the result.

        2. The generation token carried on the signals is checked again in the
           main-thread receivers. The worker checks the id, then emits; Qt may
           queue that signal while the main thread is busy, so a late delivery
           can still repaint a bar that a newer fetch already cleared. The
           token is bumped on the main thread and read back on the main thread,
           so the comparison is decisive regardless of how the two interleave —
           and it also covers a second fetch of the *same* video, where the id
           check alone would let an older request publish after a newer one.

        3. The token is bumped at the start of each fetch and again when the
           URL changes, before the new fetch starts. If it were bumped only near
           the end of get_video_info(), a response queued from a previous video
           could land after the URL changed but before the new fetch reached
           the bump, and its token would still match — briefly repainting a
           bar the URL change already cleared.

        The output lines are emitted on a scoped sb_log signal carrying the
        generation, not on append_output: append_output is also written by the
        download worker legitimately, so gating it would mean giving it a token
        too. sb_log's own main-thread receiver drops stale lines the same way
        the bar receiver drops stale segments.
        """
        error_message = None
        try:
            # Build the API URL with query parameters
            api_url = SPONSORBLOCK_API_URL
            payload = {"videoID": video_id, "category": all_categories}
            try:
                # One request, short budget: the previous 3 attempts x 15s plus
                # two 2s sleeps could hold the fetch for ~49s on a bad API.
                response = requests.get(
                    api_url,
                    params=payload,
                    timeout=SPONSORBLOCK_TIMEOUT_SECONDS,
                )

                # Staleness check: the user may have pasted a new URL and
                # started a second fetch while this request was in flight. A
                # response for a superseded video must not paint the new
                # video's bar or drop its output lines out of order — drop it
                # silently. The current id is read here, on the worker, not
                # from the snapshot, so a fetch that *did* replace this one is
                # what actually gates the result. This check covers every
                # branch below, including the error path at the end.
                if self.video_state.get("video_id", "") != snapshot_video_id:
                    return

                if response.status_code == 200:
                    segments = response.json()
                    if segments and len(segments) > 0:
                        # Count segments by category for the *returned* segments
                        category_counts = {}
                        for segment in segments:
                            category = segment.get("category", "unknown")
                            category_counts[category] = (
                                category_counts.get(category, 0) + 1
                            )
                        # Format the output to show the segments found
                        segments_info = ", ".join(
                            [
                                f"{count} {SB_DISPLAY_NAMES.get(cat, cat.capitalize())}"
                                for cat, count in sorted(category_counts.items())
                            ]
                        )
                        total = len(segments)
                        self._emit(
                            "sb_log",
                            f"📟 SponsorBlock: {total} segment(s) available ({segments_info})",
                            sb_generation,
                        )
                        # Update visual bar
                        self._emit(
                            "update_sb_bar", segments, float(duration), sb_generation
                        )
                        return
                    self._emit(
                        "sb_log",
                        "📟 SponsorBlock: No segments available",
                        sb_generation,
                    )
                    self._emit("update_sb_bar", [], 0, sb_generation)
                    return
                if response.status_code == 404:
                    self._emit(
                        "sb_log",
                        "📟 SponsorBlock: No segments available",
                        sb_generation,
                    )
                    self._emit("update_sb_bar", [], 0, sb_generation)
                    return
                error_message = f"📟 SponsorBlock: API returned status {response.status_code}"
            except requests.exceptions.RequestException as e:
                error_message = f"📟 SponsorBlock: Request error: {str(e)}"
        except Exception as e:
            error_message = f"📟 SponsorBlock: Failed to check segments: {str(e)}"

        # The id check above only ran on the success path; a failing lookup
        # still needs to be gated, so re-check here before printing the error.
        if self.video_state.get("video_id", "") != snapshot_video_id:
            return
        self._emit("sb_log", f"🚩 {error_message}", sb_generation)
