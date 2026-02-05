#!/usr/bin/env python3
"""
Cleanup script for Mirror Download Server.

Deletes old downloaded files to free up disk space.
Can be run manually or as a cron job.

Usage:
    python cleanup.py                    # Delete files older than 24 hours
    python cleanup.py --hours 2          # Delete files older than 2 hours
    python cleanup.py --all              # Delete ALL downloaded files (use with caution!)
    python cleanup.py --dry-run          # Show what would be deleted (don't actually delete)
    python cleanup.py --size-limit 80    # Delete files if disk usage > 80%
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default downloads directory
DEFAULT_DOWNLOAD_DIR = "./downloads"


def get_disk_usage(path: str) -> float:
    """Get disk usage percentage for the given path."""
    try:
        stat = os.statvfs(path)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bfree * stat.f_frsize
        used = total - free
        return (used / total) * 100 if total > 0 else 0
    except Exception as e:
        logger.error(f"Failed to get disk usage: {e}")
        return 0


def cleanup_downloads(
    download_dir: str = DEFAULT_DOWNLOAD_DIR,
    hours: int = 24,
    delete_all: bool = False,
    dry_run: bool = False,
    size_limit: int = None
) -> dict:
    """
    Clean up downloaded files.
    
    Args:
        download_dir: Directory containing downloads
        hours: Delete files older than this many hours
        delete_all: If True, delete ALL files regardless of age
        dry_run: If True, only show what would be deleted
        size_limit: If disk usage exceeds this %, delete old files
    
    Returns:
        dict with stats about cleanup
    """
    download_path = Path(download_dir)
    
    if not download_path.exists():
        logger.warning(f"Download directory does not exist: {download_dir}")
        return {'deleted': 0, 'freed_bytes': 0, 'errors': 0}
    
    # Check disk usage if size_limit specified
    if size_limit:
        disk_usage = get_disk_usage(download_dir)
        logger.info(f"Current disk usage: {disk_usage:.1f}%")
        if disk_usage < size_limit:
            logger.info(f"Disk usage ({disk_usage:.1f}%) below limit ({size_limit}%). No cleanup needed.")
            return {'deleted': 0, 'freed_bytes': 0, 'errors': 0, 'disk_usage': disk_usage}
        else:
            logger.warning(f"Disk usage ({disk_usage:.1f}%) exceeds limit ({size_limit}%). Cleaning up...")
    
    # Calculate cutoff time
    if delete_all:
        cutoff_time = datetime.now() + timedelta(days=1)  # Future time = all files
        logger.info(f"{'[DRY RUN] ' if dry_run else ''}Deleting ALL files in {download_dir}")
    else:
        cutoff_time = datetime.now() - timedelta(hours=hours)
        logger.info(f"{'[DRY RUN] ' if dry_run else ''}Deleting files older than {hours} hours ({cutoff_time.strftime('%Y-%m-%d %H:%M:%S')})")
    
    # Find and delete files
    deleted_count = 0
    freed_bytes = 0
    error_count = 0
    
    for file_path in download_path.iterdir():
        if not file_path.is_file():
            continue
        
        try:
            # Get file modification time
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            file_size = file_path.stat().st_size
            
            if mtime < cutoff_time:
                if dry_run:
                    logger.info(f"[DRY RUN] Would delete: {file_path.name} ({_human_readable_size(file_size)}, modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')})")
                else:
                    file_path.unlink()
                    logger.info(f"Deleted: {file_path.name} ({_human_readable_size(file_size)})")
                
                deleted_count += 1
                freed_bytes += file_size
                
        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
            error_count += 1
    
    # Summary
    action = "Would delete" if dry_run else "Deleted"
    logger.info(f"\n{'='*50}")
    logger.info(f"Cleanup Summary:")
    logger.info(f"  {action}: {deleted_count} files")
    logger.info(f"  Freed space: {_human_readable_size(freed_bytes)}")
    if error_count > 0:
        logger.info(f"  Errors: {error_count}")
    
    if size_limit:
        disk_usage_after = get_disk_usage(download_dir)
        logger.info(f"  Disk usage: {disk_usage:.1f}% -> {disk_usage_after:.1f}%")
    
    return {
        'deleted': deleted_count,
        'freed_bytes': freed_bytes,
        'errors': error_count,
        'disk_usage_before': disk_usage if size_limit else None,
        'disk_usage_after': get_disk_usage(download_dir) if size_limit else None
    }


def _human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human readable format."""
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.2f} {size_names[i]}"


def main():
    parser = argparse.ArgumentParser(
        description='Cleanup downloaded files from Mirror Download Server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Delete files older than 24 hours (default)
  python cleanup.py

  # Delete files older than 2 hours
  python cleanup.py --hours 2

  # Preview what would be deleted (don't actually delete)
  python cleanup.py --dry-run

  # Delete ALL downloaded files (use with caution!)
  python cleanup.py --all

  # Delete files if disk usage exceeds 80%
  python cleanup.py --size-limit 80

  # Specify custom download directory
  python cleanup.py --dir /path/to/downloads --hours 12
        """
    )
    
    parser.add_argument('--dir', default=DEFAULT_DOWNLOAD_DIR,
                        help=f'Download directory (default: {DEFAULT_DOWNLOAD_DIR})')
    parser.add_argument('--hours', type=int, default=24,
                        help='Delete files older than N hours (default: 24)')
    parser.add_argument('--all', action='store_true',
                        help='Delete ALL files regardless of age (DANGEROUS!)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be deleted without actually deleting')
    parser.add_argument('--size-limit', type=int, metavar='PERCENT',
                        help='Delete files if disk usage exceeds PERCENT%')
    parser.add_argument('--cron', action='store_true',
                        help='Run in cron mode (minimal output, only errors)')
    
    args = parser.parse_args()
    
    # Reduce logging in cron mode
    if args.cron:
        logging.getLogger().setLevel(logging.WARNING)
    
    # Safety check for --all
    if args.all and not args.dry_run:
        print("WARNING: You are about to delete ALL files in the downloads directory!")
        print(f"Directory: {args.dir}")
        response = input("Are you sure? Type 'yes' to continue: ")
        if response.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)
    
    # Run cleanup
    result = cleanup_downloads(
        download_dir=args.dir,
        hours=args.hours,
        delete_all=args.all,
        dry_run=args.dry_run,
        size_limit=args.size_limit
    )
    
    # Exit with error code if there were errors
    sys.exit(0 if result['errors'] == 0 else 1)


if __name__ == '__main__':
    main()
