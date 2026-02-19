"""Task manager for tracking download tasks - Admin Only."""
import uuid
import json
import logging
from typing import Dict, Optional
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from models import DownloadStatus, DownloadStatusResponse

logger = logging.getLogger(__name__)


class TaskManager:
    """In-memory task manager with optional persistence."""
    
    def __init__(self, persistence_file: str = "tasks.json"):
        self.tasks: Dict[str, dict] = {}
        self.persistence_file = Path(persistence_file)
        self.lock = Lock()
        self._load_tasks()
    
    def _load_tasks(self):
        """Load tasks from persistence file."""
        if self.persistence_file.exists():
            try:
                with open(self.persistence_file, 'r') as f:
                    data = json.load(f)
                    self.tasks = data
                logger.info(f"Loaded {len(self.tasks)} tasks from persistence")
            except Exception as e:
                logger.error(f"Failed to load tasks: {e}")
                self.tasks = {}
    
    def _save_tasks(self):
        """Save tasks to persistence file."""
        try:
            with open(self.persistence_file, 'w') as f:
                json.dump(self.tasks, f, default=str, indent=2)
        except Exception as e:
            logger.error(f"Failed to save tasks: {e}")
    
    def create_task(
        self, 
        url: str, 
        filename: Optional[str] = None,
        folder_id: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        callback_url: Optional[str] = None
    ) -> str:
        """Create a new download task."""
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        
        task = {
            'task_id': task_id,
            'status': DownloadStatus.PENDING.value,
            'progress': 0.0,
            'message': 'Task queued',
            'original_url': url,
            'filename': filename,
            'folder_id': folder_id,
            'telegram_chat_id': telegram_chat_id,
            'callback_url': callback_url,
            'file_size': None,
            'file_size_human': None,
            'gdrive_link': None,
            'gdrive_file_id': None,
            'error_message': None,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': None,
            'completed_at': None
        }
        
        with self.lock:
            self.tasks[task_id] = task
            self._save_tasks()
        
        logger.info(f"Created task {task_id}")
        return task_id
    
    def get_task(self, task_id: str) -> Optional[dict]:
        """Get task by ID."""
        with self.lock:
            return self.tasks.get(task_id)
    
    def update_task(
        self, 
        task_id: str, 
        status: Optional[DownloadStatus] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        filename: Optional[str] = None,
        file_size: Optional[int] = None,
        file_size_human: Optional[str] = None,
        gdrive_link: Optional[str] = None,
        gdrive_file_id: Optional[str] = None,
        error_message: Optional[str] = None
    ):
        """Update task status."""
        with self.lock:
            if task_id not in self.tasks:
                logger.warning(f"Task {task_id} not found")
                return
            
            task = self.tasks[task_id]
            
            if status:
                task['status'] = status.value
                if status == DownloadStatus.COMPLETED:
                    task['completed_at'] = datetime.now(timezone.utc).isoformat()
            
            if progress is not None:
                task['progress'] = progress
            
            if message:
                task['message'] = message
            
            if filename:
                task['filename'] = filename
            
            if file_size:
                task['file_size'] = file_size
            
            if file_size_human:
                task['file_size_human'] = file_size_human
            
            if gdrive_link:
                task['gdrive_link'] = gdrive_link
            
            if gdrive_file_id:
                task['gdrive_file_id'] = gdrive_file_id
            
            if error_message:
                task['error_message'] = error_message
            
            task['updated_at'] = datetime.now(timezone.utc).isoformat()
            self._save_tasks()
    
    def delete_task(self, task_id: str):
        """Delete a task."""
        with self.lock:
            if task_id in self.tasks:
                del self.tasks[task_id]
                self._save_tasks()
    
    def list_tasks(self, status: Optional[DownloadStatus] = None, limit: int = 100) -> list:
        """List tasks, optionally filtered by status."""
        with self.lock:
            tasks = list(self.tasks.values())
            
            if status:
                tasks = [t for t in tasks if t['status'] == status.value]
            
            # Sort by created_at descending
            tasks.sort(key=lambda x: x['created_at'], reverse=True)
            return tasks[:limit]
    
    def to_status_response(self, task_id: str) -> Optional[DownloadStatusResponse]:
        """Convert task to DownloadStatusResponse model."""
        task = self.get_task(task_id)
        if not task:
            return None
        
        return DownloadStatusResponse(
            task_id=task['task_id'],
            status=DownloadStatus(task['status']),
            progress=task.get('progress', 0.0),
            message=task.get('message', ''),
            original_url=task.get('original_url'),
            filename=task.get('filename'),
            file_size=task.get('file_size'),
            file_size_human=task.get('file_size_human'),
            gdrive_link=task.get('gdrive_link'),
            gdrive_file_id=task.get('gdrive_file_id'),
            error_message=task.get('error_message'),
            created_at=datetime.fromisoformat(task['created_at']),
            updated_at=datetime.fromisoformat(task['updated_at']) if task.get('updated_at') else None,
            completed_at=datetime.fromisoformat(task['completed_at']) if task.get('completed_at') else None
        )


# Singleton instance
task_manager = None

def get_task_manager() -> TaskManager:
    """Get task manager singleton."""
    global task_manager
    if task_manager is None:
        task_manager = TaskManager()
    return task_manager
