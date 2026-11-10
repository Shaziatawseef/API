
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

app = FastAPI(
    title="Media Extraction API",
    description="Extracts the best available audio and video formats.",
    version="1.0.0"
)

# Hardcoded Netscape cookies.
# Paste your FRESH Netscape cookie text between the triple quotes.
COOKIE_DATA = """# Netscape HTTP Cookie File
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

"""

# Vercel writable temporary directory.
COOKIE_FILE = Path("/tmp/cookies.txt")


def get_cookie_file():
    if not COOKIE_DATA.strip() or "Paste your fresh Netscape cookie contents here" in COOKIE_DATA:
        return None

    try:
        COOKIE_FILE.write_text(COOKIE_DATA, encoding="utf-8")
        return str(COOKIE_FILE)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not prepare cookie file: {str(e)}"
        )


def validate_url(url: str):
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            raise ValueError()

        if not parsed.hostname:
            raise ValueError()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid URL"
        )


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
        "http_headers": fmt.get("http_headers")
    }


@app.get("/")
def home():
    return {
        "success": True,
        "message": "Media Extraction API is running",
        "cookie_file_found": bool(COOKIE_DATA.strip()),
        "extract_endpoint": "/extract?url=YOUTUBE_URL"
    }


@app.get("/extract")
def extract(url: str = Query(...)):
    validate_url(url)

    options = {
        "quiet": False,
        "no_warnings": False,
        "verbose": True,
        "noplaylist": True,
        "skip_download": True,
        "extractor_retries": 3,
        "retries": 3,

        # Vercel writable temporary storage.
        "paths": {
            "home": "/tmp",
            "temp": "/tmp"
        },
        "cachedir": "/tmp/yt-dlp-cache",

        # Do not force a particular format.
        # All available formats are inspected below.
    }

    cookie_file = get_cookie_file()

    if cookie_file:
        options["cookiefile"] = cookie_file

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise HTTPException(
                status_code=404,
                detail="No media found"
            )

        formats = info.get("formats", [])

        combined_formats = []
        audio_formats = []
        video_formats = []

        for fmt in formats:
            vcodec = fmt.get("vcodec")
            acodec = fmt.get("acodec")

            has_video = vcodec and vcodec != "none"
            has_audio = acodec and acodec != "none"

            if has_video and has_audio:
                combined_formats.append(fmt)

            elif has_audio and not has_video:
                audio_formats.append(fmt)

            elif has_video and not has_audio:
                video_formats.append(fmt)

        combined = max(
            combined_formats,
            key=lambda f: (
                f.get("height") or 0,
                f.get("tbr") or 0
            ),
            default=None
        )

        audio = max(
            audio_formats,
            key=lambda f: (
                f.get("abr") or 0,
                f.get("tbr") or 0
            ),
            default=None
        )

        video_only = max(
            video_formats,
            key=lambda f: (
                f.get("height") or 0,
                f.get("tbr") or 0
            ),
            default=None
        )

        return {
            "success": True,

            "video": {
                "title": info.get("title"),
                "id": info.get("id"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration"),
                "webpage_url": info.get("webpage_url"),
                "extractor": info.get("extractor")
            },

            "combined": stream_info(combined),
            "audio": stream_info(audio),
            "video_only": stream_info(video_only),

            "merge_required": combined is None
        }

    except HTTPException:
        raise

    except DownloadError as e:
        raise HTTPException(
            status_code=422,
            detail=f"yt-dlp extraction failed: {str(e)}"
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
