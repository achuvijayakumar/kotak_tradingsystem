"""
Configuration management for Kotak Neo trading application.

Loads Neo API credentials from JSON config files.
"""
import json
import os
import sys
import logging


def load_config(uid: str) -> dict:
    """
    Load configuration from JSON file for the given UID.
    
    Expected config file: {base_dir}/{uid}/{uid}.json
    
    Required fields for Neo API:
        - consumer_key: Kotak Neo API consumer key
        - mobile_number: Registered mobile with country code (+91...)
        - ucc: Unique Client Code
        - mpin: MPIN for account
    
    Optional fields:
        - totp: Time-based OTP (usually passed at runtime)
    
    Args:
        uid: User ID
        
    Returns:
        dict: Configuration dictionary with API credentials
        
    Raises:
        SystemExit: If config file not found or invalid
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(base_dir)  # Go up from core/
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
    
    # Validate required fields for Neo API
    required_fields = ["consumer_key", "mobile_number", "ucc", "mpin"]
    missing = [f for f in required_fields if not creds.get(f)]
    
    if missing:
        logging.error(f"[ERROR] Missing required fields in config: {missing}")
        sys.exit(1)
    
    return creds


def get_file_paths(uid: str) -> dict:
    """
    Get file paths for positions, balance, and orderbook CSV files.
    
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


# Example config file format:
# {
#     "consumer_key": "your_kotak_neo_consumer_key",
#     "mobile_number": "+91XXXXXXXXXX",
#     "ucc": "YOUR_UCC_CODE",
#     "mpin": "1234"
# }
