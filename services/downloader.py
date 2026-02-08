"""File downloader with progress tracking - IDM-style robust download."""
import os
import asyncio
import aiohttp
import logging
from typing import Optional, Callable
from pathlib import Path
import time
import re
from urllib.parse import urlparse, unquote

from config import get_settings

logger = logging.getLogger(__name__)


class FileDownloader:
    """Async file downloader with IDM-style link detection."""
    
    def __init__(self):
        self.settings = get_settings()
        self.download_dir = Path(self.settings.TEMP_DOWNLOAD_DIR)
        self.download_dir.mkdir(parents=True, exist_ok=True)
    
    def _extract_filename(self, url: str, headers: dict) -> str:
        """Extract filename from URL or headers."""
        filename = None
        
        # Try Content-Disposition header
        content_disp = headers.get('Content-Disposition', '')
        if 'filename=' in content_disp:
            if 'filename*=' in content_disp:
                filename_part = content_disp.split('filename*=')[1].split(';')[0].strip()
                if "''" in filename_part:
                    encoding, _, name = filename_part.partition("''")
                    try:
                        filename = unquote(name)
                    except:
                        filename = name
            else:
                filename = content_disp.split('filename=')[1].strip('"\'').split(';')[0]
        
        # Try URL path
        if not filename:
            parsed = urlparse(unquote(url))
            path = parsed.path
            filename = os.path.basename(path)
            if '?' in filename:
                filename = filename.split('?')[0]
        
        # Fallback
        if not filename or filename in ['', '/', 'download', 'file']:
            filename = f"download_{int(time.time())}"
        
        # Clean
        filename = filename.strip()
        for char in '<>:"/\\|?*':
            filename = filename.replace(char, '_')
        
        return filename
    
    def _human_readable_size(self, size_bytes: int) -> str:
        """Convert bytes to human readable format."""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        
        return f"{size_bytes:.2f} {size_names[i]}"
    
    def _get_browser_headers(self, referer: Optional[str] = None) -> dict:
        """Get headers that mimic Chrome."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        if referer:
            headers['Referer'] = referer
        return headers
    
    async def _resolve_final_url(self, session: aiohttp.ClientSession, url: str, max_redirects: int = 5, _visited: set = None) -> tuple:
        """Follow redirects and resolve final download URL."""
        if _visited is None:
            _visited = set()
        
        if url in _visited:
            raise ValueError(f"Redirect loop detected for URL: {url}")
        _visited.add(url)
        
        if max_redirects <= 0:
            raise ValueError(f"Too many redirects")
        
        headers = self._get_browser_headers()
        
        try:
            logger.debug(f"Fetching: {url}")
            async with session.get(
                url,
                headers=headers,
                allow_redirects=True,  # Let aiohttp handle redirects
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=20, connect=10)
            ) as response:
                
                final_url = str(response.url)
                content_type = response.headers.get('Content-Type', '').lower()
                content_length = response.headers.get('Content-Length')
                
                logger.debug(f"Response: {response.status}, Content-Type: {content_type}, Length: {content_length}")
                
                # Check if it's a direct file
                is_html = 'text/html' in content_type
                has_length = content_length and int(content_length) > 0
                has_file_ext = bool(re.search(r'\.[a-zA-Z0-9]{2,6}$', urlparse(final_url).path))
                
                if not is_html or has_length or has_file_ext:
                    # It's a file
                    final_headers = dict(response.headers)
                    return final_url, final_headers
                
                # It's HTML - try to parse for download link
                logger.info(f"Got HTML, parsing for download link...")
                
                # Read limited content to find links
                text = await response.text()
                
                # Look for download links
                patterns = [
                    # urleecher style links
                    r'(https?://[^"\'<>\s]+/d/[a-zA-Z0-9]{10,}/[^"\'<>\s]+)',
                    # Direct file links
                    r'(https?://[^"\'<>\s]+\.(?:apk|zip|rar|7z|exe|msi|mp4|mkv|mp3|pdf))',
                    # Download links
                    r'href=["\']([^"\']+(?:download|dl)[^"\']*)["\']',
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, text, re.IGNORECASE)
                    for match in matches:
                        match = unquote(match.strip())
                        if match.startswith('http'):
                            # Skip static assets
                            if any(x in match.lower() for x in ['.css', '.js', '.png', '.jpg', '.gif', '.svg', 'favicon']):
                                continue
                            
                            logger.info(f"Found link: {match}")
                            # Recursively resolve
                            return await self._resolve_final_url(session, match, max_redirects - 1, _visited)
                
                # No link found
                raise ValueError(f"Could not find download link in HTML page")
                
        except asyncio.TimeoutError:
            raise ValueError(f"Request timed out for URL: {url}")
        except Exception as e:
            if "Could not find" in str(e) or "timed out" in str(e).lower():
                raise
            logger.error(f"Request failed: {e}")
            raise ValueError(f"Failed to fetch URL: {str(e)}")
    
    async def download(
        self,
        url: str,
        filename: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        timeout: Optional[int] = None
    ) -> dict:
        """Download a file from URL."""
        timeout = timeout or self.settings.DOWNLOAD_TIMEOUT
        
        logger.info(f"Starting download: {url}")
        
        async with aiohttp.ClientSession() as session:
            # Resolve URL with timeout wrapper
            try:
                final_url, headers = await asyncio.wait_for(
                    self._resolve_final_url(session, url),
                    timeout=30
                )
                logger.info(f"Resolved to: {final_url}")
            except asyncio.TimeoutError:
                raise ValueError("URL resolution timed out (30s)")
            
            # Extract filename
            actual_filename = filename or self._extract_filename(final_url, headers)
            file_path = self.download_dir / actual_filename
            
            # Handle duplicates
            counter = 1
            original_name = actual_filename
            while file_path.exists():
                stem = Path(original_name).stem
                suffix = Path(original_name).suffix
                actual_filename = f"{stem}_{counter}{suffix}"
                file_path = self.download_dir / actual_filename
                counter += 1
            
            # Check size
            total_size = headers.get('Content-Length')
            if total_size:
                total_size = int(total_size)
                if total_size > self.settings.MAX_FILE_SIZE:
                    raise ValueError(f"File too large: {self._human_readable_size(total_size)}")
            
            # Download
            logger.info(f"Downloading to: {file_path}")
            
            partial_file = None
            try:
                async with session.get(
                    final_url,
                    headers=self._get_browser_headers(),
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as response:
                    response.raise_for_status()
                    
                    downloaded = 0
                    start_time = time.time()
                    last_update = start_time
                    partial_file = str(file_path)
                    
                    with open(file_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(self.settings.CHUNK_SIZE):
                            f.write(chunk)
                            downloaded += len(chunk)
                            
                            current_time = time.time()
                            elapsed = current_time - start_time
                            
                            if progress_callback and (current_time - last_update >= 1 or downloaded == total_size):
                                speed_kbps = (downloaded / 1024) / elapsed if elapsed > 0 else 0
                                progress_callback(downloaded, total_size, speed_kbps)
                                last_update = current_time
            
            except asyncio.CancelledError:
                # Clean up partial download
                if partial_file and os.path.exists(partial_file):
                    try:
                        file_size = os.path.getsize(partial_file)
                        os.remove(partial_file)
                        logger.info(f"Cleaned up partial download: {partial_file} ({file_size} bytes)")
                    except Exception as e:
                        logger.warning(f"Failed to clean up partial file {partial_file}: {e}")
                raise  # Re-raise the CancelledError
        
        # Result
        end_time = time.time()
        duration = end_time - start_time
        file_size = os.path.getsize(file_path)
        speed_avg = (file_size / 1024) / duration if duration > 0 else 0
        
        logger.info(f"Complete: {actual_filename} ({self._human_readable_size(file_size)})")
        
        return {
            'file_path': str(file_path),
            'filename': actual_filename,
            'file_size': file_size,
            'file_size_human': self._human_readable_size(file_size),
            'duration_seconds': duration,
            'average_speed_kbps': speed_avg,
            'final_url': final_url
        }
    
    async def get_file_info(self, url: str) -> dict:
        """Get file info without downloading."""
        logger.info(f"Getting info: {url}")
        
        async with aiohttp.ClientSession() as session:
            # Add timeout wrapper
            try:
                final_url, headers = await asyncio.wait_for(
                    self._resolve_final_url(session, url),
                    timeout=30
                )
            except asyncio.TimeoutError:
                raise ValueError("URL resolution timed out after 30 seconds")
            
            total_size = headers.get('Content-Length')
            content_type = headers.get('Content-Type', 'unknown')
            filename = self._extract_filename(final_url, headers)
            
            return {
                'filename': filename,
                'size': int(total_size) if total_size else None,
                'size_human': self._human_readable_size(int(total_size)) if total_size else "Unknown",
                'content_type': content_type,
                'final_url': final_url,
            }
    
    def cleanup(self, file_path: str):
        """Delete downloaded file."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup {file_path}: {e}")
