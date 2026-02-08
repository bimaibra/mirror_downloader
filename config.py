"""Configuration module for the mirroring download server - Admin Only."""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Server Configuration
    APP_NAME: str = "Mirror Download Server"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Security
    API_KEY: str = "your-secret-api-key-change-this"
    
    # Google Drive Configuration
    GOOGLE_CREDENTIALS_FILE: str = "credentials.json"
    GOOGLE_TOKEN_FILE: str = "token.json"
    GDRIVE_FOLDER_ID: str = ""  # Default folder ID for admin downloads
    
    # Telegram Bot Configuration (optional)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_ADMIN_CHAT_ID: str = ""  # Only this user can use the bot
    
    # Celery / Redis Configuration (for background tasks)
    REDIS_URL: str = "redis://localhost:6379/0"
    USE_CELERY: bool = False  # Set to True to enable Celery (requires Redis)
    
    # Download Settings
    MAX_FILE_SIZE: int = 5 * 1024 * 1024 * 1024  # 5 GB
    CHUNK_SIZE: int = 8192  # 8 KB chunks for streaming
    DOWNLOAD_TIMEOUT: int = 3600  # 1 hour
    TEMP_DOWNLOAD_DIR: str = "./downloads"
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 10

    # Code/User Management
    CODE_EXPIRY_HOURS: int = 24
    USER_ACCESS_DURATION_HOURS: int = 168
    MAX_CODES_PER_ADMIN: int = 10
    
    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
