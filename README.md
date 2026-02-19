# Mirror Download Bot

A private FastAPI server that downloads files from URLs or torrents and uploads them to Google Drive, with a Telegram bot interface for admin control.

## Features

- 🔒 **Private & Secure** — API key auth, admin-only Telegram bot
- 📥 **Direct URL Downloads** — resumable, with progress tracking
- 🧲 **Torrent Downloads** — send `.torrent` files via Telegram
- ☁️ **Google Drive Upload** — auto-upload with folder organization
- 📱 **Telegram Bot** — real-time progress bars, inline status updates
- 🚀 **Async Background Worker** — non-blocking task queue
- 🐳 **Docker Support** — ready-to-deploy with Docker Compose
- 📒 **Google Colab Support** — run in Colab with ngrok tunneling

## Project Structure

```
├── main.py              # FastAPI app + Telegram webhook handler
├── worker.py            # Background download/upload worker
├── config.py            # Settings (from .env)
├── models.py            # Pydantic models
├── task_manager.py      # Task persistence (tasks.json)
├── client.py            # CLI client for the API
├── celery_app.py        # Celery config (optional)
├── tasks.py             # Celery task definitions
├── colab_config.py      # Google Colab environment setup
├── colab_runner.ipynb   # Colab notebook to run the bot
├── services/
│   ├── downloader.py    # File download logic (HTTP, resume)
│   ├── gdrive_service.py# Google Drive API wrapper
│   ├── telegram_service.py # Telegram Bot API wrapper
│   ├── torrent_service.py  # Libtorrent integration
│   └── tunnel_service.py   # Ngrok tunnel management
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Quick Start

### 1. Install Dependencies

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Google Drive

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable **Google Drive API**
3. Create OAuth credentials (Desktop app type)
4. Download `credentials.json` to project root

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

| Variable | Description | Required |
|----------|-------------|----------|
| `API_KEY` | Secret key for API auth | ✅ |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) | For bot |
| `TELEGRAM_ADMIN_CHAT_ID` | Your chat ID (from [@userinfobot](https://t.me/userinfobot)) | For bot |
| `GDRIVE_FOLDER_ID` | Default Google Drive folder ID | Optional |
| `MAX_FILE_SIZE` | Max file size in bytes (0 = unlimited) | Optional |
| `DOWNLOAD_TIMEOUT` | Timeout in seconds | Default: 3600 |
| `NGROK_AUTHTOKEN` | Ngrok auth token for tunneling | For Colab |
| `USE_CELERY` | Enable Celery workers (`true`/`false`) | Optional |
| `REDIS_URL` | Redis URL for Celery | If Celery |

### 4. Run

```bash
python main.py
```

Server starts at `http://localhost:8000`. On first run, a browser window opens for Google Drive OAuth.

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start`, `/help` | Show help message |
| `/status <task_id>` | Check task status with progress bar |
| `/abort <task_id>` | Cancel a running task |
| Send any URL | Start downloading |
| Send `.torrent` file | Start torrent download |

## API Endpoints

All endpoints require `X-API-Key` header.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check & service status |
| `POST` | `/download` | Submit download task |
| `GET` | `/status/{task_id}` | Get task status |
| `GET` | `/tasks` | List tasks (filter by status) |
| `POST` | `/preview` | Get file info without downloading |
| `DELETE` | `/tasks/{task_id}` | Cancel a task |
| `POST` | `/webhook/telegram` | Telegram webhook (auto-configured) |

### Example

```bash
# Submit download
curl -X POST "http://localhost:8000/download" \
  -H "X-API-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/file.zip"}'

# Check status
curl "http://localhost:8000/status/task-abc123" \
  -H "X-API-Key: your-secret-key"
```

## CLI Client

```bash
# Download with wait
python client.py -u http://localhost:8000 -k your-key -w download "https://example.com/file.zip"

# Check status
python client.py -u http://localhost:8000 -k your-key status task-abc123
```

## Deployment Options

### Docker

```bash
docker compose up -d
```

### Google Colab

1. Upload entire project folder to Google Drive
2. Open `colab_runner.ipynb` in Colab
3. Set environment variables (bot token, API key, ngrok token)
4. Run all cells — server starts with ngrok tunnel + auto webhook setup

See `COLAB_MIGRATION_DETAILS.md` for additional notes.

### VPS / EC2

Use ngrok or a reverse proxy (nginx) with SSL for Telegram webhook support:

```bash
# Option 1: ngrok (quick)
ngrok http 8000

# Option 2: Set NGROK_AUTHTOKEN in .env (auto-starts with server)
```

## Disclaimer

> [!WARNING]
> This project is intended for **personal and legitimate use only**. Any misuse, illegal activity, or copyright infringement conducted using this tool is solely the responsibility of the user. The developer and this repository bear **no responsibility** for any damages, legal consequences, or violations arising from the misuse of this software.

## License

Private use only.
