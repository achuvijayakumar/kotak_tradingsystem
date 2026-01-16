"""
Neo Instrument Builder - Downloads and stores instrument master in Redis.

Purpose:
- Fetches scrip master from Kotak Neo API (nse_fo segment)
- Filters NIFTY and BANKNIFTY options only
- Normalizes Redis keys as: INDEX_YYYY-MM-DD_CE/PE_STRIKE
- Stores mappings in Redis hash NEO_INSTR_OPT
- Idempotent: safe to re-run (overwrites existing keys)

Usage:
    python instruments.py [uid]
    
    If uid is provided, uses config from {uid}/{uid}.json
    Otherwise uses environment variable NEO_CONSUMER_KEY

Verification:
    redis-cli HGET NEO_INSTR_OPT NIFTY_2026-01-27_CE_26000
"""

import redis
import re
import sys
import os
import json
from datetime import datetime
from typing import Optional, Dict, Tuple
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Redis configuration
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0

# Redis hash keys
REDIS_KEY_OPT = "NEO_INSTR_OPT"
REDIS_KEY_EQ = "NEO_INSTR_EQ"

# Supported indices
SUPPORTED_INDICES = {"NIFTY", "BANKNIFTY"}

# Month name to number mapping
MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"
}


def parse_display_name(display_name: str) -> Optional[Tuple[str, str, str, str]]:
    """
    Parse DisplayName from scrip master.
    
    Input format examples:
        "NIFTY 27JAN2026 CE 26000"
        "BANKNIFTY 24FEB2026 PE 69100"
    
    Returns:
        Tuple of (index, date_iso, option_type, strike) or None if parsing fails
    """
    pattern = r'^(NIFTY|BANKNIFTY)\s+(\d{2})([A-Z]{3})(\d{4})\s+(CE|PE)\s+(\d+)$'
    match = re.match(pattern, display_name.strip())
    
    if not match:
        return None
    
    index = match.group(1)
    day = match.group(2)
    month_str = match.group(3)
    year = match.group(4)
    option_type = match.group(5)
    strike = match.group(6)
    
    month = MONTH_MAP.get(month_str.upper())
    if not month:
        return None
    
    date_iso = f"{year}-{month}-{day}"
    
    try:
        datetime.strptime(date_iso, "%Y-%m-%d")
    except ValueError:
        return None
    
    return (index, date_iso, option_type, strike)


def build_redis_key(index: str, date_iso: str, option_type: str, strike: str) -> str:
    """Build normalized Redis key: INDEX_YYYY-MM-DD_CE/PE_STRIKE"""
    return f"{index}_{date_iso}_{option_type}_{strike}"


def load_scrip_master_from_csv(csv_path: str) -> Dict[str, str]:
    """Load scrip master from local CSV file."""
    instruments = {}
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        header = f.readline().strip()
        logger.info(f"CSV Header: {header}")
        
        line_count = 0
        parsed_count = 0
        
        for line in f:
            line_count += 1
            line = line.strip()
            if not line:
                continue
            
            parts = line.split(',', 1)
            if len(parts) != 2:
                continue
            
            instrument_id = parts[0].strip()
            display_name = parts[1].strip()
            
            parsed = parse_display_name(display_name)
            if parsed is None:
                continue
            
            index, date_iso, option_type, strike = parsed
            
            if index not in SUPPORTED_INDICES:
                continue
            
            redis_key = build_redis_key(index, date_iso, option_type, strike)
            instruments[redis_key] = instrument_id
            parsed_count += 1
        
        logger.info(f"Processed {line_count} lines, parsed {parsed_count} NIFTY/BANKNIFTY options")
    
    return instruments


def load_scrip_master_from_api(consumer_key: str) -> Dict[str, str]:
    """Load scrip master from Kotak Neo API."""
    try:
        from neo_api_client import NeoAPI
        
        client = NeoAPI(
            environment='prod',
            access_token=None,
            neo_fin_key=None,
            consumer_key=consumer_key
        )
        
        logger.info("Fetching scrip master from Kotak Neo API...")
        response = client.scrip_master(exchange_segment="nse_fo")
        
        if not response:
            logger.warning("Empty response from scrip_master API")
            return {}
        
        instruments = {}
        
        for scrip in response:
            display_name = scrip.get('pSymbolName', '') or scrip.get('sSymbol', '')
            instrument_id = str(scrip.get('nToken', '')) or scrip.get('pExchSeg', '')
            trading_symbol = scrip.get('pTrdSymbol', '')
            
            parsed = parse_display_name(display_name)
            if parsed is None:
                continue
            
            index, date_iso, option_type, strike = parsed
            
            if index not in SUPPORTED_INDICES:
                continue
            
            redis_key = build_redis_key(index, date_iso, option_type, strike)
            value = trading_symbol if trading_symbol else instrument_id
            instruments[redis_key] = value
        
        logger.info(f"Fetched {len(instruments)} instruments from API")
        return instruments
        
    except ImportError:
        logger.warning("neo_api_client not installed")
        return {}
    except Exception as e:
        logger.error(f"Error fetching from API: {e}")
        return {}


def populate_redis(instruments: Dict[str, str], redis_key: str = REDIS_KEY_OPT) -> int:
    """Populate Redis hash with instrument mappings."""
    if not instruments:
        logger.warning("No instruments to populate")
        return 0
    
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
    
    try:
        r.ping()
        logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
    except redis.ConnectionError as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise
    
    existing_count = r.hlen(redis_key)
    if existing_count > 0:
        logger.info(f"Clearing existing {existing_count} keys from {redis_key}")
        r.delete(redis_key)
    
    logger.info(f"Populating {len(instruments)} instruments to {redis_key}...")
    r.hset(redis_key, mapping=instruments)
    
    final_count = r.hlen(redis_key)
    logger.info(f"Successfully populated {final_count} instruments")
    
    return final_count


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Neo Instrument Builder - Starting")
    logger.info("=" * 60)
    
    # Get consumer key from config or environment
    consumer_key = None
    
    if len(sys.argv) >= 2:
        uid = sys.argv[1]
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_file = os.path.join(base_dir, uid, f"{uid}.json")
        
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                config = json.load(f)
                consumer_key = config.get('consumer_key')
                logger.info(f"Loaded config from {config_file}")
    
    if not consumer_key:
        consumer_key = os.environ.get("NEO_CONSUMER_KEY")
    
    instruments = {}
    
    # Try API first
    if consumer_key:
        instruments = load_scrip_master_from_api(consumer_key)
    
    # Fall back to CSV
    if not instruments:
        csv_path = os.path.join(os.path.dirname(__file__), "instr.csv")
        if os.path.exists(csv_path):
            logger.info(f"Loading from CSV: {csv_path}")
            instruments = load_scrip_master_from_csv(csv_path)
        else:
            logger.error(f"CSV file not found: {csv_path}")
            return
    
    if not instruments:
        logger.error("No instruments loaded - aborting")
        return
    
    # Populate Redis
    count = populate_redis(instruments)
    
    if count > 0:
        sample_keys = list(instruments.keys())[:3]
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
        
        logger.info("\n=== Sample Verification ===")
        for key in sample_keys:
            value = r.hget(REDIS_KEY_OPT, key)
            logger.info(f"  {key} = {value}")
    
    logger.info("\n" + "=" * 60)
    logger.info("Neo Instrument Builder - Complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
