"""
Celery application configuration for Mirror Download Server.

This enables distributed task processing with Redis as broker.
For single server: Redis runs locally
For scaling: Can move Redis to separate server or use Redis Cloud
"""
from celery import Celery
from config import get_settings

settings = get_settings()

# Parse Redis URL to create separate broker and backend URLs
# Broker: DB 0 for task queue
# Backend: DB 1 for results
redis_url = settings.REDIS_URL

# Remove trailing /N if exists and add correct DB
if '/0' in redis_url or '/1' in redis_url or '/2' in redis_url:
    # Remove existing DB number
    redis_base = redis_url.rsplit('/', 1)[0]
else:
    redis_base = redis_url.rstrip('/')

broker_url = f"{redis_base}/0"      # DB 0 for task queue
backend_url = f"{redis_base}/1"      # DB 1 for results

# Create Celery app
celery_app = Celery(
    'mirror_download',
    broker=broker_url,
    backend=backend_url,
    include=['tasks']
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Jakarta',  # WIB timezone
    enable_utc=True,
    
    # Task execution
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3300,  # Warning at 55 minutes
    
    # Worker settings
    worker_prefetch_multiplier=1,  # Process one task at a time per worker
    worker_max_tasks_per_child=50,  # Restart worker after 50 tasks (prevent memory leak)
    
    # Result backend
    result_expires=3600,  # Results expire after 1 hour
    
    # Retry settings
    task_default_retry_delay=60,  # Retry after 1 minute
    task_max_retries=3,
    
    # Queue settings (for future scaling)
    task_default_queue='default',
    task_routes={
        'tasks.process_download': {'queue': 'downloads'},
    },
)

# Optional: Add beat schedule for periodic tasks (if needed in future)
celery_app.conf.beat_schedule = {
    # Example: Cleanup old tasks every hour
    # 'cleanup-old-tasks': {
    #     'task': 'tasks.cleanup_old_tasks',
    #     'schedule': 3600.0,
    # },
}

if __name__ == '__main__':
    celery_app.start()
