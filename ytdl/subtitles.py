"""Subtitle selection, file mapping and resync/merge pipeline.

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import json
import logging
import os
import re
from ytdl.preferences import PAUSE_THRESHOLD_MS_DEFAULT, pause_threshold_ms, strip_bracketed_soundcues
from ytdl.sites import DEFAULT_SITE, site_resyncs_auto_subs
from ytdl.subtitle_layout import pause_aware_cues, rolling_cues
from ytdl.utils import (
    SUBTITLE_LANG_ALIASES,
    canonical_subtitle_lang,
    format_srt_time,
    format_subtitle_stats,
    parse_srt_time,
)

logger = logging.getLogger(__name__)


def _looks_like_subtitle_file(path: str) -> bool:
    """Return True if `path` looks like an app-written subtitle file.

    Checks the first bytes for a cue number followed by a timestamp line
    with "-->", which is the SRT shape this app always writes. A bare file
    that doesn't match is left alone — it may be a file the user made
    themselves.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(200)
    except (OSError, UnicodeError):
        return False
    return bool(re.match(r"^\s*\d+\s*\n", head)) and "-->" in head


class SubtitleMixin:
    def get_selected_subtitle_codes(self):
        return [code for code, cb in self.subtitle_checkboxes.items() if cb.isChecked()]

    def _update_subtitle_warning(self):
        """Update the Subtitles group box title with a warning if >1 language selected."""
        group_box = getattr(self, "subtitle_group_box", None)
        if group_box is not None:
            checked = sum(1 for cb in self.subtitle_checkboxes.values() if cb.isChecked())
            if checked > 1:
                group_box.setTitle("Subtitles ⚠️")
                group_box.setToolTip(
                    "Multiple subtitles cause more YouTube API requests<br>"
                    "and could cause a 429 error: too many requests."
                )
            else:
                group_box.setTitle("Subtitles")
                group_box.setToolTip("")
        # Keep the Type-group "Subtitles" checkbox in sync: as soon as any
        # language is checked, make sure it is also checked there. Only
        # auto-checks — the user can still uncheck it manually afterwards.
        type_cb = getattr(self, "subtitles_checkbox", None)
        if type_cb is not None:
            any_checked = any(cb.isChecked() for cb in self.subtitle_checkboxes.values())
            if any_checked and not type_cb.isChecked():
                type_cb.setChecked(True)

    def _update_subtitle_checkboxes(self, _lang_input):
        # The language the site reported is not needed for the labels: availability
        # comes from video_state["available_subtitles"], keyed by UI language code.
        available = self.video_state.get("available_subtitles", {})
        for code, cb in self.subtitle_checkboxes.items():
            # Update label
            status = available.get(code)
            lang_names = {"en": "English", "de": "German", "es": "Spanish"}
            name = lang_names.get(code, code)

            if status:
                cb.setText(f"{name} ({status})")
                cb.setEnabled(True)
                self.subtitle_unavailable.discard(code)
            else:
                # No subtitles available: label it "(none)", uncheck it and
                # keep it disabled (also across UI re-enables, see
                # _set_ui_enabled_state)
                cb.setText(f"{name} (none)")
                cb.setEnabled(False)
                cb.setChecked(False)
                self.subtitle_unavailable.add(code)

        # Subtitle selection is manual: after a fetch the checkboxes keep
        # whatever the user checked (only "(none)" languages are force-
        # unchecked above, since they are disabled). The one exception is
        # "Subtitles only" mode, which downloads nothing else — there the
        # selection is auto-filled (same priority rule the mode's radio
        # button applies via _auto_select_subtitles()).
        if self._effective_media_type() == "subtitles":
            self._auto_select_subtitles()
        self._update_subtitle_warning()

    def _auto_select_subtitles(self):
        # Identify types
        real_codes = []
        available_codes = []

        for code, cb in self.subtitle_checkboxes.items():
            text = cb.text().lower()
            if "(real)" in text:
                real_codes.append(code)
            if "(none)" not in text and cb.isEnabled():
                available_codes.append(code)

        # Apply selection rules
        if real_codes:
            # Check all real ones, uncheck others
            for code, cb in self.subtitle_checkboxes.items():
                cb.setChecked(code in real_codes)
        elif available_codes:
            # Check all available (non-none) ones
            for code, cb in self.subtitle_checkboxes.items():
                cb.setChecked(code in available_codes)
        self._update_subtitle_warning()

    def _subtitle_lang_patterns(self, selected_langs):
        """
        Build the --sub-langs argument covering every known subtitle-key shape.

        yt-dlp matches --sub-langs entries as regexes (fullmatch) against the
        site's subtitle keys. YouTube uses "en" / "a.en" keys, but other sites
        use different shapes (Rumble: "en-auto", with generated subs in
        `subtitles`, not `automatic_captions`). Requesting each language in
        every known key shape makes the site's actual key match; unavailable
        shapes are simply ignored by yt-dlp.
        """
        variants = []
        for lang in selected_langs:
            # ISO 639-2 site keys (ARD/ZDF report German as "deu") need their
            # own pattern: yt-dlp fullmatches each entry against the site's
            # actual keys, so "de" alone would not match "deu".
            keys = [lang] + [
                alias for alias, base in SUBTITLE_LANG_ALIASES.items() if base == lang
            ]
            for key in keys:
                variants.extend([key, f"a.{key}", f"{key}-auto", f"a.{key}-auto"])
        return ",".join(variants)

    def _subtitle_type_for_file(self, lang, marked_auto):
        """
        "auto" (site-generated) or "real" (manual) for a downloaded subtitle file.

        A file name only proves a track is auto-generated when the SITE named
        it that way (Rumble's "en-auto"). It does NOT work for YouTube: current
        yt-dlp merges "--write-subs" and "--write-auto-subs" into one language
        namespace (a manual track wins the slot) and writes automatic captions
        as plain "<title>.en.srt" - no "a." prefix, exactly like a manual
        track. Treating every unmarked name as "real" therefore disabled the
        2-line merge for every YouTube ASR video and stored the raw rolling
        captions instead (see _subtitle_needs_resync).

        So an unmarked name is resolved through the info fetch, which knows the
        difference: video_state["available_subtitles"][lang] is "auto" when the
        site offers no manual track for that language. Without an info fetch
        (replays, tests) the name is the only evidence, so it stays "real".
        """
        if marked_auto:
            return "auto"
        base = canonical_subtitle_lang(lang)
        offered = (self.video_state.get("available_subtitles") or {}).get(base)
        if offered in ("auto", "real"):
            return offered
        return "real"

    def _find_downloaded_subtitles(self, selected_langs):
        """
        Locate downloaded subtitle files for the requested languages.

        Sites name subtitle files differently: YouTube writes both manual and
        auto-generated tracks as "<base>.<lang>.srt", Rumble names its generated
        subs "en-auto.srt" (they live in `subtitles` under "<code>-auto"). The
        full output filename is "<base>.<lang>.<ext>", so scan the output
        directory once and map every subtitle suffix back to the requested base
        language.

        Returns a list of (lang, path, sub_type) with sub_type
        "real" or "auto", preferring real/manual and canonical names.
        """
        out_dir = self.get_output_dir()
        found_sub_files = {}  # filepath -> (base_lang, sub_type, ext)
        try:
            for fname in os.listdir(out_dir):
                # Suffix match: the base filename (video title) is arbitrary;
                # only the trailing "<lang>[-auto].<ext>" part identifies a
                # subtitle file. An "a." prefix or a "-auto" suffix marks a
                # generated track; an unmarked name is resolved via the info
                # fetch (see _subtitle_type_for_file).
                m = re.search(
                    r"(?P<prefix>a\.)?(?P<lang>[A-Za-z0-9]+)(?P<auto>-auto)?\.(?P<ext>srt|vtt|json3)$",
                    fname,
                )
                if not m:
                    continue
                lang = m.group("lang").lower()
                marked_auto = bool(m.group("prefix") or m.group("auto"))
                sub_type = self._subtitle_type_for_file(lang, marked_auto)
                found_sub_files[os.path.join(out_dir, fname)] = (lang, sub_type, m.group("ext"))
        except OSError:
            pass

        downloaded_subs = []
        for lang in selected_langs:
            # canonical_subtitle_lang maps the site's file key (ARD/ZDF write
            # "<base>.deu.srt") onto the requested UI code ("de").
            base = canonical_subtitle_lang(lang)
            candidates = [
                path
                for path, (flang, _ftype, _ext) in found_sub_files.items()
                if canonical_subtitle_lang(flang) == base
            ]
            if not candidates:
                continue
            # Prefer: manual "real" over "auto", canonical names over
            # "-auto", json3 over other extensions (json3 is our source),
            # then a stable tiebreak.
            candidates.sort(
                key=lambda p: (
                    found_sub_files[p][1] != "real",
                    "-auto" in p,
                    found_sub_files[p][2] != "json3",
                    p,
                )
            )
            chosen = candidates[0]
            downloaded_subs.append((lang, chosen, found_sub_files[chosen][1]))
        return downloaded_subs

    def _uses_bare_subtitle_name(self, subtitle_count=1):
        """Whether subtitle files are stored without a language code.

        "<title>.en.srt" repeats what the name already implies when only one
        subtitle file was produced, so the file is simply "<title>.srt" - the
        name players and editors expect. With two or more files the code is the
        only thing that tells them apart, so it is kept.

        The count is the number of files that actually landed, not the number of
        selected languages: two selected languages resolve to a single file
        when the site offers only one of them.
        """
        return subtitle_count == 1

    def _bare_subtitle_credit(self, selected_langs):
        """Which requested language a language-less "<title>.srt" may count for.

        The bare name does not record its language, so it can only be trusted
        when the request covers every language the site offers for this video:
        the file then has to be one of them. At most ONE language is credited -
        a request for several languages still misses the others, so it is
        re-requested and the folder ends up language-tagged again.

        Returns None when the file cannot be attributed: without an info fetch
        the offered languages are unknown, and when the site offers a language
        that was NOT requested the bare file may well be that other language.
        Crediting it anyway would skip the download and silently leave the
        wrong language on disk.
        """
        if not selected_langs:
            return None
        offered = set(self.video_state.get("available_subtitles") or {})
        if offered and offered.issubset(set(selected_langs)):
            return selected_langs[0]
        return None

    def _subtitle_output_path(self, lang, extension=".srt", bare=False):
        """Path the processed subtitle file for `lang` is written to."""
        if bare:
            return self.get_full_path(extension)
        return self.get_full_path(f".{lang}{extension}")

    def _normalize_subtitle_names(self, downloaded_subs, bare=False):
        """
        Rename site-specific subtitle files to the canonical name -
        "<base>.<lang>.srt", or "<base>.srt" when `bare` (one file for the
        whole video) - returning an updated (lang, path, sub_type) list.

        yt-dlp names subtitle files after the site's subtitle key, e.g. Rumble
        writes "<base>.en-auto.srt". Post-processing (_resync_subtitle_for_language)
        always writes the canonical name - without this rename, both the
        site-named original and the processed canonical file end up on disk.
        After renaming, resync overwrites the single file in place.
        """
        normalized = []
        for lang, srt_path, sub_type in downloaded_subs:
            # JSON3 files keep their site-given name until resync; only SRT/VTT
            # files get renamed to canonical form. JSON3 is processed in place.
            if srt_path.lower().endswith(".json3"):
                normalized.append((lang, srt_path, sub_type))
                continue
            canonical = self._subtitle_output_path(lang, bare=bare)
            if srt_path != canonical and srt_path and os.path.exists(srt_path):
                try:
                    os.replace(srt_path, canonical)
                    srt_path = canonical
                except OSError as e:
                    # Non-fatal: resync then writes the canonical file and the
                    # site-named original stays (old, pre-fix behavior).
                    logger.warning(f"Could not rename subtitle file {srt_path}: {e}")
            normalized.append((lang, srt_path, sub_type))
        return normalized

    def _remove_superseded_bare_subtitle(self):
        """Drop a language-less "<base>.srt" that language-tagged files replaced.

        Only reachable when a video offers more than one subtitle language now,
        while an earlier run (when it offered just one) stored its single
        subtitle without a code. This run's tagged files supersede that
        content, and the leftover's language can no longer be read from its
        name, so it is removed rather than silently duplicating a track.

        A bare file that doesn't look like an app-written subtitle (no cue
        number + timestamp) is left alone — it may be a file the user made
        themselves.

        Returns the removed path, or None if there was nothing to remove.
        """
        removed = None
        for extension in (".srt", ".vtt", ".json3"):
            path = self.get_full_path(extension)
            if not os.path.isfile(path):
                continue
            if not _looks_like_subtitle_file(path):
                continue
            try:
                os.remove(path)
            except OSError as e:
                logger.warning(f"Could not remove superseded subtitle {path}: {e}")
                continue
            self._emit("append_output", f"  → removed superseded subtitle: {os.path.basename(path)}")
            removed = path
        return removed

    def _subtitle_needs_resync(self, sub_type):
        """
        Whether a downloaded subtitle file needs the 2-line merge/resync.

        Manual ("real") subs never do. Auto subs do on sites whose generated
        captions arrive as choppy fragments (YouTube), but not on sites that
        deliver them pre-formatted (Rumble) - decided per site profile.
        """
        if sub_type == "real":
            return False
        site = self.video_state.get("site", DEFAULT_SITE)
        return site_resyncs_auto_subs(site)

    def _resync_subtitle_for_language(self, lang, srt_path, bare=False):
        if not os.path.exists(srt_path):
            self._emit("append_output", f"No srt file: {srt_path}")
            return

        # output_srt is "Title.en.srt" (no "merged" or "resynced" suffix), or
        # plain "Title.srt" for a video whose only subtitle needs no code.
        output_srt = self._subtitle_output_path(lang, bare=bare)

        # If output_srt is different from srt_path (e.g. srt_path was .a.en.srt),
        # we process it into the final name.
        # If they are the same, we overwrite it (safe because resync_subtitles reads into memory).
        # This runs on the download worker thread, so the returned lines are
        # printed through _emit(), which drops them if the window is closing.
        _ok, lines = self.resync_subtitles(srt_path, output_srt)
        for line in lines:
            self._emit("append_output", line)
        if not _ok:
            self._emit(
                "append_output",
                f"🚩 Subtitle resync failed for {lang}; original {os.path.basename(srt_path)} kept.",
            )

    def resync_subtitles(self, srt_path, output_path):
        """Lay out a downloaded subtitle file and write it to output_path.

        Timestamps are kept in the source file's own timeline. This app never
        cuts the media - SponsorBlock segments are marked and written to an
        .edl for the player to jump - so an EDL-skipping player looks
        subtitles up by current media time, which after a jump is still the
        original time. Subtitles of an earlier version were retimed into an
        edited timeline to match a physically cut video; that workflow was
        dropped because cutting is only clean at keyframes, and the EDL route
        needs no re-encode at all.

        Returns (ok, lines): `ok` is True when the file was rewritten, and
        `lines` is the panel output to show. The method is Qt-free and emits
        nothing itself - the caller decides whether it runs on the main thread
        (download post-processing) or a worker (standalone conversion), which
        is what lets the standalone path keep the window responsive: the whole
        parse/layout/write happens off-thread and the lines are queued for the
        main thread to print.

        Subtitles can arrive either as SRT (legacy) or JSON3 (timedtext
        captions from YouTube's timedtext API). JSON3 is parsed into line
        units with line-level timing, then passed through `pause_aware_cues()`
        which breaks cues at long speech pauses (> pause_threshold_ms). SRT
        uses the existing rolling_cues() path unchanged.
        """
        lines_out = []
        if not os.path.exists(srt_path):
            logger.warning(f"Subtitle file not found: {srt_path}")
            return False, lines_out

        try:
            with open(srt_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeError) as e:
            logger.warning("Could not read subtitle file %s: %s", srt_path, e)
            lines_out.append(
                f"⚠️ Could not read subtitle file: {os.path.basename(srt_path)}"
            )
            return False, lines_out

        # JSON3 files carry per-word timestamps and are laid out by
        # pause_aware_cues(); SRT files use the existing rolling_cues() path.
        if srt_path.lower().endswith(".json3"):
            return self._resync_json3_subtitles(
                content, srt_path, output_path, lines_out
            )

        return self._resync_srt_subtitles(
            content, srt_path, output_path, lines_out
        )

    def _parse_json3_subtitles(self, content: str) -> list:
        """Parse a JSON3 subtitle string into line dicts.

        Each JSON3 `event` is one caption line:
          - tStartMs / dDurationMs give absolute start/end in ms
          - segs[].utf8 gives the text (concatenated = full line)
          - aAppend: 1 means append to the previous line (no line break)

        Returns a list of dicts: {start, end, text}, sorted by start time.
        Overlapping events are kept as-is; pause detection happens downstream
        in pause_aware_cues().
        """
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning("Could not parse JSON3 subtitle file: %s", e)
            return []
        if not isinstance(data, dict):
            logger.warning(
                "JSON3 subtitle file is not an object — ignoring"
            )
            return []

        events = data.get("events", [])
        lines_out = []

        for ev in events:
            start_ms = ev.get("tStartMs")
            if start_ms is None:
                # No start time — can't place this cue, skip it.
                continue
            dur_ms = ev.get("dDurationMs", 0)
            end_ms = start_ms + dur_ms

            segs = ev.get("segs", [])
            text = "".join(s.get("utf8", "") for s in segs).strip()
            # Strip >> speaker-change markers, same as SRT path does
            text = re.sub(r'^>>\s*', '', text, flags=re.MULTILINE).strip()

            if not text:
                continue

            start_sec = start_ms / 1000.0
            end_sec = end_ms / 1000.0

            # aAppend=1 means this event continues the previous visual line
            if ev.get("aAppend") and lines_out:
                lines_out[-1]["text"] += " " + text
                lines_out[-1]["end"] = end_sec
            else:
                lines_out.append(
                    {"start": start_sec, "end": end_sec, "text": text}
                )

        # Sort by start time so pause_aware_cues() sees a monotonic timeline.
        lines_out.sort(key=lambda sub: sub["start"])
        return lines_out

    def _resync_json3_subtitles(
        self, content: str, srt_path: str, output_path: str, lines_out: list
    ):
        """Resync a JSON3 subtitle file using per-word timestamps.

        Parses JSON3 events into lines, then groups them into cues with
        pause_aware_cues() so that long speech pauses (> pause_threshold_ms)
        break the cue and leave a blank gap on screen.
        """
        subtitle_lines = self._parse_json3_subtitles(content)

        if not subtitle_lines:
            lines_out.append(
                "⚠️ No valid subtitle cues found; keeping the original file"
            )
            return False, lines_out

        # One decision per file, so a run can never come out half-stripped
        strip_soundcues = strip_bracketed_soundcues()
        if strip_soundcues:
            for sub in subtitle_lines:
                sub["text"] = self._strip_nonspoken_brackets(sub["text"])
            # Drop lines that became empty after stripping
            subtitle_lines = [s for s in subtitle_lines if s["text"].strip()]

        # Normalize internal blank lines so cue text can't split an SRT block.
        # JSON3 segs can carry "\n" (e.g. an empty segment); a blank line inside
        # a cue would split the SRT block into two cues. Collapse blank lines
        # regardless of the strip preference so output is consistent.
        for sub in subtitle_lines:
            sub["text"] = re.sub(r"\n\s*\n", "\n", sub["text"]).strip()

        if not subtitle_lines:
            lines_out.append(
                "⚠️ No valid subtitle cues found; keeping the original file"
            )
            return False, lines_out

        # Drop cues with no usable duration. Timestamps are the source ones —
        # a missing dDurationMs defaults to 0, which would otherwise produce
        # zero-length cues that _cue_from_lines silently pads to min_dur.
        # Mirror the SRT path's 0.15s floor so both paths behave identically.
        too_short_cues = 0
        adjusted_subtitles = []
        for sub in subtitle_lines:
            dur = sub["end"] - sub["start"]
            if sub["start"] >= 0 and sub["end"] > sub["start"] and dur >= 0.15:
                adjusted_subtitles.append(sub)
            else:
                too_short_cues += 1
        subtitle_lines = adjusted_subtitles

        if not subtitle_lines:
            lines_out.append(
                "⚠️ No valid subtitle cues found; keeping the original file"
            )
            return False, lines_out

        # Pause-aware layout: break cues at long speech pauses
        pause_ms = pause_threshold_ms()
        merged_subtitles, layout_stats = pause_aware_cues(
            subtitle_lines, pause_threshold_ms=pause_ms
        )

        # Final overlap guard — same safety net the SRT path applies via
        # _format_subtitles_for_display(). pause_aware_cues() is designed to
        # produce non-overlapping cues, but this catches any edge case where
        # overlapping source events slip through.
        fixed, dropped = self._fix_subtitle_time_overlaps(merged_subtitles)
        layout_stats["dropped"] = layout_stats.get("dropped", 0) + len(dropped)
        layout_stats["dropped_cues"] = (
            (layout_stats.get("dropped_cues") or []) + dropped
        )
        merged_subtitles = fixed

        if not merged_subtitles:
            logger.warning(
                "Subtitle processing produced no cues for %s", srt_path
            )
            lines_out.append(
                "⚠️ Subtitle processing produced no cues; keeping the original file"
            )
            return False, lines_out

        resynced_blocks = []
        for i, sub in enumerate(merged_subtitles, 1):
            time_line = (
                f"{format_srt_time(sub['start'])} --> {format_srt_time(sub['end'])}"
            )
            block = f"{i}\n{time_line}\n{sub['text']}"
            resynced_blocks.append(block)

        # Write beside the destination and replace atomically
        temp_path = f"{output_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(resynced_blocks) + "\n")
            os.replace(temp_path, output_path)
        except OSError:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

        lines_out.append("💬 Subtitles merged.")

        two_line_cues = sum(
            1 for sub in merged_subtitles if "\n" in (sub.get("text") or "")
        )
        lines_out.append(
            "  📊 "
            + format_subtitle_stats(
                len(subtitle_lines),
                len(merged_subtitles),
                counters={
                    "two_line": two_line_cues,
                    "dropped": layout_stats.get("dropped", 0),
                    "too_short": too_short_cues,
                },
                name=os.path.basename(output_path),
            )
        )
        lines_out.extend(
            self._warn_about_dropped_cues(
                layout_stats.get("dropped_cues") or [],
                os.path.basename(output_path),
            )
        )
        return True, lines_out

    def _resync_srt_subtitles(
        self, content: str, srt_path: str, output_path: str, lines_out: list
    ):
        """Resync an SRT subtitle file using the existing rolling_cues path."""
        # Accept common CRLF/CR subtitle files as well as Unix LF files.
        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        blocks = re.split(r"\n\s*\n", normalized_content.strip())
        subtitles = []
        no_text_cues = 0

        # One decision per file, so a run can never come out half-stripped if
        # the preference is saved while this file is being processed.
        strip_soundcues = strip_bracketed_soundcues()

        # First pass: collect all subtitles and find max time
        for block in blocks:
            # `lines_out` is the panel output this method returns - the parse
            # loop must NOT reuse that name for a block's raw text lines.
            block_lines = block.split("\n")

            # Locate the timestamp line rather than assuming a fixed index:
            # standard SRT blocks may omit the cue number, or carry a cue
            # identifier on the line before the timestamp. Taking the first
            # line that matches the arrow form keeps both shapes valid; lines
            # before it (a number, an identifier, ...) are simply ignored.
            time_index = None
            for i, candidate in enumerate(block_lines):
                if re.match(r"[\d:,]+\s+-->\s+[\d:,]+", candidate):
                    time_index = i
                    break
            if time_index is None:
                continue

            time_line = block_lines[time_index]
            match = re.match(r"([\d:,]+)\s+-->\s+([\d:,]+)", time_line)
            if not match:
                continue

            start_str, end_str = match.groups()
            start_sec = parse_srt_time(start_str)
            end_sec = parse_srt_time(end_str)

            # Everything after the timestamp is the cue text; a block may
            # carry more than one line, and the first of those is the text
            # (not a cue number or identifier, which only precede the arrow).
            raw_text = "\n".join(block_lines[time_index + 1:])
            # Filter out ">>" artifacts often found in auto-generated captions
            clean_text = re.sub(r'^>>\s*', '', raw_text, flags=re.MULTILINE).strip()
            # Remove non-spoken tokens in square brackets, e.g. [laughter],
            # [snorts]. Controlled by the strip_bracketed_soundcues preference;
            # off keeps them, so a block that is nothing but cues still has text.
            if strip_soundcues:
                clean_text = self._strip_nonspoken_brackets(clean_text)
            # Replace all newlines and extra whitespace with a single space
            text = " ".join(clean_text.split())

            if text:
                subtitles.append({"start": start_sec, "end": end_sec, "text": text})
            else:
                # A block that held nothing but artifacts or stage directions
                # (">>", or "[musik]" when the strip preference is on, which
                # removes it on purpose). Counted so the summary reconciles with
                # the cue count in the file the user can open.
                no_text_cues += 1

        if not subtitles:
            lines_out.append(
                "⚠️ No valid subtitle cues found; keeping the original file"
            )
            return False, lines_out

        # Drop cues with no usable duration. Timestamps are the source ones -
        # see the docstring for why they are never retimed.
        adjusted_subtitles = [
            sub
            for sub in subtitles
            if sub["start"] >= 0
            and sub["end"] > sub["start"]
            and sub["end"] - sub["start"] >= 0.15
        ]

        logger.debug(f"Adjusted {len(adjusted_subtitles)} subtitle cues")

        # The 0.15s floor is the first of two places that can drop a cue, and
        # it does so silently - counted here for the summary line below.
        too_short_cues = len(subtitles) - len(adjusted_subtitles)

        # Sentence-aligned 2-line layout (see ytdl/subtitle_layout.py)
        merged_subtitles, layout_stats = self._format_subtitles_for_display(
            adjusted_subtitles
        )

        if not merged_subtitles:
            logger.warning(
                "Subtitle processing produced no cues for %s", srt_path
            )
            lines_out.append(
                "⚠️ Subtitle processing produced no cues; keeping the original file"
            )
            return False, lines_out

        resynced_blocks = []
        for i, sub in enumerate(merged_subtitles, 1):
            time_line = (
                f"{format_srt_time(sub['start'])} --> {format_srt_time(sub['end'])}"
            )
            block = f"{i}\n{time_line}\n{sub['text']}"
            resynced_blocks.append(block)

        # Write beside the destination and replace atomically so a failed write
        # never truncates the original subtitle file. Blocks are joined by a
        # blank line and the file ends with a newline: that is the shape an SRT
        # is expected to have, and a parser can drop a trailing block that has
        # none.
        temp_path = f"{output_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(resynced_blocks) + "\n")
            os.replace(temp_path, output_path)
        except OSError:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

        lines_out.append("💬 Subtitles merged.")

        # What the layout actually did, per file. A whole video's subtitles can
        # be skipped by the gate upstream (or merged into nothing) without a
        # single word of this showing up anywhere else in the panel, so the
        # counts are reported next to the file they belong to.
        #
        # The rolling stage keeps the ASR's windows, so in == out and there is no
        # reading-speed figure to report: the durations are the ASR's own gaps.
        two_line_cues = sum(
            1 for sub in merged_subtitles if "\n" in (sub.get("text") or "")
        )
        lines_out.append(
            "  📊 "
            + format_subtitle_stats(
                len(adjusted_subtitles),
                len(merged_subtitles),
                counters={
                    "two_line": two_line_cues,
                    "no_text": no_text_cues,
                    "too_short": too_short_cues,
                    "dropped": layout_stats.get("dropped", 0),
                },
                name=os.path.basename(output_path),
            )
        )
        lines_out.extend(
            self._warn_about_dropped_cues(
                layout_stats.get("dropped_cues") or [],
                os.path.basename(output_path),
            )
        )
        return True, lines_out

    def _run_subtitle_resync(self, srt_path):
        """Run resync_subtitles() off the GUI thread and print its output.

        The standalone Subs button handler used to call resync_subtitles()
        directly on the main thread: parsing, layout and writing a whole file
        are synchronous, so a long SRT froze the window. This runs the same
        work on a daemon thread and hands the result back through the queued
        subtitle_processing_complete signal, so the controls re-enable on the
        main thread exactly like the download worker's finally block.

        The same-file/download guard and the atomic .tmp + os.replace write
        are unchanged; only the thread the work runs on moves.

        The worker is a daemon: a non-daemon thread keeps the process alive
        until it finishes, so closing the window could leave the process
        running in the background for the rest of a long conversion - and a
        bounded join followed by dropping the reference would not bound it
        either. A daemon lets the process exit on close. The trade-off is that
        a process exit mid-write stops the worker before its finally runs, so
        a leftover .tmp can survive; the atomic write still protects the
        original SRT (it is never truncated), and the .tmp is written beside
        the destination so os.replace() stays atomic across the user's own
        filesystem.
        """
        try:
            ok, lines = self.resync_subtitles(srt_path, srt_path)
        except Exception as e:  # noqa: BLE001 - UI boundary: never let a worker crash the window
            logger.error(f"Subtitle processing error: {e}")
            ok = False
            lines = [f"🚩 Error processing subtitle: {e}"]
        finally:
            temp_path = f"{srt_path}.tmp"
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        # Qt-free work is done; the lines are queued for the main thread, and
        # _emit() drops them if the window is closing mid-run. The completion
        # handshake goes through _emit() too: it is what the main-thread slot
        # waits on to re-enable the controls, so it must not be emitted into a
        # dying QObject either — if the window is closing, the controls are
        # being destroyed with it and nobody is waiting for the slot.
        for line in lines:
            self._emit("append_output", line)
        self._emit("subtitle_processing_complete", ok)

    def _warn_about_dropped_cues(self, dropped, name):
        """Report cues the overlap guard could not place, by name.

        Should never fire - the layout produces a monotonic sequence, and the
        guard only drops what cannot be shifted into place. It exists so that a
        text loss is impossible to miss: the panel line names the file and the
        count, the log file carries the text of each lost cue.

        Returns the panel lines to show, so the caller can print them on
        whatever thread it is running on instead of emitting directly.
        """
        lines = []
        if not dropped:
            return lines
        for cue in dropped:
            logger.warning(
                "Subtitle cue dropped by the overlap guard in %s at %.3fs: %s",
                name,
                cue.get("start") or 0.0,
                (cue.get("text") or "").replace("\n", " ")[:80],
            )
        preview = ", ".join(
            f"{(cue.get('text') or '').replace(chr(10), ' ')[:40]}"
            for cue in dropped[:2]
        )
        suffix = f" — {preview}" if preview else ""
        lines.append(
            f"⚠️ {len(dropped)} subtitle line(s) could not be timed and were "
            f"dropped from {name}{suffix}"
        )
        return lines

    def _strip_nonspoken_brackets(self, text: str) -> str:
        """
        Remove stage directions / non-spoken tokens that commonly appear in subtitles
        inside square brackets, e.g. "[laughter]", "[snorts]".
        """
        if not text:
            return ""

        # Remove any bracketed chunks (non-greedy, no nesting support needed here)
        cleaned = re.sub(r"\[[^\[\]]*?\]", " ", text)
        # Normalize whitespace
        cleaned = " ".join(cleaned.split())
        return cleaned.strip()

    def _fix_subtitle_time_overlaps(self, subtitles):
        """Make the cue sequence monotonic and non-overlapping, without losing text.

        Returns (cues, dropped) where `dropped` lists the cues that could not be
        placed at all, so the caller can report them.

        Placement is preferred over deletion. A cue that would overlap is first
        given room by trimming its neighbour's end (text untouched, no cascade);
        only a cue that claims to start at or before its predecessor - or one
        that arrives with no duration - is shifted to where it fits. A subtitle
        that appears a little late is far better than a missing sentence, so
        nothing is dropped unless it still has no room once shifted.
        """
        if not subtitles:
            return ([], [])

        minimum_duration = 0.2
        fixed = []
        dropped = []
        for sub in subtitles:
            if not sub:
                continue
            start = sub.get("start")
            end = sub.get("end")
            if start is None or end is None:
                dropped.append(sub)
                continue
            if end <= start:
                # No duration at all: keep the text, give it the shortest
                # display rather than losing the line.
                end = start + minimum_duration

            current = dict(sub)
            if fixed:
                previous = fixed[-1]
                if start <= previous["start"]:
                    # Out-of-order cue: push it behind its predecessor.
                    start = previous["end"]
                    if end <= start:
                        dropped.append(sub)
                        continue
                if start < previous["end"]:
                    previous["end"] = start

            current["start"] = start
            current["end"] = end
            fixed.append(current)
        return fixed, dropped

    def _format_subtitles_for_display(self, subtitles):
        """Lay out parsed cues for display: the YouTube player's look, two lines.

        Delegates to ytdl.subtitle_layout.rolling_cues (pure, no Qt) and returns
        (cues, stats). The ASR's own windows stay the cues, each starting at its
        own exact timestamp, and each cue carries the finished window above the
        new one - shown whole instead of rolling in word by word. See that
        function for the rules and for why the finished line is repeated.

        `stats["dropped"]` counts the cues the final overlap guard could not
        place at all, and `stats["dropped_cues"]` carries them so the caller can
        name them. Both are zero for every real capture - they exist so that a
        text loss can never be silent.
        """
        if not subtitles:
            return ([], {})

        laid_out, stats = rolling_cues(subtitles)
        # Final guard only: rolling_cues already produces a monotonic,
        # non-overlapping sequence (a cue ends where the next window opens), so
        # this drops nothing in practice - it is here so a future change cannot
        # write an overlapping or lossy file.
        fixed, dropped = self._fix_subtitle_time_overlaps(laid_out)
        stats["dropped"] = len(dropped)
        stats["dropped_cues"] = dropped
        return fixed, stats
