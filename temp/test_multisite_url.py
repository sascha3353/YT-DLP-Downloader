# Unit tests for multi-site URL support (YouTube + Rumble proof-of-concept).
# Run from the repo root:  python3 -m unittest temp.test_multisite_url -v

import os
import threading
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import main as app
from ytdl.command import CommandMixin


class TestDetectSite(unittest.TestCase):
    def test_rumble_domains(self):
        self.assertEqual(app.detect_site("https://rumble.com/v6abcde-title.html"), "rumble")
        self.assertEqual(app.detect_site("https://www.rumble.com/v6abcde"), "rumble")
        self.assertEqual(app.detect_site("https://rumble.com/embed/v6abcde/"), "rumble")
        self.assertEqual(app.detect_site("RUMBLE.COM/V6ABCDE"), "rumble")

    def test_youtube_domains(self):
        self.assertEqual(app.detect_site("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "youtube")
        self.assertEqual(app.detect_site("https://youtu.be/dQw4w9WgXcQ"), "youtube")
        self.assertEqual(app.detect_site("https://m.youtube.com/watch?v=x"), "youtube")

    def test_odysee_domains(self):
        self.assertEqual(app.detect_site("https://odysee.com/@Mantega:1/First-day-LBRY:1"), "odysee")
        self.assertEqual(app.detect_site("https://www.odysee.com/@Mantega:1/First-day-LBRY:1"), "odysee")
        self.assertEqual(app.detect_site("https://lbry.tv/@Mantega:1/First-day-LBRY:1"), "odysee")
        self.assertEqual(
            app.detect_site("https://odysee.com/$/embed/@Mantega:1/First-day-LBRY:1"), "odysee"
        )

    def test_unknown_domain_defaults_to_youtube(self):
        # Historical behavior: unknown domains are treated as YouTube
        self.assertEqual(app.detect_site("https://example.com/video"), app.DEFAULT_SITE)
        self.assertEqual(app.detect_site(""), app.DEFAULT_SITE)
        self.assertEqual(app.detect_site("not a url"), app.DEFAULT_SITE)


class TestRumbleIdRegex(unittest.TestCase):
    def test_slug_url(self):
        m = app.RUMBLE_ID_REGEX.search("https://rumble.com/v6abcde-some-video-title.html")
        self.assertEqual(m.group(1), "v6abcde")

    def test_short_url(self):
        m = app.RUMBLE_ID_REGEX.search("https://rumble.com/v6abcde")
        self.assertEqual(m.group(1), "v6abcde")

    def test_embed_url(self):
        m = app.RUMBLE_ID_REGEX.search("https://rumble.com/embed/v6abcde/")
        self.assertEqual(m.group(1), "v6abcde")

    def test_with_query_and_trailing_punct(self):
        m = app.RUMBLE_ID_REGEX.search("https://rumble.com/v6abcde-x.html?utm=x")
        self.assertEqual(m.group(1), "v6abcde")
        m = app.RUMBLE_ID_REGEX.search("https://rumble.com/v6abcde-x.html.")
        self.assertEqual(m.group(1), "v6abcde")


class TestNormalizeUrlRumble(unittest.TestCase):
    def test_plain_url_unchanged(self):
        self.assertEqual(
            app.normalize_url("https://rumble.com/v6abcde-some-video.html"),
            "https://rumble.com/v6abcde-some-video.html",
        )

    def test_extract_from_surrounding_text(self):
        self.assertEqual(
            app.normalize_url("check this out https://rumble.com/v6abcde-title.html great"),
            "https://rumble.com/v6abcde-title.html",
        )

    def test_schemeless_token_gets_https(self):
        self.assertEqual(
            app.normalize_url("www.rumble.com/v6abcde.html"),
            "https://www.rumble.com/v6abcde.html",
        )
        self.assertEqual(app.normalize_url("rumble.com/v6abcde"), "https://rumble.com/v6abcde")

    def test_lookalike_domains_are_not_canonicalized(self):
        # A supported domain name embedded in a longer hostname must not be
        # rewritten, including when the path contains a YouTube-looking ID.
        for url in (
            "evilrumble.com/v6abcde-title.html",
            "notyoutube.com/watch?v=abcdefghijk",
            "https://evilrumble.com/v6abcde-title.html",
            "https://www.youtube.com.evil.example/watch?v=abcdefghijk",
            "https://evil.example/?youtube.com/watch?v=abcdefghijk",
        ):
            with self.subTest(url=url):
                self.assertEqual(app.normalize_url(url), url)

    def test_trailing_punctuation_stripped(self):
        self.assertEqual(
            app.normalize_url("https://rumble.com/v6abcde-title.html."),
            "https://rumble.com/v6abcde-title.html",
        )


class TestNormalizeUrlYoutubeRegressions(unittest.TestCase):
    """Ensure existing YouTube behavior is unchanged."""

    def test_watch_url_strips_extra_params(self):
        self.assertEqual(
            app.normalize_url("https://www.youtube.com/watch?v=abc12345678&list=xyz"),
            "https://www.youtube.com/watch?v=abc12345678",
        )

    def test_youtu_be_canonicalized(self):
        self.assertEqual(
            app.normalize_url("https://youtu.be/abc12345678"),
            "https://www.youtube.com/watch?v=abc12345678",
        )

    def test_naked_id(self):
        self.assertEqual(
            app.normalize_url("abc12345678"),
            "https://www.youtube.com/watch?v=abc12345678",
        )

    def test_empty(self):
        self.assertEqual(app.normalize_url(""), "")

    def test_youtube_id_overlong_not_canonicalized(self):
        # An ID longer than 11 chars must NOT be silently truncated;
        # the URL should pass through unchanged (the info-fetch gate rejects it later)
        url = "https://www.youtube.com/watch?v=abcdefghijkEXTRA"
        result = app.normalize_url(url)
        self.assertNotEqual(result, "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertIn("abcdefghijkEXTRA", result)

    def test_youtube_id_exactly_11_canonicalized(self):
        self.assertEqual(
            app.normalize_url("https://www.youtube.com/watch?v=abc12345678"),
            "https://www.youtube.com/watch?v=abc12345678",
        )


class TestNormalizeUrlOdysee(unittest.TestCase):
    """Odysee URLs keep their ':'/'$'/'#' syntax untouched."""

    def test_full_url_unchanged(self):
        url = "https://odysee.com/@Mantega:1/First-day-LBRY:1"
        self.assertEqual(app.normalize_url(url), url)

    def test_embed_url_unchanged(self):
        url = "https://odysee.com/$/embed/@Mantega:1/First-day-LBRY:1"
        self.assertEqual(app.normalize_url(url), url)

    def test_schemeless_token_gets_https(self):
        self.assertEqual(
            app.normalize_url("odysee.com/@Mantega:1/First-day-LBRY:1"),
            "https://odysee.com/@Mantega:1/First-day-LBRY:1",
        )
        self.assertEqual(
            app.normalize_url("lbry.tv/@Mantega:1/First-day-LBRY:1"),
            "https://lbry.tv/@Mantega:1/First-day-LBRY:1",
        )

    def test_extracted_from_surrounding_text(self):
        self.assertEqual(
            app.normalize_url("watch this https://odysee.com/@Mantega:1/First-day-LBRY:1 nice"),
            "https://odysee.com/@Mantega:1/First-day-LBRY:1",
        )

    def test_trailing_punctuation_stripped(self):
        self.assertEqual(
            app.normalize_url("https://odysee.com/@Mantega:1/First-day-LBRY:1."),
            "https://odysee.com/@Mantega:1/First-day-LBRY:1",
        )

    def test_query_params_kept(self):
        # Unlike YouTube ("&list=" stripping), non-YouTube URLs keep params
        url = "https://odysee.com/@Mantega:1/First-day-LBRY:1?src=home"
        self.assertEqual(app.normalize_url(url), url)


class TestSiteProfiles(unittest.TestCase):
    def test_sponsorblock_flags(self):
        self.assertTrue(app.SUPPORTED_SITES["youtube"]["sponsorblock"])
        self.assertFalse(app.SUPPORTED_SITES["rumble"]["sponsorblock"])
        self.assertFalse(app.SUPPORTED_SITES["ard"]["sponsorblock"])
        self.assertFalse(app.SUPPORTED_SITES["zdf"]["sponsorblock"])

    def test_site_supports_sponsorblock_is_the_single_gate(self):
        # One helper gates all four touch points: the info-fetch API query,
        # --sponsorblock-mark, the ".edl only" writer and the download path's
        # EDL sidecar. Unknown sites stay off — SponsorBlock is YouTube-only.
        self.assertTrue(app.site_supports_sponsorblock("youtube"))
        self.assertFalse(app.site_supports_sponsorblock("rumble"))
        self.assertFalse(app.site_supports_sponsorblock("odysee"))
        self.assertFalse(app.site_supports_sponsorblock("ard"))
        self.assertFalse(app.site_supports_sponsorblock("zdf"))
        self.assertFalse(app.site_supports_sponsorblock("some-future-site"))
        self.assertFalse(app.site_supports_sponsorblock(""))

    def test_id_regexes_wired(self):
        self.assertIs(app.SUPPORTED_SITES["youtube"]["id_regex"], app.YOUTUBE_ID_REGEX)
        self.assertIs(app.SUPPORTED_SITES["rumble"]["id_regex"], app.RUMBLE_ID_REGEX)
        self.assertIs(app.SUPPORTED_SITES["ard"]["id_regex"], app.ARD_ID_REGEX)
        self.assertIs(app.SUPPORTED_SITES["zdf"]["id_regex"], app.ZDF_ID_REGEX)

    def test_js_runtime_flags(self):
        self.assertTrue(app.SUPPORTED_SITES["youtube"]["js_runtime"])
        self.assertFalse(app.SUPPORTED_SITES["rumble"]["js_runtime"])
        self.assertFalse(app.SUPPORTED_SITES["ard"]["js_runtime"])
        self.assertFalse(app.SUPPORTED_SITES["zdf"]["js_runtime"])
        # Unknown/future sites keep the historical always-pass behavior
        self.assertTrue(app.site_wants_js_runtime("some-future-site"))
        self.assertTrue(app.site_wants_js_runtime(""))

    def test_info_timeouts(self):
        # YouTube/Rumble/ARD/ZDF keep the 15s default; Odysee's LBRY API
        # resolve can take ~40s, so it overrides the budget (reported bug)
        self.assertEqual(app.site_info_timeout("youtube"), app.INFO_FETCH_TIMEOUT_SECONDS)
        self.assertEqual(app.site_info_timeout("rumble"), app.INFO_FETCH_TIMEOUT_SECONDS)
        self.assertEqual(app.site_info_timeout("ard"), app.INFO_FETCH_TIMEOUT_SECONDS)
        self.assertEqual(app.site_info_timeout("zdf"), app.INFO_FETCH_TIMEOUT_SECONDS)
        self.assertEqual(app.site_info_timeout("odysee"), 90)
        self.assertGreater(app.site_info_timeout("odysee"), app.INFO_FETCH_TIMEOUT_SECONDS)
        self.assertEqual(app.site_info_timeout("some-future-site"), app.INFO_FETCH_TIMEOUT_SECONDS)

    def test_slow_hints(self):
        # Only Odysee explains its slow resolve; other sites fall back to the
        # generic "responding slowly" wording (see _info_wait_hint_text)
        hint = app.site_slow_hint("odysee")
        self.assertIn("LBRY", hint)
        self.assertIn("70s", hint)
        self.assertEqual(app.site_slow_hint("youtube"), "")
        self.assertEqual(app.site_slow_hint("rumble"), "")
        self.assertEqual(app.site_slow_hint("some-future-site"), "")

    def test_is_known_site_gate(self):
        # Only hostnames with a SUPPORTED_SITES profile pass the gate
        self.assertTrue(app.is_known_site("https://www.youtube.com/watch?v=x"))
        self.assertTrue(app.is_known_site("https://youtu.be/dQw4w9WgXcQ"))
        self.assertTrue(app.is_known_site("https://m.youtube.com/watch?v=x"))
        self.assertTrue(app.is_known_site("https://rumble.com/v6abcde-x.html"))
        self.assertTrue(app.is_known_site("www.rumble.com/v6abcde"))
        self.assertTrue(app.is_known_site("https://odysee.com/@Mantega:1/First-day-LBRY:1"))
        self.assertTrue(app.is_known_site("https://lbry.tv/@Mantega:1/First-day-LBRY:1"))
        self.assertTrue(app.is_known_site("https://www.ardmediathek.de/video/some/Y3JpZDox"))
        self.assertTrue(app.is_known_site("https://www.zdf.de/video/talk/x-100"))
        self.assertTrue(app.is_known_site("https://www.zdfheute.de/news/x.html"))
        # Unknown / look-alike domains must NOT pass
        self.assertFalse(app.is_known_site("https://www.google.com/"))
        self.assertFalse(app.is_known_site("https://vimeo.com/12345"))
        self.assertFalse(app.is_known_site("https://notrumble.com/v1.html"))
        self.assertFalse(app.is_known_site("https://odysee.com.evil.example/v1"))
        self.assertFalse(app.is_known_site("https://example.com/video"))
        self.assertFalse(app.is_known_site(""))
        self.assertFalse(app.is_known_site("nonsense"))

    def test_detect_site_still_falls_back(self):
        # Profile lookup keeps the historical fallback (used for behavior
        # flags); only the input gate is strict
        self.assertEqual(app.detect_site("https://example.com/video"), app.DEFAULT_SITE)


class TestChannelUrlGate(unittest.TestCase):
    """Channel/playlist URLs must be rejected for every site."""

    def test_youtube_channel_and_playlist_urls_rejected(self):
        for url in (
            "https://www.youtube.com/@SomeHandle",
            "https://www.youtube.com/@SomeHandle/videos",
            "https://www.youtube.com/c/SomeChannel",
            "https://www.youtube.com/user/SomeUser",
            "https://www.youtube.com/channel/UCabc123",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/watch?list=PL123",
            "https://www.youtube.com/watch?foo=bar&list=PL123",
            "https://www.youtube.com/watch?list=PL123&foo=bar",
            "www.youtube.com/watch?list=PL123",
            "https://www.youtube.com/feed/subscriptions",
            "https://www.youtube.com/",
        ):
            self.assertTrue(app.is_channel_url(url), url)

    def test_youtube_watch_playlist_survives_normalization(self):
        for url in (
            "https://www.youtube.com/watch?list=PL123",
            "https://www.youtube.com/watch?foo=bar&list=PL123",
            "https://www.youtube.com/watch?list=PL123&foo=bar",
            "www.youtube.com/watch?list=PL123",
        ):
            with self.subTest(url=url):
                normalized = app.normalize_url(url)
                self.assertTrue(app.is_channel_url(normalized), normalized)

    def test_youtube_video_urls_pass(self):
        for url in (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
        ):
            self.assertFalse(app.is_channel_url(url), url)

    def test_rumble_channel_urls_rejected(self):
        for url in (
            "https://rumble.com/c/SomeChannel",
            "https://rumble.com/user/SomeUser",
            "https://rumble.com/",
        ):
            self.assertTrue(app.is_channel_url(url), url)
        self.assertFalse(app.is_channel_url("https://rumble.com/v6abcde-title.html"))

    def test_odysee_channel_urls_rejected(self):
        for url in (
            "https://odysee.com/@Mantega:1",
            "https://odysee.com/@Mantega:1/",
            "https://odysee.com/$/embed/@Mantega:1",
            "https://odysee.com/",
        ):
            self.assertTrue(app.is_channel_url(url), url)

    def test_odysee_video_urls_pass(self):
        for url in (
            "https://odysee.com/@Mantega:1/First-day-LBRY:1",
            "https://odysee.com/First-day-LBRY:17f983b61f53091fb8ea58a9c56804e4ff8cff4d",
            "https://odysee.com/$/embed/@Mantega:1/First-day-LBRY:1",
            "https://lbry.tv/@Mantega:1/First-day-LBRY:1",
        ):
            self.assertFalse(app.is_channel_url(url), url)

    def test_ard_channel_urls_rejected(self):
        for url in (
            "https://www.ardmediathek.de/sendung/tagesthemen/Y3JpZDovdGVzdA",
            "https://www.ardmediathek.de/serie/babylon-berlin/staffel-4/Y3JpZDovdGVzdA",
            "https://www.ardmediathek.de/sammlung/dokus/Y3JpZDovdGVzdA",
            "https://www.ardmediathek.de/",
        ):
            self.assertTrue(app.is_channel_url(url), url)

    def test_ard_video_urls_pass(self):
        for url in (
            "https://www.ardmediathek.de/video/tagesschau-15-00-uhr-22-09-2026/das-erste/Y3JpZDox",
            "https://www.ardmediathek.de/live/tagesschau/das-erste/Y3JpZDox",
            "https://beta.ardmediathek.de/video/some-episode/Y3JpZDox",
        ):
            self.assertFalse(app.is_channel_url(url), url)

    def test_zdf_show_urls_rejected(self):
        # ZDFChannelIE is a catch-all playlist for every zdf.de path that is
        # not a video page - the observed show page resolves to 30 entries
        for url in (
            "https://www.zdf.de/magazine/heute-journal-104",
            "https://www.zdf.de/",
        ):
            self.assertTrue(app.is_channel_url(url), url)

    def test_zdf_video_urls_pass(self):
        for url in (
            "https://www.zdf.de/video/talk/markus-lanz-114/markus-lanz-vom-22-september-2026-100",
            "https://www.zdf.de/play/series/some-show-100",
            # Legacy single-video pages end in .html (incl. sister sites)
            "https://www.zdf.de/dokumentation/terra-x/terra-x-history-100.html",
            "https://www.zdfheute.de/nachrichten/jahresrueckblick-2025.html",
        ):
            self.assertFalse(app.is_channel_url(url), url)

    def test_unknown_domain_is_not_a_channel_url(self):
        # Domain gating happens before the channel check; unrelated URLs
        # report False here and are rejected by is_known_site instead
        self.assertFalse(app.is_channel_url("https://www.google.com/@handle"))


class TestOdyseeIdRegex(unittest.TestCase):
    def test_claim_ids(self):
        m = app.ODYSEE_ID_REGEX.search("https://odysee.com/@Mantega:1/First-day-LBRY:17f983b61f53091fb8ea58a9c56804e4ff8cff4d")
        self.assertEqual(m.group(1), "17f983b61f53091fb8ea58a9c56804e4ff8cff4d")
        m = app.ODYSEE_ID_REGEX.search("https://lbry.tv/@LBRYFoundation:0/Episode-1:e")
        self.assertEqual(m.group(1), "e")

    def test_resync_auto_subs_flags(self):
        self.assertTrue(app.SUPPORTED_SITES["youtube"]["resync_auto_subs"])
        self.assertFalse(app.SUPPORTED_SITES["rumble"]["resync_auto_subs"])
        self.assertFalse(app.SUPPORTED_SITES["ard"]["resync_auto_subs"])
        self.assertFalse(app.SUPPORTED_SITES["zdf"]["resync_auto_subs"])
        # Unknown/future sites keep the historical always-merge behavior
        self.assertTrue(app.site_resyncs_auto_subs("some-future-site"))
        self.assertTrue(app.site_resyncs_auto_subs(""))

    def test_supported_sites_label(self):
        # Info-panel header line: "Supported: <labels...>". ARD and ZDF share
        # one entry there - two profiles, one broadcaster family.
        self.assertEqual(
            app.SUPPORTED_SITES_LABEL,
            "YouTube, Rumble, Odysee, ARD/ZDF Mediathek",
        )

    def test_short_label_does_not_leak_into_per_site_messages(self):
        # The shortening is for the list only. A message about one ZDF URL has
        # to name ZDF, not "ARD/ZDF".
        self.assertEqual(app.SUPPORTED_SITES["ard"]["label"], "ARD Mediathek")
        self.assertEqual(app.SUPPORTED_SITES["zdf"]["label"], "ZDF Mediathek")
        for key in ("youtube", "rumble", "odysee"):
            self.assertNotIn("short_label", app.SUPPORTED_SITES[key])

    def test_header_shows_supported_sites(self):
        # Both header call sites (init_ui in ytdl/ui_build.py + clear_output
        # in ytdl/app.py) must show the supported-sites line, otherwise it
        # vanishes on first info fetch
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        count = 0
        for rel in ("ytdl/app.py", "ytdl/ui_build.py"):
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                count += fh.read().count('"Supported: "')
        self.assertEqual(count, 2)


class TestFormatChoiceLine(unittest.TestCase):
    """The panel must name what a download actually asks for.

    Nothing else on screen says whether audio is included: "Video only" and
    "Video + Audio" sit side by side and produce files that differ only in
    whether a viewer can hear anything.
    """

    class Harness(app.DownloadMixin, app.SubtitleMixin, CommandMixin):
        get_selected_sb_categories = app.YTDLPDownloaderGUI.get_selected_sb_categories

        def __init__(self, media_type):
            self.video_state = {
                "media_type": media_type, "quality": "best", "video_format": "best",
                "audio_format": "best", "video_codec": "best",
                "base_filename": "test_video", "site": "youtube",
            }
            self.yt_dlp_bin, self.ffmpeg_bin, self.deno_bin = "yt-dlp", None, None
            self.title_entry = SimpleNamespace(text=lambda: "Test Title")
            self.sb_all_checkbox = SimpleNamespace(isChecked=lambda: False)
            self.sb_checkbox_map = {}

        get_clean_url = lambda self: "https://www.youtube.com/watch?v=x"  # noqa: E731
        get_output_dir = lambda self: "/tmp/dl"   # noqa: E731
        update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def _line(self, media_type):
        h = self.Harness(media_type)
        cmd = h.build_command(selected_langs=None)
        return h.describe_format_choice(cmd, media_type)

    def test_video_mode_says_audio_is_included(self):
        line = self._line("video")
        self.assertIn("Video + Audio", line)
        # With all defaults, uses simple "best" selector
        self.assertIn("best", line)

    def test_video_only_mode_says_so(self):
        line = self._line("video_only")
        self.assertIn("Video only", line)
        # With all defaults, uses "bestvideo"
        self.assertIn("bestvideo", line)
        self.assertNotIn("bestaudio", line)

    def test_audio_only_mode_names_the_container(self):
        h = self.Harness("audio")
        h.video_state["audio_format"] = "m4a"
        cmd = h.build_command(selected_langs=None)
        line = h.describe_format_choice(cmd, "audio")
        self.assertIn("Audio only", line)
        self.assertIn("m4a", line)

    def test_subtitles_only_says_no_media(self):
        line = self._line("subtitles")
        self.assertIn("Subtitles only", line)
        self.assertIn("no media download", line)

    def test_the_line_quotes_the_real_command(self):
        # It must be read out of the command, not rebuilt from state, or it
        # can describe a request that was never made.
        h = self.Harness("video")
        cmd = h.build_command(selected_langs=None)
        self.assertIn(cmd[cmd.index("-f") + 1], h.describe_format_choice(cmd, "video"))


class TestBuildCommandAudio(unittest.TestCase):
    """Pin the audio postprocessor flags produced by build_command()."""

    class Harness(app.DownloadMixin, app.SubtitleMixin, CommandMixin):
        # Inherits the real mixins (so new helper methods come along);
        # only GUI state/overrides are provided here.
        # build_command() reads the SponsorBlock selection through the UI
        # helper (single source of truth), so bind the real one here.
        get_selected_sb_categories = app.YTDLPDownloaderGUI.get_selected_sb_categories
        HARNESS_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        def __init__(self, media_type="audio", audio_fmt="best"):
            self.video_state = {
                "media_type": media_type,
                "quality": "best",
                "video_format": "best",
                "audio_format": audio_fmt,
                "video_codec": "best",
                "base_filename": "test_video",
                "site": "youtube",
            }
            self.yt_dlp_bin = "yt-dlp"
            self.ffmpeg_bin = None   # disables --ffmpeg-location branch
            self.deno_bin = None     # disables --js-runtimes branch
            self.title_entry = SimpleNamespace(text=lambda: "Test Title")
            self.sb_all_checkbox = SimpleNamespace(isChecked=lambda: False)
            self.sb_checkbox_map = {}

        get_clean_url = lambda self: self.HARNESS_URL  # noqa: E731
        get_output_dir = lambda self: "/tmp/dl"   # noqa: E731
        update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def _cmd(self, **kwargs):
        return self.Harness(**kwargs).build_command(selected_langs=None)

    def test_m4a_option_passes_m4a_not_aac(self):
        cmd = self._cmd(audio_fmt="m4a")
        # The GUI "M4A" option must request yt-dlp's real M4A container
        # (lossless stream-copy for AAC sources), not the ADTS "aac" target
        # that used to produce ADTS bytes inside a ".m4a"-named file.
        self.assertIn("-x", cmd)
        idx = cmd.index("--audio-format")
        self.assertEqual(cmd[idx + 1], "m4a")
        self.assertNotIn("aac", cmd)

    def test_mp3_option_passthrough(self):
        cmd = self._cmd(audio_fmt="mp3")
        idx = cmd.index("--audio-format")
        self.assertEqual(cmd[idx + 1], "mp3")

    def test_best_audio_option_uses_selector(self):
        cmd = self._cmd(audio_fmt="best")
        self.assertIn("-f", cmd)
        self.assertEqual(cmd[cmd.index("-f") + 1], "bestaudio/best")
        self.assertNotIn("-x", cmd)

    def test_audio_best_extracts_on_sites_without_audio_only_streams(self):
        # Odysee has no audio-only formats: "Best" audio must still extract
        # (-x), otherwise the muxed source MP4 would be saved as-is
        h = self.Harness(audio_fmt="best")
        h.video_state["site"] = "odysee"
        cmd = h.build_command(selected_langs=None)
        self.assertIn("-x", cmd)
        idx = cmd.index("--audio-format")
        self.assertEqual(cmd[idx + 1], "best")
        self.assertNotIn("-f", cmd)

    def test_audio_best_extracts_on_zdf(self):
        # ZDF formats are all muxed (no audio-only stream): "Best" audio must
        # -x like Odysee, otherwise the source video file would be saved
        h = self.Harness(audio_fmt="best")
        h.video_state["site"] = "zdf"
        cmd = h.build_command(selected_langs=None)
        self.assertIn("-x", cmd)
        idx = cmd.index("--audio-format")
        self.assertEqual(cmd[idx + 1], "best")
        self.assertNotIn("-f", cmd)

    def test_audio_best_selector_kept_for_youtube(self):
        h = self.Harness(audio_fmt="best")
        cmd = h.build_command(selected_langs=None)
        self.assertIn("-f", cmd)
        self.assertNotIn("-x", cmd)

    def test_video_mode_uses_multiplexed_selector(self):
        # When all defaults (quality=best, video_codec=best, audio_format=best):
        # use simple "best" (single muxed stream, no merge needed).
        cmd = self._cmd(media_type="video")
        idx = cmd.index("-f")
        self.assertEqual(cmd[idx + 1], "best")

    def test_video_mode_quality_filter_applies(self):
        # When quality is specified (not "best"), use the full selector
        # with bestvideo* so muxed HLS formats (Rumble) are eligible.
        h = self.Harness(media_type="video")
        h.video_state["quality"] = "480"
        cmd = h.build_command(selected_langs=None)
        idx = cmd.index("-f")
        self.assertEqual(cmd[idx + 1], "bestvideo*[height<=480]+bestaudio/best")

    def test_video_mode_codec_filter_applies(self):
        # When video_codec is specified (not "best"), use the full selector
        h = self.Harness(media_type="video")
        h.video_state["video_codec"] = "av1"
        cmd = h.build_command(selected_langs=None)
        idx = cmd.index("-f")
        self.assertEqual(cmd[idx + 1], "bestvideo*[vcodec*=av01]+bestaudio/best")

    def test_video_mode_includes_audio_and_video_only_does_not(self):
        # The trap behind "the app downloaded no audio": the media type alone
        # decides it, and the two formats differ only by "+bestaudio".
        # For all-best case, video uses "best" (includes audio), video_only uses "bestvideo".
        with_audio = self._cmd(media_type="video")
        without = self._cmd(media_type="video_only")
        self.assertEqual(with_audio[with_audio.index("-f") + 1], "best")
        self.assertEqual(without[without.index("-f") + 1], "bestvideo")

    def test_deno_passed_for_youtube(self):
        h = self.Harness(audio_fmt="m4a")
        h.deno_bin = "deno"
        with mock.patch.object(app.shutil, "which", return_value="/usr/bin/deno"):
            cmd = h.build_command(selected_langs=None)
        self.assertIn("--js-runtimes", cmd)

    def test_deno_skipped_for_rumble(self):
        h = self.Harness(audio_fmt="m4a")
        h.deno_bin = "deno"
        h.video_state["site"] = "rumble"
        with mock.patch.object(app.shutil, "which", return_value="/usr/bin/deno"):
            cmd = h.build_command(selected_langs=None)
        self.assertNotIn("--js-runtimes", cmd)

    def test_deno_skipped_for_ard_and_zdf(self):
        for site in ("ard", "zdf"):
            h = self.Harness(audio_fmt="m4a")
            h.deno_bin = "deno"
            h.video_state["site"] = site
            with mock.patch.object(app.shutil, "which", return_value="/usr/bin/deno"):
                cmd = h.build_command(selected_langs=None)
            self.assertNotIn("--js-runtimes", cmd, site)

    def test_sub_langs_cover_rumble_keys(self):
        h = self.Harness(audio_fmt="best")
        h.video_state["wants_subtitles"] = True
        cmd = h.build_command(selected_langs=["en"])
        idx = cmd.index("--sub-langs")
        self.assertEqual(cmd[idx + 1], "en,a.en,en-auto,a.en-auto")

    def test_sub_langs_cover_german_mediathek_deu_keys(self):
        # ARD/ZDF key German subtitles "deu" (ISO 639-2); --sub-langs must
        # include that shape, "de" alone would fullmatch nothing there.
        h = self.Harness(audio_fmt="best")
        h.video_state["wants_subtitles"] = True
        cmd = h.build_command(selected_langs=["de"])
        idx = cmd.index("--sub-langs")
        self.assertEqual(
            cmd[idx + 1],
            "de,a.de,de-auto,a.de-auto,deu,a.deu,deu-auto,a.deu-auto,"
            "ger,a.ger,ger-auto,a.ger-auto",
        )

    def test_embedded_cc_strip_flag_per_site(self):
        # The H.264 SEI (unit type 6) strip must only be enabled where the
        # streams are H.264 broadcast feeds: in AV1 an OBU of type 6 is a
        # Frame OBU, so the previous unconditional filter corrupted AV1
        # downloads (picture data silently deleted during stream copy).
        flags = {
            site: profile.get("strip_embedded_cc", False)
            for site, profile in app.SUPPORTED_SITES.items()
        }
        self.assertEqual(
            flags,
            {
                "youtube": False,
                "rumble": True,
                "odysee": False,
                "ard": False,
                "zdf": False,
            },
        )

    def test_embedded_cc_strip_args_gated_by_site_video(self):
        # YouTube (the default harness site): no bitstream filter at all
        cmd = self._cmd(media_type="video")
        self.assertNotIn("--postprocessor-args", cmd)
        # Rumble: the EIA-608 strip must stay for its broadcast feeds
        h = self.Harness(media_type="video")
        h.video_state["site"] = "rumble"
        cmd = h.build_command(selected_langs=None)
        pp_args = [
            cmd[i + 1] for i, flag in enumerate(cmd) if flag == "--postprocessor-args"
        ]
        joined = "|".join(pp_args)
        self.assertIn("Merger:-bsf:v filter_units=remove_types=6", joined)
        self.assertIn("FixupM3u8:-bsf:v filter_units=remove_types=6", joined)

    def test_embedded_cc_strip_args_gated_by_site_audio_too(self):
        # Audio mode (-x --audio-format ...) goes through the same command
        # builder; its postprocessor-args must follow the site gate too
        # (they used to be unconditional there as well).
        cmd = self._cmd(audio_fmt="m4a")
        self.assertNotIn("--postprocessor-args", cmd)
        h = self.Harness(audio_fmt="m4a")
        h.video_state["site"] = "rumble"
        cmd = h.build_command(selected_langs=None)
        self.assertIn("--postprocessor-args", cmd)


class TestPlausibleUrl(unittest.TestCase):
    """is_plausible_url keeps garbage out of the yt-dlp subprocess."""

    def test_garbage_rejected(self):
        self.assertFalse(app.is_plausible_url("nonsense"))
        self.assertFalse(app.is_plausible_url(""))
        self.assertFalse(app.is_plausible_url("   "))
        self.assertFalse(app.is_plausible_url("https://nonsense/watch"))
        # The reported bug: clipboard text copied from the info panel must
        # not pass (urlparse leniently parses its host as
        # "yt-dlp downloader 1.1.25", and the dots in the version made the
        # old dot-only check accept it).
        self.assertFalse(app.is_plausible_url("YT-DLP Downloader 1.1.25"))
        self.assertFalse(app.is_plausible_url("hello world"))
        self.assertFalse(app.is_plausible_url("foo bar.com"))

    def test_real_urls_accepted(self):
        self.assertTrue(app.is_plausible_url("https://rumble.com/v6abcde-x.html"))
        self.assertTrue(app.is_plausible_url("rumble.com/v6abcde"))
        self.assertTrue(app.is_plausible_url("https://www.youtube.com/watch?v=abc12345678"))
        self.assertTrue(app.is_plausible_url("https://example.com/video"))
        self.assertTrue(app.is_plausible_url("http://localhost:8080/video"))

    def test_wellformed_hostnames_accepted(self):
        # Hyphenated labels, multi-level subdomains and IP hosts are valid
        self.assertTrue(app.is_plausible_url("https://my-site.example.co.uk/v/1"))
        self.assertTrue(app.is_plausible_url("192.168.1.10/video"))

    def test_naked_youtube_id_accepted(self):
        self.assertTrue(app.is_plausible_url("abc12345678"))


class TestProcessAndSetUrl(unittest.TestCase):
    """The URL field must only ever receive valid URLs (paste + startup)."""

    class Harness:
        def __init__(self):
            self.url_texts = []
            self.url_entry = SimpleNamespace(setText=self.url_texts.append)
            self.signals = app.SignalEmitter()

        _process_and_set_url = app.YTDLPDownloaderGUI._process_and_set_url
        url_rejection_reason = app.YTDLPDownloaderGUI.url_rejection_reason

    def test_garbage_clipboard_never_reaches_url_field(self):
        h = self.Harness()
        self.assertFalse(h._process_and_set_url("YT-DLP Downloader 1.1.25", silent_mode=True))
        self.assertEqual(h.url_texts, [])
        # Loud mode (paste button) reports instead of filling the field
        self.assertFalse(h._process_and_set_url("YT-DLP Downloader 1.1.25", silent_mode=False))
        self.assertEqual(h.url_texts, [])

    def test_unsupported_domain_never_reaches_url_field(self):
        # Valid URL syntax, but no SUPPORTED_SITES profile -> rejected
        h = self.Harness()
        self.assertFalse(h._process_and_set_url("https://www.google.com/", silent_mode=True))
        self.assertEqual(h.url_texts, [])
        self.assertFalse(h._process_and_set_url("https://www.google.com/", silent_mode=False))
        self.assertEqual(h.url_texts, [])

    def test_valid_clipboard_fills_url_field(self):
        h = self.Harness()
        url = "https://rumble.com/v6abcde-some-title.html"
        self.assertTrue(h._process_and_set_url(url, silent_mode=True))
        self.assertEqual(h.url_texts, [url])


class TestReloadButton(unittest.TestCase):
    """Reload button next to the URL field re-triggers the info fetch."""

    def test_button_wired_in_ui(self):
        import inspect
        src = inspect.getsource(app.YTDLPDownloaderGUI.init_ui)
        self.assertIn("reload_button", src)
        self.assertIn("SP_BrowserReload", src)
        self.assertIn("on_reload_button_click", src)

    def test_reload_button_in_enable_list(self):
        # The button must be disabled together with the other primary
        # controls while a fetch/download is running (no double-spawn).
        import inspect
        src = inspect.getsource(app.YTDLPDownloaderGUI._set_ui_enabled_state)
        self.assertIn("self.reload_button", src)

    def test_subs_button_in_enable_list(self):
        # The Subs button (standalone SRT processing) must be disabled
        # together with the other primary controls while a download is
        # running — otherwise a user can open and reprocess an SRT file
        # that yt-dlp or the worker's subtitle post-processing is writing.
        import inspect
        src = inspect.getsource(app.YTDLPDownloaderGUI._set_ui_enabled_state)
        self.assertIn("self.subs_button", src)

    def test_click_triggers_fetch(self):
        calls = []

        class Harness:
            fetch_title_timer = SimpleNamespace(stop=lambda: None)
            fetch_video_info = lambda self: calls.append("fetch")  # noqa: E731

        harness = Harness()
        harness.on_reload_button_click = types.MethodType(
            app.YTDLPDownloaderGUI.on_reload_button_click, harness
        )
        harness.on_reload_button_click()
        self.assertEqual(calls, ["fetch"])

    def test_click_with_empty_url_delegates_to_fetch_gate(self):
        # With an empty URL the click must NOT spawn a worker; the gate in
        # fetch_video_info handles it (title message, early return).
        # on_reload_button_click must stop the timer and delegate to fetch_video_info
        import inspect
        src = inspect.getsource(app.YTDLPDownloaderGUI.on_reload_button_click)
        self.assertIn("fetch_title_timer.stop()", src)
        self.assertIn("self.fetch_video_info()", src)


class TestFetchVideoInfoPrecheck(unittest.TestCase):
    """fetch_video_info must reject garbage before spawning yt-dlp."""

    class Harness:
        def __init__(self):
            self.title_texts = []
            self.title_entry = SimpleNamespace(setText=self.title_texts.append)
            self.video_state = {"is_fetching_info": False}

        fetch_video_info = app.YTDLPDownloaderGUI.fetch_video_info
        url_rejection_reason = app.YTDLPDownloaderGUI.url_rejection_reason
        extract_video_id = app.YTDLPDownloaderGUI.extract_video_id

        def _emit(self, name, *args):
            getattr(self.signals, name).emit(*args)

    def test_nonsense_rejected_before_ytdlp(self):
        h = self.Harness()
        h.get_clean_url = lambda: "nonsense"
        h.fetch_video_info()
        self.assertEqual(
            h.title_texts,
            ["Please enter a valid video URL"],
            "garbage must be rejected by the plausibility gate, not sent to yt-dlp",
        )

    def test_unsupported_domain_rejected_before_ytdlp(self):
        # A syntactically valid URL from a domain without a SUPPORTED_SITES
        # profile must be rejected up front (reported google.com reaching
        # yt-dlp and failing with an "[generic]" error)
        h = self.Harness()
        h.get_clean_url = lambda: "https://www.google.com/"
        h.fetch_video_info()
        self.assertEqual(
            h.title_texts,
            [f"Unsupported site — supported: {app.SUPPORTED_SITES_LABEL}"],
        )

    def test_channel_url_rejected_before_ytdlp(self):
        h = self.Harness()
        h.get_clean_url = lambda: "https://www.youtube.com/@SomeHandle"
        h.fetch_video_info()
        self.assertEqual(
            h.title_texts,
            [
                "Channel/playlist URLs are not supported — "
                "please paste a link to a single video"
            ],
        )

    def test_odysee_video_url_passes_gate(self):
        h = self.Harness()
        h.get_clean_url = lambda: "https://odysee.com/@Mantega:1/First-day-LBRY:1"
        self.assertIsNone(h.url_rejection_reason("https://odysee.com/@Mantega:1/First-day-LBRY:1"))

    def test_plausible_url_passes_the_gate(self):
        h = self.Harness()
        rumble_url = "https://rumble.com/v6abcde-some-title.html"
        h.get_clean_url = lambda: rumble_url
        # Stub the GUI helpers used after the gate passes
        h.set_download_button_status = lambda status: None
        h.download_button = SimpleNamespace(
            setText=lambda text: None, setEnabled=lambda enabled: None
        )
        h.video_state = {"url": "", "video_id": "", "site": ""}
        h._set_ui_enabled_state = lambda enabled: None
        h._reset_download_progress_bars = lambda: None
        h._set_download_busy = lambda busy: None
        h.signals = app.SignalEmitter()
        h.clearDockProgress = lambda: None
        h.clear_output = lambda: None
        # Replace the worker with a recorder so no subprocess is spawned
        h.fetched = []
        done = threading.Event()

        def fake_get_video_info(url):
            h.fetched.append(url)
            # The ID must already be there when the worker runs — that is
            # what check_sponsorblock() reads later in the same thread.
            h.video_id_in_worker = h.video_state.get("video_id")
            done.set()

        h.get_video_info = fake_get_video_info

        h.fetch_video_info()
        self.assertTrue(done.wait(timeout=5), "worker thread did not run")
        self.assertEqual(h.fetched, [rumble_url])
        # Regression: extract_video_id() was never called during an info
        # fetch, so SponsorBlock always reported "Could not extract video ID".
        self.assertEqual(h.video_state.get("video_id"), "v6abcde")
        self.assertEqual(h.video_id_in_worker, "v6abcde")


class TestGermanMediathekProfiles(unittest.TestCase):
    """ARD Mediathek and ZDF Mediathek profile wiring."""

    def test_detect_site(self):
        self.assertEqual(app.detect_site("https://www.ardmediathek.de/video/some/Y3JpZDox"), "ard")
        self.assertEqual(app.detect_site("https://www.zdf.de/video/talk/x-100"), "zdf")
        self.assertEqual(app.detect_site("https://www.zdfheute.de/news/x.html"), "zdf")
        self.assertEqual(app.detect_site("https://www.logo.de/kinder/x.html"), "zdf")

    def test_labels(self):
        self.assertEqual(app.SUPPORTED_SITES["ard"]["label"], "ARD Mediathek")
        self.assertEqual(app.SUPPORTED_SITES["zdf"]["label"], "ZDF Mediathek")

    def test_domains(self):
        self.assertIn("ardmediathek.de", app.SUPPORTED_SITES["ard"]["domains"])
        self.assertEqual(
            set(app.SUPPORTED_SITES["zdf"]["domains"]), {"zdf.de", "zdfheute.de", "logo.de"}
        )

    def test_behavior_flags(self):
        ard = app.SUPPORTED_SITES["ard"]
        zdf = app.SUPPORTED_SITES["zdf"]
        for profile in (ard, zdf):
            self.assertTrue(profile["supports_subtitles"])
            self.assertFalse(profile["sponsorblock"])
            self.assertFalse(profile["js_runtime"])
            self.assertFalse(profile["resync_auto_subs"])
        # ARD offers an audio-only HLS track, ZDF is muxed-only -> -x needed
        self.assertFalse(ard["always_extract_audio"])
        self.assertTrue(zdf["always_extract_audio"])


class TestGermanMediathekUrlGate(unittest.TestCase):
    """url_rejection_reason() end-to-end for the ARD and ZDF profiles."""

    class Harness:
        url_rejection_reason = app.YTDLPDownloaderGUI.url_rejection_reason

    CHANNEL_MSG = (
        "Channel/playlist URLs are not supported — "
        "please paste a link to a single video"
    )

    def test_ard_video_passes(self):
        h = self.Harness()
        self.assertIsNone(
            h.url_rejection_reason(
                "https://www.ardmediathek.de/video/tagesschau-15-00-uhr-22-09-2026/"
                "das-erste/Y3JpZDox"
            )
        )
        self.assertIsNone(
            h.url_rejection_reason(
                "https://www.ardmediathek.de/live/tagesschau/das-erste/Y3JpZDox"
            )
        )

    def test_ard_collection_rejected(self):
        h = self.Harness()
        self.assertEqual(
            h.url_rejection_reason(
                "https://www.ardmediathek.de/sendung/tagesthemen/Y3JpZDovdGVzdA"
            ),
            self.CHANNEL_MSG,
        )
        self.assertEqual(
            h.url_rejection_reason("https://www.ardmediathek.de/"), self.CHANNEL_MSG
        )

    def test_zdf_video_passes(self):
        h = self.Harness()
        self.assertIsNone(
            h.url_rejection_reason(
                "https://www.zdf.de/video/talk/markus-lanz-114/"
                "markus-lanz-vom-22-september-2026-100"
            )
        )
        self.assertIsNone(
            h.url_rejection_reason(
                "https://www.zdf.de/dokumentation/terra-x/terra-x-history-100.html"
            )
        )

    def test_zdf_show_page_rejected(self):
        h = self.Harness()
        self.assertEqual(
            h.url_rejection_reason("https://www.zdf.de/magazine/heute-journal-104"),
            self.CHANNEL_MSG,
        )

    def test_garbage_still_rejected(self):
        h = self.Harness()
        self.assertEqual(
            h.url_rejection_reason("nonsense"), "Please enter a valid video URL"
        )

    def test_unknown_site_still_rejected(self):
        h = self.Harness()
        self.assertEqual(
            h.url_rejection_reason("https://www.google.com/"),
            f"Unsupported site — supported: {app.SUPPORTED_SITES_LABEL}",
        )


class TestGermanMediathekIdRegex(unittest.TestCase):
    def test_ard_trailing_crid(self):
        crid = (
            "Y3JpZDovL3RhZ2Vzc2NoYXUuZGUvNmNlNTNhNjAtN2YyNi00Njc5LWIzZjIt"
            "ZGQ5MzAyYzEwZjJlLVNFTkRVTkdTVklERU8"
        )
        m = app.ARD_ID_REGEX.search(
            "https://www.ardmediathek.de/video/tagesschau-15-00-uhr-22-09-2026/"
            f"das-erste/{crid}"
        )
        self.assertEqual(m.group(1), crid)

    def test_ard_single_segment_player_and_query(self):
        m = app.ARD_ID_REGEX.search("https://www.ardmediathek.de/video/some-episode/Y3JpZDox")
        self.assertEqual(m.group(1), "Y3JpZDox")
        m = app.ARD_ID_REGEX.search("https://beta.ardmediathek.de/player/some/Y3JpZDox?foo=1")
        self.assertEqual(m.group(1), "Y3JpZDox")

    def test_ard_collection_url_has_no_video_match(self):
        # sendung/serie/sammlung pages are gated before id extraction
        m = app.ARD_ID_REGEX.search(
            "https://www.ardmediathek.de/sendung/tagesthemen/Y3JpZDovdGVzdA"
        )
        self.assertIsNone(m)

    def test_zdf_last_segment(self):
        m = app.ZDF_ID_REGEX.search(
            "https://www.zdf.de/video/talk/markus-lanz-114/"
            "markus-lanz-vom-22-september-2026-100?bcid=xyz"
        )
        self.assertEqual(m.group(1), "markus-lanz-vom-22-september-2026-100")
        m = app.ZDF_ID_REGEX.search("https://www.zdf.de/video/some-episode-100/")
        self.assertEqual(m.group(1), "some-episode-100")


class TestNormalizeUrlGermanMediathek(unittest.TestCase):
    def test_schemeless_tokens_get_https(self):
        self.assertEqual(
            app.normalize_url(
                "zdf.de/video/talk/markus-lanz-114/markus-lanz-vom-22-september-2026-100"
            ),
            "https://zdf.de/video/talk/markus-lanz-114/markus-lanz-vom-22-september-2026-100",
        )
        self.assertEqual(
            app.normalize_url("ardmediathek.de/video/some-episode/Y3JpZDox"),
            "https://ardmediathek.de/video/some-episode/Y3JpZDox",
        )
        self.assertEqual(
            app.normalize_url("www.zdfheute.de/nachrichten/jahresrueckblick-2025.html"),
            "https://www.zdfheute.de/nachrichten/jahresrueckblick-2025.html",
        )

    def test_full_urls_unchanged(self):
        for url in (
            "https://www.zdf.de/video/talk/markus-lanz-114/markus-lanz-vom-22-september-2026-100",
            "https://www.ardmediathek.de/video/some-episode/Y3JpZDox",
        ):
            self.assertEqual(app.normalize_url(url), url)


if __name__ == "__main__":
    unittest.main()
