"""Main FastAPI application for Mirror Download Server - Admin Only."""
import os
import sys
import json
import asyncio
import logging
import subprocess
from pathlib import Path
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional

from config import get_settings, Settings
from models import (
    DownloadRequest, 
    DownloadResponse, 
    DownloadStatusResponse,
    HealthResponse,
    DownloadStatus
)
from task_manager import get_task_manager
from worker import get_worker
from services.telegram_service import get_telegram_service
from services.gdrive_service import get_gdrive_service
from services.gdrive_service import get_gdrive_service

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants for guest user limits
GUEST_MAX_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1 GB max for guest users


def authenticate_gdrive():
    """Authenticate with Google Drive on startup."""
    import sys
    from pathlib import Path
    
    token_file = Path("token.json")
    creds_file = Path("credentials.json")
    
    print()
    print("=" * 60)
    print("🔐 Google Drive Authentication")
    print("=" * 60)
    
    # Check if credentials file exists
    if not creds_file.exists():
        print()
        print("❌ credentials.json not found!")
        print()
        print("📋 To get credentials.json:")
        print("   1. Go to https://console.cloud.google.com/")
        print("   2. Create a project → Enable Google Drive API")
        print("   3. APIs & Services → Credentials → Create Credentials")
        print("   4. Choose 'OAuth client ID' → Application type: 'Desktop app'")
        print("   5. Download JSON and save as 'credentials.json' in this folder")
        print()
        print(f"   Expected: {creds_file.absolute()}")
        print()
        print("🛑 Server cannot start without Google Drive credentials.")
        print("=" * 60)
        sys.exit(1)
    
    # Check existing token
    if token_file.exists():
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            
            creds = Credentials.from_authorized_user_file("token.json", 
                ['https://www.googleapis.com/auth/drive'])
            
            if creds.valid:
                print("✅ Using existing valid token.json")
                print("=" * 60)
                return True
            elif creds.expired and creds.refresh_token:
                print("🔄 Refreshing expired token...")
                creds.refresh(Request())
                with open("token.json", 'w') as token:
                    token.write(creds.to_json())
                print("✅ Token refreshed!")
                print("=" * 60)
                return True
        except Exception as e:
            print(f"⚠️  Existing token invalid: {e}")
            print("   Will re-authenticate...")
    
    # Need to authenticate
    print()
    print("🌐 Opening browser for Google authentication...")
    print("   Please sign in and grant Drive permissions.")
    print()
    
    try:
        from services.gdrive_service import get_gdrive_service
        get_gdrive_service()
        print()
        print("✅ Google Drive authenticated successfully!")
        print("=" * 60)
        return True
    except Exception as e:
        print()
        print(f"❌ Authentication failed: {e}")
        print()
        print("🔧 Troubleshooting:")
        print("   - Check your internet connection")
        print("   - Make sure credentials.json is valid")
        print("   - Try deleting token.json and try again")
        print("=" * 60)
        sys.exit(1)


def cleanup_cancelled_failed_tasks():
    """Clean up files from cancelled or failed tasks on startup."""
    settings = get_settings()
    task_manager = get_task_manager()
    download_dir = Path(settings.TEMP_DOWNLOAD_DIR)
    
    if not download_dir.exists():
        return
    
    # Get all tasks
    all_tasks = task_manager.list_tasks(limit=10000)
    
    # Find cancelled or failed tasks
    cancelled_failed_tasks = [
        task for task in all_tasks 
        if task['status'] in ['cancelled', 'failed']
    ]
    
    deleted_count = 0
    freed_bytes = 0
    
    for task in cancelled_failed_tasks:
        # Try to find and delete associated files
        filename = task.get('filename')
        task_id = task['task_id']
        
        if filename:
            # Look for files matching the task filename
            for file_path in download_dir.iterdir():
                if not file_path.is_file():
                    continue
                
                # Check if file matches the task's filename
                if file_path.name == filename or filename in file_path.name:
                    try:
                        file_size = file_path.stat().st_size
                        file_path.unlink()
                        deleted_count += 1
                        freed_bytes += file_size
                        logger.info(f"Deleted file from {task['status']} task {task_id}: {file_path.name}")
                    except Exception as e:
                        logger.warning(f"Failed to delete file {file_path}: {e}")
    
    if deleted_count > 0:
        logger.info(f"Cleanup cancelled/failed tasks: deleted {deleted_count} files, freed {freed_bytes / (1024*1024):.2f} MB")
    else:
        logger.info("No files to clean up from cancelled/failed tasks")


# Startup and shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown."""
    # Startup
    settings = get_settings()
    logger.info(f"Starting {settings.APP_NAME}")
    
    # Create download directory
    os.makedirs(settings.TEMP_DOWNLOAD_DIR, exist_ok=True)
    
    # Cleanup files from cancelled/failed tasks first
    try:
        cleanup_cancelled_failed_tasks()
    except Exception as e:
        logger.warning(f"Cleanup cancelled/failed tasks failed: {e}")
    
    # Cleanup files from cancelled/failed tasks first
    try:
        cleanup_cancelled_failed_tasks()
    except Exception as e:
        logger.warning(f"Cleanup cancelled/failed tasks failed: {e}")
    
    # Cleanup old downloaded files on startup (files older than 24 hours)
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'cleanup.py', '--hours', '24', '--cron'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if result.returncode == 0 and result.stdout:
            logger.info(f"Startup cleanup: {result.stdout.strip()}")
    except Exception as e:
        logger.warning(f"Startup cleanup failed: {e}")
    
    # Authenticate Google Drive (blocking, before server starts)
    authenticate_gdrive()
    
    # Initialize Telegram (shows status in logs)
    telegram = get_telegram_service()
    if telegram.enabled:
        logger.info("Telegram bot: ENABLED")
    else:
        logger.info("Telegram bot: DISABLED (set TELEGRAM_BOT_TOKEN in .env to enable)")
    
    # Start worker in background
    worker = get_worker()
    worker_task = asyncio.create_task(worker.start())
    
    logger.info("Server ready!")
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    worker.stop()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    logger.info("Server stopped")


app = FastAPI(
    title="Mirror Download Server",
    description="Private download server - Admin only",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependencies
def verify_api_key(
    x_api_key: Optional[str] = Header(None),
    settings: Settings = Depends(get_settings)
):
    """Verify API key from header."""
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True


# Health check
@app.get("/", response_model=HealthResponse)
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    settings = get_settings()
    
    # Check services
    services_status = {
        "task_manager": "up",
        "gdrive": "authenticated" if os.path.exists("token.json") else "not_configured",
        "telegram": "configured" if settings.TELEGRAM_BOT_TOKEN else "not_configured"
    }
    
    # Check worker type
    if settings.USE_CELERY:
        try:
            from celery_app import celery_app
            # Check Celery/Redis connection
            inspector = celery_app.control.inspect()
            active_workers = inspector.active()
            services_status["worker"] = "celery"
            services_status["celery_workers"] = len(active_workers) if active_workers else 0
        except Exception as e:
            services_status["worker"] = "celery_error"
            services_status["celery_error"] = str(e)
    else:
        worker = get_worker()
        services_status["worker"] = "busy" if worker.get_current_task() else "idle"
    
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        services=services_status
    )


# Submit download request
@app.post("/download", response_model=DownloadResponse, status_code=202)
async def create_download(
    request: DownloadRequest,
    background_tasks: BackgroundTasks,
    authorized: bool = Depends(verify_api_key)
):
    """
    Submit a new download task.
    
    The server will:
    1. Download the file from the URL
    2. Upload it to Google Drive
    3. Send the Google Drive link via Telegram (if chat_id provided)
    4. Call webhook callback (if provided)
    
    Note: If USE_CELERY=True in .env, tasks are processed by Celery workers.
    Otherwise, uses the built-in async worker.
    """
    task_manager = get_task_manager()
    settings = get_settings()
    
    # Create task
    task_id = task_manager.create_task(
        url=str(request.url),
        filename=request.filename,
        folder_id=request.folder_id,
        telegram_chat_id=request.telegram_chat_id,
        callback_url=str(request.callback_url) if request.callback_url else None
    )
    
    # Check if Celery is enabled
    if settings.USE_CELERY:
        try:
            from tasks import process_download
            
            # Queue task with Celery
            process_download.delay(
                task_id=task_id,
                url=str(request.url),
                filename=request.filename,
                folder_id=request.folder_id,
                telegram_chat_id=request.telegram_chat_id,
                callback_url=str(request.callback_url) if request.callback_url else None
            )
            logger.info(f"Task {task_id} queued with Celery")
        except Exception as e:
            logger.error(f"Failed to queue task with Celery: {e}")
            # Fallback to built-in worker
            worker = get_worker()
            worker.queue_task(task_id)
    else:
        # Use built-in async worker
        worker = get_worker()
        worker.queue_task(task_id)
    
    return DownloadResponse(
        task_id=task_id,
        status=DownloadStatus.PENDING,
        message="Download queued successfully. Use /status/{task_id} to check progress."
    )


# Get task status
@app.get("/status/{task_id}", response_model=DownloadStatusResponse)
async def get_status(task_id: str, authorized: bool = Depends(verify_api_key)):
    """Get the status of a download task."""
    task_manager = get_task_manager()
    response = task_manager.to_status_response(task_id)
    
    if not response:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    return response


# List tasks
@app.get("/tasks")
async def list_tasks(
    status: Optional[DownloadStatus] = None,
    limit: int = 20,
    authorized: bool = Depends(verify_api_key)
):
    """List download tasks."""
    task_manager = get_task_manager()
    tasks = task_manager.list_tasks(status=status, limit=limit)
    return {"tasks": tasks, "count": len(tasks)}


# Get file info without downloading
@app.post("/preview")
async def preview_file(
    request: DownloadRequest,
    authorized: bool = Depends(verify_api_key)
):
    """Get file information without downloading."""
    from services.downloader import FileDownloader
    
    downloader = FileDownloader()
    try:
        info = await downloader.get_file_info(str(request.url))
        return {
            "url": str(request.url),
            **info
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get file info: {str(e)}")


# Cancel task
@app.delete("/tasks/{task_id}")
async def cancel_task(task_id: str, authorized: bool = Depends(verify_api_key)):
    """Cancel a pending task."""
    task_manager = get_task_manager()
    task = task_manager.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    if task['status'] not in [DownloadStatus.PENDING.value, DownloadStatus.DOWNLOADING.value]:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot cancel task with status: {task['status']}"
        )
    
    task_manager.update_task(
        task_id,
        status=DownloadStatus.CANCELLED,
        message="Task cancelled by admin"
    )
    
    return {"message": f"Task {task_id} cancelled"}


# WIB (Western Indonesian Time) is UTC+7
WIB = timezone(timedelta(hours=7))

def to_wib(dt: datetime) -> datetime:
    """Convert UTC datetime to WIB (UTC+7)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(WIB)

def format_wib(dt_str: str, fmt: str = '%Y-%m-%d %H:%M') -> str:
    """Format datetime string to WIB timezone."""
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        wib_dt = dt.astimezone(WIB)
        return wib_dt.strftime(fmt)
    except:
        return dt_str


# Webhook endpoint for Telegram (Admin only)
@app.post("/webhook/telegram")
async def telegram_webhook(update: dict):
    """Handle Telegram webhook updates - Admin only."""
    from services.downloader import FileDownloader
    
    telegram = get_telegram_service()
    settings = get_settings()
    
    if 'message' not in update:
        return {"ok": True}
    
    message = update['message']
    chat_id = str(message.get('chat', {}).get('id'))
    text = message.get('text', '').strip()
    
    if not text:
        return {"ok": True}
    
    # Check if sender is admin
    if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID:
        await telegram.send_message(
            chat_id=chat_id,
            message="🚫 This bot is private. Only admin can use it."
        )
        return {"ok": True}
    
    # Commands
    if text == '/start' or text == '/help':
        await telegram.send_message(
            chat_id=chat_id,
            message="""
🤖 <b>Mirror Download Bot</b> (Private)

You are the admin. Send me a direct download link and I'll upload it to Google Drive.

<b>Commands:</b>
• /status &lt;task_id&gt; - Check task status
• /abort &lt;task_id&gt; - Cancel a running task
• /help - Show this help

Just paste any URL to start downloading.
            """.strip()
        )
        return {"ok": True}
    
    # URL detection (direct link sent)
    if text.startswith(('http://', 'https://')):
        import re
        # Extract URL
        url_match = re.match(r'(https?://\S+)', text)
        if not url_match:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ <b>Invalid URL format</b>\n\nCould not parse the URL."
            )
            return {"ok": True}
        
        url = url_match.group(1)
        logger.info(f"Processing URL from admin: {url}")
        
        try:
            downloader = FileDownloader()
            info = await downloader.get_file_info(url)
            
            await telegram.send_message(
                chat_id=chat_id,
                message=f"""
📥 <b>Starting Download...</b>

📄 <b>File:</b> <code>{info['filename']}</code>
📦 <b>Size:</b> {info['size_human']}
📋 <b>Type:</b> {info['content_type']}

⏳ Queuing download...
                """.strip()
            )
            
            # Create task
            task_manager = get_task_manager()
            worker = get_worker()
            
            # Use admin's preferred folder or default
            folder_id = settings.GDRIVE_FOLDER_ID if settings.GDRIVE_FOLDER_ID else None
            
            task_id = task_manager.create_task(
                url=url,
                filename=info['filename'],
                folder_id=folder_id,
                telegram_chat_id=str(chat_id)
            )
            
            worker.queue_task(task_id)
            
            await telegram.send_message(
                chat_id=chat_id,
                message=f"""
✅ <b>Download Queued!</b>

🆔 Task ID: <code>{task_id}</code>

⏱️ I'll notify you when it's ready!
                """.strip()
            )
            return {"ok": True}
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to process URL {url}: {error_msg}", exc_info=True)
            
            # Provide user-friendly error messages
            if "Could not find" in error_msg or "downloadable file" in error_msg.lower():
                user_message = """❌ <b>Download Link Not Found</b>

Could not find a direct download link in this URL.

<b>Possible reasons:</b>
• Link expired or requires login
• Site uses JavaScript to generate download link
• Anti-bot protection (CAPTCHA, etc.)
• Premium account required

<b>Solutions:</b>
1. Try accessing the link in browser first
2. Use the direct download URL (not the page URL)
3. Some file hosts block automated downloads"""
            
            elif "ClientConnectorError" in error_msg or "Cannot connect" in error_msg:
                user_message = f"""❌ <b>Cannot Connect to Server</b>

The server might be:
• Down or unreachable
• Blocking your region/IP
• Rate-limiting requests

<code>{error_msg[:150]}</code>"""
            
            elif "403" in error_msg or "Forbidden" in error_msg:
                user_message = """❌ <b>Access Denied (403)</b>

Server blocked the request.

<b>Common causes:</b>
• Missing Referer header
• Anti-bot protection
• IP blocked
• Requires premium account

<b>Try:</b>
• Use direct link from browser's download manager
• Different file host"""
            
            elif "404" in error_msg or "Not Found" in error_msg:
                user_message = """❌ <b>File Not Found (404)</b>

The file doesn't exist at this URL.
It may have been deleted or moved."""
            else:
                user_message = f"""❌ <b>Failed to Process URL</b>

<code>{error_msg[:200]}</code>

Please try again."""
            
            await telegram.send_message(chat_id=chat_id, message=user_message)
            return {"ok": True}
    
    # /abort command
    if text.startswith('/abort'):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ Please provide a task ID\nExample: <code>/abort task-abc123</code>"
            )
            return {"ok": True}
        
        task_id = parts[1].strip()
        task_manager = get_task_manager()
        task = task_manager.get_task(task_id)
        
        if not task:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"❌ Task <code>{task_id}</code> not found"
            )
            return {"ok": True}
        
        # Check ownership - guest users can only abort their own tasks
        if not is_authorized(chat_id) and task.get('telegram_chat_id') != str(chat_id):
            await telegram.send_message(
                chat_id=chat_id,
                message="⚠️ <b>Access Denied</b>\n\nYou can only abort your own tasks."
            )
            return {"ok": True}
        
        # Check if task can be cancelled
        if task['status'] not in ['pending', 'downloading', 'uploading']:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"❌ Cannot abort task with status: <b>{task['status']}</b>\n\nOnly pending or active tasks can be aborted."
            )
            return {"ok": True}
        
        # Cancel the task in worker
        worker = get_worker()
        worker_cancelled = worker.cancel_task(task_id)
        
        # Cancel the task in task manager
        task_manager.update_task(
            task_id,
            status=DownloadStatus.CANCELLED,
            message="Task cancelled by admin"
        )
        
        if worker_cancelled:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"""
🚫 <b>Task Aborted</b>

🆔 <code>{task_id}</code>
📄 File: <code>{task.get('filename', 'N/A')}</code>

The task has been cancelled and will stop shortly.
                """.strip()
            )
        else:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"""
🚫 <b>Task Aborted</b>

🆔 <code>{task_id}</code>
📄 File: <code>{task.get('filename', 'N/A')}</code>

The task has been cancelled (was not actively running).
                """.strip()
            )
        return {"ok": True}
    
    # /status command
    if text.startswith('/status'):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ Please provide a task ID\nExample: <code>/status task-abc123</code>"
            )
            return {"ok": True}
        
        task_id = parts[1].strip()
        task_manager = get_task_manager()
        task = task_manager.get_task(task_id)
        
        if not task:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"❌ Task <code>{task_id}</code> not found"
            )
            return {"ok": True}
        
        # Check ownership - guest users can only view their own tasks
        if not is_authorized(chat_id) and task.get('telegram_chat_id') != str(chat_id):
            await telegram.send_message(
                chat_id=chat_id,
                message="⚠️ <b>Access Denied</b>\n\nYou can only view your own tasks."
            )
            return {"ok": True}
        
        status_emoji = {
            'pending': '⏳',
            'downloading': '⬇️',
            'uploading': '⬆️',
            'completed': '✅',
            'failed': '❌',
            'cancelled': '🚫'
        }.get(task['status'], '❓')
        
        # Create progress bar
        def create_progress_bar(progress: float, length: int = 20) -> str:
            filled = int(length * progress / 100)
            empty = length - filled
            return '█' * filled + '░' * empty
        
        progress = task.get('progress', 0) or 0
        progress_bar = create_progress_bar(progress)
        
        if task['status'] in ['downloading', 'uploading']:
            msg = f"""
{status_emoji} <b>Task Status - {task['status'].upper()}</b>

📄 <b>File:</b> <code>{task.get('filename', 'N/A')}</code>
🆔 <b>Task:</b> <code>{task_id}</code>

[{progress_bar}] <b>{progress:.1f}%</b>
💬 {task.get('message', 'Processing...')}
            """.strip()
            
            if task.get('file_size_human'):
                msg += f"\n📦 Size: {task['file_size_human']}"
            
            msg += f"\n\n<i>Send /abort {task_id} to cancel</i>"
            
            # Delete old progress message
            try:
                telegram_service = get_telegram_service()
                if task_id in telegram_service._progress_messages:
                    old_msg = telegram_service._progress_messages[task_id]
                    try:
                        await telegram_service.bot.delete_message(chat_id, old_msg.message_id)
                    except Exception:
                        pass
                    del telegram_service._progress_messages[task_id]
            except Exception:
                pass
            
            # Send new message
            new_msg = await telegram.send_message(chat_id=chat_id, message=msg)
            if new_msg and task_id:
                try:
                    telegram_service = get_telegram_service()
                    telegram_service._progress_messages[task_id] = new_msg
                except Exception:
                    pass
            return {"ok": True}
            
        elif task['status'] == 'completed':
            msg = f"""
{status_emoji} <b>Task Completed!</b>

📄 <b>File:</b> <code>{task.get('filename', 'N/A')}</code>
🆔 <b>Task:</b> <code>{task_id}</code>
📦 <b>Size:</b> {task.get('file_size_human', 'N/A')}

🔗 <a href='{task.get('gdrive_link', '#')}'>Google Drive Link</a>
            """.strip()
            
        elif task['status'] == 'failed':
            msg = f"""
{status_emoji} <b>Task Failed</b>

📄 <b>File:</b> <code>{task.get('filename', 'N/A')}</code>
🆔 <b>Task:</b> <code>{task_id}</code>

⚠️ <b>Error:</b> <code>{task.get('error_message', 'Unknown error')[:200]}</code>
            """.strip()
            
        elif task['status'] == 'cancelled':
            msg = f"""
{status_emoji} <b>Task Cancelled</b>

📄 <b>File:</b> <code>{task.get('filename', 'N/A')}</code>
🆔 <b>Task:</b> <code>{task_id}</code>
            """.strip()
            
        else:  # pending
            msg = f"""
{status_emoji} <b>Task Pending</b>

📄 <b>File:</b> <code>{task.get('filename', 'N/A')}</code>
🆔 <b>Task:</b> <code>{task_id}</code>

⏳ Waiting in queue...

<i>Send /abort {task_id} to cancel</i>
            """.strip()
        
        await telegram.send_message(chat_id=chat_id, message=msg)
        return {"ok": True}
    
    # Unknown command
    if text.startswith('/'):
        await telegram.send_message(
            chat_id=chat_id,
            message="❓ Unknown command. Send /help for available commands."
        )
    
    return {"ok": True}


# Error handlers
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Handle global exceptions."""
    logger.error(f"Global exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "message": str(exc)}
    )


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
