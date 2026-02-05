"""Telegram service for notifications."""
import logging
from typing import Optional, Dict
import httpx
from telegram import Bot, Message
from telegram.constants import ParseMode
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_settings

logger = logging.getLogger(__name__)


class TelegramService:
    """Service for sending Telegram notifications."""
    
    def __init__(self):
        self.settings = get_settings()
        self.bot: Optional[Bot] = None
        self.enabled = bool(self.settings.TELEGRAM_BOT_TOKEN)
        # Store message IDs for editing progress messages
        self._progress_messages: Dict[str, Message] = {}
        
        if self.enabled:
            self.bot = Bot(token=self.settings.TELEGRAM_BOT_TOKEN)
            logger.info("Telegram service initialized")
        else:
            logger.warning("Telegram service disabled - no bot token provided")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True
    )
    async def send_message(self, chat_id: str, message: str, parse_mode: ParseMode = ParseMode.HTML):
        """Send a message to a Telegram chat."""
        if not self.enabled or not self.bot:
            logger.warning("Telegram not configured, skipping message")
            return None
        
        try:
            msg = await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode,
                disable_web_page_preview=False
            )
            logger.info(f"Telegram message sent to {chat_id}")
            return msg
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True
    )
    async def edit_message(self, chat_id: str, message_id: int, message: str, parse_mode: ParseMode = ParseMode.HTML):
        """Edit an existing message."""
        if not self.enabled or not self.bot:
            return
        
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=message,
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
            logger.debug(f"Message {message_id} edited successfully")
        except Exception as e:
            error_str = str(e).lower()
            # Ignore "message is not modified" errors (common when progress hasn't changed)
            if "message is not modified" in error_str or "message_not_modified" in error_str:
                logger.debug(f"Message not modified (no change in content)")
            else:
                logger.warning(f"Failed to edit message {message_id}: {e}")
    
    def _create_progress_bar(self, progress: float, length: int = 20) -> str:
        """Create a visual progress bar."""
        filled = int(length * progress / 100)
        empty = length - filled
        return '█' * filled + '░' * empty
    
    def _format_speed(self, speed_kbps: float) -> str:
        """Format speed in human readable format."""
        if speed_kbps >= 1024:
            return f"{speed_kbps/1024:.1f} MB/s"
        return f"{speed_kbps:.1f} KB/s"
    
    async def notify_download_started(
        self, 
        chat_id: str, 
        filename: str,
        url: str,
        task_id: str
    ) -> Optional[int]:
        """Send download started notification with initial progress."""
        progress_bar = self._create_progress_bar(0)
        
        message = f"""
⏳ <b>Downloading...</b>

📄 <b>File:</b> <code>{filename}</code>
🆔 <b>Task:</b> <code>{task_id}</code>

[{progress_bar}] <b>0.0%</b>
📊 <b>Speed:</b> 0 KB/s
💾 <b>Downloaded:</b> 0 MB / ? MB

⏱️ Starting download...
        """.strip()
        
        try:
            msg = await self.send_message(chat_id, message)
            if msg:
                # Store message for later editing
                self._progress_messages[task_id] = msg
                logger.info(f"Progress message stored for task {task_id}, msg_id: {msg.message_id}")
                return msg.message_id
        except Exception as e:
            logger.error(f"Failed to send start notification: {e}")
        return None
    
    async def update_download_progress(
        self,
        chat_id: str,
        task_id: str,
        filename: str,
        progress: float,
        speed_kbps: float,
        downloaded_bytes: int,
        total_bytes: Optional[int] = None
    ):
        """Update the progress message."""
        if not self.enabled:
            logger.debug("Telegram not enabled, skipping progress update")
            return
        
        if task_id not in self._progress_messages:
            logger.debug(f"No progress message stored for task {task_id}")
            return
        
        progress_bar = self._create_progress_bar(progress)
        speed_str = self._format_speed(speed_kbps)
        
        logger.debug(f"Updating progress for task {task_id}: {progress:.1f}%")
        
        # Format sizes
        def format_size(bytes_val: int) -> str:
            if bytes_val >= 1024**3:
                return f"{bytes_val / (1024**3):.2f} GB"
            elif bytes_val >= 1024**2:
                return f"{bytes_val / (1024**2):.2f} MB"
            else:
                return f"{bytes_val / 1024:.2f} KB"
        
        downloaded_str = format_size(downloaded_bytes)
        total_str = format_size(total_bytes) if total_bytes else "?"
        
        message = f"""
⏳ <b>Downloading...</b>

📄 <b>File:</b> <code>{filename}</code>
🆔 <b>Task:</b> <code>{task_id}</code>

[{progress_bar}] <b>{progress:.1f}%</b>
📊 <b>Speed:</b> {speed_str}
💾 <b>Downloaded:</b> {downloaded_str} / {total_str}

⏱️ Downloading...
        """.strip()
        
        try:
            msg = self._progress_messages[task_id]
            await self.edit_message(chat_id, msg.message_id, message)
        except KeyError:
            logger.warning(f"Task {task_id} not found in progress messages")
        except Exception as e:
            logger.error(f"Error updating progress for task {task_id}: {e}")
    
    async def notify_uploading(
        self,
        chat_id: str,
        task_id: str,
        filename: str
    ):
        """Notify that file is being uploaded to Google Drive."""
        if task_id in self._progress_messages:
            message = f"""
⬆️ <b>Uploading to Google Drive...</b>

📄 <b>File:</b> <code>{filename}</code>
🆔 <b>Task:</b> <code>{task_id}</code>

[{self._create_progress_bar(0)}] <b>0%</b>

☁️ Uploading to Google Drive, please wait...
            """.strip()
            
            msg = self._progress_messages[task_id]
            await self.edit_message(chat_id, msg.message_id, message)
    
    async def update_upload_progress(
        self,
        chat_id: str,
        task_id: str,
        filename: str,
        progress: float
    ):
        """Update upload progress message."""
        if not self.enabled or task_id not in self._progress_messages:
            return
        
        progress_bar = self._create_progress_bar(progress)
        
        message = f"""
⬆️ <b>Uploading to Google Drive...</b>

📄 <b>File:</b> <code>{filename}</code>
🆔 <b>Task:</b> <code>{task_id}</code>

[{progress_bar}] <b>{progress:.1f}%</b>

☁️ Uploading to Google Drive, please wait...
        """.strip()
        
        try:
            msg = self._progress_messages[task_id]
            await self.edit_message(chat_id, msg.message_id, message)
        except Exception as e:
            logger.debug(f"Failed to update upload progress: {e}")
    
    async def notify_download_complete(
        self, 
        chat_id: str, 
        filename: str, 
        gdrive_link: str,
        file_size: Optional[str] = None,
        task_id: Optional[str] = None
    ):
        """Send download completion notification."""
        # Delete progress message if exists
        if task_id and task_id in self._progress_messages:
            try:
                msg = self._progress_messages[task_id]
                await self.bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass
            del self._progress_messages[task_id]
        
        size_info = f"📦 <b>Size:</b> {file_size}\n" if file_size else ""
        task_info = f"🆔 <b>Task ID:</b> <code>{task_id}</code>\n" if task_id else ""
        
        message = f"""
✅ <b>Download Complete!</b>

📄 <b>File:</b> <code>{filename}</code>
{size_info}{task_info}
🔗 <b>Google Drive Link:</b>
<a href="{gdrive_link}">{gdrive_link}</a>

🎉 File is ready for download!
        """.strip()
        
        await self.send_message(chat_id, message)
    
    async def notify_download_failed(
        self, 
        chat_id: str, 
        filename: str,
        error: str,
        task_id: Optional[str] = None
    ):
        """Send download failure notification."""
        # Delete progress message if exists
        if task_id and task_id in self._progress_messages:
            try:
                msg = self._progress_messages[task_id]
                await self.bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass
            del self._progress_messages[task_id]
        
        task_info = f"🆔 <b>Task ID:</b> <code>{task_id}</code>\n" if task_id else ""
        
        # Truncate error if too long
        error_display = error[:200] + "..." if len(error) > 200 else error
        
        message = f"""
❌ <b>Download Failed!</b>

📄 <b>File:</b> <code>{filename}</code>
{task_info}
⚠️ <b>Error:</b> <code>{error_display}</code>

Please try again or contact admin.
        """.strip()
        
        await self.send_message(chat_id, message)
    
    async def send_admin_notification(self, message: str):
        """Send notification to admin."""
        if self.settings.TELEGRAM_ADMIN_CHAT_ID:
            await self.send_message(
                self.settings.TELEGRAM_ADMIN_CHAT_ID,
                f"🔔 <b>Admin Notification</b>\n\n{message}"
            )


# Singleton instance
telegram_service = None

def get_telegram_service() -> TelegramService:
    """Get or create Telegram service singleton."""
    global telegram_service
    if telegram_service is None:
        telegram_service = TelegramService()
    return telegram_service
