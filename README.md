# Mirror Download Server (Private)

A private FastAPI-based server for downloading files from URLs and uploading them to Google Drive. **This is an admin-only private server.**

## Features

- 🔒 **Private & Secure** - Only admin can access
- 📥 Download files from direct URLs
- ☁️ Upload to Google Drive
- 📱 Telegram bot integration for admin
- 📊 Task tracking and status monitoring
- 🚀 Async processing with background workers
- ⏸️ Supports download resume
- 📁 Automatic file organization

## Quick Start

### 1. Setup Environment

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Google Drive

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable Google Drive API
3. Create OAuth credentials (Desktop app type)
4. Download `credentials.json` and place in project root

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings:
# - API_KEY (strong secret key)
# - TELEGRAM_BOT_TOKEN (optional, from @BotFather)
# - TELEGRAM_ADMIN_CHAT_ID (your chat ID from @userinfobot)
# - GDRIVE_FOLDER_ID (optional, default folder for downloads)
```

### 4. Run the Server

```bash
python main.py
```

The server will start on `http://localhost:8000` and open a browser for Google Drive authentication on first run.

## API Usage

All endpoints require the API key in the `X-API-Key` header.

### Submit Download

```bash
curl -X POST "http://localhost:8000/download" \
  -H "X-API-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/file.zip",
    "filename": "my-file.zip",
    "folder_id": "optional-gdrive-folder-id"
  }'
```

### Check Status

```bash
curl "http://localhost:8000/status/task-abc123" \
  -H "X-API-Key: your-secret-key"
```

### List Tasks

```bash
curl "http://localhost:8000/tasks?limit=10" \
  -H "X-API-Key: your-secret-key"
```

### Preview File (without downloading)

```bash
curl -X POST "http://localhost:8000/preview" \
  -H "X-API-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/file.zip"}'
```

### Cancel Task

```bash
curl -X DELETE "http://localhost:8000/tasks/task-abc123" \
  -H "X-API-Key: your-secret-key"
```

## Telegram Bot (Admin Only)

If configured, the bot only responds to the admin:

- `/start` or `/help` - Show help message
- Send any URL - Start download
- `/status <task_id>` - Check task status
- `/abort <task_id>` - Cancel a task

## Configuration Options

| Variable | Description | Default |
|----------|-------------|---------|
| `API_KEY` | Secret key for API access | Required |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather | Optional |
| `TELEGRAM_ADMIN_CHAT_ID` | Your Telegram chat ID | Required for bot |
| `GDRIVE_FOLDER_ID` | Default Google Drive folder | Optional |
| `MAX_FILE_SIZE` | Maximum file size (bytes) | 5 GB |
| `DOWNLOAD_TIMEOUT` | Download timeout (seconds) | 3600 |

## Client Usage

Use the included Python client:

```bash
python client.py -u http://localhost:8000 -k your-secret-key download "https://example.com/file.zip"

# With wait for completion
python client.py -u http://localhost:8000 -k your-secret-key -w download "https://example.com/file.zip"

# Check status
python client.py -u http://localhost:8000 -k your-secret-key status task-abc123
```

## Docker

```bash
docker build -t mirror-downloader .
docker run -p 8000:8000 --env-file .env mirror-downloader
```

## License

Private use only.
