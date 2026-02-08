"""Google Drive service for uploading files."""
import os
import logging
from typing import Optional, Callable
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_settings

logger = logging.getLogger(__name__)

# Google Drive API scopes
SCOPES = ['https://www.googleapis.com/auth/drive']


class GoogleDriveService:
    """Service for interacting with Google Drive API."""
    
    def __init__(self):
        self.settings = get_settings()
        self.creds: Optional[Credentials] = None
        self.service = None
        self._authenticate()
    
    def _authenticate(self):
        """Authenticate with Google Drive API."""
        token_file = self.settings.GOOGLE_TOKEN_FILE
        creds_file = self.settings.GOOGLE_CREDENTIALS_FILE
        
        # Load existing token if available
        if os.path.exists(token_file):
            self.creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        
        # Refresh or create new credentials
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                logger.info("Refreshing Google Drive credentials...")
                self.creds.refresh(Request())
            else:
                if not os.path.exists(creds_file):
                    raise FileNotFoundError(
                        f"Google credentials file not found: {creds_file}\n"
                        "Please download credentials.json from Google Cloud Console."
                    )
                logger.info("Starting OAuth flow for Google Drive...")
                flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
                self.creds = flow.run_local_server(port=0)
            
            # Save token for future runs
            with open(token_file, 'w') as token:
                token.write(self.creds.to_json())
                logger.info(f"Token saved to {token_file}")
        
        self.service = build('drive', 'v3', credentials=self.creds, cache_discovery=False)
        logger.info("Google Drive service initialized successfully")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    def upload_file(
        self, 
        file_path: str, 
        filename: str,
        folder_id: Optional[str] = None,
        description: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> dict:
        """
        Upload a file to Google Drive.
        
        Args:
            file_path: Path to the local file
            filename: Name to use for the file in Google Drive
            folder_id: Optional folder ID to upload to
            description: Optional file description
            progress_callback: Callback function(uploaded_bytes, total_bytes)
            
        Returns:
            dict with file info including 'id' and 'webViewLink'
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        file_size = os.path.getsize(file_path)
        file_metadata = {
            'name': filename,
            'description': description or f"Uploaded by Mirror Download Server"
        }
        
        # Add to specific folder if provided
        if folder_id:
            file_metadata['parents'] = [folder_id]
        elif self.settings.GDRIVE_FOLDER_ID:
            file_metadata['parents'] = [self.settings.GDRIVE_FOLDER_ID]
        
        # Determine MIME type
        mime_type = self._get_mime_type(filename)
        
        media = MediaFileUpload(
            file_path,
            mimetype=mime_type,
            resumable=True  # Important for large files
        )
        
        logger.info(f"Uploading {filename} to Google Drive...")
        
        try:
            # Create the request
            request = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, mimeType, size, webViewLink, webContentLink'
            )
            
            # Execute with progress tracking if callback provided
            if progress_callback:
                response = None
                last_progress = 0
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        progress = int(status.progress() * 100)
                        if progress > last_progress:
                            progress_callback(status.resumable_progress, file_size)
                            last_progress = progress
            else:
                response = request.execute()
            
            file = response
            
            # Make file publicly readable (anyone with link)
            self._make_file_public(file['id'])
            
            logger.info(f"File uploaded successfully: {file.get('webViewLink')}")
            return file
            
        except HttpError as e:
            logger.error(f"Google Drive API error: {e}")
            raise
        finally:
            media._fd.close()  # Close file handle
    
    def _make_file_public(self, file_id: str):
        """Make a file publicly readable (anyone with link)."""
        permission = {
            'type': 'anyone',
            'role': 'reader'
        }
        self.service.permissions().create(
            fileId=file_id,
            body=permission
        ).execute()
        logger.debug(f"Made file {file_id} publicly readable")
    
    def _get_mime_type(self, filename: str) -> str:
        """Get MIME type based on file extension."""
        ext = os.path.splitext(filename)[1].lower()
        mime_types = {
            '.mp4': 'video/mp4',
            '.mkv': 'video/x-matroska',
            '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime',
            '.webm': 'video/webm',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.flac': 'audio/flac',
            '.zip': 'application/zip',
            '.rar': 'application/x-rar-compressed',
            '.7z': 'application/x-7z-compressed',
            '.tar': 'application/x-tar',
            '.gz': 'application/gzip',
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.txt': 'text/plain',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.iso': 'application/x-iso9660-image'
        }
        return mime_types.get(ext, 'application/octet-stream')
    
    def create_folder(self, folder_name: str, parent_id: Optional[str] = None) -> str:
        """
        Create a folder in Google Drive.
        
        Returns:
            Folder ID
        """
        metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            metadata['parents'] = [parent_id]
        elif self.settings.GDRIVE_FOLDER_ID:
            metadata['parents'] = [self.settings.GDRIVE_FOLDER_ID]
        
        folder = self.service.files().create(body=metadata, fields='id').execute()
        return folder.get('id')
    
    def get_or_create_folder(self, folder_name: str, parent_id: Optional[str] = None) -> str:
        """
        Get existing folder or create new one if not exists.
        
        Args:
            folder_name: Name of the folder
            parent_id: Optional parent folder ID
            
        Returns:
            Folder ID
        """
        # Build query to find folder
        query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        
        # Search for existing folder
        results = self.service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        files = results.get('files', [])
        if files:
            # Folder exists, return ID
            folder_id = files[0]['id']
            logger.info(f"Found existing folder '{folder_name}': {folder_id}")
            return folder_id
        
        # Folder doesn't exist, create it
        folder_id = self.create_folder(folder_name, parent_id)
        logger.info(f"Created new folder '{folder_name}': {folder_id}")
        return folder_id
    
    def get_or_create_folder(self, folder_name: str, parent_id: Optional[str] = None) -> str:
        """
        Get existing folder or create new one if not exists.
        
        Args:
            folder_name: Name of the folder
            parent_id: Optional parent folder ID
            
        Returns:
            Folder ID
        """
        # Build query to find folder
        query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        
        # Search for existing folder
        results = self.service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        files = results.get('files', [])
        if files:
            # Folder exists, return ID
            folder_id = files[0]['id']
            logger.info(f"Found existing folder '{folder_name}': {folder_id}")
            return folder_id
        
        # Folder doesn't exist, create it
        folder_id = self.create_folder(folder_name, parent_id)
        logger.info(f"Created new folder '{folder_name}': {folder_id}")
        return folder_id
    
    def list_files(self, query: Optional[str] = None, page_size: int = 10) -> list:
        """List files in Google Drive."""
        results = self.service.files().list(
            q=query,
            pageSize=page_size,
            fields="files(id, name, mimeType, size, createdTime, webViewLink)"
        ).execute()
        return results.get('files', [])
    
    def delete_file(self, file_id: str):
        """Delete a file from Google Drive."""
        self.service.files().delete(fileId=file_id).execute()
        logger.info(f"Deleted file {file_id} from Google Drive")


# Singleton instance
gdrive_service = None

def get_gdrive_service() -> GoogleDriveService:
    """Get or create Google Drive service singleton."""
    global gdrive_service
    if gdrive_service is None:
        gdrive_service = GoogleDriveService()
    return gdrive_service
