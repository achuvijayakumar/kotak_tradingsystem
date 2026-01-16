"""XTS client wrapper for easier testing and mocking."""
from XTSConnect import XTSConnect
import logging


class XTSClientWrapper:
    """Wrapper around XTSConnect for consistent interface."""
    
    def __init__(self, api_key, api_secret, base_url, token=None):
        """Initialize XTS client.
        
        Args:
            api_key: Interactive API key
            api_secret: Interactive API secret
            base_url: XTS API base URL
            token: Optional authentication token
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        
        self.xt = XTSConnect(
            api_key,
            api_secret,
            "WEBAPI",
            base_url
        )
        
        if token:
            self.set_token(token)
        
        logging.info("[SUCCESS] XTSConnect initialized successfully.")
    
    def set_token(self, token):
        """Set authentication token.
        
        Args:
            token: Authentication token
        """
        self.xt._set_common_variables(token, isInvestorClient=True)
    
    def interactive_login(self):
        """Perform interactive login.
        
        Returns:
            str: Authentication token
        """
        return self.xt.interactive_login()
    
    def get_client(self):
        """Get the underlying XTSConnect client.
        
        Returns:
            XTSConnect: The XTS client instance
        """
        return self.xt
