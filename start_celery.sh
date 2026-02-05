#!/bin/bash
# Start Celery worker for Mirror Download Server

set -e

echo "============================================"
echo "Starting Celery Worker"
echo "============================================"

PROJECT_DIR="/opt/mirror-download"
cd $PROJECT_DIR

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Please run setup.sh first."
    exit 1
fi

source venv/bin/activate

# Check Redis connection
echo "[*] Checking Redis connection..."
if ! redis-cli ping > /dev/null 2>&1; then
    echo "❌ Redis is not running. Starting Redis..."
    sudo systemctl start redis
    sleep 2
    if ! redis-cli ping > /dev/null 2>&1; then
        echo "❌ Failed to start Redis. Please install and start Redis manually."
        echo "   Ubuntu/Debian: sudo apt install redis-server"
        exit 1
    fi
fi
echo "✅ Redis is running"

# Default to 2 workers (adjust based on server specs)
WORKER_COUNT=${1:-2}

echo "[*] Starting Celery worker with $WORKER_COUNT worker(s)..."
echo "   Logs: $PROJECT_DIR/celery.log"

# Start Celery worker
# -A: Application (celery_app)
# -Q: Queues to consume (default,downloads)
# -c: Concurrency (number of worker processes)
# -l: Log level
# -f: Log file
# -n: Worker name
exec celery -A celery_app worker \
    -Q default,downloads \
    -c $WORKER_COUNT \
    -l info \
    -f $PROJECT_DIR/celery.log \
    -n worker@%h \
    --pidfile=$PROJECT_DIR/celery.pid
