
#!/bin/sh
set -eu

cd /app

echo "======================================"
echo " Starting YouTube Extraction API"
echo "======================================"

echo "[INFO] Node.js:"
node --version

echo "[INFO] Python:"
python --version

echo "[INFO] Starting bgutil PO Token server..."

node /opt/bgutil-ytdlp-pot-provider/server/build/main.js \
    --host 127.0.0.1 \
    --port 4416 &

BGUTIL_PID=$!

cleanup() {
    echo "[INFO] Stopping background services..."
    kill "$BGUTIL_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "[INFO] Starting FastAPI..."

exec uvicorn app:app \
    --host 0.0.0.0 \
    --port "${PORT:-10000}" \
    --workers 1
