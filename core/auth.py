"""
NeoAuthService - Kotak Neo API authentication service.

Purpose:
- Wraps Kotak Neo API authentication
- Exposes a ready-to-use client object
- Writes status to Redis (NEO_LOGIN_STATUS, NEO_LOGIN_MESSAGE)
- Treats client as disposable (no session timers, no background refresh)

Usage:
    from core.auth import NeoAuthService
    
    auth = NeoAuthService(config, redis_client, uid)
    auth.login()      # Step 1: TOTP login
    auth.validate()   # Step 2: MPIN validation
    
    if auth.is_ready():
        client = auth.client
"""

from typing import Optional, Dict, Any
import logging
from utils.telegram_notifier import send_telegram

logger = logging.getLogger(__name__)

# Redis key constants
KEY_LOGIN_STATUS = "NEO_LOGIN_STATUS"
KEY_LOGIN_MESSAGE = "NEO_LOGIN_MESSAGE"
KEY_LOGIN_REQUEST = "NEO_LOGIN_REQUEST"

# Status values
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"
STATUS_PENDING = "PENDING"
STATUS_PROCESSING = "PROCESSING"


class NeoAuthService:
    """
    Authentication service for Kotak Neo API.
    
    Design principles:
    - Client is disposable: no session tracking, no auto-refresh
    - If auth fails later, caller must re-login
    - Only touches NEO_LOGIN_* Redis keys
    """
    
    def __init__(self, config: Dict[str, str], redis_client, uid: str):
        """
        Initialize auth service.
        
        Args:
            config: Dict with Neo API credentials:
                - consumer_key: Kotak Neo API consumer key
                - mobile_number: Registered mobile with country code
                - ucc: Unique Client Code
                - totp: Time-based OTP from authenticator
                - mpin: MPIN for account
            redis_client: Redis client instance
            uid: User ID for Redis key namespacing
        """
        self.config = config
        self.redis = redis_client
        self.uid = uid
        self._client = None
        self._logged_in = False
        self._validated = False
    
    @property
    def client(self) -> Optional[Any]:
        """Get the authenticated NeoAPI client."""
        if self._validated and self._client:
            return self._client
        return None
    
    def is_ready(self) -> bool:
        """Check if client is ready for trading."""
        return self._validated and self._client is not None
    
    def _set_status(self, status: str, message: str) -> None:
        """Write status to Redis."""
        self.redis.hset(self.uid, mapping={
            KEY_LOGIN_STATUS: status,
            KEY_LOGIN_MESSAGE: message
        })
        logger.info(f"[{self.uid}] {KEY_LOGIN_STATUS}={status}: {message}")
    
    def login(self) -> bool:
        """
        Step 1: Perform TOTP login.
        
        Calls totp_login(mobile_number, ucc, totp) to get view token and session ID.
        
        Returns:
            True if login successful, False otherwise
        """
        # XTS → KOTAK NEO REPLACEMENT: Using neo_api_client SDK instead of XTS
        try:
            from neo_api_client import NeoAPI
        except ImportError as e:
            self._set_status(STATUS_FAILED, f"neo_api_client not installed: {e}")
            return False
        
        try:
            # SAFETY CHECK: Validate required config fields
            required_fields = ['consumer_key', 'mobile_number', 'ucc', 'totp', 'mpin']
            missing = [f for f in required_fields if not self.config.get(f)]
            if missing:
                error_msg = f"Missing required config fields: {missing}"
                logger.error(f"[{self.uid}] {error_msg}")
                self._set_status(STATUS_FAILED, error_msg)
                return False
            
            self._set_status(STATUS_PROCESSING, "Starting TOTP login...")
            
            # XTS → KOTAK NEO REPLACEMENT: Initialize NeoAPI client
            logger.info(f"[{self.uid}] Initializing NeoAPI client...")
            self._client = NeoAPI(
                environment='prod',
                access_token=None,
                neo_fin_key=None,
                consumer_key=self.config['consumer_key']
            )
            
            # XTS → KOTAK NEO REPLACEMENT: Call totp_login instead of XTS login
            logger.info(f"[{self.uid}] Calling totp_login...")
            response = self._client.totp_login(
                mobile_number=self.config['mobile_number'],
                ucc=self.config['ucc'],
                totp=self.config['totp']
            )
            
            # DEFENSIVE: Log full response
            logger.info(f"[{self.uid}] TOTP response: {response}")
            
            # SAFETY CHECK: Handle None response
            if response is None:
                logger.error(f"[{self.uid}] TOTP login returned None")
                self._set_status(STATUS_FAILED, "TOTP login returned empty response")
                self._client = None
                return False
            
            if self._is_success(response):
                self._logged_in = True
                self._set_status(STATUS_PENDING, "TOTP login successful, awaiting MPIN validation")
                logger.info(f"[{self.uid}] TOTP login successful")
                return True
            else:
                error_msg = self._extract_error(response, "TOTP login failed")
                logger.error(f"[{self.uid}] TOTP login failed: {error_msg}")
                self._set_status(STATUS_FAILED, error_msg)
                self._client = None
                return False
                
        except Exception as e:
            error_msg = f"Login exception: {str(e)}"
            self._set_status(STATUS_FAILED, error_msg)
            logger.exception(f"[{self.uid}] {error_msg}")
            self._client = None
            return False
    
    def validate(self) -> bool:
        """
        Step 2: Validate MPIN to get trade token.
        
        Must be called after successful login().
        
        Returns:
            True if validation successful, False otherwise
        """
        if not self._logged_in or not self._client:
            self._set_status(STATUS_FAILED, "Cannot validate: login() not called or failed")
            return False
        
        try:
            self._set_status(STATUS_PROCESSING, "Validating MPIN...")
            
            # XTS → KOTAK NEO REPLACEMENT: Call totp_validate with MPIN
            logger.info(f"[{self.uid}] Calling totp_validate...")
            response = self._client.totp_validate(
                mpin=self.config['mpin']
            )
            
            # DEFENSIVE: Log full response
            logger.info(f"[{self.uid}] MPIN validate response: {response}")
            
            # SAFETY CHECK: Handle None response
            if response is None:
                logger.error(f"[{self.uid}] MPIN validation returned None")
                self._set_status(STATUS_FAILED, "MPIN validation returned empty response")
                self._validated = False
                return False
            
            if self._is_success(response):
                self._validated = True
                self._set_status(STATUS_READY, "Authentication complete, ready for trading")
                logger.info(f"[{self.uid}] MPIN validation successful - READY")
                
                send_telegram(
                    f"🔐 <b>Neo Login Successful</b>\n"
                    f"UID: {self.uid}\n"
                    f"Status: READY for trading"
                )
                return True
            else:
                error_msg = self._extract_error(response, "MPIN validation failed")
                logger.error(f"[{self.uid}] MPIN validation failed: {error_msg}")
                self._set_status(STATUS_FAILED, error_msg)
                self._validated = False
                
                send_telegram(
                    f"❌ <b>Neo Login Failed</b>\n"
                    f"UID: {self.uid}\n"
                    f"Reason: {error_msg}"
                )
                return False
                
        except Exception as e:
            error_msg = f"Validation exception: {str(e)}"
            self._set_status(STATUS_FAILED, error_msg)
            logger.exception(f"[{self.uid}] {error_msg}")
            self._validated = False
            return False
    
    def reset(self) -> None:
        """Reset auth state before re-attempting login."""
        self._client = None
        self._logged_in = False
        self._validated = False
        self._set_status(STATUS_PENDING, "Auth reset, ready for new login attempt")
    
    def process_login_if_requested(self) -> None:
        """
        Check Redis for login request and handle it if present.
        
        Agent loop calls this to check for UI-triggered login requests.
        """
        login_flag = self.redis.hget(self.uid, KEY_LOGIN_REQUEST)
        
        if login_flag != "requested":
            return
        
        logger.info("[NEO_LOGIN] Processing login request...")
        self.redis.hset(self.uid, KEY_LOGIN_REQUEST, "processing")
        
        try:
            self.reset()
            
            if self.login():
                self.validate()
            
        finally:
            self.redis.hset(self.uid, KEY_LOGIN_REQUEST, "fetched")
    
    def _is_success(self, response: Any) -> bool:
        """Check if API response indicates success."""
        if response is None:
            return False
        
        if isinstance(response, dict):
            if response.get('error'):
                return False
            if response.get('stat') == 'Not_Ok':
                return False
            if response.get('code') and response.get('code') != 200:
                return False
            return True
        
        return bool(response)
    
    def _extract_error(self, response: Any, default: str) -> str:
        """Extract error message from API response."""
        if response is None:
            return default
        
        if isinstance(response, dict):
            for key in ['message', 'emsg', 'error', 'errMsg', 'errorMessage']:
                if key in response and response[key]:
                    return str(response[key])
        
        return default


# Backward compatibility alias
AuthService = NeoAuthService
