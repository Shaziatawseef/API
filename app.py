import subprocess
from fastapi import HTTPException

@app.get("/network-check")
def network_check():
    try:
        result = subprocess.run(
            [
                "curl",
                "-4",
                "-sS",
                "--max-time",
                "10",
                "https://api.ipify.org"
            ],
            capture_output=True,
            text=True,
            timeout=15
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())

        return {
            "success": True,
            "ipv4": result.stdout.strip()
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
