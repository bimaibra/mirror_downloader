#!/bin/bash
# Start server with ngrok tunnel and auto-configure Telegram webhook

set -e

PROJECT_DIR="/opt/mirror-download"
cd $PROJECT_DIR

# Load environment
export $(grep -v '^#' .env | xargs)

if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN not set in .env"
    exit 1
fi

echo "============================================"
echo "Starting Mirror Download Server with ngrok"
echo "============================================"

# Check if ngrok is installed
if ! command -v ngrok &> /dev/null; then
    echo "❌ ngrok not installed. Run: ./setup_ngrok.sh"
    exit 1
fi

# Start ngrok in background
echo "[*] Starting ngrok tunnel..."
ngrok http 8000 > /tmp/ngrok.log 2>&1 &
NGROK_PID=$!

# Wait for ngrok to start
sleep 3

# Get ngrok URL
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels | grep -o '"public_url":"https://[^"]*' | grep -o 'https://[^"]*' | head -1)

if [ -z "$NGROK_URL" ]; then
    echo "❌ Failed to get ngrok URL. Is ngrok running?"
    kill $NGROK_PID 2>/dev/null
    exit 1
fi

echo "✅ ngrok tunnel: $NGROK_URL"

# Set Telegram webhook
echo "[*] Setting Telegram webhook..."
WEBHOOK_URL="${NGROK_URL}/webhook/telegram"
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook?url=${WEBHOOK_URL}" | grep -q '"ok":true' && echo "✅ Webhook set" || echo "⚠️  Failed to set webhook"

echo ""
echo "============================================"
echo "Server Ready!"
echo "============================================"
echo "API URL: $NGROK_URL"
echo "Webhook: $WEBHOOK_URL"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Start the server
source venv/bin/activate
exec python main.py
