"""Main FastAPI application for Mirror Download Server."""
import os
import sys
import json
import asyncio
import logging
import subprocess
from pathlib import Path
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
        # Files are typically named with task_id or contain the filename
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
_authorized_users = {}  # Map of chat_id -> {authorized_at: timestamp, code: code_used}
_secret_codes = {}  # Map of code -> {created_by, used_by, created_at, expires_at}
_blocked_users = set()  # Set of blocked chat IDs
_revoked_codes = set()  # Set of revoked (deactivated) codes
_code_to_user = {}  # Map of code -> user_chat_id (for tracking which code was used by whom)

# Persistence files
_AUTHORIZED_USERS_FILE = Path("authorized_users.json")
_BLOCKED_USERS_FILE = Path("blocked_users.json")
_SECRET_CODES_FILE = Path("secret_codes.json")
_CODE_TO_USER_FILE = Path("code_to_user.json")

def _load_authorized_users():
    """Load authorized users from persistence file."""
    global _authorized_users
    if _AUTHORIZED_USERS_FILE.exists():
        try:
            with open(_AUTHORIZED_USERS_FILE, 'r') as f:
                data = json.load(f)
                # Handle migration from old format (list) to new format (dict)
                if isinstance(data, list):
                    _authorized_users = {uid: {"authorized_at": datetime.now(timezone.utc).isoformat(), "code": None} for uid in data}
                else:
                    _authorized_users = data
            logger.info(f"Loaded {len(_authorized_users)} authorized users")
        except Exception as e:
            logger.error(f"Failed to load authorized users: {e}")
            _authorized_users = {}

def _save_authorized_users():
    """Save authorized users to persistence file."""
    try:
        with open(_AUTHORIZED_USERS_FILE, 'w') as f:
            json.dump(_authorized_users, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save authorized users: {e}")

def _is_user_access_expired(chat_id: str) -> bool:
    """Check if user's access duration has expired."""
    settings = get_settings()
    if settings.USER_ACCESS_DURATION_HOURS <= 0:
        return False  # Never expire
    
    chat_id_str = str(chat_id)
    if chat_id_str not in _authorized_users:
        return True  # Not authorized = expired
    
    user_data = _authorized_users[chat_id_str]
    authorized_at = user_data.get("authorized_at")
    
    if not authorized_at:
        return True
    
    try:
        auth_time = datetime.fromisoformat(authorized_at)
        if auth_time.tzinfo is None:
            auth_time = auth_time.replace(tzinfo=timezone.utc)
        
        expires_at = auth_time + timedelta(hours=settings.USER_ACCESS_DURATION_HOURS)
        now = datetime.now(timezone.utc)
        
        return now > expires_at
    except Exception as e:
        logger.error(f"Error checking user access expiry: {e}")
        return False

def _load_blocked_users():
    """Load blocked users from persistence file."""
    global _blocked_users
    if _BLOCKED_USERS_FILE.exists():
        try:
            with open(_BLOCKED_USERS_FILE, 'r') as f:
                data = json.load(f)
                _blocked_users = set(data)
            logger.info(f"Loaded {len(_blocked_users)} blocked users")
        except Exception as e:
            logger.error(f"Failed to load blocked users: {e}")
            _blocked_users = set()

def _save_blocked_users():
    """Save blocked users to persistence file."""
    try:
        with open(_BLOCKED_USERS_FILE, 'w') as f:
            json.dump(list(_blocked_users), f)
    except Exception as e:
        logger.error(f"Failed to save blocked users: {e}")

def _load_secret_codes():
    """Load secret codes from persistence file."""
    global _secret_codes
    if _SECRET_CODES_FILE.exists():
        try:
            with open(_SECRET_CODES_FILE, 'r') as f:
                _secret_codes = json.load(f)
            logger.info(f"Loaded {len(_secret_codes)} secret codes")
        except Exception as e:
            logger.error(f"Failed to load secret codes: {e}")
            _secret_codes = {}

def _save_secret_codes():
    """Save secret codes to persistence file."""
    try:
        with open(_SECRET_CODES_FILE, 'w') as f:
            json.dump(_secret_codes, f, default=str, indent=2)
    except Exception as e:
        logger.error(f"Failed to save secret codes: {e}")

def _load_code_to_user():
    """Load code-to-user mapping from persistence file."""
    global _code_to_user
    if _CODE_TO_USER_FILE.exists():
        try:
            with open(_CODE_TO_USER_FILE, 'r') as f:
                _code_to_user = json.load(f)
            logger.info(f"Loaded {len(_code_to_user)} code-to-user mappings")
        except Exception as e:
            logger.error(f"Failed to load code-to-user mappings: {e}")
            _code_to_user = {}

def _save_code_to_user():
    """Save code-to-user mapping to persistence file."""
    try:
        with open(_CODE_TO_USER_FILE, 'w') as f:
            json.dump(_code_to_user, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save code-to-user mappings: {e}")

def cleanup_expired_authorizations():
    """Remove authorized users whose codes have expired or been revoked."""
    settings = get_settings()
    removed_count = 0
    
    for code, user_id in list(_code_to_user.items()):
        # Check if code exists
        if code not in _secret_codes:
            # Code doesn't exist anymore, remove user
            if user_id in _authorized_users:
                del _authorized_users[user_id]
                removed_count += 1
                logger.info(f"Removed user {user_id} - code {code} no longer exists")
            del _code_to_user[code]
            continue
        
        code_info = _secret_codes[code]
        
        # Check if code is revoked
        if is_code_revoked(code) or code_info.get('used_by') == 'REVOKED':
            if user_id in _authorized_users:
                del _authorized_users[user_id]
                removed_count += 1
                logger.info(f"Removed user {user_id} - code {code} was revoked")
            continue
        
        # Check if code is expired
        if _is_code_expired(code_info):
            if user_id in _authorized_users:
                del _authorized_users[user_id]
                removed_count += 1
                logger.info(f"Removed user {user_id} - code {code} expired")
            continue
    
    if removed_count > 0:
        _save_authorized_users()
        _save_code_to_user()
        logger.info(f"Cleaned up {removed_count} expired/revoked authorizations")

# Load persisted data on module load
_load_authorized_users()
_load_blocked_users()
_load_secret_codes()
_load_code_to_user()

# Cleanup expired authorizations on startup
cleanup_expired_authorizations()

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
    
    created_str = code_info['created_at']
    created = datetime.fromisoformat(created_str)
    
    # Ensure created is timezone-aware (UTC)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    
    expires = created + timedelta(hours=settings.CODE_EXPIRY_HOURS)
    now = datetime.now(timezone.utc)
    
    return now > expires

def _cleanup_expired_codes():
    """Remove expired codes from the dict and revoke access for users who used them."""
    expired = []
    expired_used = []  # Codes that were used and now expired
    
    for code, info in _secret_codes.items():
        if _is_code_expired(info):
            if info['used_by'] is None:
                expired.append(code)
            elif info['used_by'] != 'REVOKED':
                expired_used.append(code)
    
    # Remove unused expired codes
    for code in expired:
        del _secret_codes[code]
        logger.info(f"Expired code removed: {code}")
    
    # Remove access for users who used expired codes
    for code in expired_used:
        user_id = _secret_codes[code]['used_by']
        if user_id in _authorized_users:
            del _authorized_users[user_id]
            logger.info(f"Removed user {user_id} access - code {code} expired")
        if code in _code_to_user:
            del _code_to_user[code]
        # Mark as revoked so it won't be processed again
        _secret_codes[code]['used_by'] = 'REVOKED'
    
    if expired or expired_used:
        _save_secret_codes()
        _save_authorized_users()
        _save_code_to_user()

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
    
    created_at = datetime.now(timezone.utc)
    expires_at = None
    if settings.CODE_EXPIRY_HOURS > 0:
        expires_at = (created_at + timedelta(hours=settings.CODE_EXPIRY_HOURS)).isoformat()
    
    _secret_codes[code] = {
        'created_by': admin_chat_id,
        'used_by': None,
        'created_at': created_at.isoformat(),
        'expires_at': expires_at
    }
    _save_secret_codes()
    return code

def is_authorized(chat_id: str) -> bool:
    """Check if user is authorized to use the bot."""
    settings = get_settings()
    chat_id_str = str(chat_id)
    
    # Admin is always authorized (and can't be blocked)
    if chat_id_str == settings.TELEGRAM_ADMIN_CHAT_ID:
        return True
    
    # Check if user is blocked
    if chat_id_str in _blocked_users:
        return False
    
    # Check if user is in authorized list
    if chat_id_str not in _authorized_users:
        return False
    
    # Check if user's access has expired
    if _is_user_access_expired(chat_id_str):
        # Remove from authorized (cleanup)
        if chat_id_str in _authorized_users:
            del _authorized_users[chat_id_str]
            _save_authorized_users()
            logger.info(f"Removed user {chat_id_str} - access duration expired")
        return False
    
    return True

def is_blocked(chat_id: str) -> bool:
    """Check if user is blocked."""
    return str(chat_id) in _blocked_users

def check_previous_access(chat_id: str) -> Optional[str]:
    """
    Check if user previously had access that expired or was revoked.
    Returns message to show user if applicable.
    """
    settings = get_settings()
    chat_id_str = str(chat_id)
    
    # Check if user is currently not authorized
    if is_authorized(chat_id):
        return None
    
    # Check if user was in authorized list with data (had access before)
    if chat_id_str in _authorized_users:
        user_data = _authorized_users[chat_id_str]
        auth_time = user_data.get("authorized_at")
        if auth_time:
            try:
                auth_dt = datetime.fromisoformat(auth_time)
                if auth_dt.tzinfo is None:
                    auth_dt = auth_dt.replace(tzinfo=timezone.utc)
                expires_dt = auth_dt + timedelta(hours=settings.USER_ACCESS_DURATION_HOURS)
                now = datetime.now(timezone.utc)
                
                if now > expires_dt:
                    return f"""
⏰ <b>Access Duration Expired</b>

Your access has expired after {settings.USER_ACCESS_DURATION_HOURS} hours.
You are now back to guest mode with limits:
• Max file size: 1 GB
• Files go to: Public folder

Contact admin for a new access code to restore full access.
                    """.strip()
            except Exception:
                pass
    
    # Check if user was in code_to_user mapping (had access before)
    for code, user_id in _code_to_user.items():
        if user_id == chat_id_str:
            # User had this code, check status
            if code in _secret_codes:
                code_info = _secret_codes[code]
                if code_info.get('used_by') == 'REVOKED':
                    return """
⏰ <b>Access Code Revoked</b>

Your access code has been revoked by admin.
You are now back to guest mode with limits:
• Max file size: 1 GB
• Files go to: Public folder

Contact admin for a new access code to restore full access.
                    """.strip()
                elif _is_code_expired(code_info):
                    return """
⏰ <b>Access Code Expired</b>

Your access code has expired.
You are now back to guest mode with limits:
• Max file size: 1 GB
• Files go to: Public folder

Contact admin for a new access code to restore full access.
                    """.strip()
    
    return None

def block_user(chat_id: str) -> bool:
    """Block a user from using the bot."""
    chat_id_str = str(chat_id)
    if chat_id_str not in _blocked_users:
        _blocked_users.add(chat_id_str)
        _save_blocked_users()
        # Also remove from authorized if they were authorized
        if chat_id_str in _authorized_users:
            del _authorized_users[chat_id_str]
            _save_authorized_users()
        logger.info(f"User {chat_id} has been blocked")
        return True
    return False

def unblock_user(chat_id: str) -> bool:
    """Unblock a user."""
    chat_id_str = str(chat_id)
    if chat_id_str in _blocked_users:
        _blocked_users.discard(chat_id_str)
        _save_blocked_users()
        logger.info(f"User {chat_id} has been unblocked")
        return True
    return False

def revoke_code(code: str) -> bool:
    """Revoke (deactivate) an access code and remove associated user access."""
    if code in _secret_codes:
        # Get the user who used this code
        user_id = _secret_codes[code].get('used_by')
        
        # Revoke the code
        _revoked_codes.add(code)
        _secret_codes[code]['used_by'] = 'REVOKED'
        
        # Remove user from authorized if they used this code
        if user_id and user_id != 'REVOKED' and user_id in _authorized_users:
            del _authorized_users[user_id]
            logger.info(f"Removed user {user_id} access - code {code} revoked")
        
        # Clean up code-to-user mapping
        if code in _code_to_user:
            del _code_to_user[code]
        
        _save_secret_codes()
        _save_authorized_users()
        _save_code_to_user()
        logger.info(f"Code {code} has been revoked")
        return True
    return False

def is_code_revoked(code: str) -> bool:
    """Check if a code has been revoked."""
    return code in _revoked_codes

def authorize_user(chat_id: str, code: str) -> bool:
    """Authorize a user with a secret code."""
    _cleanup_expired_codes()
    
    # Check if user is blocked
    if is_blocked(chat_id):
        return False
    
    if code in _secret_codes:
        code_info = _secret_codes[code]
        
        # Check if code is revoked
        if is_code_revoked(code):
            return False
        
        # Check if expired
        if _is_code_expired(code_info):
            return False
        
        if code_info['used_by'] is None:
            chat_id_str = str(chat_id)
            _secret_codes[code]['used_by'] = chat_id
            _authorized_users[chat_id_str] = {
                "authorized_at": datetime.now(timezone.utc).isoformat(),
                "code": code
            }
            _code_to_user[code] = chat_id_str  # Track which code was used by this user
            _save_authorized_users()
            _save_secret_codes()
            _save_code_to_user()
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
• /status &lt;task_id&gt; - Check task status
• /abort &lt;task_id&gt; - Cancel a running task{admin_hint}

Just paste any URL to start downloading.
                """.strip()
            )
        else:
            # Show guest user menu (public access)
            await telegram.send_message(
                chat_id=chat_id,
                message=f"""
🤖 <b>Mirror Download Bot</b>

👋 <b>Welcome Guest!</b>

You can download files directly without registration!

<b>Limits:</b>
• 📦 Max file size: <b>1 GB</b>
• 📁 Files go to: <b>Public folder</b>

<b>To download:</b>
Just paste any direct download link!
/abort &lt;task_id&gt; - Cancel a running task
/status &lt;task_id&gt; - Check task status

<b>To unlock more features:</b>
1. Contact admin for access code
2. Send: <code>/auth YOUR_CODE</code>
3. Enjoy higher limits & personal folder!

<i>Example: /auth ABC12345</i>
                """.strip()
            )
        return {"ok": True}
    
    # /auth command - authorize with code
    if text.startswith('/auth'):
        # Check if user is blocked
        if is_blocked(chat_id):
            await telegram.send_message(
                chat_id=chat_id,
                message="""
🚫 <b>Access Blocked</b>

Your account has been blocked from using this bot.

Contact admin for more information.
                """.strip()
            )
            return {"ok": True}
        
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ Please provide a code\nExample: <code>/auth ABC12345</code>"
            )
            return {"ok": True}
        
        code = parts[1].strip().upper()
        
        # Check if code is revoked
        if is_code_revoked(code):
            await telegram.send_message(
                chat_id=chat_id,
                message="""
❌ <b>Code Revoked</b>

This access code has been deactivated by admin.

Contact admin for a new code.
                """.strip()
            )
            return {"ok": True}
        
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
• Has been revoked by admin

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
                # Calculate actual expiry time (now + expiry hours)
                expires_utc = datetime.now(timezone.utc) + timedelta(hours=settings.CODE_EXPIRY_HOURS)
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
• /revoke &lt;CODE&gt; - Revoke an access code

<b>User Management:</b>
• /users - List all users with stats
• /block &lt;chat_id&gt; - Block a user
• /unblock &lt;chat_id&gt; - Unblock a user

<b>Task Management:</b>
• /status &lt;task_id&gt; - Check task status
• /abort &lt;task_id&gt; - Cancel a task

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
    
    # /users command - admin only, list all authorized users
    if text == '/users':
        if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ This command is for admin only."
            )
            return {"ok": True}
        
        # Get all tasks to extract user activity
        task_manager = get_task_manager()
        all_tasks = task_manager.list_tasks(limit=1000)
        
        # Build user stats from tasks
        user_stats = {}
        for task in all_tasks:
            user_id = task.get('telegram_chat_id')
            if not user_id:
                continue
            
            if user_id not in user_stats:
                user_stats[user_id] = {
                    'total': 0,
                    'completed': 0,
                    'failed': 0,
                    'last_active': task.get('created_at')
                }
            
            user_stats[user_id]['total'] += 1
            if task['status'] == 'completed':
                user_stats[user_id]['completed'] += 1
            elif task['status'] == 'failed':
                user_stats[user_id]['failed'] += 1
        
        # Build message
        msg_lines = ["👥 <b>Authorized Users</b>\n"]
        
        if not _authorized_users:
            msg_lines.append("No authorized users yet.")
        else:
            for user_id in sorted(_authorized_users):
                stats = user_stats.get(user_id, {})
                total = stats.get('total', 0)
                completed = stats.get('completed', 0)
                failed = stats.get('failed', 0)
                last_active = stats.get('last_active', 'Never')
                
                # Check if blocked
                if is_blocked(user_id):
                    status = "🚫 BLOCKED"
                else:
                    status = "✅ Active"
                
                msg_lines.append(
                    f"<code>{user_id}</code>\n"
                    f"  {status} | Downloads: {total} ({completed}✓ {failed}✗)\n"
                    f"  Last: {format_wib(last_active) if last_active != 'Never' else 'Never'}\n"
                )
        
        # Show blocked users separately
        if _blocked_users:
            msg_lines.append("\n🚫 <b>Blocked Users:</b>")
            for blocked_id in sorted(_blocked_users):
                stats = user_stats.get(blocked_id, {})
                total = stats.get('total', 0)
                msg_lines.append(f"  <code>{blocked_id}</code> ({total} downloads)")
        
        msg_lines.append(f"\n📊 Total: {len(_authorized_users)} users, {len(_blocked_users)} blocked")
        
        await telegram.send_message(
            chat_id=chat_id,
            message="\n".join(msg_lines)
        )
        return {"ok": True}
    
    # /block command - admin only, block a user
    if text.startswith('/block'):
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
                message="❌ Usage: <code>/block &lt;chat_id&gt;</code>\n\nExample: <code>/block 123456789</code>"
            )
            return {"ok": True}
        
        target_id = parts[1].strip()
        
        # Prevent blocking admin
        if target_id == settings.TELEGRAM_ADMIN_CHAT_ID:
            await telegram.send_message(
                chat_id=chat_id,
                message="❌ You cannot block yourself (admin)."
            )
            return {"ok": True}
        
        if block_user(target_id):
            await telegram.send_message(
                chat_id=chat_id,
                message=f"🚫 User <code>{target_id}</code> has been blocked.\n\nThey can no longer use the bot."
            )
        else:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"⚠️ User <code>{target_id}</code> is already blocked."
            )
        return {"ok": True}
    
    # /unblock command - admin only, unblock a user
    if text.startswith('/unblock'):
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
                message="❌ Usage: <code>/unblock &lt;chat_id&gt;</code>\n\nExample: <code>/unblock 123456789</code>"
            )
            return {"ok": True}
        
        target_id = parts[1].strip()
        
        if unblock_user(target_id):
            await telegram.send_message(
                chat_id=chat_id,
                message=f"✅ User <code>{target_id}</code> has been unblocked.\n\nThey can now use the bot again."
            )
        else:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"⚠️ User <code>{target_id}</code> was not blocked."
            )
        return {"ok": True}
    
    # /revoke command - admin only, revoke an access code
    if text.startswith('/revoke'):
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
                message="❌ Usage: <code>/revoke &lt;CODE&gt;</code>\n\nExample: <code>/revoke ABC12345</code>"
            )
            return {"ok": True}
        
        code = parts[1].strip().upper()
        
        if revoke_code(code):
            await telegram.send_message(
                chat_id=chat_id,
                message=f"🚫 Code <code>{code}</code> has been revoked.\n\nIt can no longer be used."
            )
        else:
            await telegram.send_message(
                chat_id=chat_id,
                message=f"❌ Code <code>{code}</code> not found."
            )
        return {"ok": True}
    
    # URL detection (direct link sent) - ALLOWED for guest users
    if text.startswith(('http://', 'https://')):
        # Check if user is blocked
        if is_blocked(chat_id):
            await telegram.send_message(
                chat_id=chat_id,
                message="""
🚫 <b>Access Blocked</b>

Your account has been blocked from using this bot.

Contact admin for more information.
                """.strip()
            )
            return {"ok": True}
        
        # Check if user previously had access that expired/revoked
        access_expired_msg = check_previous_access(chat_id)
        if access_expired_msg:
            await telegram.send_message(
                chat_id=chat_id,
                message=access_expired_msg
            )
        
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
            
            # Check file size limit for guest users (not authorized)
            is_user_authorized = is_authorized(chat_id)
            file_size = info.get('size', 0) or 0
            
            if not is_user_authorized and file_size > GUEST_MAX_FILE_SIZE:
                await telegram.send_message(
                    chat_id=chat_id,
                    message=f"""
❌ <b>File Too Large for Guest</b>

📄 <b>File:</b> <code>{info['filename']}</code>
📦 <b>Size:</b> {info['size_human']}
🚫 <b>Guest Limit:</b> 1 GB

<b>To download larger files:</b>
1. Get an access code from admin
2. Send: <code>/auth YOUR_CODE</code>
3. Then send the URL again
                    """.strip()
                )
                return {"ok": True}
            
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
            
            # Determine folder based on user authentication status
            task_manager = get_task_manager()
            settings = get_settings()
            folder_id = None
            folder_type_msg = ""
            
            # Initialize Google Drive service for folder creation
            gdrive = get_gdrive_service()
            
            if is_user_authorized:
                # Authenticated user - use/create personal folder
                user_folder_id = task_manager.get_user_folder(str(chat_id))
                
                if user_folder_id:
                    # User already has a folder
                    folder_id = user_folder_id
                    folder_type_msg = "Folder: Personal"
                else:
                    # Create new folder for this user
                    try:
                        # Get or create users root folder
                        if settings.GDRIVE_USERS_ROOT_FOLDER_ID:
                            users_root_id = settings.GDRIVE_USERS_ROOT_FOLDER_ID
                        else:
                            # Auto-create users root folder
                            users_root_id = gdrive.get_or_create_folder("Users")
                        
                        folder_name = f"User_{chat_id}"
                        user_folder_id = gdrive.create_folder(
                            folder_name=folder_name,
                            parent_id=users_root_id
                        )
                        task_manager.set_user_folder(str(chat_id), user_folder_id)
                        folder_id = user_folder_id
                        folder_type_msg = "Folder: Personal (new)"
                        logger.info(f"Created personal folder for user {chat_id}: {user_folder_id}")
                    except Exception as e:
                        logger.error(f"Failed to create user folder: {e}")
                        # Fallback to public folder
                        try:
                            if settings.GDRIVE_PUBLIC_FOLDER_ID:
                                folder_id = settings.GDRIVE_PUBLIC_FOLDER_ID
                            else:
                                folder_id = gdrive.get_or_create_folder("Public")
                            folder_type_msg = "Folder: Public"
                        except Exception as e2:
                            logger.error(f"Fallback folder creation failed: {e2}")
                            folder_id = None
                            folder_type_msg = "Folder: Root (fallback)"
            else:
                # Guest user - use/create public folder
                try:
                    if settings.GDRIVE_PUBLIC_FOLDER_ID:
                        folder_id = settings.GDRIVE_PUBLIC_FOLDER_ID
                    else:
                        # Auto-create public folder
                        folder_id = gdrive.get_or_create_folder("Public")
                    folder_type_msg = "Folder: Public"
                except Exception as e:
                    logger.error(f"Failed to create public folder: {e}")
                    folder_id = None
                    folder_type_msg = "Folder: Root (fallback)"
            
            # Create task
            worker = get_worker()
            
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
{folder_type_msg}

⏱️ I'll notify you when it's ready!
                """.strip()
            )
            return {"ok": True}
            
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

<code>{error_msg[:200]}</code>

Please try again or contact admin."""
            
            await telegram.send_message(chat_id=chat_id, message=user_message)
            return {"ok": True}
    
    # Helper function to check if user owns the task
    def _owns_task(task, user_chat_id: str) -> bool:
        return task.get('telegram_chat_id') == user_chat_id
    
    # /abort command - cancel a running task (allowed for task owners including guests)
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
    
    # /status command (allowed for task owners including guests)
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
        
        # Create progress bar for visual status
        def create_progress_bar(progress: float, length: int = 20) -> str:
            filled = int(length * progress / 100)
            empty = length - filled
            return '█' * filled + '░' * empty
        
        # Build visual status message
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
        return {"ok": True}
    
    # Check authorization for all other commands (admin commands, etc.)
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
