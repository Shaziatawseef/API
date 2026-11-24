
#!/bin/sh
set -eu

echo "======================================"
echo "Starting BgUtils PO Token Provider"
echo "======================================"

cd /opt/bgutil-ytdlp-pot-provider/server

node build/main.js --host 127.0.0.1 --port 4416 &

POT_PID=$!

# Wait for the provider to accept connections
READY=0

for i in $(seq 1 30); do

    if ! kill -0 "$POT_PID" 2>/dev/null; then
        echo "ERROR: BgUtils provider process exited."
        wait "$POT_PID"
        exit 1
    fi

    if curl -sS --max-time 2 \
        -o /dev/null \
        http://127.0.0.1:4416/; then

        READY=1
        break
    fi

    sleep 1
done

if [ "$READY" -ne 1 ]; then
    echo "ERROR: BgUtils provider failed to become reachable."
    exit 1
fi

echo "BgUtils provider is reachable."

echo "======================================"
echo "Starting FastAPI"
echo "======================================"

cd /app

exec uvicorn app:app \
    --host 0.0.0.0 \
    --port "${PORT:-10000}" \
    --workers 1
    
