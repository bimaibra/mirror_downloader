# Mirror Download Server 🚀

Download files from URLs and automatically upload to Google Drive. Notifies users via Telegram with the download link.

## Features

- 📥 Download files from direct URLs
- ☁️ Auto-upload to Google Drive (public link)
- 🤖 Telegram bot notifications
- 📊 Real-time progress tracking
- 🔐 API key authentication
- 📁 Custom Google Drive folders
- ⚡ Async processing

## Quick Deploy to EC2 Ubuntu (with ngrok)

### 1. Launch EC2 Instance

- **AMI**: Ubuntu 22.04 LTS
- **Instance Type**: t3.medium (recommended)
- **Storage**: 50GB GP3 SSD
- **Security Group**: Allow 22 (SSH), 80 (HTTP), 443 (HTTPS), 8000 (for direct access)

### 2. Connect & Setup

```bash
# SSH to your EC2
ssh -i your-key.pem ubuntu@your-ec2-ip

# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3-pip python3-venv nginx git curl unzip

# Create project directory
sudo mkdir -p /opt/mirror-download
sudo chown ubuntu:ubuntu /opt/mirror-download
cd /opt/mirror-download

# Upload your project files (from local machine)
# scp -i your-key.pem -r * ubuntu@your-ec2-ip:/opt/mirror-download/
```

### 3. Install ngrok

```bash
# Download and install ngrok
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install -y ngrok

# Authenticate ngrok (get token from https://dashboard.ngrok.com/get-started/your-authtoken)
ngrok config add-authtoken YOUR_NGROK_AUTH_TOKEN
```

### 4. Configure Environment

```bash
cd /opt/mirror-download
cp .env.example .env
nano .env
```

**Edit these values:**
```env
API_KEY=your-strong-secret-key-here
TELEGRAM_BOT_TOKEN=your-bot-token-from-botfather
TELEGRAM_ADMIN_CHAT_ID=your-chat-id-from-userinfobot
```

### 5. Setup Google Drive

1. Place `credentials.json` in `/opt/mirror-download/`
2. First run to authenticate:

```bash
cd /opt/mirror-download
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

You'll see a URL - complete OAuth in your browser. Then Ctrl+C.

### 6. Start Server

```bash
# Terminal 1: Start the server
sudo systemctl enable mirror-download
sudo systemctl start mirror-download
sudo systemctl status mirror-download

# Or run manually:
# source venv/bin/activate
# python main.py
```

### 7. Start ngrok Tunnel

```bash
# Terminal 2: Start ngrok (run this in a separate SSH session)
ngrok http 8000
```

You'll see output like:
```
Forwarding: https://abc123-def.ngrok-free.app -> http://localhost:8000
```

Copy the **HTTPS URL**.

### 8. Set Telegram Webhook

```bash
# Set webhook using your ngrok URL
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://abc123-def.ngrok-free.app/webhook/telegram"

# Verify webhook
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

### 9. Test Your Bot

1. Message your bot on Telegram: `/start`
2. Send any download URL
3. Bot will reply with progress and Google Drive link!

**API is accessible at:** `https://abc123-def.ngrok-free.app`

---

## Alternative: Using a Domain (Production)

If you want a permanent setup without ngrok:

1. Get a free domain from [duckdns.org](https://duckdns.org) or [freenom.com](https://freenom.com)
2. Point domain to your EC2 IP
3. Run: `sudo ./setup_nginx.sh`
4. Follow the prompts for SSL setup

---

## API Usage

### Submit Download
```bash
curl -X POST https://abc123-def.ngrok-free.app/download \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{
    "url": "https://example.com/file.zip",
    "filename": "custom-name.zip",
    "telegram_chat_id": "123456789"
  }'
```

### Check Status
```bash
curl https://abc123-def.ngrok-free.app/status/task-xxx \
  -H "X-API-Key: your-secret-key"
```

---

## Project Structure

```
mirror-download/
├── main.py              # FastAPI server
├── worker.py            # Download worker
├── config.py            # Settings
├── models.py            # Pydantic models
├── task_manager.py      # Task tracking
├── services/
│   ├── gdrive_service.py    # Google Drive API
│   ├── telegram_service.py  # Telegram bot
│   └── downloader.py        # File downloader
├── requirements.txt
├── .env.example
├── setup.sh             # EC2 setup script
├── setup_nginx.sh       # Nginx setup (for domain)
├── Dockerfile           # Docker support
└── README.md
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `API_KEY` | ✅ | Secret key for API auth |
| `GOOGLE_CREDENTIALS_FILE` | ✅ | Path to credentials.json |
| `TELEGRAM_BOT_TOKEN` | ❌ | Bot token from @BotFather |
| `TELEGRAM_ADMIN_CHAT_ID` | ❌ | Your Telegram chat ID |
| `MAX_FILE_SIZE` | ❌ | Max file size (default: 5GB) |

---

## Logs & Monitoring

```bash
# View application logs
sudo journalctl -u mirror-download -f

# Check disk space
df -h

# View cleanup logs
sudo tail -f /var/log/mirror-download-cleanup.log
```

## Automatic Cleanup

Downloaded files are **automatically deleted** after successful upload to Google Drive.

### Manual Cleanup Script

```bash
cd /opt/mirror-download

# Delete files older than 24 hours (default)
python3 cleanup.py

# Delete files older than 2 hours
python3 cleanup.py --hours 2

# Preview what would be deleted (dry run)
python3 cleanup.py --dry-run

# Delete if disk usage > 80%
python3 cleanup.py --size-limit 80
```

### Setup Automatic Cleanup (Cron)

```bash
# Setup cron jobs for automatic cleanup
chmod +x setup_cron.sh
./setup_cron.sh
```

This configures:
1. **Hourly cleanup**: Delete files older than 24 hours
2. **Disk monitor**: Every 30 min, clean if disk > 80% full

### View/Remove Cron Jobs

```bash
# View current cron jobs
crontab -l

# Edit cron jobs
crontab -e
```

---

## Troubleshooting

**Server won't start:**
```bash
sudo journalctl -u mirror-download -n 50
```

**Google Drive auth expired:**
```bash
cd /opt/mirror-download
rm token.json
source venv/bin/activate
python main.py  # Re-authenticate
```

**ngrok URL changed:**
- Free ngrok URLs change on restart
- Update Telegram webhook with new URL
- Or use ngrok paid plan for static domain

---

## Security Tips

1. **Change default API_KEY** in .env
2. **Keep credentials.json and token.json secure**
3. **Don't commit .env to git**
4. **Use firewall rules** on EC2 (security groups)
5. **For production**: Use a real domain + SSL instead of ngrok

## License

MIT License
