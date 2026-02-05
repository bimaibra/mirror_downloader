#!/bin/bash
# Setup ngrok for HTTPS tunneling (for Telegram webhook)

set -e

echo "============================================"
echo "ngrok Setup for Telegram Webhook"
echo "============================================"

# Check if ngrok is installed
if ! command -v ngrok &> /dev/null; then
    echo "[*] Installing ngrok..."
    
    # Download ngrok
    curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
    echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
    sudo apt update
    sudo apt install -y ngrok
    
    echo "✅ ngrok installed"
else
    echo "✅ ngrok already installed"
fi

# Check if authenticated
if [ ! -f "$HOME/.config/ngrok/ngrok.yml" ]; then
    echo ""
    echo "⚠️  ngrok not authenticated!"
    echo ""
    echo "1. Get your authtoken from: https://dashboard.ngrok.com/get-started/your-authtoken"
    echo "2. Run: ngrok config add-authtoken YOUR_TOKEN"
    echo ""
    read -p "Enter your ngrok authtoken: " TOKEN
    ngrok config add-authtoken "$TOKEN"
fi

echo ""
echo "============================================"
echo "Setup Complete!"
echo "============================================"
echo ""
echo "To start ngrok tunnel:"
echo "   ngrok http 8000"
echo ""
echo "Then set Telegram webhook:"
echo "   curl \"https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<NGROK_URL>/webhook/telegram\""
echo ""
