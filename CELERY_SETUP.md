# Celery Integration Guide

This document explains the Celery integration for Mirror Download Server.

## 📋 Overview

Celery is a distributed task queue that provides:
- **Task Persistence**: Tasks survive server restarts
- **Better Reliability**: Automatic retries on failure
- **Scalability**: Can add more workers easily
- **Monitoring**: Track task progress and status

## 🏗️ Architecture

### Without Celery (Built-in Worker)
```
User Request → FastAPI → Async Worker (in-memory queue) → Download → Upload → Done
```

### With Celery (Redis + Workers)
```
User Request → FastAPI → Redis Queue → Celery Worker(s) → Download → Upload → Done
                                        ↓
                                   Can add more workers!
```

## 🚀 Setup Instructions

### 1. Enable Celery

Edit `.env`:
```env
USE_CELERY=true
REDIS_URL=redis://localhost:6379/0
```

### 2. Install Redis

```bash
# Ubuntu/Debian
sudo apt install redis-server
sudo systemctl enable redis
sudo systemctl start redis

# Test Redis
redis-cli ping
# Should return: PONG
```

### 3. Start Celery Worker

**Option A: Manual Start**
```bash
# Start worker (2 concurrent tasks)
./start_celery.sh 2

# Or start with specific number of workers
./start_celery.sh 4
```

**Option B: Systemd Service**
```bash
# Setup systemd services
sudo ./celery_systemd.sh

# Start services
sudo systemctl start celery-worker
sudo systemctl enable celery-worker

# Check status
sudo systemctl status celery-worker
sudo journalctl -u celery-worker -f
```

### 4. Start Main Server

```bash
# Start as usual
python main.py
# or
sudo systemctl start mirror-download
```

## 📊 Worker Types

### Single Server (Current)
```bash
# 1 Redis + 1 Worker = Single server setup
Redis: localhost:6379
Worker: 2 concurrent tasks
```

### Scale Later (Future)
When you need more power, you can:

1. **Multiple Workers on Same Server**
   ```bash
   # Terminal 1
   ./start_celery.sh 4
   
   # Terminal 2 (additional workers)
   ./start_celery.sh 4
   ```

2. **Separate Worker Server**
   ```bash
   # Server 1: Redis + FastAPI
   REDIS_URL=redis://server1-ip:6379/0
   
   # Server 2: Celery Workers only
   REDIS_URL=redis://server1-ip:6379/0
   ./start_celery.sh 8
   ```

3. **Redis Cloud Service**
   ```env
   # Use managed Redis (Redis Cloud, AWS ElastiCache, etc.)
   REDIS_URL=redis://user:pass@redis-cloud-provider.com:6379/0
   ```

## 🔧 Configuration Options

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_CELERY` | `false` | Enable Celery processing |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |

### Celery Settings (in `celery_app.py`)

```python
# Task timeout
task_time_limit = 3600  # 1 hour max per task

# Retries
task_default_retry_delay = 60  # Retry after 1 minute
task_max_retries = 3

# Worker concurrency
worker_prefetch_multiplier = 1  # One task at a time per worker
```

## 📈 Monitoring

### Check Celery Status

```bash
# List active workers
cd /opt/mirror-download
source venv/bin/activate
celery -A celery_app inspect active

# Check worker stats
celery -A celery_app inspect stats

# View registered tasks
celery -A celery_app inspect registered
```

### Flower Dashboard (Optional)

```bash
# Install Flower
pip install flower

# Start dashboard
celery -A celery_app flower --port=5555

# Access at http://your-server:5555
```

## 🔄 Switching Between Modes

### From Built-in to Celery

```bash
# 1. Edit config
nano .env
# Set USE_CELERY=true

# 2. Start Redis
sudo systemctl start redis

# 3. Start Celery worker
./start_celery.sh 2

# 4. Restart main server
sudo systemctl restart mirror-download
```

### From Celery to Built-in

```bash
# 1. Edit config
nano .env
# Set USE_CELERY=false

# 2. Stop Celery worker
sudo systemctl stop celery-worker
# or Ctrl+C if running manually

# 3. Restart main server
sudo systemctl restart mirror-download
```

## 🆚 Comparison

| Feature | Built-in Worker | Celery |
|---------|----------------|--------|
| Setup | Simple | Requires Redis |
| Task Persistence | No (lost on restart) | Yes (stored in Redis) |
| Multiple Workers | No | Yes |
| Automatic Retries | Limited | Yes |
| Monitoring | Basic | Advanced |
| Resource Usage | Lower | Higher (Redis) |
| Best For | Single server, simple use | Production, scale |

## 🛠️ Troubleshooting

### Redis Connection Error
```bash
# Check if Redis is running
sudo systemctl status redis

# Start Redis
sudo systemctl start redis

# Check Redis logs
sudo journalctl -u redis -f
```

### Celery Worker Not Processing
```bash
# Check worker status
celery -A celery_app inspect ping

# Restart worker
sudo systemctl restart celery-worker

# Check logs
tail -f celery.log
```

### Task Stuck
```bash
# View active tasks
celery -A celery_app inspect active

# Revoke a stuck task
celery -A celery_app control revoke <task_id>

# Purge all pending tasks (DANGEROUS!)
celery -A celery_app purge
```

## 📁 Files Added/Modified

### New Files
- `celery_app.py` - Celery configuration
- `tasks.py` - Celery task definitions
- `start_celery.sh` - Start Celery worker script
- `start_celery_beat.sh` - Start Celery scheduler
- `celery_systemd.sh` - Setup systemd services
- `CELERY_SETUP.md` - This documentation

### Modified Files
- `requirements.txt` - Added celery, redis
- `config.py` - Added USE_CELERY, REDIS_URL
- `main.py` - Added Celery task queuing
- `setup.sh` - Added Redis installation

## 📝 Summary

**For single server**: Celery adds reliability (tasks survive restarts) with minimal overhead.

**For scaling**: Celery allows easy horizontal scaling by adding more worker servers.

**Migration path**: You can start with `USE_CELERY=false`, then enable it later without code changes.
