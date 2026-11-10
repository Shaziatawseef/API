from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("media-api")

app = FastAPI(
    title="Media Extraction API",
    description="Extracts available audio and video stream information.",
    version="1.1.0",
)

# Render provides writable temporary storage. Never hardcode account cookies here.
COOKIE_FILE = Path("/tmp/youtube-cookies.txt")


def get_cookie_file() -> str | None:
    """Write Netscape-format cookies supplied through Render environment vars."""
    cookie_data = os.getenv("YOUTUBE_COOKIES", "").strip()
    if not cookie_data:
        return None

    if not (
        cookie_data.startswith("# Netscape HTTP Cookie File")
        or cookie_data.startswith("# HTTP Cookie File")
    ):
        raise HTTPException(
            status_code=500,
            detail="YOUTUBE_COOKIES must contain Netscape-format cookies.txt content.",
        )

    try:
        COOKIE_FILE.write_text(cookie_data + "\n", encoding="utf-8")
        try:
            COOKIE_FILE.chmod(0o600)
        except OSError:
            pass
        return str(COOKIE_FILE)
    except OSError as exc:
        logger.exception("Could not write the YouTube cookie file")
        raise HTTPException(status_code=500, detail="Could not prepare the cookie file.") from exc


def validate_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Invalid URL")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid URL") from exc

    # This endpoint is intended for YouTube, not as a general-purpose URL fetcher.
    host = (parsed.hostname or "").lower()
    if not (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtu.be"
        or host.endswith(".youtu.be")
    ):
        raise HTTPException(status_code=400, detail="Only youtube.com and youtu.be URLs are supported.")


def stream_info(fmt: dict | None) -> dict | None:
    if not fmt:
        return None

    return {
        "url": fmt.get("url"),
        "format_id": fmt.get("format_id"),
        "ext": fmt.get("ext"),
        "resolution": fmt.get("resolution"),
        "width": fmt.get("width"),
        "height": fmt.get("height"),
        "fps": fmt.get("fps"),
        "vcodec": fmt.get("vcodec"),
        "acodec": fmt.get("acodec"),
        "abr": fmt.get("abr"),
        "tbr": fmt.get("tbr"),
        "filesize": fmt.get("filesize") or fmt.get("filesize_approx"),
        "protocol": fmt.get("protocol"),
        "http_headers": fmt.get("http_headers"),
    }


def build_ydl_options() -> dict:
    options = {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,
        "skip_download": True,
        # Prefer separate best video + audio, but fall back to a single-file format.
        "format": "bestvideo*+bestaudio/best",
        # Metadata/format inspection should still return when no preferred format matches.
        "ignore_no_formats_error": True,
        "extractor_retries": 3,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 20,
        "cachedir": "/tmp/yt-dlp-cache",
        "paths": {"home": "/tmp", "temp": "/tmp"},
    }

    cookie_file = get_cookie_file()
    if cookie_file:
        options["cookiefile"] = cookie_file

    # Some YouTube player challenges require a supported JavaScript runtime.
    deno_path = shutil.which("deno")
    node_path = shutil.which("node")
    js_runtimes = {}
    if deno_path:
        js_runtimes["deno"] = {"path": deno_path}
    if node_path:
        js_runtimes["node"] = {"path": node_path}
    if js_runtimes:
        options["js_runtimes"] = js_runtimes
        options["remote_components"] = {"ejs:github"}

    return options


@app.get("/")
def home():
    return {
        "success": True,
        "message": "Media Extraction API is running",
        "cookie_configured": bool(os.getenv("YOUTUBE_COOKIES", "").strip()),
        "javascript_runtime": (
            "deno" if shutil.which("deno") else
            "node" if shutil.which("node") else
            None
        ),
        "extract_endpoint": "/extract?url=YOUTUBE_URL",
    }


@app.get("/extract")
def extract(url: str = Query(..., description="A YouTube video URL")):
    validate_url(url)

    try:
        with YoutubeDL(build_ydl_options()) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise HTTPException(status_code=404, detail="No media information returned.")

        formats = info.get("formats") or []
        if not formats:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "YouTube returned no playable formats.",
                    "video_id": info.get("id"),
                    "hint": (
                        "Check Render logs for player challenge, authentication, age/region "
                        "restriction, or extractor errors. Update yt-dlp and ensure Deno is installed."
                    ),
                },
            )

        combined_formats = []
        audio_formats = []
        video_formats = []

        for fmt in formats:
            vcodec = fmt.get("vcodec")
            acodec = fmt.get("acodec")
            has_video = bool(vcodec and vcodec != "none")
            has_audio = bool(acodec and acodec != "none")

            if has_video and has_audio:
                combined_formats.append(fmt)
            elif has_audio:
                audio_formats.append(fmt)
            elif has_video:
                video_formats.append(fmt)

        combined = max(
            combined_formats,
            key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
            default=None,
        )
        audio = max(
            audio_formats,
            key=lambda f: (f.get("abr") or 0, f.get("tbr") or 0),
            default=None,
        )
        video_only = max(
            video_formats,
            key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
            default=None,
        )

        if not any((combined, audio, video_only)):
            raise HTTPException(status_code=422, detail="No usable audio/video stream URLs were returned.")

        return {
            "success": True,
            "video": {
                "title": info.get("title"),
                "id": info.get("id"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration"),
                "webpage_url": info.get("webpage_url"),
                "extractor": info.get("extractor"),
            },
            "combined": stream_info(combined),
            "audio": stream_info(audio),
            "video_only": stream_info(video_only),
            "merge_required": combined is None,
            "format_count": len(formats),
        }

    except HTTPException:
        raise
    except DownloadError as exc:
        logger.warning("yt-dlp extraction failed: %s", exc)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "yt-dlp could not extract playable formats.",
                "reason": str(exc),
                "hint": (
                    "Update yt-dlp, check Render logs, and verify that a JavaScript runtime "
                    "(Deno recommended) is installed. Some videos may also require valid access."
                ),
            },
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected extraction error")
        raise HTTPException(
            status_code=500,
            detail="Unexpected extraction error. Check the Render service logs.",
        ) from exc
