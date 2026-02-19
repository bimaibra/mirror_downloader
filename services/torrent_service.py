"""Service for handling torrent downloads using libtorrent."""
import os
import sys
import time
import asyncio
import logging
import threading
from typing import Optional, Dict, Any, List, Callable
from pathlib import Path

# Try to import libtorrent
try:
    import libtorrent as lt
    LIBTORRENT_AVAILABLE = True
except ImportError:
    LIBTORRENT_AVAILABLE = False

from config import get_settings

logger = logging.getLogger(__name__)

class TorrentService:
    """Service to handle torrent downloads."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TorrentService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.settings = get_settings()
        self.session = None
        self._downloads: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._initialized = True
        
        if LIBTORRENT_AVAILABLE:
            self._init_session()
        else:
            logger.warning("libtorrent not available. Torrent features will be disabled.")
    
    def _init_session(self):
        """Initialize libtorrent session."""
        try:
            self.session = lt.session()
            self.session.listen_on(6881, 6891)
            
            # Apply High Performance Settings
            try:
                pack = lt.default_settings()
                
                # Network settings
                pack['connections_limit'] = 200
                pack['active_downloads'] = 5
                pack['active_seeds'] = 5
                
                # Increase bandwidth limits
                pack['download_rate_limit'] = 0  # 0 = unlimited
                pack['upload_rate_limit'] = 0    # 0 = unlimited
                
                # Cache settings
                pack['cache_size'] = 4096  # 64MB cache (in 16kb blocks)
                pack['cache_expiry'] = 60  # 60s
                
                # Queueing
                pack['active_limit'] = 15
                
                self.session.apply_settings(pack)
                logger.info("Libtorrent high-performance settings applied")
            
            except Exception as e:
                logger.warning(f"Could not apply advanced libtorrent settings (version mismatch?): {e}")

            logger.info("Libtorrent session initialized")
        except Exception as e:
            logger.error(f"Failed to initialize libtorrent session: {e}")
    
    def add_torrent(self, link: str, save_path: str) -> Optional[str]:
        """
        Add a torrent/magnet link to the session.
        
        Args:
            link: Magnet link or path to .torrent file
            save_path: Directory to save downloaded files
            
        Returns:
            info_hash (str) if successful, None otherwise
        """
        if not LIBTORRENT_AVAILABLE or not self.session:
            raise RuntimeError("Libtorrent is not available")
            
        try:
            params = {
                'save_path': save_path,
                'storage_mode': lt.storage_mode_t(1), # storage_mode_sparse
            }
            
            handle = None
            if link.startswith('magnet:'):
                handle = lt.add_magnet_uri(self.session, link, params)
            elif os.path.exists(link):
                info = lt.torrent_info(link)
                handle = self.session.add_torrent({'ti': info, 'save_path': save_path})
            else:
                # treat as URL to torrent file? (Not implemented for now)
                raise ValueError("Invalid torrent link provided")
            
            # Wait for metadata (MOVED TO ASYNC DOWNLOAD)
            # if link.startswith('magnet:'):
            #     logger.info("Downloading metadata...")
            #     timeout = 60 # wait up to 60 seconds for metadata
            #     start = time.time()
            #     while not handle.has_metadata():
            #         time.sleep(1)
            #         if time.time() - start > timeout:
            #             raise TimeoutError("Timeout waiting for torrent metadata")
            
            info_hash = str(handle.info_hash())
            
            with self._lock:
                self._downloads[info_hash] = {
                    'handle': handle,
                    'name': handle.name(),
                    'start_time': time.time(),
                    'save_path': save_path,
                    'is_magnet': link.startswith('magnet:')
                }
            
            logger.info(f"Torrent added: {handle.name()} ({info_hash})")
            return info_hash
            
        except Exception as e:
            logger.error(f"Failed to add torrent: {e}")
            raise
    
    async def download_torrent(self, link: str, save_path: str, progress_callback: Callable = None):
        """
        Async wrapper to download a torrent.
        
        Args:
            link: Magnet link or .torrent file path
            save_path: Destination directory
            progress_callback: Async function(downloaded, total, speed_kbps)
        """
        info_hash = self.add_torrent(link, save_path)
            
        handle = self._downloads[info_hash]['handle']
        is_magnet = self._downloads[info_hash].get('is_magnet', False)

        # Force recheck and resume
        handle.resume()
        handle.set_upload_limit(0) # Unlimited
        handle.set_download_limit(0) # Unlimited

        # Wait for metadata (Async)
        # Note: add_torrent no longer blocks. We must wait for metadata here if it's a magnet link.
        if is_magnet or not handle.has_metadata():
            logger.info("Waiting for metadata (async)...")
            start = time.time()
            timeout = 180 # 3 minutes
            while not handle.has_metadata():
                # Force DHT announce more frequently while waiting
                if int(time.time()) % 5 == 0:
                    handle.force_reannounce()
                    
                await asyncio.sleep(1) # Yield to event loop
                if time.time() - start > timeout:
                     raise TimeoutError("Timeout waiting for torrent metadata")
            
            # Update name after metadata received
            with self._lock:
                # Re-fetch handle if needed or just use current
                self._downloads[info_hash]['name'] = handle.name()
            logger.info(f"Metadata received for: {handle.name()}")
        
        # Monitor loop
        consecutive_zero_speed = 0
        
        while True:
            try:
                s = handle.status()
                
                # Check for critical errors
                if s.error:
                     logger.error(f"Torrent error: {s.error}")
                     raise RuntimeError(f"Torrent '{s.name}' error: {s.error}")

                # Calculate metrics
                total_size = s.total_wanted
                downloaded = s.total_done
                speed = s.download_rate / 1024 # KB/s
                
                # Attempt to boost speeds if stuck
                if speed < 1.0 and s.total_done < s.total_wanted:
                     consecutive_zero_speed += 1
                     if consecutive_zero_speed > 30 and consecutive_zero_speed % 10 == 0:
                          logger.warning(f"Torrent '{s.name}' stuck at 0 speed. Forcing reannounce...")
                          handle.force_reannounce()
                          handle.force_dht_announce()
                else:
                     consecutive_zero_speed = 0
                
                if speed > 0 or downloaded > 0:
                    logger.debug(f"Torrent '{handle.name()}' progress: {s.progress*100:.1f}%, {downloaded}/{total_size} bytes, {speed:.2f} KB/s, peers: {s.num_peers}")
                
                if progress_callback:
                    if asyncio.iscoroutinefunction(progress_callback):
                        await progress_callback(downloaded, total_size, speed)
                    else:
                        progress_callback(downloaded, total_size, speed)
                
                if s.is_seeding:
                    logger.info(f"Torrent {s.name} completed seeding.")
                    break
                    
                # Allow to finish if active
                if s.state == lt.torrent_status.seeding or s.state == lt.torrent_status.finished:
                    logger.info(f"Torrent {s.name} finished downloading.")
                    break
                    
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error in torrent loop: {e}")
                # Don't break immediately, maybe temporary
                await asyncio.sleep(5)
        
        
        # Return result info
        return {
            'info_hash': info_hash,
            'filename': handle.name(),
            'file_path': os.path.join(save_path, handle.name()),
            'file_size': s.total_done,
            'file_size_human': self._format_size(s.total_done)
        }
    
    def remove_torrent(self, info_hash: str, delete_files: bool = False):
        """Remove a torrent from session."""
        with self._lock:
            if info_hash in self._downloads:
                handle = self._downloads[info_hash]['handle']
                self.session.remove_torrent(handle, delete_files)
                del self._downloads[info_hash]
                logger.info(f"Torrent removed: {info_hash}")

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes to human readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"

def get_torrent_service() -> TorrentService:
    return TorrentService()
