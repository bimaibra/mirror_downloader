# AGENTS.md - Mirror Download Server

This document provides essential information for AI coding agents working on this project.

## Project Overview

Mirror Download Server is a **private admin-only FastAPI application** for downloading files from URLs and uploading them to Google Drive. It supports Telegram bot integration for admin notifications and control.

**Key Purpose**: Download files from direct URLs → Upload to Google Drive → Notify admin via Telegram with shareable links.

**Architecture Mode**: 
- Simple mode: Built-in async worker (single server)
- Scaled mode: Celery + Redis (distributed workers)

## Technology Stack

| Component | Technology |
|-----------|------------|
| Web Framework | FastAPI |
| Server | Uvicorn |
| Language | Python 3.11+ |
| Task Queue | Built-in async (default) or Celery + Redis |
| External APIs | Google Drive API v3, Telegram Bot API |
| HTTP Client | aiohttp (downloads), httpx (webhooks) |
| Configuration | pydantic-settings + .env files |
| Containerization | Docker + Docker Compose |

## Project Structure

```
mirror-downloader/
├── main.py                  # FastAPI app, endpoints, Telegram webhook handler
├── config.py                # Pydantic settings, environment configuration
├── models.py                # Pydantic models for requests/responses
├── worker.py                # Built-in async download worker (simple mode)
├── task_manager.py          # In-memory task tracking with JSON persistence
├── celery_app.py            # Celery configuration (optional scaling)
├── tasks.py                 # Celery task definitions
├── client.py                # CLI client for interacting with the API
├── services/                # Service modules
│   ├── __init__.py          # Service exports
│   ├── downloader.py        # File download logic (aiohttp-based)
│   ├── gdrive_service.py    # Google Drive API integration
│   └── telegram_service.py  # Telegram bot notifications
├── downloads/               # Temporary download directory (gitignored)
├── requirements.txt         # Python dependencies
├── Dockerfile               # Container build configuration
├── docker-compose.yml       # Docker orchestration
├── .env                     # Environment configuration (gitignored)
├── .env.example             # Configuration template
├── credentials.json         # Google OAuth credentials (gitignored)
├── token.json               # Google OAuth token (gitignored, auto-generated)
└── tasks.json               # Task persistence (gitignored, auto-generated)
```

## Configuration

Configuration is managed via environment variables through `.env` file (loaded by pydantic-settings).

**Required Setup**:
1. Copy `.env.example` to `.env`
2. Set strong `API_KEY`
3. Download `credentials.json` from Google Cloud Console (see GOOGLE_DRIVE_SETUP.md)
4. Optionally configure Telegram bot

**Key Configuration Variables**:

```env
# Server
DEBUG=false
HOST=0.0.0.0
PORT=8000
API_KEY=change-this-to-strong-secret-key  # REQUIRED

# Google Drive
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json
GDRIVE_FOLDER_ID=                          # Optional default folder

# Telegram (optional but recommended)
TELEGRAM_BOT_TOKEN=                        # From @BotFather
TELEGRAM_ADMIN_CHAT_ID=                    # From @userinfobot

# Celery / Redis (for production scaling)
USE_CELERY=false                          # Set to 'true' for Celery mode
REDIS_URL=redis://localhost:6379/0

# Limits
MAX_FILE_SIZE=5368709120                  # 5 GB default
DOWNLOAD_TIMEOUT=3600                     # 1 hour
```

## Build and Run Commands

### Local Development (Simple Mode)

```bash
# Setup
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your settings

# Run
python main.py
```

### With Celery (Production Mode)

```bash
# Terminal 1 - Start Redis
redis-server

# Terminal 2 - Start Celery Worker
celery -A celery_app worker -l info -c 2

# Terminal 3 - Start Main Server
python main.py
```

### Docker

```bash
# Build and run
docker-compose up --build

# Or manually
docker build -t mirror-downloader .
docker run -p 8000:8000 --env-file .env mirror-downloader
```

## Code Organization

### Core Modules

**main.py** (853 lines)
- FastAPI application with lifespan management
- API endpoints: `/download`, `/status/{task_id}`, `/tasks`, `/preview`, `/webhook/telegram`
- Google Drive authentication on startup
- Telegram webhook handler with admin-only access control
- Task cancellation endpoint

**worker.py** (497 lines)
- `DownloadWorker` class - processes download tasks
- Handles: download → upload → notify workflow
- Progress callbacks for real-time updates
- Task cancellation support
- Cleanup of partial/failed downloads

**task_manager.py**
- `TaskManager` class - in-memory task tracking
- JSON persistence to `tasks.json`
- Thread-safe operations with locks

**services/downloader.py** (343 lines)
- `FileDownloader` class - async file downloads
- URL resolution with redirect following
- HTML page parsing for hidden download links
- Browser-like headers to bypass restrictions
- Progress callbacks

**services/gdrive_service.py** (305 lines)
- `GoogleDriveService` class - Google Drive API wrapper
- OAuth2 authentication with token refresh
- Resumable uploads for large files
- Automatic file permission setting (public read)
- Folder creation and management

**services/telegram_service.py** (349 lines)
- `TelegramService` class - bot notifications
- Rich HTML messages with progress bars
- Message editing for progress updates
- Admin-only access control

### Data Models

**DownloadStatus** (Enum): `pending`, `downloading`, `uploading`, `completed`, `failed`, `cancelled`

**Key Models**:
- `DownloadRequest`: URL, filename, folder_id, callback_url, telegram_chat_id
- `DownloadResponse`: task_id, status, message
- `DownloadStatusResponse`: Full task status with progress, file info, GDrive links

## API Endpoints

All endpoints require `X-API-Key` header (except `/health`).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check, service status |
| `/download` | POST | Submit new download task |
| `/status/{task_id}` | GET | Get task status |
| `/tasks` | GET | List tasks (optional status filter) |
| `/preview` | POST | Get file info without downloading |
| `/tasks/{task_id}` | DELETE | Cancel pending/running task |
| `/webhook/telegram` | POST | Telegram bot webhook |

## Code Style Guidelines

- **Imports**: Standard lib → Third-party → Local modules
- **Type hints**: Used throughout (Optional, Dict, etc.)
- **Docstrings**: Google-style docstrings for classes and methods
- **Logging**: Use `logging.getLogger(__name__)` per module
- **Error handling**: Try-except with meaningful error messages
- **Async/await**: Primary pattern for I/O operations
- **Singleton pattern**: Services use module-level singletons (e.g., `get_gdrive_service()`)

## Development Conventions

### Adding New Features

1. **Models**: Add to `models.py` with Pydantic validation
2. **Services**: Add to `services/` directory, export in `__init__.py`
3. **Endpoints**: Add to `main.py` with proper authentication
4. **Configuration**: Add to `config.py` with env variable support

### Task Status Flow

```
pending → downloading → uploading → completed
   ↓
failed / cancelled
```

### File Cleanup
- Successful uploads: Auto-deleted after completion
- Failed/cancelled: Cleaned up immediately
- On startup: Files from cancelled/failed tasks are cleaned

## Security Considerations

**CRITICAL**: This is a PRIVATE server - only admin should access.

1. **API Key**: Use strong random key, never commit to git
2. **Google Credentials**: `credentials.json` and `token.json` are gitignored
3. **Telegram**: Only `TELEGRAM_ADMIN_CHAT_ID` can use the bot
4. **CORS**: Currently allows all origins (`["*"]`) - restrict for production
5. **File Size**: Limited by `MAX_FILE_SIZE` (default 5GB)
6. **Rate Limiting**: `RATE_LIMIT_PER_MINUTE` (currently not enforced in code)

## Testing

No formal test suite exists. Manual testing via:

```bash
# Health check
curl http://localhost:8000/health

# Submit download
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"url": "https://example.com/file.zip"}'

# Use CLI client
python client.py -u http://localhost:8000 -k your-key download "URL"
```

## Deployment Notes

### Production Checklist

- [ ] Set strong `API_KEY`
- [ ] Enable `USE_CELERY=true` with Redis
- [ ] Configure `GDRIVE_FOLDER_ID`
- [ ] Set up Telegram bot (optional but useful)
- [ ] Use HTTPS for Telegram webhooks
- [ ] Set up systemd services for auto-start
- [ ] Configure firewall (port 8000)

### Environment-Specific Notes

- **Local**: Use `USE_CELERY=false`, simple setup
- **VPS with IP**: HTTP works, Telegram webhook requires HTTPS
- **With Domain**: Use nginx + SSL, set webhook to HTTPS URL
- **Docker**: Volumes for downloads, credentials, and token persistence

## Common Issues

1. **Token expired**: Delete `token.json`, re-run to re-authenticate
2. **Redis connection**: Ensure Redis is running before starting Celery
3. **Port in use**: Change `PORT` in `.env`
4. **Module not found**: Reinstall requirements: `pip install -r requirements.txt --force-reinstall`

## Documentation References

- `README.md` - Quick start and API usage
- `README_LOCAL.md` - Local development guide (Indonesian/English)
- `CELERY_SETUP.md` - Celery/Redis configuration guide
- `GOOGLE_DRIVE_SETUP.md` - Google Cloud Console setup

---

**Last Updated**: Based on codebase as of current analysis.
**Language**: Documentation uses mixed English/Indonesian; code uses English.
