#!/bin/bash
# Setup automatic cleanup cron job for Mirror Download Server

set -e

echo "============================================"
echo "Setting up automatic cleanup cron job"
echo "============================================"

PROJECT_DIR="/opt/mirror-download"
CLEANUP_SCRIPT="$PROJECT_DIR/cleanup.py"

# Check if cleanup script exists
if [ ! -f "$CLEANUP_SCRIPT" ]; then
    echo "❌ cleanup.py not found at $CLEANUP_SCRIPT"
    exit 1
fi

echo "[*] Adding cron job..."

# Create cron job that runs every hour to clean files older than 24 hours
CRON_JOB="0 * * * * cd $PROJECT_DIR && /usr/bin/python3 $CLEANUP_SCRIPT --hours 24 --cron >> /var/log/mirror-download-cleanup.log 2>&1"

# Add to crontab if not already exists
(crontab -l 2>/dev/null | grep -F "$CLEANUP_SCRIPT" >/dev/null) || {
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "✅ Cron job added: Cleanup every hour"
}

# Also add disk usage check cron (runs every 30 minutes, cleans if disk > 80%)
DISK_CRON_JOB="*/30 * * * * cd $PROJECT_DIR && /usr/bin/python3 $CLEANUP_SCRIPT --size-limit 80 --hours 12 --cron >> /var/log/mirror-download-cleanup.log 2>&1"

(crontab -l 2>/dev/null | grep -F "size-limit 80" >/dev/null) || {
    (crontab -l 2>/dev/null; echo "$DISK_CRON_JOB") | crontab -
    echo "✅ Cron job added: Disk usage check every 30 minutes"
}

echo ""
echo "Current cron jobs:"
crontab -l | grep mirror-download || echo "No jobs found"

echo ""
echo "============================================"
echo "Cron setup complete!"
echo "============================================"
echo ""
echo "Jobs configured:"
echo "  1. Every hour: Delete files older than 24 hours"
echo "  2. Every 30 min: Delete old files if disk usage > 80%"
echo ""
echo "Logs: /var/log/mirror-download-cleanup.log"
echo ""
echo "To manually run cleanup:"
echo "  cd $PROJECT_DIR && python3 cleanup.py --hours 24"
echo ""
echo "To remove cron jobs:"
echo "  crontab -e  # Edit and remove the lines"
echo ""
