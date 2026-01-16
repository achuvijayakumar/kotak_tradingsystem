"""
User management logic for creating and initializing user workspaces.
"""
import os
import json
import pandas as pd
import logging

# Set up logging
logger = logging.getLogger(__name__)

def setup_user(uid: str) -> bool:
    """
    Initialize directory and files for a new (or existing) user.
    
    1. Check if directory exists; if not, create it.
    2. Check if config file exists; if not, create template.
    3. Check if CSV files exist; if not, create structured value.
    
    Args:
        uid: User ID string
        
    Returns:
        True if successful, False on error.
    """
    if not uid:
        return False
        
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__)) # core/
        base_dir = os.path.dirname(base_dir) # root
        
        user_dir = os.path.join(base_dir, uid)
        
        # 1. Create User Directory
        if not os.path.exists(user_dir):
            os.makedirs(user_dir)
            logger.info(f"Created user directory: {user_dir}")
        else:
            logger.info(f"User directory exists: {user_dir}")
            
        # 2. Create Config Template
        config_file = os.path.join(user_dir, f"{uid}.json")
        if not os.path.exists(config_file):
            template = {
                "consumer_key": "",
                "mobile_number": "",
                "ucc": "",
                "mpin": ""
            }
            with open(config_file, 'w') as f:
                json.dump(template, f, indent=4)
            logger.info(f"Created config template: {config_file}")
            
        # 3. Create positions.csv
        pos_file = os.path.join(user_dir, "positions.csv")
        if not os.path.exists(pos_file):
            # Columns expected by ui.py
            columns = ["TradingSymbol", "BuyAveragePrice", "SellAveragePrice", "Quantity", "ExchangeInstrumentId", "OptionType", "Buy Price", "Sell Price", "Exit", "Payoff"]
            # Creating empty DF with minimal columns to avoid read errors
            # Only essential columns needed for empty state
            df = pd.DataFrame(columns=["TradingSymbol", "BuyAveragePrice", "SellAveragePrice", "Quantity", "ExchangeInstrumentId"])
            df.to_csv(pos_file, index=False)
            logger.info(f"Initialized positions.csv")
            
        # 4. Create balance.csv
        bal_file = os.path.join(user_dir, "balance.csv")
        if not os.path.exists(bal_file):
            df = pd.DataFrame(columns=["cashAvailable", "netMarginAvailable", "marginUtilized"])
            df.to_csv(bal_file, index=False)
            logger.info(f"Initialized balance.csv")
            
        # 5. Create orderbook.csv
        ob_file = os.path.join(user_dir, "orderbook.csv")
        if not os.path.exists(ob_file):
            df = pd.DataFrame(columns=["nOrdNo", "trdSym", "ordSt", "qty", "prc"])
            df.to_csv(ob_file, index=False)
            logger.info(f"Initialized orderbook.csv")
            
        return True
        
    except Exception as e:
        logger.error(f"Failed to setup user {uid}: {e}")
        return False
