"""Configuration management for the trading application."""
import json
import os
import sys
import logging


def load_config(uid):
    """Load configuration from JSON file for the given UID.
    
    Args:
        uid: User ID
        
    Returns:
        dict: Configuration dictionary with API credentials
        
    Raises:
        SystemExit: If config file not found or invalid
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up one level from core/ to poozhi/
    base_dir = os.path.dirname(base_dir)
    config_file = os.path.join(base_dir, uid, f"{uid}.json")
    
    try:
        with open(config_file, "r") as file:
            creds = json.load(file)
    except FileNotFoundError:
        logging.error(f"[ERROR] Config file not found: {config_file}")
        sys.exit(1)
    except json.JSONDecodeError:
        logging.error(f"[ERROR] Invalid JSON format in '{config_file}'")
        sys.exit(1)
    
    # Validate required fields
    required_fields = ["INTERACTIVE_API_KEY", "INTERACTIVE_API_SECRET", "INTERACTIVE_XTS_API_BASE_URL"]
    if not all(creds.get(field) for field in required_fields):
        logging.error("[ERROR] Missing one or more required credentials in config file.")
        sys.exit(1)
    
    return creds


def load_token(uid):
    """Load authentication token from file.
    
    Args:
        uid: User ID
        
    Returns:
        str: Authentication token
        
    Raises:
        SystemExit: If token file not found or invalid
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(base_dir)
    token_file = os.path.join(base_dir, uid, "token.txt")
    
    try:
        with open(token_file, "r") as f:
            token = f.read().strip()
            logging.info(f"Token read successfully for {uid}")
    except FileNotFoundError:
        logging.error(f"[ERROR] Token file not found for {uid}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"[ERROR] Could not read token: {e}")
        sys.exit(1)
    
    if len(token) < 25:
        logging.error("Invalid Token")
        sys.exit(1)
    
    return token


def save_token(uid, token):
    """Save authentication token to file.
    
    Args:
        uid: User ID
        token: Authentication token to save
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(base_dir)
    token_file = os.path.join(base_dir, uid, "token.txt")
    
    with open(token_file, "w") as f:
        f.write(token)
    logging.info(f"[SUCCESS] Token stored at: {token_file}")


def get_file_paths(uid):
    """Get file paths for positions, balance, and orderbook.
    
    Args:
        uid: User ID
        
    Returns:
        dict: Dictionary with keys 'position', 'balance', 'orderbook', 'base_dir'
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(base_dir)
    
    return {
        'position': os.path.join(base_dir, uid, "positions.csv"),
        'balance': os.path.join(base_dir, uid, "balance.csv"),
        'orderbook': os.path.join(base_dir, uid, "orderbook.csv"),
        'base_dir': base_dir
    }
