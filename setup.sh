#!/bin/bash
# Setup script for Mirror Download Server on Ubuntu/EC2

set -e

echo "============================================"
echo "Mirror Download Server - EC2 Setup"
echo "============================================"

# Update system
echo "[*] Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install dependencies
echo "[*] Installing dependencies..."
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    libssl-dev \
    libffi-dev \
    nginx \
    curl \
    git \
    redis-server

# Setup project directory
echo "[*] Setting up project directory..."
PROJECT_DIR="/opt/mirror-download"
sudo mkdir -p $PROJECT_DIR
sudo chown $USER:$USER $PROJECT_DIR

# Note: Copy your project files to $PROJECT_DIR manually or via git
echo "[*] Please ensure your project files are in $PROJECT_DIR"

# Create virtual environment
echo "[*] Creating Python virtual environment..."
cd $PROJECT_DIR
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "[*] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create downloads directory
mkdir -p downloads

# Setup environment file
echo "[*] Setting up environment file..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[!] Please edit .env file with your configuration:"
    echo "   sudo nano $PROJECT_DIR/.env"
fi

# Setup systemd service
echo "[*] Creating systemd service..."
sudo tee /etc/systemd/system/mirror-download.service > /dev/null <<EOF
[Unit]
Description=Mirror Download Server
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin
ExecStart=$PROJECT_DIR/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
echo "[*] Enabling service..."
sudo systemctl daemon-reload
sudo systemctl enable mirror-download

echo ""
echo "============================================"
echo "Setup Complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo ""
echo "Option A - Using ngrok (easiest, for testing):"
echo "   1. Place credentials.json in $PROJECT_DIR"
echo "   2. Edit config: nano $PROJECT_DIR/.env"
echo "   3. Setup ngrok: ./setup_ngrok.sh"
echo "   4. First run for Google auth:"
echo "      cd $PROJECT_DIR && source venv/bin/activate && python main.py"
echo "   5. Start with ngrok: ./start_with_ngrok.sh"
echo ""
echo "Option B - Using a domain (production):"
echo "   1. Place credentials.json in $PROJECT_DIR"
echo "   2. Edit config: nano $PROJECT_DIR/.env"
echo "   3. First run for Google auth:"
echo "      cd $PROJECT_DIR && source venv/bin/activate && python main.py"
echo "   4. Start service: sudo systemctl start mirror-download"
echo "   5. Setup Nginx + SSL: sudo ./setup_nginx.sh"
echo ""
echo "Option C - With Celery (for production/scale):"
echo "   1. Edit .env and set USE_CELERY=true"
echo "   2. Start Redis: sudo systemctl start redis"
echo "   3. Start Celery Worker: ./start_celery.sh"
echo "   4. Or use systemd: sudo ./celery_systemd.sh"
echo "   5. Start main server: sudo systemctl start mirror-download"
echo ""
