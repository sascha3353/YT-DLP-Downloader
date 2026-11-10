# End-to-end unittest for get_video_info() against a captured RTVE Play
# info JSON. Replays the real captured yt-dlp -J output through the real
# get_video_info() bound to a lightweight harness (no Qt event loop needed).
#
# Run from the repo root:  python3 -m unittest temp.test_rtve_info_parse -v

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app

CAPTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "rtve-info-capture.json"
)


class Harness(app.InfoFetchMixin):
    """Minimal stand-in for YTDLPDownloaderGUI around get_video_info.

    Inherits the real InfoFetchMixin so new helper methods are picked up
    automatically; only shared GUI state/overrides are provided here.
    """

    def __init__(self, url):
        self.video_state = {
            "url": url,
            "clean_url": url,
            "video_id": "",
            "site": "rtve",
        }
        self.signals = app.SignalEmitter()
        self.yt_dlp_bin = "yt-dlp"
        self.deno_bin = None
        self.sb_calls = []
        self.description_summaries = []
        # The real class routes worker emissions through DownloadMixin._emit,
        # which guards on _shutting_down. This harness inherits only
        # InfoFetchMixin, so provide a forwarding shim — the tests have no
        # window to close, so no shutdown guard is needed.
        self._shutting_down = False

    def _emit(self, name, *args):
        getattr(self.signals, name).emit(*args)

    update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def _emit_description_summary(self):
        self.description_summaries.append(True)


class TestRtvInfoParse(unittest.TestCase):
    def setUp(self):
        with open(CAPTURE_PATH, encoding="utf-8") as fh:
            self.capture = json.load(fh)
        self.url = self.capture.get("webpage_url") or (
            "https://www.rtve.es/play/videos/la-revuelta/"
            "programa-09-11-26-santiago-abascal/17306884/"
        )

    def _run(self):
        h = Harness(self.url)
        captured = []
        h.signals.append_output.connect(captured.append)
        h.signals.sb_log.connect(captured.append)
        h.signals.update_title.connect(captured.append)
        statuses = []
        h.signals.title_fetch_complete.connect(lambda r: statuses.append(r))

        payload = json.dumps(self.capture)
        fake_result = SimpleNamespace(returncode=0, stdout=payload, stderr="")
        with (
            mock.patch.object(app.subprocess, "run", return_value=fake_result),
            mock.patch.object(app.shutil, "which", return_value="/usr/bin/deno"),
        ):
            h.get_video_info(self.url)

        return h, captured, (statuses[-1] if statuses else {})

    def test_real_rtve_capture_parses(self):
        h, captured, status = self._run()
        text = "\n".join(captured)

        self.assertNotIn("Could not read subtitle information", text)
        self.assertNotIn("no audio formats", text)
        self.assertFalse(status.get("error"))
        self.assertEqual(h.video_state.get("original_title"), self.capture["title"].strip())
        # RTVE exposes no upload_date but does expose epoch; the app should
        # derive the date from it and generate the S%yE%m%d prefix.
        self.assertEqual(h.video_state.get("upload_date"), "20261110")
        self.assertEqual(h.video_state.get("channel"), self.capture["series"])
        self.assertIn("Channel: La Revuelta", text)
        self.assertIn("Upload date: 2026-11-10", text)
        self.assertIn("Resolutions:", text)
        self.assertIn("Audio Codecs:", text)
        # The title should carry the S%yE%m%d prefix derived from epoch.
        self.assertIn("S26E1110 - Santiago Abascal", text)

    def test_subtitles_discovered_for_rtve(self):
        # RTVE's extractor only exposes subtitle metadata in --print-json
        # when --write-subs is passed; the info fetch now adds it, so the
        # app should discover the available languages (es, en, ca, gl, eu).
        h, captured, status = self._run()
        text = "\n".join(captured)

        self.assertNotIn("Could not read subtitle information", text)
        self.assertNotIn("no audio formats", text)
        self.assertFalse(status.get("error"))
        self.assertEqual(h.video_state.get("original_title"), self.capture["title"].strip())
        # Spanish should be among the discovered (real) subtitle languages
        self.assertIn("Subtitles: ca (real), en (real), es (real), eu (real), gl (real)", text)
        # Spanish and English are discovered as real (manual) tracks; German
        # is not offered by this video so it has no checkbox. ca/eu/gl show
        # in the output text but have no UI checkbox, so they are not in the
        # availability dict.
        self.assertEqual(h.video_state.get("available_subtitles"), {"en": "real", "es": "real"})

    def test_sponsorblock_skipped_for_rtve(self):
        h, captured, status = self._run()
        self.assertEqual(h.sb_calls, [])
        self.assertIn(
            "SponsorBlock: Not available for this site (YouTube only) — skipping",
            "\n".join(captured),
        )

    def test_info_command_has_no_js_runtime(self):
        # RTVE needs no deno even when it is installed
        h = Harness(self.url)
        h.deno_bin = "deno"
        fake_result = SimpleNamespace(
            returncode=0, stdout=json.dumps(self.capture), stderr=""
        )
        with (
            mock.patch.object(app.shutil, "which", return_value="/usr/bin/deno"),
            mock.patch.object(app.subprocess, "run", return_value=fake_result) as m,
        ):
            h.get_video_info(self.url)
        self.assertNotIn("--js-runtimes", m.call_args.args[0])

    def test_timeout_reports_default_budget(self):
        h = Harness(self.url)
        captured = []
        h.signals.append_output.connect(captured.append)
        with mock.patch.object(
            app.subprocess,
            "run",
            side_effect=app.subprocess.TimeoutExpired(cmd="yt-dlp", timeout=15),
        ):
            h.get_video_info(self.url)
        self.assertIn(
            f"Timeout fetching video info (yt-dlp killed after {app.INFO_FETCH_TIMEOUT_SECONDS}s)",
            "\n".join(captured),
        )

    def test_wait_hint_text_is_generic_for_rtve(self):
        # No per-site slow_hint -> the generic wording
        h = Harness(self.url)
        self.assertIn("Still fetching video info (30s)", h._info_wait_hint_text(30))
        self.assertIn("responding slowly", h._info_wait_hint_text(30))
        self.assertNotIn("LBRY", h._info_wait_hint_text(30))


if __name__ == "__main__":
    unittest.main()
