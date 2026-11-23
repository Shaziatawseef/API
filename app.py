
import urllib.request
from fastapi import FastAPI, HTTPException

app = FastAPI(
    title="Network Check API",
    version="1.0.0"
)


@app.get("/")
def root():
    return {
        "success": True,
        "message": "Network Check API is running"
    }


@app.get("/network-check")
def network_check():
    try:
        request = urllib.request.Request(
            "https://api.ipify.org",
            headers={
                "User-Agent": "Network-Check-API/1.0"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=10
        ) as response:
            ipv4 = response.read().decode("utf-8").strip()

        if not ipv4:
            raise RuntimeError("No IPv4 address returned")

        return {
            "success": True,
            "ipv4": ipv4
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
