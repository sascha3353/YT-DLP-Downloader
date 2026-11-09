"""Subtitle display layout: turn ASR cues into what a viewer should read.

Pure logic - no Qt, no filesystem. One stage lives here, a module-level
function, so the behaviour can be tested without the app (see
temp/test_subtitle_rolling.py and the seven real captures
temp/subtitle-capture-*.srt).

  rolling_cues() - groups the ASR's own windows in twos, one window per line,
                   so two caption lines show at once exactly as the YouTube
                   player shows them - shown whole instead of rolling in word
                   by word. Nothing is repeated and nothing is re-cut.

Why the ASR file needs no re-packing: the captions already ARE the display.
Each window is a short phrase, every window overlaps the next, and the file has
no line breaks at all - the two lines a viewer sees are two consecutive
windows. So this module groups those windows in twos and changes nothing else.

A sentence-aware layout stage (re-pack the words so cues end at sentence ends,
with durations from a reading-speed model) shipped as 1.4.0-1.4.1 and was
removed in 1.4.3, together with the `subtitle_words_per_cue` preference that
drove it: nothing called either once the paired-window layout took over. It is
in git history if it is ever wanted back - `git show 349d0eb` is the last state
that had it.
"""

from ytdl.preferences import PAUSE_THRESHOLD_MS_DEFAULT

# ---------------------------------------------------------------------------
# Rolling layout: the YouTube player's own look.
#
# The ASR file is already the display, and it needs only one change: pairing.
# Each window is a short phrase, every window overlaps the next (100% of pairs on
# the real captures, median 1.6s), and the downloaded file has NO line breaks at
# all (0 of 2398 cues across seven captures) - because in the YouTube player the
# two lines a viewer sees are two consecutive windows, the finished one on top
# and the next one rolling in underneath.
#
# So the display is rebuilt by grouping the windows in twos: two consecutive
# ASR windows become one two-line cue, shown whole. Nothing is repeated - each
# window appears exactly once, on the line it occupies in the player:
#
#     SET 1   the Peruvian skulls and                  <- window 1
#             No, before I thought Oh, go ahead.       <- window 2
#     SET 2   Sorry.                                   <- window 3
#             No, go ahead.                            <- window 4
#
# A cue starts at its FIRST window's own timestamp - verbatim from the source
# file, not an interpolated word time and not delayed for reading speed - so the
# top line appears exactly when YouTube's would. It ends where the NEXT set
# starts, which is that set's first window, so cues never overlap and the screen
# changes at a moment the ASR itself marks.
#
# No reading-speed model is involved: durations are the ASR's own gaps, so a fast
# speaker gets short cues and a real pause stays blank - the same trade the
# player makes.
#
# Each window's text is written verbatim on its own line - NEVER re-wrapped. A
# long window stays a long line, exactly as YouTube shows it; a player that wants
# to fit it wraps it itself, and second-guessing that here would make the file
# differ from the captions for no gain.
# ---------------------------------------------------------------------------
ROLLING_MIN_DUR = 0.2
# How many consecutive ASR windows share one cue. 2 is the player's look: the
# finished line on top, the new one below.
ROLLING_WINDOWS_PER_CUE = 2


def rolling_cues(cues, windows_per_cue=ROLLING_WINDOWS_PER_CUE,
                 min_dur=ROLLING_MIN_DUR):
    """Group ASR windows into the two-line cues the YouTube player shows.

    Returns (cues, stats). `windows_per_cue` consecutive source windows become
    one cue (2 by default), each window on its own line and shown whole - never
    repeated, never merged with another window's words, and never re-wrapped.

    `start` is the group's FIRST window's own timestamp, verbatim from the
    source file, so the top line appears exactly when YouTube's would. `end` is
    the next group's first window, so cues never overlap and the display changes
    at an instant the ASR itself marks. The last group has no successor and keeps
    its own last window's end.

    A window longer than one line stays one long line: that is what the ASR says
    and what the player shows.
    """
    usable = [c for c in cues if (c.get("text") or "").strip()]
    if not usable:
        return [], {"cues": 0, "two_line": 0, "windows": 0}

    laid_out = []
    step = max(1, windows_per_cue)
    for index in range(0, len(usable), step):
        group = usable[index:index + step]
        start = group[0].get("start") or 0.0

        # Ends where the next group opens: the next window the ASR starts. The
        # last group has no successor, so it keeps its own end - with a floor, so
        # a zero-length source window still displays.
        if index + step < len(usable):
            end = usable[index + step].get("start") or start
        else:
            end = group[-1].get("end") or start
        if end <= start:
            end = start + min_dur

        # One window per line, text verbatim. Whitespace is normalised (the ASR
        # emits stray double spaces and trailing blanks) but nothing is split.
        lines = [" ".join((w.get("text") or "").split()) for w in group]

        laid_out.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": end - start,
            "words": sum(len(line.split()) for line in lines),
            "text": "\n".join(lines),
        })

    stats = {
        "cues": len(laid_out),
        "two_line": sum(1 for c in laid_out if "\n" in c["text"]),
        "windows": len(usable),
    }
    return laid_out, stats


# ---------------------------------------------------------------------------
# Pause-aware layout: break cues at long speech pauses
# ---------------------------------------------------------------------------
#
# rolling_cues() blindly pairs ASR windows in twos, so a long silence between
# two phrases gets hidden inside a single cue block. This function instead
# walks the per-word/per-phrase timeline and starts a new cue whenever the
# gap between the end of one line and the start of the next exceeds
# `pause_threshold_ms`.
#
# Each input line is a dict with "start", "end" (seconds) and "text" (str).
# Lines are grouped into cues of up to two lines (YouTube's rolling look).
# When the gap between consecutive lines is larger than the threshold, the
# current cue is closed and the next line starts a fresh cue — even if that
# means a single-line cue.
#
# Cue start = first line's own start timestamp. Cue end = last line's own end
# timestamp (so the screen goes blank during the pause until the next cue).
#
# Lines are never split or re-wrapped - we only decide where to break between
# existing lines, exactly as requested.
# ---------------------------------------------------------------------------
PAUSE_CUE_MIN_DUR = 0.2


def pause_aware_cues(lines, pause_threshold_ms=PAUSE_THRESHOLD_MS_DEFAULT,
                     min_dur=PAUSE_CUE_MIN_DUR):
    """Group subtitle lines into cues, breaking at long speech pauses.

    `lines` is a list of dicts with "start" (float seconds), "end" (float
    seconds) and "text" (str). They must be sorted by start time.

    Returns (cues, stats). Each cue is a dict with "start", "end", "duration",
    "words" and "text" (lines joined with "\n"). A cue holds at most two lines.

    A gap between the end of line N and the start of line N+1 that exceeds
    `pause_threshold_ms` (milliseconds) closes the current cue and starts a
    new one - producing a blank pause on screen between them. Lines are never
    split or re-wrapped; only the boundary between existing lines can break a
    cue.

    `pause_threshold_ms=0` disables pause-based splitting, in which case lines
    are simply paired in twos like rolling_cues().
    """
    if not lines:
        return [], {"cues": 0, "two_line": 0, "windows": 0}

    threshold_s = pause_threshold_ms / 1000.0
    laid_out = []
    current_cue = [lines[0]]

    for i in range(1, len(lines)):
        prev = lines[i - 1]
        curr = lines[i]
        gap = curr["start"] - prev["end"]

        if gap > threshold_s:
            # Long pause: close current cue at its own last line's end so a
            # blank gap appears on screen until the next phrase starts.
            # Ending at curr["start"] would swallow the pause; instead end at
            # the last line's own end (a pause means curr.start > prev.end, so
            # this can never overlap with the next cue).
            laid_out.append(_cue_from_lines(current_cue, min_dur))
            current_cue = [curr]
        elif len(current_cue) >= 2:
            # Cue is full (two lines) - close it even without a pause.
            # End at curr's start so cues never overlap.
            laid_out.append(_cue_from_lines(current_cue, min_dur, end_override=curr["start"]))
            current_cue = [curr]
        else:
            current_cue.append(curr)

    # Flush the last cue - uses its own last line's end (no successor).
    laid_out.append(_cue_from_lines(current_cue, min_dur))

    stats = {
        "cues": len(laid_out),
        "two_line": sum(1 for c in laid_out if "\n" in c["text"]),
        "windows": len(lines),
    }
    return laid_out, stats


def _cue_from_lines(lines, min_dur, end_override=None):
    """Build a single cue dict from a list of line dicts.

    `end_override` forces the cue end time (used when the next cue's first
    line starts here, so cues never overlap). When None, uses the last line's
    own end time (for the final cue which has no successor).
    """
    start = lines[0]["start"]
    end = end_override if end_override is not None else lines[-1]["end"]
    if end <= start:
        end = start + min_dur
    text = "\n".join(line["text"] for line in lines)
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": end - start,
        "words": sum(len(line.split()) for line in text.split("\n")),
        "text": text,
    }
