"""
Celery tasks for Mirror Download Server.

This module contains all background tasks that can be executed asynchronously.
"""
import os
import asyncio
import logging
from typing import Optional

from celery import states
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded

from celery_app import celery_app
from config import get_settings
from models import DownloadStatus, NotificationPayload
from task_manager import get_task_manager
from services import FileDownloader, get_gdrive_service, get_telegram_service

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_download(self, task_id: str, url: str, filename: Optional[str] = None,
                     folder_id: Optional[str] = None, telegram_chat_id: Optional[str] = None,
                     callback_url: Optional[str] = None):
    """
    Celery task to process a download.
    
    This task runs in a separate worker process and handles:
    1. Downloading the file
    2. Uploading to Google Drive
    3. Sending notifications
    
    Args:
        self: Celery task instance (for retry/tracking)
        task_id: Unique task identifier
        url: URL to download
        filename: Optional custom filename
        folder_id: Optional Google Drive folder ID
        telegram_chat_id: Optional Telegram chat ID for notifications
        callback_url: Optional webhook URL
    """
    task_manager = get_task_manager()
    downloader = FileDownloader()
    
    # Update task status to started
    self.update_state(state=states.STARTED, meta={'progress': 0})
    task_manager.update_task(
        task_id,
        status=DownloadStatus.DOWNLOADING,
        message="Starting download..."
    )
    
    # Create event loop for async operations
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Initialize services
        gdrive = get_gdrive_service()
        telegram = get_telegram_service()
        
        # Send Telegram notification (started)
        if telegram_chat_id:
            loop.run_until_complete(
                telegram.notify_download_started(
                    chat_id=telegram_chat_id,
                    filename=filename or "unknown",
                    url=url,
                    task_id=task_id
                )
            )
        
        # Progress callback for download
        def progress_callback(downloaded: int, total: Optional[int], speed_kbps: float):
            if total:
                progress = (downloaded / total) * 100
                task_manager.update_task(
                    task_id,
                    status=DownloadStatus.DOWNLOADING,
                    progress=progress,
                    message=f"Downloading... {progress:.1f}% @ {speed_kbps:.1f} KB/s"
                )
                
                # Update Celery task state
                self.update_state(
                    state='PROGRESS',
                    meta={
                        'progress': progress,
                        'downloaded': downloaded,
                        'total': total,
                        'speed_kbps': speed_kbps
                    }
                )
                
                # Update Telegram progress
                if telegram_chat_id:
                    loop.run_until_complete(
                        telegram.update_download_progress(
                            chat_id=telegram_chat_id,
                            task_id=task_id,
                            filename=filename or "unknown",
                            progress=progress,
                            speed_kbps=speed_kbps,
                            downloaded_bytes=downloaded,
                            total_bytes=total
                        )
                    )
        
        # Download file
        download_result = loop.run_until_complete(
            downloader.download(
                url=url,
                filename=filename,
                progress_callback=progress_callback
            )
        )
        
        file_path = download_result['file_path']
        actual_filename = download_result['filename']
        file_size = download_result['file_size']
        file_size_human = download_result['file_size_human']
        
        # Update task with file info
        task_manager.update_task(
            task_id,
            filename=actual_filename,
            file_size=file_size,
            file_size_human=file_size_human
        )
        
        # Upload to Google Drive
        task_manager.update_task(
            task_id,
            status=DownloadStatus.UPLOADING,
            progress=0,
            message="Uploading to Google Drive..."
        )
        
        # Notify Telegram
        if telegram_chat_id:
            loop.run_until_complete(
                telegram.notify_uploading(
                    chat_id=telegram_chat_id,
                    task_id=task_id,
                    filename=actual_filename
                )
            )
        
        # Upload progress callback
        def upload_progress_callback(uploaded_bytes: int, total_bytes: int):
            if total_bytes > 0:
                progress = (uploaded_bytes / total_bytes) * 100
                # Update task manager
                task_manager.update_task(
                    task_id,
                    status=DownloadStatus.UPLOADING,
                    progress=progress,
                    message=f"Uploading... {progress:.1f}%"
                )
                
                # Update Telegram
                if telegram_chat_id:
                    loop.run_until_complete(
                        telegram.update_upload_progress(
                            chat_id=telegram_chat_id,
                            task_id=task_id,
                            filename=actual_filename,
                            progress=progress
                        )
                    )
        
        # Upload file
        gdrive_result = gdrive.upload_file(
            file_path=file_path,
            filename=actual_filename,
            folder_id=folder_id,
            description=f"Downloaded from: {url}",
            progress_callback=upload_progress_callback
        )
        
        gdrive_link = gdrive_result.get('webViewLink')
        
        # Update task as completed
        task_manager.update_task(
            task_id,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            message="Download completed successfully!",
            gdrive_link=gdrive_link,
            gdrive_file_id=gdrive_result.get('id')
        )
        
        # Send completion notification
        if telegram_chat_id:
            loop.run_until_complete(
                telegram.notify_download_complete(
                    chat_id=telegram_chat_id,
                    filename=actual_filename,
                    gdrive_link=gdrive_link,
                    file_size=file_size_human,
                    task_id=task_id
                )
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
            # TODO: Implement webhook call
            logger.info(f"Webhook would be sent to: {callback_url}")
        
        # Cleanup
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup {file_path}: {e}")
        
        # Close loop
        loop.close()
        
        return {
            'status': 'completed',
            'task_id': task_id,
            'gdrive_link': gdrive_link,
            'filename': actual_filename
        }
        
    except SoftTimeLimitExceeded:
        logger.error(f"Task {task_id} exceeded time limit")
        task_manager.update_task(
            task_id,
            status=DownloadStatus.FAILED,
            message="Download failed - time limit exceeded",
            error_message="Task took too long to complete"
        )
        if telegram_chat_id:
            telegram = get_telegram_service()
            loop.run_until_complete(
                telegram.notify_download_failed(
                    chat_id=telegram_chat_id,
                    filename=filename or url,
                    error="Download timeout - file too large or connection too slow",
                    task_id=task_id
                )
            )
        loop.close()
        raise
        
    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}", exc_info=True)
        
        # Update task as failed
        task_manager.update_task(
            task_id,
            status=DownloadStatus.FAILED,
            message="Download failed",
            error_message=str(e)
        )
        
        # Send failure notification
        if telegram_chat_id:
            telegram = get_telegram_service()
            loop.run_until_complete(
                telegram.notify_download_failed(
                    chat_id=telegram_chat_id,
                    filename=filename or url,
                    error=str(e),
                    task_id=task_id
                )
            )
        
        loop.close()
        
        # Retry logic
        try:
            self.retry(countdown=60)
        except MaxRetriesExceededError:
            logger.error(f"Max retries exceeded for task {task_id}")
            raise


@celery_app.task
def cleanup_old_tasks():
    """Periodic task to cleanup old completed/failed tasks."""
    # This can be scheduled to run periodically
    logger.info("Running periodic cleanup of old tasks")
    # Implementation depends on your cleanup policy
    pass
