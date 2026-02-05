#!/bin/bash
# Start Celery Beat (scheduler) for periodic tasks

set -e

PROJECT_DIR="/opt/mirror-download"
cd $PROJECT_DIR

source venv/bin/activate

echo "Starting Celery Beat (Scheduler)..."
echo "Logs: $PROJECT_DIR/celery-beat.log"

exec celery -A celery_app beat \
    -l info \
    -f $PROJECT_DIR/celery-beat.log \
    --pidfile=$PROJECT_DIR/celery-beat.pid \
    -s $PROJECT_DIR/celerybeat-schedule
