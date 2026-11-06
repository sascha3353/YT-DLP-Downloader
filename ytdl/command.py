"""Command building and output redaction — extracted from ytdl/download.py.

Mixin for YTDLPDownloaderGUI; methods access shared state via ``self``.
Builds the yt-dlp argument list from the GUI's quality/format/cookie
settings and redacts credentialed flags before logging.
"""

import logging
import os
import shutil

from ytdl import preferences as prefs
from ytdl.sites import (
    DEFAULT_SITE,
    SUPPORTED_SITES,
    site_supports_sponsorblock,
    site_wants_js_runtime,
)
from ytdl.utils import sanitize_title

logger = logging.getLogger(__name__)

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


def _redact_command(cmd):
    """Return a copy of `cmd` with the value of value-taking flags redacted.

    The flag itself is kept (so the log shows what was requested) but its
    argument is replaced, so a token in a cookie, a header, an extractor arg,
    a username, a password or a video password never reaches the log.

    URLs and the output template are deliberately NOT touched. A URL is the
    only thing that identifies which job failed, and no supported site's URL
    carries a credential: YouTube's is `watch?v=<public id>`, Rumble/Odysee/ARD
    and ZDF have no query string at all. An earlier revision masked every
    query string on the theory that one might hold a session token; that
    hid the video ID of the very job the log was reporting, and truncated any
    output line containing a "?" - which cost more debuggability than the
    secrecy it could ever have bought was worth.
    """
    redacted = []
    skip_next = False
    for item in cmd:
        if skip_next:
            skip_next = False
            redacted.append(_REDACTED_ARG)
            continue
        # "--flag=value": the flag and its value are one argument.
        if "=" in item:
            flag, _sep, _value = item.partition("=")
            if flag in _VALUE_FLAGS:
                redacted.append(f"{flag}={_REDACTED_ARG}")
                continue
        redacted.append(item)
        if item in _VALUE_FLAGS:
            skip_next = True
    return redacted


def _format_captured_output(captured_output, truncated):
    """Render the failure capture as one log string, noting truncation.

    yt-dlp can emit thousands of lines on a long download, so only the
    bounded tail is kept. Saying so out loud matters: a truncated capture
    presented as complete would mislead anyone debugging from the log.
    """
    if not captured_output:
        return ""
    body = "\n".join(captured_output)
    if truncated:
        return (
            f"Download failed — captured output (last "
            f"{len(captured_output)} of {MAX_CAPTURED_OUTPUT_LINES}+ lines):\n"
            + body
        )
    return "Download failed — captured output:\n" + body


class CommandMixin:
    def build_command(self, selected_langs=None):
        url = self.get_clean_url()
        if not url:
            return None

        # Get the user-edited title for filename. It must be sanitized here
        # too: start_download() already stored the sanitized basename (and
        # created that directory), so writing the raw text back would make
        # base_filename, get_full_path() and the -o template disagree with the
        # directory that actually exists.
        custom_title = self.title_entry.text().strip()
        if not custom_title:
            custom_title = "downloaded_video"
        sanitized_title = sanitize_title(custom_title)
        if not sanitized_title:
            # A title made only of illegal characters (e.g. "///") sanitizes
            # to an empty string, which would collapse the path to the output
            # root. Fall back to a safe, non-empty basename.
            sanitized_title = "downloaded_video"

        # Update state
        self.update_video_state(title=sanitized_title)

        cmd = [self.yt_dlp_bin]

        # Dependency Paths
        if self.ffmpeg_bin and shutil.which(self.ffmpeg_bin):
            ffmpeg_dir = os.path.dirname(self.ffmpeg_bin)
            cmd.extend(["--ffmpeg-location", ffmpeg_dir])

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

        # Media Type and Format Logic
        media_type = self._effective_media_type() or "video"
        quality = self.video_state["quality"]
        video_fmt = self.video_state["video_format"]
        audio_fmt = self.video_state["audio_format"]
        video_codec = self.video_state["video_codec"]

        all_best = quality == "best" and video_fmt == "best" and video_codec == "best"

        # yt-dlp's format selector uses "av01" for AV1 while the GUI stores
        # "av1". Map GUI codec names to selector names.
        _codec_map = {"av1": "av01"}
        selector_codec = _codec_map.get(video_codec, video_codec)

        if media_type == "audio":
            # Sites without audio-only streams (Odysee: every format is muxed,
            # video claims only offer the source MP4) would otherwise save a
            # VIDEO file for an audio request - "bestaudio/best" falls back to
            # a muxed format. Such sites set "always_extract_audio", which
            # forces -x so the audio is extracted from whatever is downloaded.
            site_profile = SUPPORTED_SITES.get(
                self.video_state.get("site", DEFAULT_SITE), {}
            )
            force_extract = site_profile.get("always_extract_audio", False)

            if force_extract:
                # No -f: yt-dlp picks bestaudio/best internally and -x extracts
                cmd.extend(["-x", "--audio-format", audio_fmt])
            elif audio_fmt == "best":
                cmd.extend(["-f", "bestaudio/best"])
            else:
                # NOTE: this used to map the GUI's "M4A" option to yt-dlp's
                # "aac" postprocessor format. Per yt-dlp's FFmpegExtractAudioPP,
                # "aac" forces a raw ADTS stream (-f adts) while the output file
                # is still named ".m4a" - a mislabeled file. "m4a" instead writes
                # a real MP4/M4A container and stream-copies AAC sources
                # losslessly (YouTube AAC, Rumble's ADTS "audio-192p") via the
                # aac_adtstoasc bitstream filter; only non-AAC sources
                # (Opus/Vorbis) get re-encoded.
                cmd.extend(
                    [
                        "-x",
                        "--audio-format",
                        audio_fmt,
                        "--audio-quality",
                        "0",
                        "-f",
                        "bestaudio/best",
                    ]
                )

        elif media_type == "video":
            if all_best:
                # All-defaults optimization: "-f best" picks a single muxed
                # stream (typically ≤720p on YouTube since 1080p+ are DASH),
                # avoiding a separate ffmpeg merge pass.
                cmd.extend(["-f", "best"])
            else:
                format_parts = [
                    f"[height<={int(quality)}]" if quality != "best" else "",
                    f"[vcodec*={selector_codec}]" if selector_codec != "best" else "",
                ]
                video_format_filter = "".join(p for p in format_parts if p)
                format_string = f"bestvideo*{video_format_filter}+bestaudio/best"
                cmd.extend(["-f", format_string])
                if video_fmt != "best":
                    cmd.extend(["--merge-output-format", video_fmt])

        elif media_type == "video_only":
            if all_best:
                cmd.extend(["-f", "bestvideo"])
            else:
                format_parts = [
                    f"[height<={int(quality)}]" if quality != "best" else "",
                    f"[vcodec*={selector_codec}]" if selector_codec != "best" else "",
                ]
                video_format_filter = "".join(p for p in format_parts if p)
                format_string = f"bestvideo{video_format_filter}/best"
                cmd.extend(["-f", format_string])
                if video_fmt != "best":
                    cmd.extend(["--recode-video", video_fmt])

        elif media_type == "subtitles":
            cmd.extend(["--skip-download"])

        # SponsorBlock Integration
        sb_categories = self.get_selected_sb_categories()

        if sb_categories and site_supports_sponsorblock(
            self.video_state.get("site", DEFAULT_SITE)
        ):
            # SponsorBlock chapters only exist on YouTube
            cmd.extend(["--sponsorblock-mark", "all"])

        # Strip embedded broadcast captions (EIA-608 carried in H.264 SEI
        # "User Data Registered ITU-T T.35" NALs) from video downloads.
        # Rumble passes US broadcast feeds through, so the MP4 carries a
        # hidden CC track that some players (IINA) surface as a second
        # subtitle track; the app's own SRT files are the intended
        # subtitles. Removing SEI NAL type 6 is lossless (-c copy semantics
        # via the ffmpeg bitstream filter) for H.264 - but the numbering is
        # codec-specific: in AV1 an OBU of type 6 is a Frame OBU, so
        # applying this unconditionally deleted actual picture data and
        # corrupted AV1 downloads. Hence the per-site "strip_embedded_cc"
        # profile flag (True only for Rumble). Only relevant for formats
        # that contain video; audio-only downloads are unaffected anyway.
        if SUPPORTED_SITES.get(
            self.video_state.get("site", DEFAULT_SITE), {}
        ).get("strip_embedded_cc", False):
            cc_strip_args = "-bsf:v filter_units=remove_types=6"
            cmd.extend(["--postprocessor-args", f"Merger:{cc_strip_args}"])
            cmd.extend(["--postprocessor-args", f"FixupM3u8:{cc_strip_args}"])

        # Final parameters - use helper method
        cmd.extend(["--add-metadata"])
        cmd.extend(["-o", self.get_filename_template()])
        cmd.extend(["--no-playlist", "-N", "8"])
        cmd.extend(["--write-info-json"])
        # Subtitles Integration
        # Subtitles are only downloaded when the Type "Subtitles" checkbox is
        # checked (wants_subtitles). Language checkboxes alone are not enough.
        if self.video_state.get("wants_subtitles", False) and selected_langs:
            lang_arg = self._subtitle_lang_patterns(selected_langs)
            cmd.extend(
                [
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    lang_arg,
                    "--sub-format",
                    "json3",
                ]
            )

        # cmd.extend(["--extractor-args", "youtube:player_client=default,ios"])
        cmd.append(url)
        return cmd

    def describe_format_choice(self, cmd, media_type):
        """One line naming what this download actually asks yt-dlp for.

        Read out of the command that is about to run, never out of video_state
        alone, so it cannot drift from the real request. It exists because the
        media type silently decides whether audio is fetched at all - "Video
        only" asks for bestvideo*/best, with no audio track - and nothing else
        on screen says which of the two was chosen.
        """
        labels = {
            "video": "Video + Audio",
            "video_only": "Video only",
            "audio": "Audio only",
            "subtitles": "Subtitles only",
        }
        parts = [labels.get(media_type, str(media_type))]
        if "-x" in cmd:
            parts.append(f"extract {cmd[cmd.index('--audio-format') + 1]}")
        elif "-f" in cmd:
            parts.append(cmd[cmd.index("-f") + 1])
        else:
            parts.append("no media download")
        return "▶ " + " · ".join(parts)
