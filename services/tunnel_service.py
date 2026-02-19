"""Service to manage ngrok tunnel and expose public URL."""
import os
import logging
from typing import Optional
import asyncio

logger = logging.getLogger(__name__)

class TunnelService:
    """Service to manage public tunnel."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TunnelService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.public_url = None
        self._initialized = True
        
    def start_tunnel(self, port: int = 8000) -> Optional[str]:
        """
        Start ngrok tunnel.
        
        Returns:
            public_url (str) or None if failed
        """
        if self.public_url:
            return self.public_url
            
        try:
            from pyngrok import ngrok, conf
            
            # Check for auth token in environment
            auth_token = os.environ.get("NGROK_AUTHTOKEN")
            if auth_token:
                ngrok.set_auth_token(auth_token)
            
            # Start tunnel
            self.public_url = ngrok.connect(port).public_url
            logger.info(f"🚀 Ngrok Tunnel started: {self.public_url}")
            return self.public_url
            
        except ImportError:
            logger.warning("pyngrok not installed. Tunneling disabled.")
            return None
        except Exception as e:
            logger.error(f"Failed to start ngrok tunnel: {e}")
            return None
            
    def get_public_url(self) -> Optional[str]:
        return self.public_url

def get_tunnel_service() -> TunnelService:
    return TunnelService()
