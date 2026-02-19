"""Pydantic models for the application."""
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, Literal
from datetime import datetime, timezone
from enum import Enum

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DownloadStatus(str, Enum):
    """Download status enum."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadRequest(BaseModel):
    """Request model for downloading a file."""
    url: str = Field(..., description="URL or Magnet link")
    filename: Optional[str] = Field(None, description="Custom filename (optional)")
    folder_id: Optional[str] = Field(None, description="Google Drive folder ID (optional)")
    callback_url: Optional[HttpUrl] = Field(None, description="Webhook URL for notification (optional)")
    telegram_chat_id: Optional[str] = Field(None, description="Telegram chat ID for notification (optional)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://example.com/file.zip",
                "filename": "my-file.zip",
                "folder_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
                "telegram_chat_id": "123456789"
            }
        }


class DownloadResponse(BaseModel):
    """Response model for download request."""
    task_id: str = Field(..., description="Unique task ID for tracking")
    status: DownloadStatus = Field(..., description="Current status")
    message: str = Field(..., description="Status message")
    created_at: datetime = Field(default_factory=_utcnow)
    
    class Config:
        json_schema_extra = {
            "example": {
                "task_id": "task-123-abc",
                "status": "pending",
                "message": "Download queued successfully",
                "created_at": "2024-01-15T10:30:00Z"
            }
        }


class DownloadStatusResponse(BaseModel):
    """Response model for download status check."""
    task_id: str
    status: DownloadStatus
    progress: float = Field(0.0, ge=0.0, le=100.0, description="Download progress %")
    message: str
    original_url: Optional[str] = None
    filename: Optional[str] = None
    file_size: Optional[int] = None
    file_size_human: Optional[str] = None
    gdrive_link: Optional[str] = None
    gdrive_file_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class NotificationPayload(BaseModel):
    """Payload for webhook notifications."""
    task_id: str
    status: DownloadStatus
    message: str
    gdrive_link: Optional[str] = None
    filename: Optional[str] = None
    file_size_human: Optional[str] = None
    timestamp: datetime = Field(default_factory=_utcnow)


class HealthResponse(BaseModel):
    """Health check response."""
    status: Literal["healthy", "unhealthy"] = "healthy"
    version: str = "1.0.0"
    timestamp: datetime = Field(default_factory=_utcnow)
    services: dict = Field(default_factory=dict)
