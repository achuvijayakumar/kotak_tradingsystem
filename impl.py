"""Main orchestrator for the trading application.

This is the refactored version of impl.py that coordinates all components.
"""
import sys
import time
import logging

# Core imports
from core.config import load_config, load_token, get_file_paths
from core.auth import AuthService

# Service imports
from services.balance_service import BalanceService
from services.position_service import PositionService
from services.orderbook_service import OrderbookService
from services.order_service import OrderService

# Watcher imports
from watchers.level_ce_watcher import LevelCEWatcher
from watchers.level_pe_watcher import LevelPEWatcher

# Utility imports
from utils.logging_config import setup_logging
from utils.redis_helper import get_redis_client


def main():
    """Main entry point for the trading application."""
    # Setup logging
    setup_logging()
    
    # Ensure a UID argument is passed
    if len(sys.argv) < 2:
        logging.error("[ERROR] UID argument missing.")
        sys.exit(1)
    
    # Read the UID from command-line arguments
    uid = sys.argv[1]
    
    # Load configuration and token
    config = load_config(uid)
    token = load_token(uid)
    file_paths = get_file_paths(uid)
    
    # Initialize Redis client
    redis_client = get_redis_client()
    
    # Initialize authentication service
    auth_service = AuthService(config, redis_client, uid)
    auth_service.initialize_client(token)
    
    # Get XTS client
    xt = auth_service.get_xt_client()
    
    # Initialize services
    balance_service = BalanceService(xt, redis_client, uid, file_paths['balance'])
    position_service = PositionService(xt, redis_client, uid, file_paths['position'])
    orderbook_service = OrderbookService(xt, redis_client, uid, file_paths['orderbook'], file_paths['base_dir'])
    order_service = OrderService(xt, redis_client, uid)
    
    # Initialize watchers
    watchers = [
        LevelCEWatcher(redis_client, uid),
        LevelPEWatcher(redis_client, uid)
    ]
    
    logging.info(f"[SUCCESS] All services initialized for UID: {uid}")
    
    # Main loop
    while True:
        try:
            # Process login requests
            auth_service.process_login_if_requested()
            auth_service.process_session_check_if_requested()
            
            # Process data fetch requests
            balance_service.process_if_requested()
            position_service.process_if_requested()
            orderbook_service.process_if_requested()
            
            # Process order requests
            order_service.process_all()
            
            # Check watchers for trigger conditions
            for watcher in watchers:
                watcher.check_and_trigger()
            
            # Sleep before next iteration
            time.sleep(3)
        
        except Exception as e:
            logging.error(f"Critical error in main loop: {e}")
            time.sleep(5)  # Wait before retrying to avoid tight loop on persistent error


if __name__ == "__main__":
    main()
