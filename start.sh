
#!/bin/sh
set -e

echo "Starting YouTube Media Extraction API..."

echo "Node version:"
node --version

echo "Python version:"
python --version

echo "Starting FastAPI..."

exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-10000}" --workers 1
