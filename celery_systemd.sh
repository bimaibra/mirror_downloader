#!/bin/bash
# Setup systemd services for Celery

set -e

echo "============================================"
echo "Setting up Celery Systemd Services"
echo "============================================"

PROJECT_DIR="/opt/mirror-download"
USER="ubuntu"  # Change this to your username

# Create Celery Worker service
cat << EOF | sudo tee /etc/systemd/system/celery-worker.service > /dev/null
[Unit]
Description=Celery Worker for Mirror Download Server
After=network.target redis.service

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin
ExecStart=$PROJECT_DIR/venv/bin/celery -A celery_app worker -Q default,downloads -c 2 -l info
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Create Celery Beat service
cat << EOF | sudo tee /etc/systemd/system/celery-beat.service > /dev/null
[Unit]
Description=Celery Beat Scheduler for Mirror Download Server
After=network.target redis.service

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin
ExecStart=$PROJECT_DIR/venv/bin/celery -A celery_app beat -l info -s $PROJECT_DIR/celerybeat-schedule
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

echo ""
echo "============================================"
echo "Systemd services created!"
echo "============================================"
echo ""
echo "Commands:"
echo "  sudo systemctl start celery-worker   # Start worker"
echo "  sudo systemctl stop celery-worker    # Stop worker"
echo "  sudo systemctl status celery-worker  # Check status"
echo "  sudo systemctl enable celery-worker  # Auto-start on boot"
echo ""
echo "  sudo systemctl start celery-beat     # Start scheduler"
echo "  sudo systemctl stop celery-beat      # Stop scheduler"
echo "  sudo systemctl status celery-beat    # Check status"
echo "  sudo systemctl enable celery-beat    # Auto-start on boot"
echo ""
echo "View logs:"
echo "  sudo journalctl -u celery-worker -f"
echo "  sudo journalctl -u celery-beat -f"
echo ""
