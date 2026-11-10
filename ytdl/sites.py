"""Site profiles, URL detection and normalization (site-aware)."""

import re
from urllib.parse import urlparse, parse_qs

from ytdl.config import INFO_FETCH_TIMEOUT_SECONDS


YOUTUBE_ID_REGEX = re.compile(
    r"(?:v=|\/|embed\/|shorts\/|live\/)([a-zA-Z0-9_-]{11})(?:[?&/ ]|$)"
)

RUMBLE_ID_REGEX = re.compile(
    r"rumble\.com/(?:embed/)?(v[a-zA-Z0-9]+)(?:[?&/ -]|$)"
)

# LBRY/Odysee claim ids are 1-40 hex chars, appended to a slug/intent with
# ":" (web URLs) or "#" (lbry:// URIs, not accepted as input here). Anchored
# to the end of the URL so the video claim wins over a channel's "…@Name:1".
ODYSEE_ID_REGEX = re.compile(r"[:#]([0-9a-f]{1,40})/?(?:[?#].*)?$")

# ARD Mediathek video pages: trailing CRID id after /video/ (also /player/,
# /live/), optionally under a sender prefix - mirrors ARDBetaMediathekIE.
ARD_ID_REGEX = re.compile(r"/(?:player|live|video)/(?:[^?#]+/)?([^/?#]+)/?(?:[?#].*)?$")

# ZDF video pages: last path segment of a /video/ or /play/ URL (legacy
# "<slug>.html" pages carry no stable segment we need - extract_video_id is
# only wired for YouTube today).
ZDF_ID_REGEX = re.compile(r"/(?:video|play)/(?:[^?#]+/)?([^/?#]+)/?(?:[?#].*)?$")

# RTVE Play / RTVE.es a la carte video pages: trailing numeric asset id after
# /play/videos/<programa>/<slug>/ (mirrors RTVEAlacartaIE's own pattern).
RTVE_ID_REGEX = re.compile(r"/(\d{6,})(?:/|\.shtml)?(?:[?#].*)?$")

# ----------------------------------------------------------------------------------------------------
# Supported sites (YouTube + Rumble + Odysee + ARD/ZDF Mediathek + RTVE Play)
# Each entry defines how the site is detected and which features it supports.
# ----------------------------------------------------------------------------------------------------
SUPPORTED_SITES = {
    "youtube": {
        "label": "YouTube",
        "domains": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
        "sponsorblock": True,
        "js_runtime": True,  # yt-dlp evaluates YouTube's JS sig/nsig challenges
        "resync_auto_subs": True,  # YouTube ASR captions arrive as choppy fragments
        "supports_subtitles": True,
        "always_extract_audio": False,  # YouTube offers audio-only streams
        # Never strip: YouTube has no embedded EIA-608 CCs, and unit type 6
        # is a Frame OBU in AV1 - the filter corrupted AV1 downloads
        "strip_embedded_cc": False,
        # Channel / playlist / non-video pages (rejected by the input gate)
        "channel_url_regex": re.compile(
            r"^/?$|^/(?:@|c/|user/|channel/|playlist|results|feed)"
        ),
        "id_regex": YOUTUBE_ID_REGEX,
    },
    "rumble": {
        "label": "Rumble",
        "domains": ("rumble.com",),
        "sponsorblock": False,  # SponsorBlock is YouTube-only
        "js_runtime": False,  # the Rumble extractor needs no JS runtime
        "resync_auto_subs": False,  # Rumble's subs are already well-formatted
        "supports_subtitles": True,
        "always_extract_audio": False,  # audio-only stream ("audio-192p") exists
        # US broadcast feeds: EIA-608 captions live in H.264 SEI type 6 NALs
        "strip_embedded_cc": True,
        "channel_url_regex": re.compile(r"^/?$|^/(?:c/|user/)"),
        "id_regex": RUMBLE_ID_REGEX,
    },
    "odysee": {
        "label": "Odysee",
        "domains": ("odysee.com", "lbry.tv"),
        "sponsorblock": False,  # SponsorBlock is YouTube-only
        "js_runtime": False,  # the LBRY extractor needs no JS runtime
        "resync_auto_subs": False,  # no subtitle tracks at all (see below)
        "supports_subtitles": False,  # extractor exposes no subtitles
        "always_extract_audio": True,  # no audio-only streams -> -x needed
        "strip_embedded_cc": False,  # web uploads, no broadcast CCs
        # The LBRY API "resolve" call can take ~40s (measured; short claim ids
        # like ":d" are slow), far beyond the 15s default budget
        "info_timeout": 90,
        # Shown as a progress hint while the info fetch is still running
        "slow_hint": "Odysee's LBRY API is slow; a claim can take ~70s to resolve",
        # Channel page without a video claim (optionally under $/embed/)
        "channel_url_regex": re.compile(
            r"^/?$|^/(?:\$/(?:embed|download)/)?@[^/]+/?$"
        ),
        "id_regex": ODYSEE_ID_REGEX,
    },
    "ard": {
        "label": "ARD Mediathek",
        # Only for the supported-sites LIST; per-site messages still say
        # "ARD Mediathek" (see _site_list_label).
        "short_label": "ARD/ZDF Mediathek",
        "domains": ("ardmediathek.de",),
        "sponsorblock": False,  # SponsorBlock is YouTube-only
        "js_runtime": False,  # the ARD extractor needs no JS runtime
        "resync_auto_subs": False,  # ARD captions are real (ebutt/webvtt), not ASR
        "supports_subtitles": True,
        "always_extract_audio": False,  # audio-only HLS track exists
        "strip_embedded_cc": False,  # real ebutt/webvtt tracks, no EIA-608
        # Collection pages (sendung/serie/sammlung, optionally under a sender
        # prefix) resolve as playlists via ARDMediathekCollectionIE; the
        # homepage is rejected as well
        "channel_url_regex": re.compile(
            r"^/?$|^/(?:[^/?#]+/)?(?:sendung|serie|sammlung)/"
        ),
        "id_regex": ARD_ID_REGEX,
    },
    "zdf": {
        "label": "ZDF Mediathek",
        "short_label": "ARD/ZDF Mediathek",
        "domains": ("zdf.de", "zdfheute.de", "logo.de"),
        "sponsorblock": False,  # SponsorBlock is YouTube-only
        "js_runtime": False,  # the ZDF extractor needs no JS runtime
        "resync_auto_subs": False,  # ZDF captions are real (xml/vtt), not ASR
        "supports_subtitles": True,
        # All ZDF formats are muxed (no audio-only stream) -> "Best" audio
        # must -x like Odysee, otherwise the source video file would be saved
        "always_extract_audio": True,
        "strip_embedded_cc": False,  # real xml/vtt tracks, no EIA-608
        # Inverse heuristic: ZDFChannelIE is a catch-all playlist for every
        # zdf.de path that is not a video page - /video/, /play/ and legacy
        # "<slug>.html" single videos (incl. the sister sites) pass,
        # everything else (shows, magazines, the homepage) is rejected.
        "channel_url_regex": re.compile(r"^(?!(?:/video/|/play/))(?!.*\.html$)"),
        "id_regex": ZDF_ID_REGEX,
    },
    "rtve": {
        "label": "RTVE Play",
        # RTVE's on-demand service (rtve.es/play, formerly RTVE A la Carta);
        # www.rtve.es is canonicalized to rtve.es by _hostname().
        "domains": ("rtve.es",),
        "sponsorblock": False,  # SponsorBlock is YouTube-only
        "js_runtime": False,  # the RTVE extractor needs no JS runtime
        "resync_auto_subs": False,  # captions are broadcast/teletext tracks, not choppy ASR
        "supports_subtitles": True,  # the extractor exposes subtitle tracks
        "always_extract_audio": False,  # audio-only DASH streams exist
        "strip_embedded_cc": False,  # no broadcast EIA-608 in the streams
        # yt-dlp's RTVE extractor only populates subtitle metadata in
        # --print-json output when --write-subs is also passed; the app's
        # info fetch needs --write-subs to discover available languages.
        "needs_write_subs_for_info": True,
        # Program/collection listing pages (one path segment after /videos/)
        # resolve to playlists in yt-dlp; single videos carry a trailing
        # numeric asset id plus a slug.
        "channel_url_regex": re.compile(
            r"^/?$|^/(?:m/)?(?:(?:alacarta|play)/videos|filmoteca)/[^/?#]+/?(?:[?#].*)?$"
        ),
        "id_regex": RTVE_ID_REGEX,
    },
}

# Default site profile for unknown domains (yt-dlp may still support them)
DEFAULT_SITE = "youtube"

def _site_list_label(profile):
    """Name to use when a site is listed next to its siblings.

    ARD and ZDF are two extractor profiles but one broadcaster family, so the
    header line says "ARD/ZDF Mediathek" once instead of spending a comma and
    twelve characters on a duplicate "Mediathek". Per-site messages keep using
    the full `label` - "Subtitles: Not available on Odysee" has no siblings to
    disambiguate it from, and a message about one ZDF URL should name ZDF.
    """
    return profile.get("short_label") or profile["label"]


# Human-readable site list for the info panel header, e.g.
# "YouTube, Rumble, Odysee, ARD/ZDF Mediathek". Names are de-duplicated in
# profile order, so two profiles that share a name (ARD + ZDF) are listed once.
SUPPORTED_SITES_LABEL = ", ".join(dict.fromkeys(
    _site_list_label(profile) for profile in SUPPORTED_SITES.values()
))

# Alternation of every known site domain - used by normalize_url() to spot
# schemeless tokens like "zdf.de/video/...". Built from the profiles so a
# new site works without touching the pattern.
KNOWN_SITE_DOMAINS = "|".join(
    re.escape(domain)
    for profile in SUPPORTED_SITES.values()
    for domain in profile["domains"]
)

def _hostname(url):
    """Lowercase hostname of a URL (scheme optional); "" when unparseable.

    urlparse only yields a hostname for scheme URLs, so a scheme is prepended
    for schemeless input like "rumble.com/vXXXXXX" or "RUMBLE.COM/...".
    """
    if not url:
        return ""
    candidate = str(url).strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        host = urlparse(candidate).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def _match_site_key(host):
    """SUPPORTED_SITES key whose domains contain `host`, or None."""
    for site_key, profile in SUPPORTED_SITES.items():
        for domain in profile["domains"]:
            if host == domain or host.endswith("." + domain):
                return site_key
    return None


def detect_site(url):
    """
    Detect which supported site profile a URL belongs to.

    Returns a key into SUPPORTED_SITES ("youtube", "rumble", ...).
    Unknown domains fall back to DEFAULT_SITE (YouTube), preserving the
    historical behavior where any non-matching input was treated as YouTube.
    For gating user input use `is_known_site()` instead.
    """
    return _match_site_key(_hostname(url)) or DEFAULT_SITE


def _path(url):
    """Path component of a URL (scheme optional); "/" when unparseable."""
    if not url:
        return "/"
    candidate = str(url).strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        return urlparse(candidate).path or "/"
    except ValueError:
        return "/"


def _query_params(url):
    """Return parsed query parameters for scheme-optional URL input."""
    if not url:
        return {}
    candidate = str(url).strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        return parse_qs(urlparse(candidate).query)
    except ValueError:
        return {}


def is_known_site(url):
    """
    Whether the URL's hostname belongs to a SUPPORTED_SITES profile.

    This is the info-fetch/paste gate: unknown domains are rejected with a
    message instead of being handed to yt-dlp's generic extractor (which
    would only fail with an "[generic] ..." error). To accept another site,
    add a profile to SUPPORTED_SITES.
    """
    return _match_site_key(_hostname(url)) is not None


def is_channel_url(url):
    """
    Whether the URL points at a channel/playlist/profile page instead of a
    single video (e.g. youtube.com/@handle, rumble.com/c/Name,
    odysee.com/@Channel:1). Those resolve to playlists in yt-dlp and are
    rejected by the input gate; each site profile defines its patterns via
    "channel_url_regex" (matched against the URL path).
    """
    site_key = _match_site_key(_hostname(url))
    if not site_key:
        return False
    # YouTube: a watch URL with ?list= but no ?v= is a playlist, not a video
    if site_key == "youtube":
        qs = _query_params(url)
        if "list" in qs and "v" not in qs:
            return True
    pattern = SUPPORTED_SITES[site_key].get("channel_url_regex")
    if pattern is None:
        return False
    return pattern.search(_path(url)) is not None


def site_info_timeout(site):
    """
    Seconds budget for the yt-dlp info fetch on a site.

    Sites whose extractor/API is slow override the default via their profile's
    "info_timeout" key (e.g. Odysee, whose LBRY API resolve call can take ~40s).
    """
    return SUPPORTED_SITES.get(site, {}).get("info_timeout", INFO_FETCH_TIMEOUT_SECONDS)


def site_slow_hint(site):
    """
    Optional per-site hint shown while a long info fetch is still running
    (e.g. Odysee's slow LBRY resolve). Empty string when the profile has none.
    """
    return SUPPORTED_SITES.get(site, {}).get("slow_hint", "")


def site_resyncs_auto_subs(site):
    """
    Whether auto-generated subtitles of a site need the 2-line merge.

    YouTube's ASR captions arrive as choppy fragments that the resync/merge
    step reformats; sites like Rumble deliver them already well-formatted.
    Site profiles opt out via the "resync_auto_subs" key. Defaults to True
    so unknown/future sites keep the previous always-merge behavior.
    """
    return SUPPORTED_SITES.get(site, {}).get("resync_auto_subs", True)


def site_wants_js_runtime(site):
    """
    Whether yt-dlp benefits from an external JS runtime (deno) for a site.

    Only extractors that evaluate JavaScript need one (YouTube's signature
    challenges); site profiles opt out via the "js_runtime" key. Defaults to
    True so unknown/future sites keep the previous always-pass behavior.
    """
    return SUPPORTED_SITES.get(site, {}).get("js_runtime", True)


def site_supports_sponsorblock(site):
    """
    Whether SponsorBlock data can exist for a site's videos.

    The SponsorBlock database is keyed by YouTube video ids, so only
    YouTube's profile sets "sponsorblock". This is the single gate for every
    SponsorBlock touch point: the API query in check_sponsorblock(),
    --sponsorblock-mark in build_command(), the ".edl only" writer, and the
    download path's EDL sidecar (create_edl_file). Defaults to False — unlike
    js_runtime / resync_auto_subs, which default True to preserve their
    historical always-on behavior: an unknown site must never query the API
    or claim to have segments.
    """
    return SUPPORTED_SITES.get(site, {}).get("sponsorblock", False)


def is_plausible_url(url):
    """
    Cheap syntactic pre-check so obvious non-URLs never reach yt-dlp.

    yt-dlp's generic extractor turns anything it cannot classify into an
    ugly "[generic] 'nonsense' is not a valid URL" subprocess error; this
    keeps typos and garbage out of the yt-dlp call entirely. Deliberately
    permissive: any well-formed dotted hostname (or localhost) passes -
    yt-dlp itself decides whether the site is actually supported.
    """
    if not url:
        return False
    url = str(url).strip()
    # Naked 11-char YouTube ID (normalize_url canonicalizes these too)
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return True
    host = _hostname(url)
    if not host:
        return False
    if host == "localhost":
        return True
    # The hostname must be well-formed: dot-separated labels of letters,
    # digits and hyphens. urlparse is lenient and happily returns hostnames
    # with spaces (e.g. clipboard text like "YT-DLP Downloader 1.1.25"
    # parses as host "yt-dlp downloader 1.1.25"), so an explicit shape
    # check is needed on top of the dot requirement.
    if not re.fullmatch(
        r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*",
        host,
    ):
        return False
    # Single-label hosts ("nonsense") are not URLs; a dot is required.
    return "." in host


# ----------------------------------------------------------------------------------------------------
# Normalize (clean) a YouTube URL
# ----------------------------------------------------------------------------------------------------
def normalize_url(url):
    # Normalize (and if needed, extract) a video URL from user input.
    #
    # The URL field may contain extra surrounding text (e.g. copied from chat):
    #   mytext `https://www.youtube.com/watch?v=xxxxxxxxxxx`
    # In that case we want to extract the actual URL/id instead of passing the
    # whole string to yt-dlp.
    if not url:
        return ""
    # Remove extra whitespace and normalize
    url = re.sub(r"\s+", " ", str(url)).strip()

    # 1) Extract the first URL-like token if there is any scheme URL inside the text.
    m = re.search(r"(https?://[^\s]+)", url, flags=re.IGNORECASE)
    if m:
        url = m.group(1)
    else:
        # 2) Extract a known-site domain token without scheme, e.g.
        #    "www.youtube.com/..." or "rumble.com/vXXXXXX-title.html"
        #    or "zdf.de/video/..." (domains taken from SUPPORTED_SITES)
        m = re.search(
            rf"(?<![A-Za-z0-9./-])((?:[A-Za-z0-9-]+\.)*(?:{KNOWN_SITE_DOMAINS})/[^\s]+)",
            url,
            flags=re.IGNORECASE,
        )
        if m:
            url = "https://" + m.group(1)

    # Strip common wrappers / trailing punctuation from copied text
    url = url.strip("`\"'<>[](){}.,;")

    # A naked 11-character ID is the one intentional exception: it has no
    # hostname, but is explicitly accepted as a YouTube ID.
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", url):
        return f"https://www.youtube.com/watch?v={url}"

    # detect_site() intentionally falls back to YouTube for unknown hosts.
    # Do not use that fallback for canonicalization: only a URL whose actual
    # hostname belongs to the YouTube profile may be rewritten.
    if _match_site_key(_hostname(url)) != "youtube":
        return url

    # If the input still isn't a clean URL, but contains a recognizable YouTube ID,
    # canonicalize it to a watch URL (handles cases like trailing backticks, etc.).
    if m := re.search(
        r"(?:v=|embed/|shorts/|live/|youtu\.be/)([a-zA-Z0-9_-]{11})(?:[&?#/ ]|$)",
        url,
        flags=re.IGNORECASE,
    ):
        return f"https://www.youtube.com/watch?v={m.group(1)}"

    # Keep the query intact when no valid video ID was recognized. In
    # particular, a playlist URL such as watch?foo=bar&list=... must reach
    # is_channel_url() before any cleanup can discard the list parameter.
    return url
