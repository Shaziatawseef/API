
#!/bin/sh
set -eu

cd /app

POT_DIR="/opt/bgutil-ytdlp-pot-provider/server"
POT_URL="http://127.0.0.1:4416/ping"

echo "========================================"
echo " YouTube Media Extraction API"
echo "========================================"

echo "[INFO] Node: $(node --version)"
echo "[INFO] Python: $(python --version)"

# Verify the compiled provider exists
if [ ! -f "$POT_DIR/build/main.js" ]; then
    echo "[ERROR] bgutil server build/main.js not found"
    echo "[ERROR] Expected: $POT_DIR/build/main.js"
    exit 1
fi

# Start the PO Token provider
echo "[INFO] Starting bgutil provider..."

cd "$POT_DIR"
node build/main.js --host 127.0.0.1 --port 4416 &
POT_PID=$!

cd /app

# Wait up to 15 seconds for the provider
POT_READY=0

for i in $(seq 1 15); do
    if ! kill -0 "$POT_PID" 2>/dev/null; then
        echo "[ERROR] bgutil provider process exited"
        exit 1
    fi

    if curl -fsS "$POT_URL"; then
        echo ""
        echo "[SUCCESS] PO Token provider is ready"
        POT_READY=1
        break
    fi

    sleep 1
done

if [ "$POT_READY" -ne 1 ]; then
    echo "[ERROR] PO Token provider did not become ready"
    kill "$POT_PID" 2>/dev/null || true
    exit 1
fi

# Start FastAPI
echo "[INFO] Starting FastAPI..."

uvicorn app:app \
    --host 0.0.0.0 \
    --port "${PORT:-10000}" \
    --workers 1 &
API_PID=$!

cleanup() {
    echo "[INFO] Shutting down..."
    kill "$API_PID" "$POT_PID" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

wait "$API_PID"
