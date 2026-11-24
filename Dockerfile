
from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import subprocess
import threading
import time

from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import yt_dlp
import uvicorn

from fastapi import FastAPI, HTTPException, Query
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("media-api")


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "Media Extraction API"
APP_VERSION = "2.1.0"

BASE_DIR = Path(__file__).resolve().parent

COOKIE_FILE = Path("/tmp/youtube-cookies.txt")
CACHE_DIR = Path("/tmp/yt-dlp-cache")

PORT = int(os.getenv("SERVER_PORT", os.getenv("PORT", "10000")))

POT_PROVIDER_URL = os.getenv(
    "POT_PROVIDER_URL",
    "http://127.0.0.1:4416",
)

DOWNLOAD_TIMEOUT = 120

NODE_READY = False
NODE_ERROR = None
NODE_PATH = None

POT_READY = False
POT_ERROR = None

EXTRACTION_LOCK = threading.Semaphore(1)


# ============================================================
# NODE.JS RUNTIME
# ============================================================

def initialize_runtime():
    global NODE_READY, NODE_ERROR, NODE_PATH

    NODE_PATH = shutil.which("node")

    if not NODE_PATH:
        NODE_READY = False
        NODE_ERROR = "Node.js was not found in PATH."
        logger.error(NODE_ERROR)
        return

    try:
        result = subprocess.run(
            [NODE_PATH, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

        version = result.stdout.strip()

        major = int(version.lstrip("v").split(".")[0])

        if major < 22:
            raise RuntimeError(
                f"Node.js 22 or newer is required. Found {version}"
            )

        NODE_READY = True
        NODE_ERROR = None

        logger.info("Node.js ready: %s (%s)", NODE_PATH, version)

    except Exception as exc:
        NODE_READY = False
        NODE_ERROR = str(exc)
        logger.exception("Node.js initialization failed")


def runtime_info():
    result = {}

    if NODE_PATH and NODE_READY:
        result["node"] = NODE_PATH

    return result


# ============================================================
# BGUTIL PO TOKEN PROVIDER
# ============================================================

def check_pot_provider():
    """
    Check whether the local BgUtils HTTP provider is reachable.
    """

    global POT_READY, POT_ERROR

    parsed = urlparse(POT_PROVIDER_URL)

    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 4416

    try:
        with socket.create_connection(
            (host, port),
            timeout=3,
        ):
            POT_READY = True
            POT_ERROR = None

            logger.info(
                "BgUtils PO Token provider reachable at %s",
                POT_PROVIDER_URL,
            )

    except Exception as exc:
        POT_READY = False
        POT_ERROR = str(exc)

        logger.error(
            "BgUtils provider is unavailable: %s",
            POT_ERROR,
        )


# ============================================================
# COOKIES
# ============================================================

def get_cookie_file():
    """
    Read Netscape-format cookies from YOUTUBE_COOKIES.

    Never hardcode account cookies in source code.
    """

    cookie_data = os.getenv(
        "YOUTUBE_COOKIES",
        "",
    ).strip()

    if not cookie_data:
        try:
            COOKIE_FILE.unlink(missing_ok=True)
        except OSError:
            pass

        return None

    if not cookie_data.startswith(
        (
            "# Netscape HTTP Cookie File",
            "# HTTP Cookie File",
        )
    ):
        raise HTTPException(
            status_code=500,
            detail=(
                "Invalid YOUTUBE_COOKIES format. "
                "Use Netscape cookies.txt."
            ),
        )

    try:
        COOKIE_FILE.write_text(
            cookie_data + "\n",
            encoding="utf-8",
        )

        COOKIE_FILE.chmod(0o600)

        return str(COOKIE_FILE)

    except OSError:
        logger.exception("Cookie file preparation failed")

        raise HTTPException(
            status_code=500,
            detail="Could not prepare cookie file.",
        )


# ============================================================
# URL VALIDATION
# ============================================================

def validate_url(url: str):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        valid_host = (
            host == "youtube.com"
            or host.endswith(".youtube.com")
            or host == "youtu.be"
            or host.endswith(".youtu.be")
        )

        if parsed.scheme not in ("http", "https") or not valid_host:
            raise ValueError()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Only valid YouTube URLs are supported.",
        )


# ============================================================
# YT-DLP OPTIONS
# ============================================================

def build_ydl_options():

    options = {
        "quiet": True,
        "no_warnings": False,
        "verbose": os.getenv("YTDLP_VERBOSE", "0") == "1",

        "noplaylist": True,
        "skip_download": True,

        "ignore_no_formats_error": True,

        "extractor_retries": 2,
        "retries": 1,
        "fragment_retries": 1,

        "socket_timeout": 20,

        "cachedir": str(CACHE_DIR),

        "paths": {
            "home": "/tmp",
            "temp": "/tmp",
        },

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },

        # BgUtils PO Token Provider
        "extractor_args": {
            "youtubepot-bgutilhttp": {
                "base_url": POT_PROVIDER_URL,
            },
        },
    }

    # JavaScript challenge solver
    if NODE_READY:
        options["js_runtimes"] = {
            "node": {
                "path": NODE_PATH,
            },
        }

        options["remote_components"] = ["ejs:github"]

    # Optional cookies
    cookie_file = get_cookie_file()

    if cookie_file:
        options["cookiefile"] = cookie_file

    return options


# ============================================================
# FORMAT HELPERS
# ============================================================

def has_video(fmt):
    codec = fmt.get("vcodec")
    return bool(codec and codec != "none")


def has_audio(fmt):
    codec = fmt.get("acodec")
    return bool(codec and codec != "none")


def is_direct_stream(fmt):
    return fmt.get("protocol") in ("https", "http")


def stream_protocol_score(fmt):
    protocol = fmt.get("protocol")

    if protocol == "https":
        return 3

    if protocol == "http":
        return 2

    if protocol in ("http_dash_segments", "dash"):
        return 1

    return 0


def format_score(fmt, media_type):

    protocol_score = stream_protocol_score(fmt)

    filesize = (
        fmt.get("filesize")
        or fmt.get("filesize_approx")
        or 0
    )

    if media_type == "video":
        return (
            fmt.get("height") or 0,
            fmt.get("fps") or 0,
            fmt.get("vbr") or fmt.get("tbr") or 0,
            protocol_score,
            filesize,
        )

    if media_type == "audio":
        return (
            fmt.get("abr") or fmt.get("tbr") or 0,
            fmt.get("asr") or 0,
            fmt.get("audio_channels") or 0,
            protocol_score,
            filesize,
        )

    return (
        fmt.get("height") or 0,
        fmt.get("fps") or 0,
        fmt.get("vbr") or fmt.get("tbr") or 0,
        fmt.get("abr") or 0,
        protocol_score,
        filesize,
    )


def select_best_format(formats, media_type):

    candidates = []

    for fmt in formats:

        if not fmt.get("url"):
            continue

        if fmt.get("has_drm"):
            continue

        video = has_video(fmt)
        audio = has_audio(fmt)

        if media_type == "video" and video and not audio:

            if is_direct_stream(fmt):
                candidates.append(fmt)

        elif media_type == "audio" and audio and not video:

            candidates.append(fmt)

        elif media_type == "combined" and video and audio:

            candidates.append(fmt)

    if not candidates:
        return None

    if media_type != "video":

        direct_candidates = [
            fmt for fmt in candidates
            if is_direct_stream(fmt)
        ]

        if direct_candidates:
            candidates = direct_candidates

    return max(
        candidates,
        key=lambda fmt: format_score(fmt, media_type),
        default=None,
    )


# ============================================================
# STREAM INFORMATION
# ============================================================

def stream_info(fmt, media_type):

    if not fmt:
        return None

    ext = fmt.get("ext")

    if media_type == "audio":

        bitrate = fmt.get("abr") or fmt.get("tbr")

        if bitrate:
            quality = (
                "high" if bitrate >= 128
                else "medium" if bitrate >= 96
                else "low"
            )
        else:
            quality = fmt.get("format_note") or "unknown"

    else:

        height = fmt.get("height")

        quality = (
            f"{height}p"
            if height
            else fmt.get("format_note") or "unknown"
        )

    result = {
        "url": fmt.get("url"),
        "format_id": fmt.get("format_id"),
        "ext": ext,
        "quality": quality,
    }

    if media_type == "audio":
        result["bitrate"] = fmt.get("abr") or fmt.get("tbr")

    elif media_type == "video":
        result["width"] = fmt.get("width")
        result["height"] = fmt.get("height")

    return result


# ============================================================
# FORMAT CLASSIFICATION
# ============================================================

def classify_formats(formats):

    best_combined = select_best_format(
        formats,
        "combined",
    )

    best_audio = select_best_format(
        formats,
        "audio",
    )

    best_video = select_best_format(
        formats,
        "video",
    )

    return best_combined, best_audio, best_video


# ============================================================
# VIDEO EXTRACTION
# ============================================================

def extract_video(url):

    started = time.perf_counter()

    options = build_ydl_options()

    logger.info(
        "Starting extraction. PO provider ready: %s",
        POT_READY,
    )

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(
            url,
            download=False,
        )

    if not info:
        raise HTTPException(
            status_code=404,
            detail="No media information returned.",
        )

    if info.get("_type") == "playlist":
        raise HTTPException(
            status_code=400,
            detail="Playlist URLs are not supported.",
        )

    formats = info.get("formats") or []

    if not formats:

        logger.warning(
            "No formats returned for video ID: %s",
            info.get("id"),
        )

        raise HTTPException(
            status_code=422,
            detail={
                "error": "YouTube returned no playable formats.",

                "video_id": info.get("id"),

                "diagnostics": {
                    "node": NODE_READY,
                    "node_path": NODE_PATH,
                    "cookies_configured": bool(
                        os.getenv("YOUTUBE_COOKIES", "").strip()
                    ),
                    "po_token_provider_ready": POT_READY,
                    "po_token_provider_error": POT_ERROR,
                },

                "possible_causes": [
                    "YouTube player challenge",
                    "Authentication requirement",
                    "PO Token requirement",
                    "Age or region restriction",
                    "Unavailable video formats",
                    "Server IP restriction",
                ],

                "hint": (
                    "Check Render logs for the original yt-dlp "
                    "warnings and BgUtils provider status."
                ),
            },
        )

    combined, audio, video_only = classify_formats(formats)

    if not any((combined, audio, video_only)):

        raise HTTPException(
            status_code=422,
            detail="No usable direct stream URLs were returned.",
        )

    elapsed = round(
        time.perf_counter() - started,
        3,
    )

    return {
        "success": True,

        "video": {
            "id": info.get("id"),
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
        },

        "streams": {
            "audio": stream_info(audio, "audio"),
            "video": stream_info(video_only, "video"),
            "combined": stream_info(combined, "combined"),
        },

        "merge_required": combined is None and bool(
            audio and video_only
        ),

        "processing_time": elapsed,
    }


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    logger.info("Starting %s", APP_NAME)

    initialize_runtime()
    check_pot_provider()

    yield

    logger.info("Shutting down %s", APP_NAME)


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    description=(
        "YouTube video, audio and combined stream extraction API"
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():

    runtimes = runtime_info()

    return {
        "success": True,
        "service": APP_NAME,
        "version": APP_VERSION,

        "yt_dlp_version": yt_dlp.version.__version__,

        "javascript_runtimes": runtimes,

        "node_ready": NODE_READY,
        "node_error": NODE_ERROR,

        "cookie_configured": bool(
            os.getenv("YOUTUBE_COOKIES", "").strip()
        ),

        "po_token_provider": {
            "enabled": True,
            "ready": POT_READY,
            "url": POT_PROVIDER_URL,
            "error": POT_ERROR,
        },

        "endpoints": {
            "home": "/",
            "health": "/health",
            "extract": "/extract?url=YOUTUBE_URL",
        },

        "supported_streams": [
            "audio",
            "video",
            "combined",
        ],
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "success": True,
        "status": "healthy",

        "node": NODE_READY,
        "node_path": NODE_PATH,
        "node_error": NODE_ERROR,

        "cookies_configured": bool(
            os.getenv("YOUTUBE_COOKIES", "").strip()
        ),

        "po_token_provider_ready": POT_READY,
        "po_token_provider_error": POT_ERROR,

        "yt_dlp_version": yt_dlp.version.__version__,
    }


# ============================================================
# EXTRACT
# ============================================================

@app.get("/extract")
def extract(
    url: str = Query(
        ...,
        description="YouTube video URL",
    ),
):

    validate_url(url)

    if not EXTRACTION_LOCK.acquire(blocking=False):

        raise HTTPException(
            status_code=429,
            detail="Another extraction is running. Try again shortly.",
        )

    try:

        return extract_video(url)

    except HTTPException:
        raise

    except DownloadError as exc:

        logger.warning(
            "YouTube extraction failed: %s",
            exc,
        )

        raise HTTPException(
            status_code=422,
            detail={
                "error": "YouTube extraction failed.",
                "reason": str(exc),
                "hint": (
                    "Check Node.js, cookies, BgUtils PO Token "
                    "provider and yt-dlp logs."
                ),
            },
        ) from exc

    except Exception as exc:

        logger.exception("Unexpected extraction error")

        raise HTTPException(
            status_code=500,
            detail=(
                "Unexpected extraction error. Check server logs."
            ),
        ) from exc

    finally:

        EXTRACTION_LOCK.release()


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        workers=1,
        access_log=False,
    )
