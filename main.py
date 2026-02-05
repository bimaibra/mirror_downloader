"""Main FastAPI application for Mirror Download Server."""
import os
import sys
import asyncio
import logging
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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


# Startup and shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown."""
    # Startup
    settings = get_settings()
    logger.info(f"Starting {settings.APP_NAME}")
    
    # Create download directory
    os.makedirs(settings.TEMP_DOWNLOAD_DIR, exist_ok=True)
    
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
    description="Download files from URLs and upload to Google Drive",
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
        message="Task cancelled by user"
    )
    
    return {"message": f"Task {task_id} cancelled"}


# Bot Authorization System
# Store authorized users and secret codes
_authorized_users = set()  # Set of authorized chat IDs
_secret_codes = {}  # Map of code -> {created_by, used_by, created_at, expires_at}

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

def _is_code_expired(code_info: dict) -> bool:
    """Check if a code has expired."""
    settings = get_settings()
    if settings.CODE_EXPIRY_HOURS <= 0:
        return False  # Never expire
    
    created = datetime.fromisoformat(code_info['created_at'])
    expires = created + timedelta(hours=settings.CODE_EXPIRY_HOURS)
    return datetime.utcnow() > expires

def _cleanup_expired_codes():
    """Remove expired codes from the dict."""
    expired = []
    for code, info in _secret_codes.items():
        if _is_code_expired(info) and info['used_by'] is None:
            expired.append(code)
    for code in expired:
        del _secret_codes[code]
        logger.info(f"Expired code removed: {code}")

def generate_secret_code(admin_chat_id: str) -> str:
    """Generate a new secret code for bot access."""
    import secrets
    import string
    from datetime import datetime, timedelta
    
    settings = get_settings()
    
    # Cleanup old codes first
    _cleanup_expired_codes()
    
    # Count active codes for this admin
    active_codes = sum(
        1 for info in _secret_codes.values()
        if info['created_by'] == admin_chat_id and info['used_by'] is None
    )
    if active_codes >= settings.MAX_CODES_PER_ADMIN:
        raise ValueError(f"Maximum {settings.MAX_CODES_PER_ADMIN} active codes per admin")
    
    # Generate 8-character code
    code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    
    created_at = datetime.utcnow()
    expires_at = None
    if settings.CODE_EXPIRY_HOURS > 0:
        expires_at = (created_at + timedelta(hours=settings.CODE_EXPIRY_HOURS)).isoformat()
    
    _secret_codes[code] = {
        'created_by': admin_chat_id,
        'used_by': None,
        'created_at': created_at.isoformat(),
        'expires_at': expires_at
    }
    return code

def is_authorized(chat_id: str) -> bool:
    """Check if user is authorized to use the bot."""
    settings = get_settings()
    # Admin is always authorized
    if chat_id == settings.TELEGRAM_ADMIN_CHAT_ID:
        return True
    return str(chat_id) in _authorized_users

def authorize_user(chat_id: str, code: str) -> bool:
    """Authorize a user with a secret code."""
    _cleanup_expired_codes()
    
    if code in _secret_codes:
        code_info = _secret_codes[code]
        
        # Check if expired
        if _is_code_expired(code_info):
            return False
        
        if code_info['used_by'] is None:
            code_info['used_by'] = chat_id
            _authorized_users.add(str(chat_id))
            return True
    return False

def get_code_info(code: str) -> dict:
    """Get code info with expiration status."""
    if code not in _secret_codes:
        return None
    
    info = _secret_codes[code].copy()
    info['expired'] = _is_code_expired(_secret_codes[code])
    return info


# Webhook endpoint for external integrations
@app.post("/webhook/telegram")
async def telegram_webhook(update: dict):
    """Handle Telegram webhook updates."""
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
    
    # Commands that don't require auth
    if text == '/start' or text == '/help':
        if is_authorized(chat_id):
            # Show authorized user menu
            # Check if user is admin to show admin hint
            admin_hint = "\n• /admin - Admin menu" if chat_id == settings.TELEGRAM_ADMIN_CHAT_ID else ""
            
            await telegram.send_message(
                chat_id=chat_id,
                message=f"""
🤖 <b>Mirror Download Bot</b>

✅ You are authorized!

Send me a direct download link and I'll upload it to Google Drive for you!

<b>Commands:</b>
• /status &lt;task_id&gt; - Check task status with progress bar
• /abort &lt;task_id&gt; - Cancel a running task{admin_hint}

Just paste any URL to start downloading.
                """.strip()
            )
        else:
            # Show unauthorized user menu
            await telegram.send_message(
                chat_id=chat_id,
                message=f"""
🤖 <b>Mirror Download Bot</b>

⚠️ <b>Authorization Required</b>

This bot is private. You need an access code to use it.

<b>To get access:</b>
1. Contact the admin
2. Request an access code
3. Send: <code>/auth YOUR_CODE</code>

<i>Example: /auth ABC12345</i>
                """.strip()
            )
        return {"ok": True}
    
    # /auth command - authorize with code
    if text.startswith('/auth'):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ Please provide a code\nExample: <code>/auth ABC12345</code>"
            )
            return {"ok": True}
        
        code = parts[1].strip().upper()
        
        # Check if code exists and get info
        code_info = get_code_info(code)
        
        if code_info and code_info.get('expired') and code_info['used_by'] is None:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"""
❌ <b>Code Expired</b>

This access code has expired.

Contact admin for a new code.

<i>Codes expire after {settings.CODE_EXPIRY_HOURS} hours (WIB timezone)</i>
                """.strip()
            )
        elif authorize_user(chat_id, code):
            await telegram.send_message(
                chat_id=chat_id,
                message="""
✅ <b>Authorization Successful!</b>

You can now use the bot to download files.

Send me any download link to get started!
                """.strip()
            )
        else:
            await telegram.send_message(
                chat_id=chat_id,
                message="""
❌ <b>Invalid or Used Code</b>

The code you entered is either:
• Incorrect
• Already used by someone else

Contact admin for a new code.
                """.strip()
            )
        return {"ok": True}
    
    # /gencode command - admin only
    if text == '/gencode':
        if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ This command is for admin only."
            )
            return {"ok": True}
        
        try:
            code = generate_secret_code(chat_id)
            
            expiry_info = ""
            if settings.CODE_EXPIRY_HOURS > 0:
                expires_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
                expires_wib = to_wib(expires_utc)
                expiry_info = f"\n⏰ Expires: <code>{expires_wib.strftime('%Y-%m-%d %H:%M')} WIB</code>"
            else:
                expiry_info = "\n⏰ Never expires"
            
            await telegram.send_message(
                chat_id=chat_id,
                message=f"""
🔑 <b>New Access Code Generated</b>

<code>{code}</code>{expiry_info}

Share this code with users you want to authorize.
Each code can only be used once.

Send <code>/codes</code> to see all codes.
                """.strip()
            )
        except ValueError as e:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"❌ <b>Error:</b> {str(e)}\n\nDelete some unused codes with <code>/codes</code> first."
            )
        return {"ok": True}
    
    # Settings change tracking
    _pending_settings = {}  # chat_id -> {setting_name, value}
    
    # /admin command - admin only, list admin commands
    if text == '/admin':
        if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ This command is for admin only."
            )
            return {"ok": True}
        
        await telegram.send_message(
            chat_id=chat_id,
            message="""
🔐 <b>Admin Commands</b>

<b>Access Control:</b>
• /gencode - Generate new access code
• /codes - List all access codes

<b>Bot Management:</b>
• /start - Start bot
• /status &lt;task_id&gt; - Check task status

<b>Configuration:</b>
• /settings - View/change bot settings
• /admin - Show this menu

<i>These commands are hidden from regular users.</i>
            """.strip()
        )
        return {"ok": True}
    
    # /settings command - admin only, view/change settings
    if text == '/settings':
        if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ This command is for admin only."
            )
            return {"ok": True}
        
        expiry_str = f"{settings.CODE_EXPIRY_HOURS} hours" if settings.CODE_EXPIRY_HOURS > 0 else "Never"
        
        await telegram.send_message(
            chat_id=chat_id,
            message=f"""
⚙️ <b>Bot Settings</b>

<b>Access Control:</b>
• Code expiry: <code>{expiry_str}</code>
• Max codes per admin: <code>{settings.MAX_CODES_PER_ADMIN}</code>

<b>Download Settings:</b>
• Max file size: <code>{settings.MAX_FILE_SIZE / (1024**3):.1f} GB</code>
• Download timeout: <code>{settings.DOWNLOAD_TIMEOUT} seconds</code>
• Admin chat ID: <code>{settings.TELEGRAM_ADMIN_CHAT_ID}</code>

<b>Change Settings:</b>
• <code>/setexpiry &lt;hours&gt;</code> - Set code expiry (0 = never)
• <code>/setmaxcodes &lt;number&gt;</code> - Set max codes per admin
• <code>/setmaxsize &lt;GB&gt;</code> - Set max file size in GB

<i>Changes apply immediately but reset on server restart.</i>
<i>To make permanent, update the .env file.</i>
            """.strip()
        )
        return {"ok": True}
    
    # /setexpiry command - change code expiry
    if text.startswith('/setexpiry'):
        if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ This command is for admin only."
            )
            return {"ok": True}
        
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ Usage: <code>/setexpiry &lt;hours&gt;</code>\nExample: <code>/setexpiry 24</code> or <code>/setexpiry 0</code> (never expire)"
            )
            return {"ok": True}
        
        try:
            hours = int(parts[1].strip())
            if hours < 0:
                raise ValueError("Hours must be >= 0")
            
            # Update settings directly
            settings.CODE_EXPIRY_HOURS = hours
            
            expiry_str = f"{hours} hours" if hours > 0 else "Never (codes don't expire)"
            await telegram.send_message(
                chat_id=chat_id,
                message=f"✅ <b>Setting Updated</b>\n\nCode expiry set to: <code>{expiry_str}</code>\n\n<i>Note: This change is temporary. Update CODE_EXPIRY_HOURS in .env to make it permanent.</i>"
            )
        except ValueError as e:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"❌ Invalid value: {str(e)}\n\nPlease provide a number >= 0."
            )
        return {"ok": True}
    
    # /setmaxcodes command - change max codes per admin
    if text.startswith('/setmaxcodes'):
        if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ This command is for admin only."
            )
            return {"ok": True}
        
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ Usage: <code>/setmaxcodes &lt;number&gt;</code>\nExample: <code>/setmaxcodes 10</code>"
            )
            return {"ok": True}
        
        try:
            max_codes = int(parts[1].strip())
            if max_codes < 1:
                raise ValueError("Must be at least 1")
            
            settings.MAX_CODES_PER_ADMIN = max_codes
            
            await telegram.send_message(
                chat_id=chat_id,
                message=f"✅ <b>Setting Updated</b>\n\nMax codes per admin set to: <code>{max_codes}</code>\n\n<i>Note: This change is temporary. Update MAX_CODES_PER_ADMIN in .env to make it permanent.</i>"
            )
        except ValueError as e:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"❌ Invalid value: {str(e)}\n\nPlease provide a number >= 1."
            )
        return {"ok": True}
    
    # /setmaxsize command - change max file size
    if text.startswith('/setmaxsize'):
        if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ This command is for admin only."
            )
            return {"ok": True}
        
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ Usage: <code>/setmaxsize &lt;GB&gt;</code>\nExample: <code>/setmaxsize 5</code> for 5 GB"
            )
            return {"ok": True}
        
        try:
            gb = float(parts[1].strip())
            if gb < 0.1:
                raise ValueError("Must be at least 0.1 GB")
            
            bytes_val = int(gb * 1024 * 1024 * 1024)
            settings.MAX_FILE_SIZE = bytes_val
            
            await telegram.send_message(
                chat_id=chat_id,
                message=f"✅ <b>Setting Updated</b>\n\nMax file size set to: <code>{gb:.1f} GB</code>\n\n<i>Note: This change is temporary. Update MAX_FILE_SIZE in .env to make it permanent.</i>"
            )
        except ValueError as e:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"❌ Invalid value: {str(e)}\n\nPlease provide a number >= 0.1"
            )
        return {"ok": True}
    
    # /codes command - admin only, list all codes
    if text == '/codes':
        if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ This command is for admin only."
            )
            return {"ok": True}
        
        _cleanup_expired_codes()
        
        if not _secret_codes:
            await telegram.send_message(chat_id=chat_id, message="No codes generated yet.")
            return {"ok": True}
        
        available_count = 0
        used_count = 0
        expired_count = 0
        msg_lines = ["📋 <b>Access Codes</b>\n"]
        
        for code, info in _secret_codes.items():
            created_wib = format_wib(info['created_at'], '%m-%d %H:%M')
            
            if info['used_by']:
                status = "✅ Used"
                used_by = f"by <code>{info['used_by'][:10]}...</code>" if len(str(info['used_by'])) > 10 else f"by <code>{info['used_by']}</code>"
                msg_lines.append(f"<code>{code}</code> {status} {used_by} (created: {created_wib} WIB)")
                used_count += 1
            else:
                is_expired = _is_code_expired(info)
                if is_expired:
                    status = "💀 Expired"
                    expired_count += 1
                else:
                    status = "⏳ Available"
                    available_count += 1
                
                expiry_str = ""
                if info.get('expires_at'):
                    expires_wib = format_wib(info['expires_at'], '%m-%d %H:%M')
                    expiry_str = f" | Expires: {expires_wib} WIB"
                
                msg_lines.append(f"<code>{code}</code> {status} (created: {created_wib} WIB){expiry_str}")
        
        # Summary
        summary = f"\n📊 <b>Summary:</b> {available_count} available, {used_count} used, {expired_count} expired"
        if settings.CODE_EXPIRY_HOURS > 0:
            summary += f"\n⏰ Codes expire after {settings.CODE_EXPIRY_HOURS} hours"
        else:
            summary += "\n⏰ Codes never expire"
        
        await telegram.send_message(
            chat_id=chat_id,
            message="\n".join(msg_lines) + summary
        )
        return {"ok": True}
    
    # Check authorization for all other commands
    if not is_authorized(chat_id):
        logger.warning(f"Unauthorized access attempt from chat_id: {chat_id}")
        await telegram.send_message(
            chat_id=chat_id,
            message="""
⚠️ <b>Not Authorized</b>

You need to authenticate first.
Send: <code>/auth YOUR_CODE</code>

Or contact admin for access.
            """.strip()
        )
        return {"ok": True}
    
    # /abort command - cancel a running task
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
        
        # Check if task can be cancelled
        if task['status'] not in ['pending', 'downloading', 'uploading']:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"❌ Cannot abort task with status: <b>{task['status']}</b>\n\nOnly pending or active tasks can be aborted."
            )
            return {"ok": True}
        
        # Cancel the task in worker (if currently running)
        worker = get_worker()
        worker_cancelled = worker.cancel_task(task_id)
        
        # Cancel the task in task manager
        task_manager.update_task(
            task_id,
            status=DownloadStatus.CANCELLED,
            message="Task cancelled by user"
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
    
    # /status command (requires auth)
    if text.startswith('/status'):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ Please provide a task ID\nExample: <code>/status task-abc123</code>\n\n<b>Commands:</b>\n• /status &lt;task_id&gt; - Show progress with visual bar\n• /abort &lt;task_id&gt; - Cancel running task"
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
        
        status_emoji = {
            'pending': '⏳',
            'downloading': '⬇️',
            'uploading': '⬆️',
            'completed': '✅',
            'failed': '❌',
            'cancelled': '🚫'
        }.get(task['status'], '❓')
        
        # Create progress bar for visual status
        def create_progress_bar(progress: float, length: int = 20) -> str:
            filled = int(length * progress / 100)
            empty = length - filled
            return '█' * filled + '░' * empty
        
        # Build visual status message with progress bar
        progress = task.get('progress', 0) or 0
        progress_bar = create_progress_bar(progress)
        
        # For active tasks, show visual progress bar
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
            
            # Delete old progress message and resend for recent chat view
            try:
                telegram_service = get_telegram_service()
                if task_id in telegram_service._progress_messages:
                    old_msg = telegram_service._progress_messages[task_id]
                    try:
                        await telegram_service.bot.delete_message(chat_id, old_msg.message_id)
                    except Exception:
                        pass  # Ignore if already deleted or too old
                    del telegram_service._progress_messages[task_id]
            except Exception:
                pass  # Continue even if delete fails
            
            # Send new message and track it for future updates
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
    
    # URL detection (direct link sent)
    elif text.startswith(('http://', 'https://')):
        import re
        # Extract URL (handle URLs with spaces encoded as %20)
        url_match = re.match(r'(https?://\S+)', text)
        if not url_match:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ <b>Invalid URL format</b>\n\nCould not parse the URL."
            )
            return {"ok": True}
        
        url = url_match.group(1)
        logger.info(f"Processing URL from Telegram: {url}")
        
        # Validate URL
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
            
            task_id = task_manager.create_task(
                url=url,
                filename=info['filename'],
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
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to process URL {url}: {error_msg}", exc_info=True)
            
            # Provide user-friendly error messages based on error type
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

Could not download from this URL.

Error: <code>{error_msg[:200]}</code>

Common issues:
• Link expired
• Requires browser cookies
• Anti-bot protection"""
            
            await telegram.send_message(
                chat_id=chat_id,
                message=user_message
            )
    
    # Unknown command
    elif text.startswith('/'):
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
    import sys
    
    settings = get_settings()
    
    # Windows doesn't support multiple workers with asyncio
    is_windows = sys.platform == "win32"
    workers = 1 if is_windows else 2
    
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        workers=workers
    )
