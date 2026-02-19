"""Background worker for processing download tasks."""
import os
import asyncio
import logging
import time
import shutil
import httpx
from pathlib import Path
from typing import Optional, Dict

from config import get_settings
from models import DownloadStatus, NotificationPayload
from task_manager import get_task_manager
from services import FileDownloader, get_gdrive_service, get_telegram_service
from services.torrent_service import get_torrent_service

logger = logging.getLogger(__name__)


class DownloadWorker:
    """Worker that processes download tasks."""
    
    def __init__(self):
        self.settings = get_settings()
        self.task_manager = get_task_manager()
        self.downloader = FileDownloader()
        self.torrent_service = get_torrent_service()
        self.gdrive = None  # Lazy init
        self.telegram = None  # Lazy init
        self._running = False
        self._queue = asyncio.Queue()
        self._current_task: Optional[str] = None
        self._active_tasks: Dict[str, asyncio.Task] = {}  # Track running tasks for cancellation
        self._cancelled_tasks: set = set()  # Track cancelled task IDs
    
    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a running task.
        
        Args:
            task_id: The task ID to cancel
            
        Returns:
            True if task was found and cancelled, False otherwise
        """
        if task_id in self._active_tasks:
            task = self._active_tasks[task_id]
            if not task.done():
                task.cancel()
                self._cancelled_tasks.add(task_id)
                logger.info(f"Task {task_id} cancellation requested")
                return True
        return False
    
    def is_task_cancelled(self, task_id: str) -> bool:
        """Check if a task has been marked for cancellation."""
        return task_id in self._cancelled_tasks
    
    def clear_cancellation(self, task_id: str):
        """Remove task from cancelled set (called after task completes)."""
        self._cancelled_tasks.discard(task_id)
    
    async def _init_services(self):
        """Initialize services lazily."""
        if self.gdrive is None:
            # Run in thread pool since gdrive auth might block
            loop = asyncio.get_event_loop()
            try:
                self.gdrive = await loop.run_in_executor(None, get_gdrive_service)
            except Exception as e:
                logger.error(f"Failed to initialize Google Drive service: {e}")
                raise RuntimeError(f"Google Drive authentication failed: {e}")
        
        if self.telegram is None:
            self.telegram = get_telegram_service()
    
    def _progress_callback(self, task_id: str, telegram_chat_id: Optional[str] = None, filename: Optional[str] = None):
        """Create progress callback for a task with Telegram progress updates."""
        import time
        last_update = time.time()
        last_progress = 0.0
        
        def callback(downloaded: int, total: Optional[int], speed_kbps: float):
            nonlocal last_update, last_progress
            
            current_time = time.time()
            
            # Calculate progress
            if total and total > 0:
                progress = (downloaded / total) * 100
            else:
                progress = 0
            
            logger.debug(f"Progress callback called: {progress:.1f}%, {speed_kbps:.1f} KB/s")
            
            # Always update task manager (internal tracking)
            if total:
                self.task_manager.update_task(
                    task_id,
                    status=DownloadStatus.DOWNLOADING,
                    progress=progress,
                    message=f"Downloading... {progress:.1f}% @ {speed_kbps:.1f} KB/s"
                )
            else:
                downloaded_mb = downloaded / (1024 * 1024)
                self.task_manager.update_task(
                    task_id,
                    status=DownloadStatus.DOWNLOADING,
                    message=f"Downloading... {downloaded_mb:.1f} MB @ {speed_kbps:.1f} KB/s"
                )
            
            # Update Telegram message every 3 seconds or when progress changes significantly
            time_since_last = current_time - last_update
            progress_diff = abs(progress - last_progress)
            
            if telegram_chat_id and (time_since_last >= 3 or progress_diff >= 10):
                logger.debug(f"Scheduling Telegram update for task {task_id}: {progress:.1f}% (time: {time_since_last:.1f}s, diff: {progress_diff:.1f}%)")
                try:
                    # Use asyncio.create_task to run async function from sync callback
                    asyncio.create_task(
                        self._update_telegram_progress(
                            telegram_chat_id, task_id, filename or "unknown", 
                            progress, speed_kbps, downloaded, total
                        )
                    )
                    last_update = current_time
                    last_progress = progress
                    logger.debug(f"Telegram update scheduled for task {task_id}")
                except Exception as e:
                    logger.debug(f"Failed to schedule Telegram update: {e}")
        
        return callback
    
    async def _update_telegram_progress(self, chat_id: str, task_id: str, filename: str, 
                                        progress: float, speed_kbps: float, 
                                        downloaded: int, total: Optional[int]):
        """Helper to update Telegram progress message."""
        try:
            await self.telegram.update_download_progress(
                chat_id=chat_id,
                task_id=task_id,
                filename=filename,
                progress=progress,
                speed_kbps=speed_kbps,
                downloaded_bytes=downloaded,
                total_bytes=total
            )
        except Exception as e:
            logger.debug(f"Telegram progress update failed: {e}")
    
    async def _send_webhook(self, callback_url: str, payload: NotificationPayload):
        """Send webhook notification."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(callback_url, json=payload.model_dump())
                logger.info(f"Webhook sent to {callback_url}")
        except Exception as e:
            logger.error(f"Failed to send webhook: {e}")

    def _cleanup_path(self, path: Optional[str], context: str):
        """Helper to clean up a file or directory."""
        if not path:
            return

        try:
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                    logger.info(f"Cleaned up directory {path} for {context}")
                else:
                    os.remove(path)
                    logger.info(f"Cleaned up file {path} for {context}")
        except Exception as e:
            logger.error(f"Failed to clean up {path} for {context}: {e}")
    
    async def process_task(self, task_id: str):
        """Process a single download task."""
        task = self.task_manager.get_task(task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return
        
        self._current_task = task_id
        url = task['original_url']
        filename = task.get('filename')
        folder_id = task.get('folder_id')
        telegram_chat_id = task.get('telegram_chat_id')
        callback_url = task.get('callback_url')
        
        # Register task for cancellation tracking
        current_task = asyncio.current_task()
        self._active_tasks[task_id] = current_task
        self.clear_cancellation(task_id)
        
        # Initialize variables that may be used in exception handlers
        actual_filename = None
        file_path = None
        file_size = 0
        file_size_human = "0 B"
        
        try:
            # Initialize services
            await self._init_services()
            
            # Update status to downloading
            self.task_manager.update_task(
                task_id,
                status=DownloadStatus.DOWNLOADING,
                message="Starting download..."
            )
            
            # Send Telegram notification (started) - returns message ID for editing
            if telegram_chat_id:
                logger.info(f"Sending start notification to Telegram for task {task_id}")
                msg_id = await self.telegram.notify_download_started(
                    chat_id=telegram_chat_id,
                    filename=filename or "unknown",
                    url=url,
                    task_id=task_id
                )
                if msg_id:
                    logger.info(f"Start notification sent, msg_id: {msg_id}")
                else:
                    logger.warning(f"Failed to send start notification for task {task_id}")
            
            # Check if task was cancelled before starting download
            if self.is_task_cancelled(task_id):
                logger.info(f"Task {task_id} was cancelled before download started")
                raise asyncio.CancelledError("Task cancelled by user")
            
            # Download file with progress updates
            progress_cb = self._progress_callback(
                task_id, 
                telegram_chat_id=telegram_chat_id, 
                filename=filename or "unknown"
            )
            
            # Check if it's a torrent/magnet
            if url.startswith('magnet:') or url.endswith('.torrent'):
                logger.info(f"Processing torrent for task {task_id}")
                download_result = await self.torrent_service.download_torrent(
                    link=url,
                    save_path=self.settings.TEMP_DOWNLOAD_DIR,
                    progress_callback=progress_cb
                )
            else:
                download_result = await self.downloader.download(
                    url=url,
                    filename=filename,
                    progress_callback=progress_cb
                )
            
            # Check if task was cancelled after download
            if self.is_task_cancelled(task_id):
                logger.info(f"Task {task_id} was cancelled after download, cleaning up")
                # Clean up downloaded file
                self._cleanup_path(download_result['file_path'], f"cancelled task {task_id} after download")
                raise asyncio.CancelledError("Task cancelled by user")
            
            file_path = download_result['file_path']
            actual_filename = download_result['filename']
            file_size = download_result['file_size']
            file_size_human = download_result['file_size_human']
            
            # Update task with file info
            self.task_manager.update_task(
                task_id,
                filename=actual_filename,
                file_size=file_size,
                file_size_human=file_size_human
            )
            
            # Check if task was cancelled before upload
            if self.is_task_cancelled(task_id):
                logger.info(f"Task {task_id} was cancelled before upload, cleaning up")
                self._cleanup_path(file_path, f"cancelled task {task_id} before upload")
                raise asyncio.CancelledError("Task cancelled by user")
            
            # Upload to Google Drive
            self.task_manager.update_task(
                task_id,
                status=DownloadStatus.UPLOADING,
                progress=0,
                message="Uploading to Google Drive..."
            )
            
            # Notify Telegram that we're uploading
            if telegram_chat_id:
                await self.telegram.notify_uploading(
                    chat_id=telegram_chat_id,
                    task_id=task_id,
                    filename=actual_filename
                )
            
            # Check if task was cancelled right before upload starts
            if self.is_task_cancelled(task_id):
                logger.info(f"Task {task_id} was cancelled before upload starts, cleaning up")
                self._cleanup_path(file_path, f"cancelled task {task_id} before upload starts")
                raise asyncio.CancelledError("Task cancelled by user")
            
            # Create upload progress callback
            last_upload_progress = 0
            last_upload_time = time.time()
            
            def upload_progress_callback(uploaded_bytes: int, total_bytes: int):
                nonlocal last_upload_progress, last_upload_time
                if total_bytes > 0:
                    progress = (uploaded_bytes / total_bytes) * 100
                    current_time = time.time()
                    time_since_last = current_time - last_upload_time
                    if progress - last_upload_progress >= 5 or time_since_last >= 5:
                        if telegram_chat_id:
                            asyncio.create_task(
                                self.telegram.update_upload_progress(
                                    chat_id=telegram_chat_id,
                                    task_id=task_id,
                                    filename=actual_filename,
                                    progress=progress
                                )
                            )
                        last_upload_progress = progress
                        last_upload_time = current_time
            
            # Handle upload (Directory vs Single File)
            loop = asyncio.get_event_loop()
            gdrive_result = None

            if os.path.exists(file_path) and os.path.isdir(file_path):
                logger.info(f"Task {task_id}: Path is a directory, uploading recursively: {file_path}")
                
                def upload_recursive(current_path, parent_id=None, is_root=False):
                    folder_name = os.path.basename(current_path)
                    
                    # Update status
                    if is_root:
                        self.task_manager.update_task(
                            task_id,
                            status=DownloadStatus.UPLOADING,
                            message=f"Creating folder: {folder_name}"
                        )
                    
                    # Create folder
                    remote_folder_id = self.gdrive.create_folder(folder_name, parent_id)
                    
                    # Make public if root
                    if is_root:
                         try:
                             self.gdrive._make_file_public(remote_folder_id)
                         except Exception:
                             pass
                    
                    items = sorted(os.listdir(current_path))
                    total_items = len(items)
                    
                    for index, item in enumerate(items):
                        item_path = os.path.join(current_path, item)
                        
                        if is_root:
                             self.task_manager.update_task(
                                task_id,
                                status=DownloadStatus.UPLOADING,
                                message=f"Processing {index+1}/{total_items}: {item}"
                            )

                        if os.path.isdir(item_path):
                            upload_recursive(item_path, remote_folder_id, is_root=False)
                        else:
                            self.gdrive.upload_file(
                                file_path=item_path,
                                filename=item,
                                folder_id=remote_folder_id,
                                description=f"Part of {actual_filename}"
                            )
                            
                    return {
                        'id': remote_folder_id, 
                        'webViewLink': f"https://drive.google.com/drive/folders/{remote_folder_id}"
                    }

                gdrive_result = await loop.run_in_executor(
                    None,
                    lambda: upload_recursive(file_path, folder_id, is_root=True)
                )

            else:
                # Single file upload
                gdrive_result = await loop.run_in_executor(
                    None,
                    lambda: self.gdrive.upload_file(
                        file_path=file_path,
                        filename=actual_filename,
                        folder_id=folder_id,
                        description=f"Downloaded from: {url}",
                        progress_callback=upload_progress_callback if telegram_chat_id else None
                    )
                )
            
            # (Zip cleanup removed)
            is_temp_zip = False  # Flag kept for compatibility with variable defined later if any
            
            gdrive_link = gdrive_result.get('webViewLink')
            gdrive_file_id = gdrive_result.get('id')
            
            # Update task as completed
            self.task_manager.update_task(
                task_id,
                status=DownloadStatus.COMPLETED,
                progress=100.0,
                message="Download completed successfully!",
                gdrive_link=gdrive_link,
                gdrive_file_id=gdrive_file_id
            )
            
            # Send notifications
            if telegram_chat_id:
                await self.telegram.notify_download_complete(
                    chat_id=telegram_chat_id,
                    filename=actual_filename,
                    gdrive_link=gdrive_link,
                    file_size=file_size_human,
                    task_id=task_id
                )
            
            if callback_url:
                payload = NotificationPayload(
                    task_id=task_id,
                    status=DownloadStatus.COMPLETED,
                    message="Download completed",
                    gdrive_link=gdrive_link,
                    filename=actual_filename,
                    file_size_human=file_size_human
                )
                await self._send_webhook(callback_url, payload)
            
            logger.info(f"Task {task_id} completed: {gdrive_link}")
            
            # Clean up downloaded file after successful upload
            self._cleanup_path(file_path, f"completed task {task_id}")
            
        except asyncio.CancelledError as e:
            # Task was cancelled by user
            error_msg = str(e) if str(e) else "Task cancelled by user"
            logger.info(f"Task {task_id} was cancelled: {error_msg}")
            
            # Clean up downloaded file if exists
            self._cleanup_path(file_path, f"cancelled task {task_id}")
            
            # Update task as cancelled
            self.task_manager.update_task(
                task_id,
                status=DownloadStatus.CANCELLED,
                message="Task cancelled",
                error_message=error_msg
            )
            
            # Notify Telegram about cancellation
            if telegram_chat_id:
                await self.telegram.notify_download_cancelled(
                    chat_id=telegram_chat_id,
                    filename=actual_filename or filename or url,
                    task_id=task_id
                )
            
            if callback_url:
                payload = NotificationPayload(
                    task_id=task_id,
                    status=DownloadStatus.CANCELLED,
                    message=f"Task cancelled: {error_msg}",
                    filename=filename
                )
                await self._send_webhook(callback_url, payload)
        
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Task {task_id} failed: {error_msg}", exc_info=True)
            
            # Clean up downloaded file if exists
            self._cleanup_path(file_path, f"failed task {task_id}")
            
            # Update task as failed
            self.task_manager.update_task(
                task_id,
                status=DownloadStatus.FAILED,
                message="Download failed",
                error_message=error_msg
            )
            
            # Send failure notification
            if telegram_chat_id:
                await self.telegram.notify_download_failed(
                    chat_id=telegram_chat_id,
                    filename=filename or url,
                    error=error_msg,
                    task_id=task_id
                )
            
            if callback_url:
                payload = NotificationPayload(
                    task_id=task_id,
                    status=DownloadStatus.FAILED,
                    message=f"Download failed: {error_msg}",
                    filename=filename
                )
                await self._send_webhook(callback_url, payload)
        
        finally:
            # Remove from active and cancelled tasks sets
            self._active_tasks.pop(task_id, None)
            self._cancelled_tasks.discard(task_id)
            self._current_task = None
    
    async def start(self):
        """Start the worker."""
        self._running = True
        logger.info("Download worker started")
        
        while self._running:
            try:
                # Use wait_for to check cancellation or shutdown if needed, or simply await get()
                # But here wait_for allows us to log "tick" or handle shutdown gracefully if needed
                # However, await self._queue.get() is fine and efficient.
                task_id = await self._queue.get()
                logger.info(f"Processing task {task_id}")
                await self.process_task(task_id)
            except asyncio.CancelledError:
                logger.info("Worker cancelled")
                break
            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)
                await asyncio.sleep(1) # Prevent tight loop on error
    
    def stop(self):
        """Stop the worker."""
        self._running = False
        logger.info("Download worker stopped")
    
    def queue_task(self, task_id: str):
        """Queue a task for processing."""
        self._queue.put_nowait(task_id)
        logger.info(f"Task {task_id} queued")
    
    def get_current_task(self) -> Optional[str]:
        """Get currently processing task ID."""
        return self._current_task


# Singleton instance
worker = None

def get_worker() -> DownloadWorker:
    """Get worker singleton."""
    global worker
    if worker is None:
        worker = DownloadWorker()
    return worker
