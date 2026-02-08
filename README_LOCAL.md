# Local Development Guide

Panduan menjalankan Mirror Download Server di komputer lokal (Windows/Mac/Linux).
**Ini adalah private server - hanya admin yang bisa mengakses.**

## 🚀 Quick Start (Tanpa Celery - Simple)

### 1. Install Dependencies
```bash
# Pastikan Python 3.10+ terinstall
python --version

# Install requirements
pip install -r requirements.txt
```

### 2. Setup Environment
```bash
# Copy config template
copy .env.example .env    # Windows
cp .env.example .env      # Mac/Linux

# Edit .env
# Isi: API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_ID
```

### 3. Setup Google Drive
- Download `credentials.json` dari Google Cloud Console
- Taruh di folder project

### 4. Jalankan Server
```bash
# Pastikan USE_CELERY=false di .env (default)
python main.py
```

Server berjalan di: http://localhost:8000

---

## 🚀 Dengan Celery (Redis + Worker)

### Windows

#### 1. Install Redis
```bash
# Download Redis for Windows:
# https://github.com/microsoftarchive/redis/releases
# Atau pakai WSL:
wsl sudo apt install redis-server
wsl sudo service redis-server start

# Test koneksi
redis-cli ping
# Harus balas: PONG
```

#### 2. Jalankan Server (3 Terminal)

**Terminal 1 - Redis (jika belum jalan):**
```bash
redis-server
```

**Terminal 2 - Celery Worker:**
```bash
# Pastikan di folder project
cd "d:\Dev\Python\mirror downloader"

# Jalankan worker
celery -A celery_app worker -l info -c 2
```

**Terminal 3 - Main Server:**
```bash
cd "d:\Dev\Python\mirror downloader"
python main.py
```

### Mac/Linux

```bash
# 1. Install & jalankan Redis
sudo apt install redis-server
sudo service redis-server start

# 2. Terminal 1 - Celery Worker
celery -A celery_app worker -l info -c 2

# 3. Terminal 2 - Main Server
python main.py
```

---

## 📝 Contoh .env untuk Local

```env
# Server
DEBUG=true
HOST=0.0.0.0
PORT=8000
API_KEY=test-key-local

# Google Drive
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json
GDRIVE_FOLDER_ID=              # Optional: default folder

# Telegram (opsional - hanya admin)
TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_CHAT_ID=

# Celery - UNTUK LOCAL TESTING
USE_CELERY=false              # false = simple, true = dengan Celery
# REDIS_URL=redis://localhost:6379/0
```

---

## 🧪 Testing

### Test API
```bash
# Health check
curl http://localhost:8000/health

# Submit download
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-key-local" \
  -d '{"url": "https://example.com/file.zip"}'
```

### Test dengan Python Script
```bash
python client.py -u http://localhost:8000 \
  -k test-key-local \
  download "https://speed.hetzner.de/100MB.bin"
```

---

## 🔧 Troubleshooting Local

### Error: `ModuleNotFoundError: No module named 'xxx'`
```bash
# Reinstall requirements
pip install -r requirements.txt --force-reinstall
```

### Error: `Address already in use`
```bash
# Port 8000 dipakai aplikasi lain
# Ganti port di .env:
PORT=8001
```

### Error: Redis connection refused (jika pakai Celery)
```bash
# Pastikan Redis jalan
redis-cli ping

# Kalau tidak, start Redis:
# Windows: redis-server.exe
# Mac/Linux: redis-server
```

### Token Google Drive expired
```bash
# Hapus token lama
del token.json    # Windows
rm token.json     # Mac/Linux

# Jalankan ulang, akan minta auth baru
python main.py
```

---

## 📂 Struktur Folder untuk Local

```
mirror-downloader/
├── .env                    # Config local
├── credentials.json        # Google credentials
├── token.json             # Google token (auto-generate)
├── downloads/             # Folder download
├── main.py               # Server
├── celery_app.py         # Celery config (jika pakai)
├── tasks.py              # Celery tasks
└── ...
```

---

## ✅ Checklist Sebelum Push ke VPS

- [ ] `.env` diisi dengan config production
- [ ] `credentials.json` sudah siap
- [ ] `USE_CELERY=true` (untuk production)
- [ ] Test download berfungsi
- [ ] Bot Telegram merespons (jika pakai)

---

## 🆚 Local vs Production

| Fitur | Local | Production (VPS) |
|-------|-------|------------------|
| `USE_CELERY` | `false` (bisa `true`) | `true` |
| Redis | Local/WSL | Server localhost |
| Domain | localhost | IP/domain |
| SSL | Tidak | Ya (HTTPS) |
| Auto-start | Manual | Systemd |

Selamat coding! 🚀
