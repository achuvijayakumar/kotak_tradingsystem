"""Authentication service for handling login requests."""
import logging
from core.config import save_token
from core.xts_client import XTSClientWrapper
from utils.telegram_notifier import send_telegram



class AuthService:
    """Handles authentication and login requests."""
    
    def __init__(self, config, redis_client, uid):
        """Initialize authentication service.
        
        Args:
            config: Configuration dictionary with API credentials
            redis_client: Redis client instance
            uid: User ID
        """
        self.config = config
        self.redis_client = redis_client
        self.uid = uid
        self.xt_wrapper = None
    
    def initialize_client(self, token):
        """Initialize XTS client with token.
        
        Args:
            token: Authentication token
        """
        self.xt_wrapper = XTSClientWrapper(
            self.config["INTERACTIVE_API_KEY"],
            self.config["INTERACTIVE_API_SECRET"],
            self.config["INTERACTIVE_XTS_API_BASE_URL"],
            token
        )
    
    def get_xt_client(self):
        """Get the XTS client instance.
        
        Returns:
            XTSConnect: The XTS client
        """
        if not self.xt_wrapper:
            raise RuntimeError("XTS client not initialized. Call initialize_client first.")
        return self.xt_wrapper.get_client()
    
    def process_login_if_requested(self):
        """Check Redis for login request and handle it if present."""
        login_flag = self.redis_client.hget(self.uid, "LOGIN")
        
        if login_flag != "requested":
            return
        
        logging.info("[LOGIN] Processing login request...")
        self.redis_client.hset(self.uid, mapping={"LOGIN": "processing", "LOGIN_STATUS": "PROCESSING"})
        
        try:
            # Create temporary client for login
            temp_wrapper = XTSClientWrapper(
                self.config["INTERACTIVE_API_KEY"],
                self.config["INTERACTIVE_API_SECRET"],
                self.config["INTERACTIVE_XTS_API_BASE_URL"]
            )
            
            # Perform login
            resp = temp_wrapper.interactive_login()
            logging.info(f"[LOGIN] Response: {resp}")
            
            if resp and len(resp) > 25:  # Simple validation for token
                # Save token
                save_token(self.uid, resp)
                
                # Update current session
                if self.xt_wrapper:
                    self.xt_wrapper.set_token(resp)
                else:
                    self.initialize_client(resp)
                
                self.redis_client.hset(self.uid, mapping={
                    "LOGIN_STATUS": "SUCCESS",
                    "LOGIN_MESSAGE": "Token generated successfully"
                })
                send_telegram(
                    f"🔐 <b>Login Successful</b>\n"
                    f"UID: {self.uid}\n"
                    f"Token refreshed and active."
                )
            else:
                self.redis_client.hset(self.uid, mapping={
                    "LOGIN_STATUS": "FAILED",
                    "LOGIN_MESSAGE": "Invalid token received"
                })
        
        except Exception as e:
            logging.error(f"[LOGIN] Error: {e}")
            self.redis_client.hset(self.uid, mapping={
                "LOGIN_STATUS": "FAILED",
                "LOGIN_MESSAGE": str(e)
            })
            send_telegram(
                f"❌ <b>Login Failed</b>\n"
                f"UID: {self.uid}\n"
                f"Reason: {str(e)}"
            )
        finally:
            self.redis_client.hset(self.uid, mapping={"LOGIN": "fetched"})

    def process_session_check_if_requested(self):
        """Check Redis for session health check request and handle it."""
        check_flag = self.redis_client.hget(self.uid, "CHECK_SESSION")
        
        if check_flag != "requested":
            return
        
        logging.info("[SESSION_CHECK] Processing health check...")
        self.redis_client.hset(self.uid, mapping={
            "CHECK_SESSION": "processing",
            "SESSION_STATUS": "PROCESSING"
        })
        
        try:
            xt = self.get_xt_client()
            resp = xt.get_profile()
            
            if resp and resp.get('type') == 'success':
                self.redis_client.hset(self.uid, mapping={
                    "SESSION_STATUS": "ACTIVE",
                    "SESSION_MESSAGE": "XTS Session is ACTIVE"
                })
                logging.info("[SESSION_CHECK] Session is active.")
            else:
                error_msg = resp.get('description', 'Unknown error') if resp else 'No response from XTS'
                self.redis_client.hset(self.uid, mapping={
                    "SESSION_STATUS": "EXPIRED",
                    "SESSION_MESSAGE": error_msg
                })
                logging.warning(f"[SESSION_CHECK] Session expired/invalid: {error_msg}")
        
        except Exception as e:
            logging.error(f"[SESSION_CHECK] Error: {e}")
            self.redis_client.hset(self.uid, mapping={
                "SESSION_STATUS": "EXPIRED",
                "SESSION_MESSAGE": str(e)
            })
        finally:
            self.redis_client.hset(self.uid, mapping={"CHECK_SESSION": "fetched"})
