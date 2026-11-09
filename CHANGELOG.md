# Changelog

Every user-visible change to YT-DLP Downloader, newest first.

This file is the **canonical** record of what each version does. GitHub
release notes are written from it and link back here — if the two ever
disagree, this file is right.

**Conventions**

- Versions are the `APP_VERSION` values the app actually ran as
  (`ytdl/__init__.py`), not git tags. A version listed here *without* a
  release link was committed and shipped, but never published as a
  GitHub Release; its notes are folded into the next published release.
- Dates are the commit dates.
- Entries describe what a **user** observes. The commit history has the
  internals.

**Markers**

- **⚠ Changed on disk** — the release renames, moves or rewrites files
  you already have, or changes what gets written from now on. Read these
  before upgrading.
- **🧪 Verified** — the release's verification story, where it is a claim
  worth checking rather than assuming.

---

## 1.5.17 — 2026-11-09

- **Subtitle fixes.** The JSON3 parser now sorts lines by start time (as its docstring already promised), its docstrings no longer claim per-word timing (only line-level timestamps are read), the unused `max_time` variable in the SRT path is gone, and the file-content variable in `resync_subtitles` was renamed from `content_json3` to `content` since it holds both SRT and JSON3 content.

## 1.5.16 — 2026-11-09

- Version bump.

## 1.5.15 — 2026-11-09

- **JSON3 subtitle path gets the same safety nets as SRT.** Zero-duration cues (missing `dDurationMs`) are now dropped at the 0.15s floor, and the final overlap guard runs on the JSON3 layout output too — so neither path can write overlapping or zero-length subtitle cues.

## 1.5.14 — 2026-11-09

- **Pause threshold moved below Show Info.** The “Pause threshold ms” field now sits below the “show_fetchinfo_command” checkbox in the Preferences dialog, after the three switches, instead of directly under the cookies row.

## 1.5.13 — 2026-11-09

- **Pause threshold field matches the other text fields.** The “Pause threshold ms” line edit now uses the same font size as the “cookies from browser” field next to it.

## 1.5.12 — 2026-11-09

- **Pause threshold is now settable in Preferences.** The “Pause threshold ms” field (below “cookies from browser”) controls how long a silence between spoken phrases must be before a subtitle cue breaks into two — default 2000 ms, set to 0 to disable pause-based splitting.

## 1.5.11 — 2026-11-09

- **Subtitle pauses show a blank gap.** A long silence between spoken phrases in downloaded subtitles now leaves the screen blank until the next phrase appears, instead of the previous subtitle lingering through the whole pause.

## 1.5.10 — 2026-11-06

- **Subtitles only download when the Type "Subtitles" checkbox is on.** Checking a subtitle language (English/German/Spanish) no longer downloads subtitles by itself — the Type group's "Subtitles" checkbox must also be checked. When you check "Subtitles" with no language chosen yet, the first selectable language is pre-selected so the download has a target.

## 1.5.9 — 2026-10-29

- **Show Info thumbnail tries a fallback when the source URL 404s.** Some YouTube
  videos' localized thumbnail URL returned by yt-dlp is not found; Show Info now
  also tries the standard `https://img.youtube.com/vi/<id>/maxresdefault.jpg`
  pattern, so the thumbnail loads for those videos too.

## 1.5.8 — 2026-10-28

- **Show Info thumbnail no longer forced square.** The video thumbnail shown on the
  Show Info button is scaled to 24px height with aspect ratio preserved, so a
  1280x720 thumbnail becomes roughly 43px wide instead of being squashed into a
  24x24 square.

### 🧪 Verified

- 587 tests pass (`python3 -m unittest discover -s temp -p 'test_*.py'`), and pylint is unchanged at 9.91/10.
- GUI constructs cleanly headless (`temp/smoke_gui.py`).

## 1.5.7 — 2026-10-28

- **New left sidebar toolbar.** The URL paste, Reload Info, Nice Title, Show Info, Show Log, Nice SRT, Cancel and Reset UI buttons moved out of the main panels into a vertical toolbar on the left side of the window. Each button shows an icon with a text label underneath. The Download button stays unchanged in the main UI.
- **Show Info now displays the video thumbnail.** When an info fetch returns a thumbnail, the Show Info button in the sidebar shows that thumbnail as its icon instead of a generic symbol.
- **Cancel moved to the sidebar.** The Cancel button is now in the left toolbar, greyed out when no download is running and enabled only while a download is active.
- **Tooltips simplified.** Sidebar button tooltips now read as direct actions (e.g. "Paste Clipboard Content", "Re-Fetch Video Info") instead of "Click to …" phrasing.

### 🧪 Verified

- 587 tests pass (`python3 -m unittest discover -s temp -p 'test_*.py'`), and pylint is unchanged at 9.91/10.
- GUI constructs cleanly headless (`temp/smoke_gui.py`).

## 1.5.6 — 2026-10-20

- **The Preferences dialog is now just the settings.** It opens straight on the switches and the channel name map: the "Editing: …preferences.json" line at the top is gone (the file being edited is named in the tooltip of the channel name map editor), and the separate "Show explanations" section is gone too — each setting now explains itself in its own tooltip when you hover over it, covering what the key does, whether it is `true` or `false`, and what the default is.
- **The Preferences' switches are now checkboxes.** `strip_bracketed_soundcues` and `show_fetchinfo_command` join the browser-cookie switch as real checkboxes, and the JSON editor below them now holds **only** `channel_name_map`. The four switches are set in the form and no longer have to be typed as JSON; the JSON editor is for the channel name map alone. Any other key already in your `preferences.json` is carried over untouched when you save, so a hand-added entry is never lost.
- **Browser cookies are now a checkbox plus a browser field.** The Preferences dialog has a browser-cookies row: a `cookies_from_browser` on/off switch and a field for the browser to read cookies from (`firefox`, `chrome`, `chromium`, `edge`, `brave`, `opera`, `safari`, `vivaldi`, `whale`, or `firefox:profile_dir`). Cookies are only used when the switch is on. Existing setups keep working: a configured browser name without the new switch stays enabled.
- **A corrupted `preferences.json` no longer blocks the Preferences dialog.** If the file was not valid UTF-8 (e.g. edited by a tool that wrote the wrong encoding), opening the dialog raised a `UnicodeDecodeError` and saving failed with it. The dialog now treats such a file as empty, so it opens, and saving replaces it with a valid file.
- **A failed info fetch no longer shows the last video's description.** If yt-dlp failed (error exit, timeout, unreadable output, a video without audio) the output panel still printed the description of the *previous* successfully fetched video, and the same stale text could end up in the thumbnail dialog and in the NFO sidecar. A failed fetch now shows no description at all.
- **No more SponsorBlock chatter on non-YouTube sites.** A download from ARD/ZDF Mediathek, Rumble or Odysee still printed the EDL-generation lines ("📟 Reading … for EDL generation…", "Selected SB categories", "No segments to skip found in selected categories") even though the info fetch had already reported "SponsorBlock: Not available for this site (YouTube only) — skipping". Such downloads now skip the step with a single "skipping EDL" line. Nothing was ever written for these sites (SponsorBlock is YouTube-only), but the output wrongly looked like the video was being processed.

### 🧪 Verified

- 587 tests pass (`python3 -m unittest discover -s temp -p 'test_*.py'`), and pylint is unchanged at 9.91/10.
- The Preferences dialog was checked headless (`temp/check_prefs_dialog.py`): the four switches carry their tooltips, the browser field's tooltip is the browser help, the editor's tooltip names the file being edited, and there is no explanations link and no path label.
- The save path was checked headless (`temp/check_prefs_save.py`): the four form switches land in the file, a hand-added key (`my_own_key`) survives the save, and the JSON editor's `channel_name_map` is what gets written.

## 1.5.5 — 2026-10-17

- **Type group: radio buttons → checkboxes.** The Type group's five radio buttons (Video+Audio, Video only, Audio only, Subtitles only, .edl only) are replaced with four independent checkboxes: **Video**, **Audio**, **Subtitles**, and **.edl only**. You can now combine options (e.g. Video + Subtitles without Audio, or Audio + Subtitles). When `.edl only` is checked, the other three are greyed out. Checking a subtitle language in the Subtitles group automatically checks the Type group's "Subtitles" checkbox as well.

## 1.5.4 — 2026-10-16

- **Internal refactor: `download.py` split into mixin modules.** No user-visible changes — download behavior, SponsorBlock handling, EDL/NFO creation, subtitles and progress parsing are unchanged. `ytdl/download.py` is now under pylint's 1,000-line limit.

## 1.5.3 — 2026-10-15

- **New media type: `.edl only`.** The Type group gained an `.edl only`
  option: pressing Download writes the SponsorBlock skip file
  (`<title>.edl`) for the current video and downloads **nothing** — for
  media that is already on disk. It reuses the segments already fetched for
  the SponsorBlock bar (one direct API request only when the bar is empty),
  names the file after the video's exact title (a title like "Lecture 1.5"
  keeps its suffix), and reports the outcome on the button: "EDL Created
  ✅", "No EDL Written" or "EDL Error 🚨".
- **The Hook checkbox now finds Hook segments.** SponsorBlock's "hook"
  category (greetings and teasers for upcoming videos) was never requested
  from the API, so a video whose only segments were hooks showed an empty
  SponsorBlock bar — and `.edl only` wrote nothing for it — no matter what
  was checked.
- **Ending, Tangents or Highlight checked on their own now produce a skip
  file after a download.** The `.edl` written after a download compared
  yt-dlp's raw category names (`outro`, `filler`, `poi_highlight`) against
  the checkbox labels, so those three selections never matched and the file
  came out empty. The names are now translated the same way the SponsorBlock
  bar already did.
- **Clicking "Video + Audio" no longer downloads a video without sound.**
  The Type group resolved its buttons by their first word, and "Video +
  Audio" and "Video only" share it — so re-selecting "Video + Audio"
  silently switched the download to a video-only stream.

---

## 1.5.2 — 2026-10-15

- **The output panel no longer prints a raw subtitle block ahead of the
  subtitle summary.** After the "merging into 2-line format" line, the panel
  showed the file's final cue verbatim — cue number, timestamp and
  unprocessed text such as `>> [music]` — before "💬 Subtitles merged." and
  the `📊` counts. It was display-only: the `.srt` written to disk was always
  fully processed and the counts were always correct. A file that could not
  be processed showed the same leak ahead of its ⚠️ warning instead.

---

## 1.5.1 — 2026-10-14

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.5.1) · tag `v1.5.1`

- **A successful download now writes one line to `app.log` instead of four.**
  The log recorded the channel, the episode title, the URL and the result —
  and then the result line repeated the URL, so every successful download put
  a redundant address into the log. The start-of-download URL line is gone
  (the result line already carries it alongside the outcome), and the
  SponsorBlock segment list moved to debug level: the ranges are already shown
  in the app (the overlay bar and its log lines) and written into the `.edl`
  the player reads, so restating them per download served nobody. A successful
  download now logs the channel, the title and the result:

  ```
  2026-10-14 19:15:12,525 Tucker Carlson
  2026-10-14 19:15:12,525 S26E1012 - Tristan Harris - Rogue AI
  2026-10-14 19:15:12,525 Download succeeded: https://www.youtube.com/watch?v=EKXJrUHudeA
  ```

  Failures are unaffected — they still dump the captured yt-dlp output, which
  is the part worth having. That segment line also said "removed" when the app
  only *marks* segments and lets the player skip them; it now says "marked".

---

## 1.5.0 — 2026-10-14

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.5.0) · tag `v1.5.0`

- **The output panel header now names the commit the app was built from**,
  after the version: `YT-DLP Downloader 1.5.0 [babb7fb]`. A version can
  cover many commits, so this ties a bug report to an exact checkout. The
  packaged `.app` reports it too: the build scripts stamp the commit into a
  bundled `BUILD_COMMIT` file, since a bundle ships no repository to read.
  When no commit can be determined the header shows the version alone rather
  than failing, so nothing changes for those runs.
- **Log output no longer masks URL query strings.** Masking every `?...` hid
  the YouTube video ID — the one detail that identifies which download failed —
  and truncated any captured yt-dlp line containing a `?`, which could cut an
  error message in half. None of the supported sites' URLs carry a credential
  (YouTube's is a public `v=` id; Rumble, Odysee, ARD and ZDF have no query
  string), so `https://www.youtube.com/watch?v=...` now appears in full in
  `app.log`, the output panel and the failure dump. Credential redaction is
  unchanged: the value of `--cookies-from-browser` and the other value-taking
  flags is still logged as `***`. If you paste `app.log` into a public issue,
  check it first — it now contains complete URLs.
- **Running the test suite no longer writes to `app.log`.** Logging used to be
  configured at import time, so importing the app — which every test does —
  pointed the logger at the live `app.log` and appended synthetic entries to
  it (a 35-test module wrote 89 lines of `Download failed: unknown` and
  `sidecar boom`). Logging is now set up by the launcher when the app actually
  starts. Nothing changes for normal use; if you build or hack on the source,
  note that `app.log` is now written only by a real run.

### 🧪 Verified

- The commit reaches a real bundle end to end, not just in a source run: a
  `./build-macos.sh` build reported `1.4.23 [5e20afe]` in the output panel,
  with `5e20afe` present in the bundle's `Resources/BUILD_COMMIT`.
- Test isolation measured with a canary rather than inferred: a marker line
  placed in `app.log` survived two consecutive full-suite runs unchanged,
  with zero lines added. The same suite wrote 89 lines before the change.
- No new lint warnings (7, all pre-existing) and 464 tests passing.

---

## 1.4.23 — 2026-10-14

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.23) · tag `v1.4.23`

- **Log output no longer masks URL query strings.** Masking every `?...` hid
  the YouTube video ID — the one detail that identifies which download failed —
  and truncated any captured yt-dlp line containing a `?`, which could cut an
  error message in half. None of the supported sites' URLs carry a credential
  (YouTube's is a public `v=` id; Rumble, Odysee, ARD and ZDF have no query
  string), so `https://www.youtube.com/watch?v=...` now appears in full in
  `app.log`, the output panel and the failure dump. Credential redaction is
  unchanged: the value of `--cookies-from-browser` and the other value-taking
  flags is still logged as `***`. If you paste `app.log` into a public issue,
  check it first — it now contains complete URLs.

---

## 1.4.22 — 2026-10-14

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.22) · tag `v1.4.22`

- **URL query strings are now masked in every log path.** The failure-log redaction shipped in 1.4.7–1.4.10, but the download-start log line, the download and info-fetch commands shown in the output panel, and the unexpected-exception log still printed the raw URL — so a session token or auth code in a query string could reach `app.log` from a successful or non-failing run. All four now route through the shared `_redact_url` / `_redact_command` helpers, leaving only the scheme, host and path to identify the job.

---

## 1.4.21 — 2026-10-14

- **A URL change now releases the thumbnail flight guard.** Bumping the request token on a URL change left `_fetching_thumbnail` true, so an in-flight request's result was stale and neither the worker nor the receiver reset the flag — the Info button stayed blocked from fetching the new video's thumbnail. The flag is now reset in `on_url_text_change()` too; the token keeps the old request from clearing the state of any newer one, so a result that arrives later is still dropped.

---

## 1.4.20 — 2026-10-14

- **A stale thumbnail error is now dropped after a URL change too.** The error signal carried only the message and request token, and the token is only bumped when Info is clicked again — so a failure queued after a URL change but before the next Info click could still match the token and be logged over the new video's output panel. The error signal now carries the snapshot URL, and the receiver compares it against the current `thumbnail_url` before appending. The token is also bumped in `on_url_text_change()` alongside the SponsorBlock generation, as a second line of defence.

---

## 1.4.19 — 2026-10-14

- **The thumbnail dialog is now gated by a request token, not just the URL.** A stale request could clear a newer request's in-flight guard: the worker reset `_fetching_thumbnail` in its `finally` as soon as it emitted the queued result, and the receiver reset it again even when it rejected the result as stale. Each Info click now bumps a token carried on both the success and error signals, and both the worker's `finally` and the receiver only reset the guard when the token still matches the current request — so an older response cannot clear a newer one's guard. The error path also moved onto a scoped `thumbnail_error` signal carrying the token, with its own receiver dropping a failure for an old request.
- **A comment claimed the worker re-checked the URL before emitting; it did not.** The URL check lives in the receiver, and the comment now says so, with the token as the first line of defence and the URL check the second.

---

## 1.4.18 — 2026-10-14

- **The thumbnail signal and its use now agree on argument count.** `thumbnail_ready` was declared with two args (bytes, str) but the worker emitted three (image, description, url) and the receiver expected all three. A mismatched emit can raise a PyQt argument-count error, and because it sat outside the worker's try block the result would not be shown and `_fetching_thumbnail` would not be reset — leaving the Info button unclickable for the rest of the session. The signal now declares all three, and the emit sits inside a `try/finally` that resets the flight guard on every exit path, including a raise from the emit itself.

---

## 1.4.17 — 2026-10-14

- **The thumbnail dialog no longer mixes metadata across videos.** `open_thumbnail_dialog()` fetches the image on a daemon worker thread, so by the time it arrives the user may have switched videos. The worker used to capture only the URL; `_show_thumbnail_dialog()` then read the description from live `video_state`, pairing an old image with a new video's description. The description is now captured with the URL at click time and travels on the `thumbnail_ready` signal, and the receiver re-checks the URL against the current one before constructing the dialog — a response for a superseded video is dropped instead of being shown over the new video's metadata.

---

## 1.4.16 — 2026-10-14

- **The last two SponsorBlock gate messages are generation-gated too.** The site-unsupported and missing-video-ID messages in `check_sponsorblock()` used to go through plain `append_output`. That method runs after `title_fetch_complete`, so a URL change could happen before those queued messages were displayed, letting a message for the previous video appear in the new video's log. They now go through the scoped `sb_log` signal carrying the generation, with the same main-thread receiver dropping a stale one.

---

## 1.4.15 — 2026-10-14

- **SponsorBlock results for a superseded video are now discarded at three points, not two.** The lookup thread re-checks the *current* video id before emitting, and the main-thread receiver compares a generation token before painting the bar — but the token used to be bumped only near the end of `get_video_info()`, so a response queued from a previous video could land after the URL changed but before the new fetch reached the bump, and its token would still match, briefly repainting the cleared bar. The generation is now bumped at the start of each fetch and again when the URL changes, before the new fetch starts, so that window is closed.
- **Stale SponsorBlock log lines are gated too.** The lookup thread's output lines moved onto a scoped `sb_log` signal carrying the generation, with its own main-thread receiver comparing the token before appending — log signals can be queued and delivered after a URL change just as easily as bar updates can. `append_output` itself is untouched, since the download worker also writes to it legitimately.

---

## 1.4.14 — 2026-10-14

- **SponsorBlock results for a superseded video are now discarded at two points, not one.** The lookup thread re-checks the *current* video id before emitting, but Qt may queue that signal while the main thread is busy, so a late delivery can still repaint a bar a newer fetch already cleared. Each info fetch now bumps a **generation token** carried on the `update_sb_bar` signal, and the main-thread receiver compares it before painting — so a delivery for an older generation is dropped there even though it was emitted. The token also covers a second fetch of the *same* video, where an id check alone would let an older request publish after a newer one. Both guards gate every emission from the lookup thread, including the error lines, so a failing lookup for a superseded video never reaches the log either.

---

## 1.4.13 — 2026-10-14

- **The SponsorBlock bar no longer shows a previous video's segments after you switch URLs.** The info fetch releases the UI first, so you can paste a new URL and start a second fetch while the first SponsorBlock request is still in flight. The lookup thread now re-checks the *current* video id before painting, so a response for a superseded video is discarded instead of being drawn over the new video's bar (and its output lines landing out of order). The id/site/duration are also snapshotted before the completion signal, so the thread never reads a video_state that has already been replaced.

---

## 1.4.12 — 2026-10-14

- **Closing the window during a download no longer crashes the process.** Worker-thread UI notifications — info-fetch progress, SponsorBlock lookup, thumbnail generation, the download's own output lines, and the post-download subtitle resync — all route through `_emit()`, which drops them once the window is closing. Previously the resync worker's "No srt file" line still emitted directly, so a close mid-resync could hit the dying signal object and abort the interpreter with no traceback.

---

## 1.4.11 — 2026-10-14

- **A post-download exception no longer reports a subtitle-only job as failed when the SRT is on disk.** `media_downloaded` was only set inside the non-subtitle path, so a subtitle-only run whose SRT was written still said "▶ Subtitles only · no media download failed; no video sidecars to generate" when cleanup or later post-download work raised. Subtitle output completion is now tracked separately (`subtitles_downloaded`), so the failure line says "Post-download processing failed; the media file is on disk" instead.

---

## 1.4.10 — 2026-10-14

- **Raw yt-dlp output lines are now redacted before they reach the failure log.** Previously only the command line was redacted, so an ERROR line echoing the URL yt-dlp actually fetched could carry a query string with a session token into the log. Every captured line is now masked through `_redact_line()`.
- **`--netrc` is treated as a boolean flag.** It tells yt-dlp to read `~/.netrc` and does not consume the next argument; treating it as value-taking redacted the URL that followed it and hid the failed job from the log. The sensitive-flag set is now split into value-taking and boolean flags.

---

## 1.4.9 — 2026-10-14

- **A post-download exception no longer reports the media as failed when it is on disk.** The failure-sidecar helper now checks `media_downloaded` before the media-type gate, so an audio-only (or subtitles-only) job whose file already landed says "Post-download processing failed; the media file is on disk" instead of "▶ Audio only · no media download failed; no video sidecars to generate". For video jobs the sidecars are still attempted when the media landed but the EDL/NFO were not yet written.

---

## 1.4.8 — 2026-10-14

- **Error-log redaction now covers both flag shapes.** yt-dlp accepts `--flag value` and `--flag=value`; only the first was redacted, so a long header or extractor arg passed as one token leaked its value. Both forms are now redacted, and ordinary equals-form flags (e.g. `--merge-output-format=mp4`) are left untouched.
- **URL query strings are masked in the failure log.** The URL itself is kept (it identifies the failed job), but its query string — where a session token or auth code would live — is replaced with `?***`.

---

## 1.4.7 — 2026-10-14

- **Subs button is now disabled during a download.** Previously the standalone SRT converter stayed clickable while yt-dlp or the worker's subtitle post-processing was writing the same file, which could race a read against a write. The button now disables with the rest of the primary controls, and the handler refuses to run while a download is in flight.
- **Subs accepts standard SRT variants.** A block may now omit its cue number or carry a cue identifier on the line before the timestamp; the parser locates the timestamp line by content instead of assuming it is the second line. Files written in either shape no longer report "No changes made".
- **Failure sidecars are scoped to video jobs.** A failed audio-only or subtitles-only download no longer generates an `.nfo`/`.edl` (they describe a video episode) and the failure line names the requested operation instead of saying "media download failed".
- **Error logs are bounded and redacted.** yt-dlp output kept for a failure log is capped at a tail of 200 lines (with the truncation stated) and the logged command redacts the value of `--cookies-from-browser`, `--header`, `--extractor-args`, `--username`, `--password`, `--netrc` and `--video-password`; the URL and output template are kept so the failed job stays identifiable.

---

## 1.4.6 — 2026-10-14

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.6) · tag `v1.4.6`

- **Subs button**: new button below "Log" opens a file dialog to select an SRT file. The file is then processed in-place through our subtitle pipeline (2-line paired-window layout matching YouTube's player, bracket stripping per preference). Tooltip: "Convert SRT into more readable format".
- Updated reference preferences.json with `cookies_from_browser` and `show_fetchinfo_command` keys.

---

## 1.4.5 — 2026-10-13

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.5) · tag `v1.4.5`

- **Subtitle/preference messages now shown in UI output panel** (previously only in log file):
  - "No valid subtitle cues found; keeping the original file"
  - "Removed superseded subtitle: Title.srt"
  - "strip_bracketed_soundcues is <type>, not true or false — using on" (preferences dialog also validates on save)
  - "Could not read subtitle information for this video"

---

## 1.4.4 — 2026-10-13

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.4) · tag `v1.4.4`

- Fixed the video-codec selection sending a filter yt-dlp cannot match: choosing AV1 built `[vcodec*=av1]`, but yt-dlp reports `av01…`, so the filter matched nothing and the run silently fell back to `/best` (H264). Each choice now maps onto the codec string yt-dlp actually reports.
- When the video stream itself is refused with HTTP 403, the output panel now names the format that was refused (e.g. `format 399 (AV1)`) and suggests trying a different video codec in the format selection. A webpage or subtitle 403 is a different failure and keeps its existing reporting.
- **All-defaults optimization**: when Quality=Best, Video=Best, Audio=Best (the defaults), the download uses `-f best` (single muxed stream, no merge). This typically yields ≤720p on YouTube because 1080p+ are DASH (separate streams). Any non-"best" setting uses the full `bestvideo*[filters]+bestaudio/best` selector for full resolution.
- **cookies_from_browser** preference: reads cookies from your browser (`firefox:profile_dir`, `chrome`, etc.) for authenticated requests (fixes 403, sign-in, age-restricted).
- **show_fetchinfo_command** preference (default `false`): logs the info-fetch yt-dlp command to the output panel for debugging.
- Preferences editor: switched to plain text (`QPlainTextEdit`), added show/hide explanations link.
- Subtitles group: shows ⚠️ + tooltip when >1 language selected (warns about 429 rate-limit risk).
- Info-fetch command logged with `🖥️` prefix when enabled (consistent with download command).
- Video codec radio order changed: Best → AV1 → VP9 → H264.
- Audio format radio order changed: Best → Opus → M4A → MP3.
- Single muxed stream: both video and audio progress bars show 100% at completion.
- Metadata sidecar files (`.edl`, `.nfo`) are now created even when the media download fails, since yt-dlp writes `.info.json` before downloading.

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.3) · tag `v1.4.3`

No **⚠ Changed on disk** marker this time: both changes default to exactly
what the previous version already did, so nothing on disk differs unless
you opt in.

- **Bracketed sound cues are now optional.** `[laughter]`, `[snorts]`,
  `[music]` and similar have always been removed from automatic
  captions; there is now a preference to keep them, for anyone who
  wants the captions exactly as YouTube's ASR emitted them:

      { "strip_bracketed_soundcues": false }

  A JSON `true`/`false`, default **`true`**, so nothing changes
  unless you set it. Editable in the Preferences dialog alongside the
  other keys, which now explains it. Affects newly processed
  subtitles only.

  Two things it deliberately does not touch: the `>>` speaker marker
  is still always removed (it is an ASR artifact, not authored text),
  and hand-written subtitle tracks are left completely alone, as
  before — they are not re-laid-out at all.

- **The unused subtitle-pace setting is gone.** `subtitle_words_per_cue`
  and the sentence-aware layout it controlled were both removed, together
  with the leftovers that only served them (`_SUBTITLE_SENTENCE_ABBREVS`
  and `_flatten_subtitle_text`). Nothing had read any of it since 1.4.2
  switched subtitles to the captions' own lines — it is now simply ignored
  if it is still in your `preferences.json`, like any other key the app
  does not know. No other behaviour changes.

- **Cleanup.** Two URL helpers that nothing called any more —
  `is_supported_url()` and `is_youtube_url()` — were removed. Input is
  gated by `url_rejection_reason()`, which is unchanged, so unsupported
  and malformed URLs are still rejected before yt-dlp runs.

## 1.4.2 — 2026-10-10

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.2) · tag `v1.4.2`

### ⚠ Changed on disk — subtitles match the YouTube player again

- Subtitles are laid out from the captions' **own lines**, two at a time,
  instead of being regrouped into sentence-aligned cues:

      LINE 1: the Peruvian skulls and
      LINE 2: No, before I thought Oh, go ahead.

  Nothing is repeated and nothing is re-cut — every caption line appears
  exactly once, on the line it occupies in the player — and a long line is
  left exactly as long as the captions make it rather than being wrapped.
  The top line of each pair starts at exactly the time the caption file
  gives it, so it appears when YouTube's would.
- This **reverses the 1.4.0 layout**, which broke subtitles at sentence
  ends (75% mid-sentence → 20–44%). Copying the player means a subtitle can
  end mid-sentence again — the captions' lines are phrases, not sentences —
  and that is the trade. `layout_cues()` and the `subtitle_words_per_cue`
  preference from 1.4.1 are both still in the code and still tested, so the
  sentence-aware layout is one call site away if it is ever wanted back;
  the preference has **no effect** on new downloads until then, and the
  Preferences dialog now says so.
- Affects newly processed subtitles only; existing `.srt` files are never
  re-laid-out.

### Also

- **No more one-word subtitles from a cut.** In the sentence-aware layout,
  a sentence one word over the word ceiling used to be cut as a full cue
  plus a single-word cue ("…take care of" / "them.") that no packing rule
  could merge back. The cut is now rebalanced so the tail carries at least
  three words (the 11-word sentence splits 8+3, not 10+1). A subtitle that
  is genuinely a one-word sentence ("Yeah.", "Mhm.") still stands alone —
  that is what was said.

## 1.4.1 — 2026-10-08

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.1) · tag `v1.4.1`

- **Subtitle pace is now a preference.** `subtitle_words_per_cue` in
  `preferences.json` (whole number, 6–16, default 8) sets how many
  words one subtitle carries. Lower values split subtitles sooner, so
  the next cue starts earlier — for videos where subtitles feel a
  moment too late or one line runs long. Affects newly processed
  subtitles only; existing `.srt` files are not re-laid-out. The
  setting is also editable in the Preferences dialog, which now
  explains it. Reading speed itself stays fixed — it is a property of
  the video, not an adjustable pace.

Docs:

- `README.md` drops its prerequisites table for a three-line summary plus
  a link to `setup.md` — two copies of a dependency list drift apart.
- The "Common Gotchas" list is merged into a single copy in `AGENTS.md`.
  The two items that were user-facing (the yt-dlp nightly escape hatch,
  and the ARD/ZDF "not available but it downloads anyway" note) move to
  `setup.md` §6, which the README already links to.

## 1.4.0 — 2026-10-01

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.0) · tag `v1.4.0`

The release that ships the new subtitle layout. The layout landed in code
as 1.3.13 and waited here until its output had been checked against real
downloads — **1.4.0 is the first version you get it in**.

### ⚠ Changed on disk — subtitles get a new layout

- Subtitles break at **sentence ends**, not wherever YouTube's rolling
  ASR window happened to end. Measured across six real videos, cues
  ending mid-sentence drop from **75% to 20–44%**.
- Time on screen comes from a reading-speed model (2.4 words/sec target,
  the video's own pace used inside a 2.4–3.4 band) instead of being
  inherited from the ASR window — so a 10-word subtitle and a 30-word
  one no longer flash past at the same speed.
- Start times stay on the video's own clock. Cues never overlap, never
  exceed two lines, and no sentence is ever dropped.
- The output panel reports what it did:
  `📊 Title.srt: 2957 in → 1132 out · 1114 two-line · 3.28 wps · 638 fast-paced`.
  "fast-paced" describes the speaker, not a problem with the file.
- **Existing `.srt` files are never re-laid out** — there is deliberately
  no re-layout feature, so only newly downloaded subtitles use the new
  layout. The `subtitle-layout-baseline` tag marks the last pre-refactor
  output if you want to compare against it.

### ⚠ Changed on disk — subtitle naming and SponsorBlock

- A video's only subtitle is stored as `Title.srt` rather than
  `Title.en.srt`. Two or more languages keep their codes, since the code
  is then the only thing telling them apart. *(Shipped in 1.3.10; listed
  here because this is the release people upgrade to.)*
- SponsorBlock ranges are **marked** and written to an `.edl`; the media
  is never cut or re-encoded. The subtitle-retiming code that belonged
  to the old cut-the-video workflow is now **removed** — subtitles stay
  in the video's own timeline, which is what an EDL-skipping player
  expects. This is a no-op in practice: the code was already dead, and
  subtitles already used source timestamps.

### Downloads

- **Cancel** a running download with the new button or `Esc`. It kills
  the whole process group, so `ffmpeg` stops with `yt-dlp` instead of
  being orphaned mid-merge. *(1.3.9)*
- The panel names what it is downloading — "Video + Audio", "Video
  only", "Audio only" or "Subtitles only" — so the two adjacent radio
  buttons can no longer be confused. *(1.3.15)*

### Fixed

- YouTube's automatic captions are classified from the info fetch again
  instead of from the filename, restoring the two-line treatment for
  every ASR video. *(1.3.12)*
- Subtitle sync, line width, and the flicker between cues. *(1.3.14)*
- The UI no longer stays disabled while a slow SponsorBlock lookup is in
  flight. *(1.3.8)*
- The output folder and the files written into it can no longer disagree.
  *(1.3.8)*
- Hardened URL gating and download finalization. *(1.3.7)*

### Smaller things

- The dock progress bar no longer jumps backwards at the video→audio
  handover. *(1.3.11)*
- The supported-sites line reads "ARD/ZDF Mediathek" instead of listing
  both.
- The layout is tunable per run via
  `layout_cues(cues, targets={...})`, so pacing can be adjusted without
  editing code.

### 🧪 Verified

- The word stream in equals the word stream out — asserted per capture
  across six real `.srt` fixtures. Text only ever moves between
  neighbouring cues; it is never rewritten or lost.
- Sync, width and gap fixes were each measured before and after:
  +2.05 s → **+0.74 s** mean subtitle lag against YouTube's own grid,
  median 15 → **12** words per subtitle, blank cue transitions
  6200 → **1368**.
- Full-length check on a 291-minute capture: 0 overlaps, 0 dropped cues,
  word stream intact.

## 1.3.15 — 2026-10-01

The output panel names the requested format at the start of every
download, read out of the command that is about to run:

```
▶ Video + Audio · bestvideo*+bestaudio/best
▶ Video only · bestvideo/best
▶ Audio only · extract m4a
▶ Subtitles only · no media download
```

Reported as "the app downloaded no audio": the media type alone decides
whether an audio track is fetched, and the resulting file looks complete
either way. It was verified not to be a selection bug first — with the
app's own format string the download and merge succeed, with and without
`--js-runtimes deno`.

## 1.3.14 — 2026-10-01

Three subtitle problems found by watching real output, each measured
before it changed.

- **Sync** — subtitles ran ~2 s behind the speech, and that was our bug.
  YouTube's ASR windows *roll*: 100% of window pairs overlap, by a median
  of 1.6 s, and each window's words were being stamped across its own
  span, so timestamps ratcheted forward without bound. Measured against
  YouTube's own grid: **+2.05 s mean, up to +6.7 s** → **+0.74 s mean**,
  still non-decreasing.
- **Width** — the word ceiling did nothing, because the forced-merge rule
  overrode it. A forced merge now respects the same ceiling, set to 12
  words: median **15 → 12** words per subtitle, 36 → 31 chars per line.
- **Gaps** — 40% of subtitle transitions went blank for a median of 0.6 s.
  A cue now lingers into the pause that follows it, never more than 1.5 s
  past the time it needs to be read. Blank transitions: **6200 → 1368**.

Also: the written `.srt` ends with a newline (a parser can drop a
trailing block that has none), and a cue that still cannot be placed is
now *named* — file and count in the panel, its text in the log — rather
than silently dropped.

## 1.3.13 — 2026-10-01

**⚠ Changed on disk** — the subtitle layout stage is replaced. The three
heuristic passes (merge choppy cues → shift words across pauses → merge
again) are gone, replaced by one stage that ends cues at sentence ends.
The version bump to 1.4.0 came later, once the new output had been
checked against real downloads — see 1.4.0 above for the verified
numbers.

Also in this commit: the `📊` summary line gained the words/sec rate and
the overshoot count, so a dense video is distinguishable from a packing
failure; and the layout constants moved to the top of `subtitles.py`,
pinned by a test so retuning them has to be deliberate.

## 1.3.12 — 2026-10-01

Fixed a regression that had disabled the two-line subtitle merge for
*every* YouTube ASR video.

The multi-site work had started deciding `real` vs `auto` from the
downloaded filename alone. Current yt-dlp merges `--write-subs` and
`--write-auto-subs` into one language namespace and writes YouTube's
automatic captions as plain `<title>.en.srt` — the `a.` prefix is never
produced. Every automatic track was therefore classified "real", which
skips the merge.

Symptom on an 87-minute episode: **2965 overlapping single-line cues
instead of the 2-line layout, 0 two-line cues.** Players stack
overlapping rolling cues newest-above-previous, which reads as "the
second line appears above the first".

Tag `subtitle-layout-baseline` marks this commit as the last
known-good layout, to compare against when the refactor proceeded.

## 1.3.11 — 2026-09-26

The dock progress bar no longer jumps backwards. It filled to 100% during
the video transfer and then dropped to ~85% when the audio transfer
started.

The video transfer now fills only its share of the bar and the audio
fills the rest, so the overlay keeps rising when the second stream
starts. The reserved share is released at completion for a download that
turned out to be a single muxed stream, so it still finishes at exactly
1.0 — the final dock value is now pushed explicitly, since nothing
emitted it before.

The trade-off: a single-stream download fills 0–85% and fills the
reserved share once at the end. That is the price of a bar that never
moves backwards.

  median 15 → **12** words per subtitle, blank cue transitions
  6200 → **1368**.
- Full-length check on a 291-minute capture: 0 overlaps, 0 dropped cues,
  word stream intact.

## 1.3.10 — 2026-09-26

**⚠ Changed on disk** — a video with a single subtitle file now gets
`Title.srt` instead of `Title.en.srt`. With nothing to disambiguate, the
code repeats what the name already implies, and `Title.srt` is the name
players and editors look for.

Two or more files keep their codes — that is what tells them apart. The
rule is literal: the number of subtitle files that actually landed, not
the number of languages selected or offered. Requesting `en`+`de` on a
video that only delivers `en` ends up as `Title.srt` too.

The processed file is still always written under a name the app picks:
site-named files (`<title>.en-auto.srt`, `<title>.deu.srt`,
`<title>.a.en.srt`) are renamed to the canonical name first and then
overwritten in place, so a duplicate can never be left behind. A leftover
bare file from an earlier run is deleted once a later run produces two or
more tagged files.

## 1.3.9 — 2026-09-25

**Cancel** a running download, via a new button in the download row or
`Esc`. Closing the window cancels too.

It works by killing the yt-dlp **process group**, so an `ffmpeg` merge
child dies with it instead of writing into a torn-down directory. The
Cancel button stays clickable while the rest of the UI is disabled, and
is shown only after every early return in the download path — so it never
lingers with no download behind it.

A cancelled run reports "Cancelled" in neutral styling, not the red
error state, and partial `.part` files are intentionally left for you to
remove.

## 1.3.8 — 2026-09-25

- The UI no longer stays disabled while a slow SponsorBlock lookup is in
  flight. The lookup moved off the metadata path onto its own thread, so
  a failing API no longer holds the hand-off for ~49 s; one request on a
  4 s timeout, no retry loop.
- Subtitle state can no longer go stale. A failed info fetch (nonzero
  exit, timeout, no audio, parse error) can no longer leave the previous
  video's languages enabled and requested.
- **⚠ Changed on disk** — the output folder and the files written into
  it can no longer disagree. The `-o` template previously pointed at a
  subpath of the created folder; both now store the same sanitized
  basename. A title or channel that sanitizes to an empty string falls
  back to `downloaded_video` / `Unknown Channel` instead of collapsing
  the path to the output root.
- A missing or malformed `upload_date` no longer produces a title like
  `" - Title"` — an orphaned dash that became the folder and file name.
- Dock tile repaints are throttled (~96% fewer; each one allocates an
  `NSImage` and repaints the app icon). The 0% and 100% endpoints always
  repaint, so a download never appears to stall just short of full.

## 1.3.7 — 2026-09-25

Hardening pass over URL gating, download finalization and subtitles.

- Look-alike hosts (`evilrumble.com`, `notyoutube.com`) are rejected, and
  `/watch?list=...` is caught by the channel gate.
- The output artifact is resolved and verified before EDL/NFO/metadata
  post-processing; a download with no expected file present now **fails**
  instead of reporting success.
- Progress is marked complete only on exit code 0, and reset on failure.
- Subtitle downloads are skipped when a matching `.srt`/`.vtt` already
  exists; overlapping SRT cues are clamped, out-of-order cues dropped,
  malformed files preserved untouched, and results written atomically.
- A corrupt preferences file can no longer break startup.

## 1.3.6 — 2026-09-25

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.3.6) · tag `v1.3.6`

Tooling and correctness; the first release after 1.1.24.

- A project-tailored `.pylintrc` (pylint 10.00/10, 0 messages). Every
  disabled check carries its reason in the file, so it can be re-enabled
  selectively. The genuine findings it surfaced were fixed: dead code in
  the info-fetch path, an unread `stderr`, a `sb_bar` signal connected
  through a deferred lookup (which broke startup).
- 202 unit tests, up from 35.

## 1.3.5 — 2026-09-25

The log viewer gained a **Clear** action, with a dialog-local `Ctrl`/`Cmd+K`
shortcut. It deletes the actual `app.log` on disk, not just the view, after
a confirmation prompt that shows the full path. It is disabled when the log
is missing or empty — and the shortcut cannot bypass that.

The truncation goes through the logging handler's own stream while holding
its lock, so the stream offset stays consistent in any file mode and the
truncation cannot interleave with an in-flight write from the download
thread.

## 1.3.4 — 2026-09-25

**Fixed** — the thumbnail dialog stopped opening in 1.3.3. The fetch
worker handed the bytes back with `QTimer.singleShot(0, ...)`, which Qt 6
silently drops because a plain worker thread has no event loop — no
warning, no exception, and the dialog simply never appeared. It now
returns through a queued signal.

Also:

- Per-channel episode-number extraction (PowerfulJRE, Shawn Ryan Show,
  Lex Fridman, PBD Podcast) moved into a pure, tested rule table — adding
  a channel is now a one-line entry.
- A missing stylesheet is now reported in the output panel, not only in
  the log, and the log viewer reports an unreadable file instead of
  failing.
- Log records are written as UTF-8, so non-ASCII channel names match what
  the dialog reads.

## 1.3.3 — 2026-09-25

Code-review cleanup. No intended behaviour change — but it introduced the
thumbnail regression that 1.3.4 fixed.

## 1.3.2 — 2026-09-24

**⚠ Changed on disk — fixed silent video corruption.**

The ffmpeg bitstream filter that strips embedded broadcast captions
(`filter_units=remove_types=6`) is **H.264-specific**: unit type 6 is an
SEI NAL there, but a Frame OBU in AV1. Applied unconditionally, it silently
deleted picture data from AV1 downloads — which matters because YouTube's
`best` format commonly picks AV1.

The filter is now gated behind a per-site flag, enabled only for Rumble,
the only site serving US broadcast feeds with EIA-608 captions.

## 1.3.1 — 2026-09-23

Version bump only.

## 1.3.0 — 2026-09-23

**ARD Mediathek** and **ZDF Mediathek** join the supported sites.

- Channel and show pages are rejected per site, mirroring yt-dlp's own
  extractors — including ZDF's catch-all channel playlist, where only
  `/video/`, `/play/` and legacy single-video paths pass. A ZDF show page
  that resolves to a 30-entry playlist is refused.
- Both key their captions by ISO 639-2 (`deu`); that is mapped onto the
  German checkbox in the UI and onto `<title>.de.srt` on disk.
- **Note:** a browser reporting a video as "not available" (geo or rights)
  is a separate client-side check and does not predict yt-dlp. Observed
  live: a video the browser called unavailable downloaded fine.

## 1.2.1 — 2026-09-23

**Odysee** (odysee.com / lbry.tv) joins the supported sites.

- Odysee has **no subtitle tracks at all**, so the info panel prints
  "Subtitles: Not available on Odysee" instead of an empty language list.
- Every Odysee format is muxed, so audio-only extraction is forced —
  otherwise "Best" audio would hand you the source MP4.
- The info-fetch budget is 90 s for this site, not the default 15 s: the
  LBRY `resolve` call consistently takes ~40 s and short claim ids are
  slower. Long fetches report progress every 20 s rather than looking hung.

- Look-alike hosts (`evilrumble.com`, `notyoutube.com`) are rejected, and
  `/watch?list=...` is caught by the channel gate.
- The output artifact is resolved and verified before EDL/NFO/metadata
  post-processing; a download with no expected file present now **fails**
  instead of reporting success.
- Progress is marked complete only on exit code 0, and reset on failure.
- Subtitle downloads are skipped when a matching `.srt`/`.vtt` already
  exists; overlapping SRT cues are clamped, out-of-order cues dropped,
  malformed files preserved untouched, and results written atomically.
- A corrupt preferences file can no longer break startup.

## 1.2.0 — 2026-09-23

Internal refactor: the 4.9k-line `app.py` became the `ytdl/` package
(`app.py` assembling per-concern mixins, with `info_fetch`, `download`,
`subtitles`, `ui_build`, `preferences_dialog`, `sites`, `progress`,
`utils` and others holding the logic), plus a thin `main.py` launcher at
the repo root. No behaviour change.

## 1.1.26 — 2026-09-23

**Rumble** joins the supported sites, and per-site behaviour profiles are
introduced as the mechanism for it. Unknown domains keep the historical
YouTube behaviour.

- Rumble's `en-auto` subtitle keys are requested and mapped back to the
  canonical name; its subtitles arrive pre-formatted and are kept as-is.
- Embedded EIA-608 captions are stripped losslessly, so IINA does not
  surface them as a phantom subtitle track.
- A URL plausibility gate was added, with a well-formed-hostname check, so
  clipboard garbage (e.g. the version string copied from the info panel)
  never reaches yt-dlp or the URL field.

*Note: 1.1.25 was skipped; no code ever carried that version.*

## 1.1.24 — 2026-09-15

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.1.24) · tag `v1.1.24`

When fetching video info fails, yt-dlp's captured stdout/stderr is now
dumped to the output panel with the exit code in the headline, and a
timeout dumps whatever was captured before the kill. Previously stderr was
swallowed, so HTTP 403 and sign-in-wall errors made the app look like it
had silently stopped. Also fixed a `NameError` in the no-audio-formats
branch.

## 1.1.23 — 2026-09-01

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.1.23) · tag `v1.1.23`

Fixed the progress bars on retried downloads. After a mid-download error
(`Got error ... Retrying (n/10)`), yt-dlp re-prints the `[download]
Destination:` of the **same** file, which previously advanced the stream
accounting video → audio and filled the audio bar with video progress.
Destination re-prints are now deduped by normalized path, so only a
genuinely new destination switches the stream.

## 1.1.22 — 2026-08-31

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.1.22) · tag `v1.1.22`

After an info fetch, languages with `(real)` subtitles are checked
automatically; if none exist, `(auto)` captions are checked instead. This
applies in all download modes, and stale checks from a previous video are
cleared first.

## 1.1.21 — 2026-08-31

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.1.21) · tag `v1.1.21`

First cross-platform release.

- **Linux support**: optional AppKit import so the macOS dock integration
  degrades cleanly, XDG config and log paths, and `build-linux.sh`.
- Icons and stylesheet moved into `assets/`; `build.sh` renamed to
  `build-macos.sh` for platform symmetry; window icon added.
- The build scripts locate `pyinstaller` themselves (venv, `~/.local/bin`,
  `PATH`) instead of assuming it is on `PATH`.
- Stable app identity on Linux, so the launcher/taskbar association
  survives.
- Fixed a duplicate `Ctrl+,` binding — Qt treats a key bound to both a
  `QShortcut` and a menu action as ambiguous and fires **neither**, so the
  Preferences shortcut silently did nothing.
- `LICENSE` and a legal note covering content types and terms of service.

## 1.1.20 — 2026-08-31

Initial public import. Single-site YouTube downloader: quality selection,
subtitle handling with resync/merge, SponsorBlock, NFO and EDL output,
preferences, and the macOS dock progress overlay.

---

## Reading this against your files

Three releases change what lands on disk, and only one of them announces
itself:

- **1.3.10** — a single subtitle is written as `Title.srt`, not
  `Title.en.srt`.
- **1.3.13 / 1.4.0** — subtitle layout is rewritten. Existing `.srt`
  files are **not** re-laid out, so an old file and a new one downloaded
  from the same app will legitimately differ.
- **1.4.0** — SponsorBlock subtitles are no longer retimed into an edited
  timeline. In practice a no-op (the code was already dead), but it is
  recorded here so the behaviour is not mistaken for a regression.

**SponsorBlock marks, it never cuts.** `--sponsorblock-mark all` has been
in place since the first public release; the media is untouched and an
`.edl` is written for the player to jump. Cutting was abandoned because
SponsorBlock boundaries fall mid-GOP: a clean cut means snapping to the
nearest keyframe, so the cut is not where it was asked to be — or
re-encoding the whole file. Letting the player jump has neither
constraint.
