"""Configuration helper for Google Colab environment."""
import os
import shutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def setup_colab_environment():
    """
    Setup the environment for running in Google Colab.
    
    This function:
    1. Checks if we are running in Colab
    2. Copies config files from Drive if needed
    3. Sets up temporary directories
    """
    # Check if running in Colab
    try:
        import google.colab
        is_colab = True
    except ImportError:
        is_colab = False
        
    if not is_colab and os.environ.get('IS_COLAB') != 'true':
        return
        
    logger.info("🔧 Configuring for Google Colab environment...")
    
    # 1. Define paths
    drive_mount_path = Path('/content/drive')
    project_drive_path = drive_mount_path / 'MyDrive' / 'mirror_downloader'
    local_workspace = Path('/content/mirror_downloader')
    
    # 2. Sync Configs (if we are running in a fresh workspace)
    # If the user is running directly from Drive, this might not be needed,
    # but if they copied files to /content/ execution is faster.
    
    # 3. Create temp download dir (faster IO on local disk than Drive)
    download_dir = Path('/content/downloads')
    download_dir.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP_DOWNLOAD_DIR'] = str(download_dir)
    
    logger.info(f"   Download directory set to: {download_dir}")
    
    # 4. Check for Ngrok token
    if not os.environ.get('NGROK_AUTHTOKEN'):
        logger.warning("⚠️ NGROK_AUTHTOKEN not found in environment variables.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    setup_colab_environment()
