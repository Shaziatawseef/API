
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("media-api")

app = FastAPI(
    title="Media Extraction API",
    description="YouTube media format information API",
    version="1.2.0",
)

COOKIE_FILE = Path("/tmp/youtube-cookies.txt")


def get_cookie_file():
    cookie_data = ("# Netscape HTTP Cookie File
# Generated locally by ToolMi.

.youtube.com	TRUE	/	TRUE	0	__Secure-1PAPISID	W-qx8vOUsPw3FdsT/AuPQ3OSc86j22wtrO
#HttpOnly_.youtube.com	TRUE	/	TRUE	0	__Secure-1PSID	g.a000EgmeBOg4hD9O89M5qnyiyLgKkwiXKSP7vSbIGxed1qSCqFYeixaBXZSTS6b2RJ1IKJ_U0QACgYKAXASARcSFQHGX2MiVjnhJrzuK5LhdlJ2mM1P_BoVAUF8yKpC2isyTVfJrOSF4lNnGhJc0076
#HttpOnly_.youtube.com	TRUE	/	TRUE	0	__Secure-1PSIDCC	APD_SKGuqzxEfdQhzr5JU7FuLYwWHo5DZqBNND7phDKJooz3SuDjcNGTIkycVGx2tGxyLslChQ
#HttpOnly_.youtube.com	TRUE	/	TRUE	0	__Secure-1PSIDTS	sidts-CjUBnJZzamxN3IEJcrmpbtTZrZRjA1FPy3mSpAlC1ZpfW7RJjeFIfJNJaT01Afyq0DmN4E6zwxAA
.youtube.com	TRUE	/	TRUE	0	__Secure-3PAPISID	W-qx8vOUsPw3FdsT/AuPQ3OSc86j22wtrO
#HttpOnly_.youtube.com	TRUE	/	TRUE	0	__Secure-3PSID	g.a000EgmeBOg4hD9O89M5qnyiyLgKkwiXKSP7vSbIGxed1qSCqFYeKMiAepIhEUupD_0Af-r0cAACgYKAXQSARcSFQHGX2MiY5WHFSQbDNMjqXfEM7-lZhoVAUF8yKqwD9NPuKtP2vvHu0fHQ-hp0076
#HttpOnly_.youtube.com	TRUE	/	TRUE	0	__Secure-3PSIDCC	APD_SKFPYxGoaR-fFxDTtX4oUdYYDeSDW8v3jDosz0MMu2NKZOHoAJ7AWjz3tOHHZHn-XtvIpw
#HttpOnly_.youtube.com	TRUE	/	TRUE	0	__Secure-3PSIDTS	sidts-CjUBnJZzamxN3IEJcrmpbtTZrZRjA1FPy3mSpAlC1ZpfW7RJjeFIfJNJaT01Afyq0DmN4E6zwxAA
.youtube.com	TRUE	/	FALSE	0	APISID	mnGgmqRoHzkwarnK/AoxhnN3ktny3nKPfS
#HttpOnly_.youtube.com	TRUE	/	FALSE	0	HSID	AWGYz7e9Dd25CHwOJ
#HttpOnly_.youtube.com	TRUE	/	TRUE	0	LOGIN_INFO	AFmmF2swRAIgBRrx5UZSrFoxnjH1TjUuhjbDbaQbn_1tq1VoCPL8n1ACICQck4QdhCkmT444j44VzzpeHLFQ53gAvNmERhZgSqrg:QUQ3MjNmd01UdXhadzBqYy12M3g5SHo2WHBxeENEMm5PMjU5aF9EOVdqR3J6emtWQWNUT1EtcndMMUliOUliQTQ5cWk3NUxJWWJWemZkR2xieUpVX0V5bTFaa1VJbFlSelI1c3FGeE5NelUxejJpTmMxTjE2UWhJNS1sTGZPcXNER0s0bWNQOFJzRlBiaGhsdHR4b3RJQk92WDFJMjcwRllB
.youtube.com	TRUE	/	TRUE	0	PREF	f6=40000000&tz=Asia.Calcutta
.youtube.com	TRUE	/	TRUE	0	SAPISID	W-qx8vOUsPw3FdsT/AuPQ3OSc86j22wtrO
.youtube.com	TRUE	/	FALSE	0	SID	g.a000EgmeBOg4hD9O89M5qnyiyLgKkwiXKSP7vSbIGxed1qSCqFYe9o8d1dY62Oi17B7pN4PdigACgYKAeUSARcSFQHGX2Mid0e7Ew-RHW6zg-yNRYuHUBoVAUF8yKp6PeenRaSbsDvVAh2XSEna0076
.youtube.com	TRUE	/	FALSE	0	SIDCC	APD_SKFa4qvDjw1LKOk1TYhXt9EoFLn5y8QINuTyJO1t8rEuN_5GBGBzc3U6YuXUmQZoR7WVzw
#HttpOnly_.youtube.com	TRUE	/	TRUE	0	SSID	AQ9u3iJcFPP8Ri2R5
").strip()

    if not cookie_data:
        return None

    if not cookie_data.startswith(
        ("# Netscape HTTP Cookie File", "# HTTP Cookie File")
    ):
        raise HTTPException(
            status_code=500,
            detail="Invalid YOUTUBE_COOKIES format. Use Netscape cookies.txt."
        )

    try:
        COOKIE_FILE.write_text(cookie_data + "\n", encoding="utf-8")
        COOKIE_FILE.chmod(0o600)
        return str(COOKIE_FILE)
    except OSError:
        logger.exception("Cookie file preparation failed")
        raise HTTPException(
            status_code=500,
            detail="Could not prepare cookie file."
        )


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
            detail="Only valid YouTube URLs are supported."
        )


def runtime_info():
    result = {}

    for runtime in ("deno", "node"):
        path = shutil.which(runtime)
        if path:
            result[runtime] = path

    return result


def build_ydl_options():
    runtimes = runtime_info()

    options = {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,
        "skip_download": True,

        "format": "bestvideo*+bestaudio/best",

        "ignore_no_formats_error": True,

        "extractor_retries": 3,
        "retries": 3,
        "fragment_retries": 3,

        "socket_timeout": 30,

        "cachedir": "/tmp/yt-dlp-cache",

        "paths": {
            "home": "/tmp",
            "temp": "/tmp",
        },
    }

    # Configure available JavaScript runtimes.
    js_runtimes = {}

    if "deno" in runtimes:
        js_runtimes["deno"] = {
            "path": runtimes["deno"]
        }

    if "node" in runtimes:
        js_runtimes["node"] = {
            "path": runtimes["node"]
        }

    if js_runtimes:
        options["js_runtimes"] = js_runtimes
        options["remote_components"] = ["ejs:github"]

    cookie_file = get_cookie_file()

    if cookie_file:
        options["cookiefile"] = cookie_file

    return options


def stream_info(fmt):
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


def classify_formats(formats):
    combined = []
    audio = []
    video = []

    for fmt in formats:
        if not fmt.get("url"):
            continue

        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")

        has_video = bool(vcodec and vcodec != "none")
        has_audio = bool(acodec and acodec != "none")

        if has_video and has_audio:
            combined.append(fmt)
        elif has_audio:
            audio.append(fmt)
        elif has_video:
            video.append(fmt)

    best_combined = max(
        combined,
        key=lambda f: (
            f.get("height") or 0,
            f.get("tbr") or 0
        ),
        default=None,
    )

    best_audio = max(
        audio,
        key=lambda f: (
            f.get("abr") or 0,
            f.get("tbr") or 0
        ),
        default=None,
    )

    best_video = max(
        video,
        key=lambda f: (
            f.get("height") or 0,
            f.get("tbr") or 0
        ),
        default=None,
    )

    return best_combined, best_audio, best_video


@app.get("/")
def home():
    runtimes = runtime_info()

    return {
        "success": True,
        "service": "Media Extraction API",
        "version": "1.2.0",
        "yt_dlp_version": __import__("yt_dlp").version.__version__,
        "javascript_runtimes": list(runtimes.keys()),
        "deno_path": runtimes.get("deno"),
        "node_path": runtimes.get("node"),
        "cookie_configured": bool(
            os.getenv("YOUTUBE_COOKIES", "").strip()
        ),
        "endpoints": {
            "home": "/",
            "extract": "/extract?url=YOUTUBE_URL",
            "health": "/health"
        }
    }


@app.get("/health")
def health():
    return {
        "success": True,
        "status": "healthy",
        "deno": bool(shutil.which("deno")),
        "node": bool(shutil.which("node")),
    }


@app.get("/extract")
def extract(
    url: str = Query(..., description="YouTube video URL")
):
    validate_url(url)

    try:
        options = build_ydl_options()

        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise HTTPException(
                status_code=404,
                detail="No media information returned."
            )

        formats = info.get("formats") or []

        if not formats:
            logger.warning(
                "No formats returned for video ID: %s",
                info.get("id")
            )

            raise HTTPException(
                status_code=422,
                detail={
                    "error": "YouTube returned no playable formats.",
                    "video_id": info.get("id"),
                    "diagnostics": {
                        "deno": bool(shutil.which("deno")),
                        "node": bool(shutil.which("node")),
                        "cookies_configured": bool(
                            os.getenv("YOUTUBE_COOKIES", "").strip()
                        ),
                    },
                    "possible_causes": [
                        "YouTube player challenge",
                        "Authentication requirement",
                        "PO Token requirement",
                        "Age or region restriction",
                        "Unavailable video formats",
                        "Server IP restriction"
                    ],
                    "hint": "Check Render logs for the original yt-dlp warnings."
                }
            )

        combined, audio, video_only = classify_formats(formats)

        if not any((combined, audio, video_only)):
            raise HTTPException(
                status_code=422,
                detail="No usable direct stream URLs were returned."
            )

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

            "available_types": {
                "combined": combined is not None,
                "audio": audio is not None,
                "video_only": video_only is not None,
            }
        }

    except HTTPException:
        raise

    except DownloadError as exc:
        logger.warning("Extraction failed: %s", exc)

        raise HTTPException(
            status_code=422,
            detail={
                "error": "YouTube extraction failed.",
                "reason": str(exc),
                "hint": "Inspect Render logs for player challenge, authentication, PO Token or extractor errors."
            }
        ) from exc

    except Exception as exc:
        logger.exception("Unexpected extraction error")

        raise HTTPException(
            status_code=500,
            detail="Unexpected extraction error. Check Render logs."
        ) from exc
