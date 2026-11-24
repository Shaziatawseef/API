
from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import yt_dlp
import uvicorn

from fastapi import FastAPI, HTTPException, Query
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "Media Extraction API"
APP_VERSION = "2.3.0"

BASE_DIR = Path(__file__).resolve().parent

CACHE_DIR = Path("/tmp/yt-dlp-cache")

PORT = int(
    os.getenv(
        "SERVER_PORT",
        os.getenv("PORT", "10000")
    )
)

POT_PROVIDER_URL = os.getenv(
    "POT_PROVIDER_URL",
    "http://127.0.0.1:4416"
).rstrip("/")

DOWNLOAD_TIMEOUT = int(
    os.getenv("DOWNLOAD_TIMEOUT", "120")
)

NODE_PATH = shutil.which("node")

NODE_READY = False
NODE_VERSION = None

POT_PROVIDER_READY = False

EXTRACTION_LOCK = threading.Semaphore(1)

START_TIME = time.time()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

logger = logging.getLogger("media-api")


# ============================================================
# RUNTIME INITIALIZATION
# ============================================================

def initialize_runtime():
    global NODE_READY, NODE_VERSION

    NODE_PATH = shutil.which("node")

    if not NODE_PATH:
        NODE_READY = False
        NODE_VERSION = None

        logger.warning("Node.js was not found.")

        return

    try:
        result = subprocess.run(
            [NODE_PATH, "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )

        version = result.stdout.strip()

        NODE_VERSION = version

        major = int(
            version.lstrip("v").split(".")[0]
        )

        NODE_READY = major >= 22

        if NODE_READY:
            logger.info(
                "Node.js runtime ready: %s",
                version
            )
        else:
            logger.warning(
                "Node.js version %s is below recommended v22.",
                version
            )

    except Exception as exc:
        NODE_READY = False
        NODE_VERSION = None

        logger.exception(
            "Node.js initialization failed: %s",
            exc
        )


# ============================================================
# PO TOKEN PROVIDER
# ============================================================

def check_pot_provider():
    global POT_PROVIDER_READY

    try:
        parsed = urlparse(POT_PROVIDER_URL)

        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 4416

        # First check whether the provider port is reachable.
        with socket.create_connection(
            (host, port),
            timeout=3
        ):
            pass

        # Then check its HTTP health endpoint.
        with urlopen(
            f"{POT_PROVIDER_URL}/ping",
            timeout=5
        ) as response:

            POT_PROVIDER_READY = (
                response.status == 200
            )

        if POT_PROVIDER_READY:
            logger.info(
                "PO Token provider is ready: %s",
                POT_PROVIDER_URL
            )

    except Exception as exc:
        POT_PROVIDER_READY = False

        logger.warning(
            "PO Token provider is unavailable: %s",
            exc
        )

    return POT_PROVIDER_READY


# ============================================================
# RUNTIME INFORMATION
# ============================================================

def runtime_info():

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),

        "node": NODE_READY,
        "node_version": NODE_VERSION,
        "node_path": NODE_PATH,

        "yt_dlp_version": yt_dlp.version.__version__,

        "po_token_provider": POT_PROVIDER_READY,
        "po_token_provider_url": POT_PROVIDER_URL,

        "youtube_client": "mweb",

        "authentication_mode": "guest",
        "cookies_configured": False,

        "javascript_runtime": (
            "node" if NODE_READY else None
        )
    }


# ============================================================
# URL VALIDATION
# ============================================================

def validate_url(url: str):

    try:
        parsed = urlparse(url)

        hostname = (parsed.hostname or "").lower()

        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only HTTP and HTTPS URLs are supported.")

        valid_host = (
            hostname == "youtube.com"
            or hostname.endswith(".youtube.com")
            or hostname == "youtu.be"
        )

        if not valid_host:
            raise ValueError("Only YouTube URLs are supported.")

        return url

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid YouTube URL",
                "message": str(exc)
            }
        )


# ============================================================
# YT-DLP OPTIONS
# ============================================================

def build_ydl_options():

    options = {
        "quiet": True,

        "no_warnings": False,

        "verbose": (
            os.getenv("YTDLP_VERBOSE", "0") == "1"
        ),

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
            "temp": "/tmp"
        },

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),

            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),

            "Accept-Language": "en-US,en;q=0.9",

            "Sec-Fetch-Mode": "navigate"
        },

        # Guest mode:
        # No Google account cookies.
        # No cookies-from-browser.
        # No manually supplied authentication.

        "extractor_args": {
            "youtube": {
                "player_client": ["mweb"]
            },

            "youtubepot-bgutilhttp": {
                "base_url": POT_PROVIDER_URL
            }
        }
    }

    # JavaScript challenge support.
    if NODE_READY and NODE_PATH:

        options["js_runtimes"] = {
            "node": {
                "path": NODE_PATH
            }
        }

        options["remote_components"] = [
            "ejs:github"
        ]

    return options


# ============================================================
# FORMAT HELPERS
# ============================================================

def has_video(fmt):

    codec = fmt.get("vcodec")

    return (
        codec is not None
        and codec != "none"
    )


def has_audio(fmt):

    codec = fmt.get("acodec")

    return (
        codec is not None
        and codec != "none"
    )


def is_direct_stream(fmt):

    protocol = str(
        fmt.get("protocol") or ""
    ).lower()

    url = fmt.get("url")

    return (
        protocol in ("https", "http")
        and bool(url)
    )


def stream_protocol_score(fmt):

    protocol = str(
        fmt.get("protocol") or ""
    ).lower()

    if protocol == "https":
        return 3

    if protocol == "http":
        return 2

    return 0


def format_score(fmt):

    return (
        int(fmt.get("quality") or 0),

        int(fmt.get("height") or 0),

        float(fmt.get("fps") or 0),

        float(fmt.get("tbr") or 0),

        stream_protocol_score(fmt),

        int(fmt.get("filesize") or 0)
    )


def select_best_format(formats):

    if not formats:
        return None

    return max(
        formats,
        key=format_score
    )


# ============================================================
# STREAM INFORMATION
# ============================================================

def stream_info(fmt):

    return {
        "format_id": fmt.get("format_id"),

        "format": fmt.get("format"),

        "ext": fmt.get("ext"),

        "url": fmt.get("url"),

        "protocol": fmt.get("protocol"),

        "width": fmt.get("width"),

        "height": fmt.get("height"),

        "resolution": fmt.get("resolution"),

        "fps": fmt.get("fps"),

        "vcodec": fmt.get("vcodec"),

        "acodec": fmt.get("acodec"),

        "abr": fmt.get("abr"),

        "vbr": fmt.get("vbr"),

        "tbr": fmt.get("tbr"),

        "asr": fmt.get("asr"),

        "audio_channels": fmt.get("audio_channels"),

        "filesize": fmt.get("filesize"),

        "filesize_approx": fmt.get("filesize_approx"),

        "format_note": fmt.get("format_note"),

        "language": fmt.get("language"),

        "quality": fmt.get("quality"),

        "direct": is_direct_stream(fmt)
    }


# ============================================================
# FORMAT CLASSIFICATION
# ============================================================

def classify_formats(formats):

    audio_formats = []
    video_formats = []
    combined_formats = []

    for fmt in formats:

        if not is_direct_stream(fmt):
            continue

        video = has_video(fmt)
        audio = has_audio(fmt)

        info = stream_info(fmt)

        if video and audio:

            combined_formats.append(info)

        elif video:

            video_formats.append(info)

        elif audio:

            audio_formats.append(info)

    audio_formats.sort(
        key=lambda x: (
            x.get("abr") or 0,
            x.get("tbr") or 0
        ),
        reverse=True
    )

    video_formats.sort(
        key=lambda x: (
            x.get("height") or 0,
            x.get("fps") or 0,
            x.get("tbr") or 0
        ),
        reverse=True
    )

    combined_formats.sort(
        key=lambda x: (
            x.get("height") or 0,
            x.get("fps") or 0,
            x.get("tbr") or 0
        ),
        reverse=True
    )

    return {
        "audio": audio_formats,
        "video": video_formats,
        "combined": combined_formats
    }


# ============================================================
# ERROR DIAGNOSTICS
# ============================================================

def build_diagnostics(video_id):

    return {
        "video_id": video_id,

        "node": NODE_READY,

        "node_version": NODE_VERSION,

        "cookies_configured": False,

        "authentication_mode": "guest",

        "youtube_client": "mweb",

        "po_token_provider": POT_PROVIDER_READY,

        "po_token_provider_url": POT_PROVIDER_URL,

        "yt_dlp_version": yt_dlp.version.__version__,

        "possible_causes": [
            "YouTube HTTP 429 rate limiting",

            "YouTube IP reputation restriction",

            "YouTube guest session LOGIN_REQUIRED",

            "PO Token generation or validation failure",

            "Video age or region restriction",

            "Video unavailable or private"
        ]
    }


def classify_extraction_error(message):

    message = message.lower()

    if "429" in message or "too many requests" in message:

        return "YOUTUBE_RATE_LIMITED"

    if (
        "login_required" in message
        or "sign in to confirm" in message
        or "not a bot" in message
    ):

        return "YOUTUBE_GUEST_AUTH_REQUIRED"

    if "private video" in message:

        return "PRIVATE_VIDEO"

    if "video unavailable" in message:

        return "VIDEO_UNAVAILABLE"

    return "EXTRACTION_FAILED"


# ============================================================
# VIDEO EXTRACTION
# ============================================================

def extract_video(url: str):

    validate_url(url)

    video_id = None

    logger.info(
        "Starting guest-mode extraction. "
        "PO provider ready: %s; "
        "YouTube client: mweb; "
        "cookies configured: False",
        POT_PROVIDER_READY
    )

    CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    options = build_ydl_options()

    try:

        with YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=False
            )

        if not info:

            raise ValueError(
                "YouTube returned an empty response."
            )

        video_id = info.get("id")

        formats = info.get("formats") or []

        if not formats:

            logger.warning(
                "No formats returned for video ID: %s",
                video_id
            )

            raise HTTPException(
                status_code=422,
                detail={
                    "error": "YouTube returned no playable formats.",

                    "code": "NO_PLAYABLE_FORMATS",

                    "video_id": video_id,

                    "diagnostics": build_diagnostics(
                        video_id
                    )
                }
            )

        streams = classify_formats(formats)

        total_streams = (
            len(streams["audio"])
            + len(streams["video"])
            + len(streams["combined"])
        )

        if total_streams == 0:

            raise HTTPException(
                status_code=422,
                detail={
                    "error": "No supported direct HTTP streams were found.",

                    "code": "NO_DIRECT_STREAMS",

                    "video_id": video_id,

                    "diagnostics": build_diagnostics(
                        video_id
                    )
                }
            )

        result = {
            "success": True,

            "service": APP_NAME,

            "version": APP_VERSION,

            "video_id": video_id,

            "title": info.get("title"),

            "thumbnail": info.get("thumbnail"),

            "duration": info.get("duration"),

            "duration_string": info.get("duration_string"),

            "uploader": info.get("uploader"),

            "channel": info.get("channel"),

            "channel_url": info.get("channel_url"),

            "view_count": info.get("view_count"),

            "upload_date": info.get("upload_date"),

            "webpage_url": info.get("webpage_url"),

            "age_limit": info.get("age_limit"),

            "streams": streams,

            "total_formats": len(formats),

            "total_direct_streams": total_streams,

            "best": {
                "audio": (
                    streams["audio"][0]
                    if streams["audio"]
                    else None
                ),

                "video": (
                    streams["video"][0]
                    if streams["video"]
                    else None
                ),

                "combined": (
                    streams["combined"][0]
                    if streams["combined"]
                    else None
                )
            },

            "authentication": {
                "mode": "guest",
                "cookies_used": False,
                "youtube_client": "mweb"
            }
        }

        logger.info(
            "Extraction successful: %s | "
            "Audio: %s | Video: %s | Combined: %s",
            video_id,
            len(streams["audio"]),
            len(streams["video"]),
            len(streams["combined"])
        )

        return result

    except HTTPException:

        raise

    except DownloadError as exc:

        message = str(exc)

        logger.warning(
            "yt-dlp extraction error for %s: %s",
            video_id,
            message
        )

        raise HTTPException(
            status_code=422,
            detail={
                "error": message,

                "code": classify_extraction_error(
                    message
                ),

                "video_id": video_id,

                "diagnostics": build_diagnostics(
                    video_id
                )
            }
        )

    except Exception as exc:

        logger.exception(
            "Unexpected extraction error for %s",
            video_id
        )

        raise HTTPException(
            status_code=500,
            detail={
                "error": str(exc),

                "code": "INTERNAL_EXTRACTION_ERROR",

                "video_id": video_id,

                "diagnostics": build_diagnostics(
                    video_id
                )
            }
        )


# ============================================================
# APPLICATION LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    logger.info(
        "Starting %s version %s",
        APP_NAME,
        APP_VERSION
    )

    initialize_runtime()

    check_pot_provider()

    logger.info(
        "Authentication mode: guest"
    )

    logger.info(
        "Google cookies: disabled"
    )

    yield

    logger.info(
        "Shutting down %s",
        APP_NAME
    )


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title=APP_NAME,

    description=(
        "YouTube media extraction API "
        "with guest-mode authentication "
        "and automatic PO Token support."
    ),

    version=APP_VERSION,

    lifespan=lifespan
)


# ============================================================
# HOME ENDPOINT
# ============================================================

@app.get("/")
async def home():

    return {
        "success": True,

        "service": APP_NAME,

        "version": APP_VERSION,

        "status": "online",

        "authentication_mode": "guest",

        "cookies_required": False,

        "youtube_client": "mweb",

        "po_token_provider": POT_PROVIDER_READY,

        "yt_dlp_version": yt_dlp.version.__version__,

        "endpoints": {
            "home": "/",

            "health": "/health",

            "extract": "/extract?url=YOUTUBE_URL",

            "docs": "/docs"
        }
    }


# ============================================================
# HEALTH ENDPOINT
# ============================================================

@app.get("/health")
async def health():

    return {
        "success": True,

        "status": "healthy",

        "service": APP_NAME,

        "version": APP_VERSION,

        "uptime": round(
            time.time() - START_TIME,
            2
        ),

        "runtime": runtime_info(),

        "extraction": {
            "timeout": DOWNLOAD_TIMEOUT,

            "concurrent_extractions": 1
        }
    }


# ============================================================
# EXTRACTION ENDPOINT
# ============================================================

@app.get("/extract")
def extract(
    url: str = Query(
        ...,
        description="Public YouTube video URL"
    )
):

    validate_url(url)

    acquired = EXTRACTION_LOCK.acquire(
        timeout=DOWNLOAD_TIMEOUT
    )

    if not acquired:

        raise HTTPException(
            status_code=503,
            detail={
                "error": "Extraction service is busy.",

                "code": "EXTRACTION_BUSY",

                "retry_after": DOWNLOAD_TIMEOUT
            }
        )

    try:

        return extract_video(url)

    finally:

        EXTRACTION_LOCK.release()


# ============================================================
# APPLICATION ENTRY POINT
# ============================================================

if __name__ == "__main__":

    uvicorn.run(
        "app:app",

        host="0.0.0.0",

        port=PORT,

        log_level="info",

        workers=1
    )
