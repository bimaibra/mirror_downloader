"""CLI Client for Mirror Download Server."""
import sys
import time
import argparse
import requests
from typing import Optional


class MirrorClient:
    """CLI client for interacting with the Mirror Download Server."""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {}
        if api_key:
            self.headers['X-API-Key'] = api_key
    
    def submit_download(
        self, 
        url: str, 
        filename: Optional[str] = None,
        folder_id: Optional[str] = None,
        telegram_chat_id: Optional[str] = None
    ) -> dict:
        """Submit a download request."""
        payload = {"url": url}
        if filename:
            payload["filename"] = filename
        if folder_id:
            payload["folder_id"] = folder_id
        if telegram_chat_id:
            payload["telegram_chat_id"] = telegram_chat_id
        
        response = requests.post(
            f"{self.base_url}/download",
            json=payload,
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def get_status(self, task_id: str) -> dict:
        """Get task status."""
        response = requests.get(
            f"{self.base_url}/status/{task_id}",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def preview(self, url: str) -> dict:
        """Preview file info."""
        payload = {"url": url}
        response = requests.post(
            f"{self.base_url}/preview",
            json=payload,
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def list_tasks(self, status: Optional[str] = None, limit: int = 20) -> dict:
        """List tasks."""
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        response = requests.get(
            f"{self.base_url}/tasks",
            params=params,
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def wait_for_completion(self, task_id: str, poll_interval: int = 5):
        """Wait for a task to complete and show progress."""
        print(f"⏳ Waiting for task {task_id} to complete...")
        print("-" * 50)
        
        last_message = ""
        
        while True:
            status = self.get_status(task_id)
            current_status = status.get('status', 'unknown')
            message = status.get('message', 'No message')
            progress = status.get('progress', 0)
            
            # Only print when message changes
            if message != last_message:
                if current_status == 'downloading':
                    bar_length = 30
                    filled = int(bar_length * progress / 100)
                    bar = '█' * filled + '░' * (bar_length - filled)
                    print(f"\r[{bar}] {progress:.1f}% - {message}", end='', flush=True)
                else:
                    print(f"\n[{current_status.upper()}] {message}")
                last_message = message
            
            if current_status in ['completed', 'failed', 'cancelled']:
                print()  # New line after progress bar
                break
            
            time.sleep(poll_interval)
        
        # Print final result
        print("-" * 50)
        if current_status == 'completed':
            print("✅ Download Complete!")
            print(f"📄 File: {status.get('filename')}")
            print(f"📦 Size: {status.get('file_size_human')}")
            print(f"🔗 Google Drive: {status.get('gdrive_link')}")
            return True
        else:
            print(f"❌ Download {current_status}")
            if status.get('error_message'):
                print(f"⚠️  Error: {status['error_message']}")
            return False


def main():
    parser = argparse.ArgumentParser(
        description='Mirror Download Server Client',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Submit a download
  python client.py -u http://server:8000 download "https://example.com/file.zip"
  
  # Download with custom filename
  python client.py -u http://server:8000 download "https://example.com/file.zip" -n myfile.zip
  
  # Check status
  python client.py -u http://server:8000 status task-abc123
  
  # Preview file info
  python client.py -u http://server:8000 preview "https://example.com/file.zip"
  
  # List recent tasks
  python client.py -u http://server:8000 list
        """
    )
    
    parser.add_argument('-u', '--url', required=True, help='Server base URL')
    parser.add_argument('-k', '--api-key', help='API key for authentication')
    parser.add_argument('-w', '--wait', action='store_true', help='Wait for completion')
    
    subparsers = parser.add_subparsers(dest='command', help='Command')
    
    # Download command
    download_parser = subparsers.add_parser('download', help='Submit download')
    download_parser.add_argument('link', help='URL to download')
    download_parser.add_argument('-n', '--filename', help='Custom filename')
    download_parser.add_argument('-f', '--folder', help='Google Drive folder ID')
    download_parser.add_argument('-t', '--telegram', help='Telegram chat ID')
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Check task status')
    status_parser.add_argument('task_id', help='Task ID')
    
    # Preview command
    preview_parser = subparsers.add_parser('preview', help='Preview file info')
    preview_parser.add_argument('link', help='URL to preview')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List tasks')
    list_parser.add_argument('-s', '--status', help='Filter by status')
    list_parser.add_argument('-l', '--limit', type=int, default=20, help='Limit results')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Create client
    client = MirrorClient(args.url, args.api_key)
    
    try:
        if args.command == 'download':
            result = client.submit_download(
                url=args.link,
                filename=args.filename,
                folder_id=args.folder,
                telegram_chat_id=args.telegram
            )
            print(f"✅ Download queued!")
            print(f"🆔 Task ID: {result['task_id']}")
            print(f"📊 Status: {result['status']}")
            print(f"💬 {result['message']}")
            
            if args.wait:
                success = client.wait_for_completion(result['task_id'])
                sys.exit(0 if success else 1)
        
        elif args.command == 'status':
            status = client.get_status(args.task_id)
            print(f"🆔 Task ID: {status['task_id']}")
            print(f"📊 Status: {status['status']}")
            print(f"📄 File: {status.get('filename', 'N/A')}")
            print(f"📦 Size: {status.get('file_size_human', 'N/A')}")
            print(f"💬 {status['message']}")
            if status.get('progress'):
                print(f"📈 Progress: {status['progress']:.1f}%")
            if status.get('gdrive_link'):
                print(f"🔗 Google Drive: {status['gdrive_link']}")
            if status.get('error_message'):
                print(f"⚠️  Error: {status['error_message']}")
        
        elif args.command == 'preview':
            info = client.preview(args.link)
            print(f"📄 Filename: {info['filename']}")
            print(f"📦 Size: {info['size_human']}")
            print(f"📋 Content Type: {info['content_type']}")
            print(f"⏯️  Resume Support: {info['supports_resume']}")
        
        elif args.command == 'list':
            result = client.list_tasks(status=args.status, limit=args.limit)
            print(f"📋 Tasks ({result['count']} total):")
            print("-" * 50)
            for task in result['tasks']:
                status_emoji = {
                    'pending': '⏳',
                    'downloading': '⬇️',
                    'uploading': '⬆️',
                    'completed': '✅',
                    'failed': '❌',
                    'cancelled': '🚫'
                }.get(task['status'], '❓')
                
                print(f"{status_emoji} {task['task_id']}")
                print(f"   Status: {task['status']}")
                print(f"   File: {task.get('filename', 'N/A')}")
                print(f"   Created: {task['created_at']}")
                if task.get('gdrive_link'):
                    print(f"   Link: {task['gdrive_link']}")
                print()
    
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n👋 Interrupted by user")
        sys.exit(0)


if __name__ == '__main__':
    main()
