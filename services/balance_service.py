"""Balance service for fetching and saving balance data."""
import logging
import json
import pandas as pd


class BalanceService:
    """Handles balance fetching and saving."""
    
    def __init__(self, xt, redis_client, uid, balance_file):
        """Initialize balance service.
        
        Args:
            xt: XTS client instance
            redis_client: Redis client instance
            uid: User ID
            balance_file: Path to balance CSV file
        """
        self.xt = xt
        self.redis_client = redis_client
        self.uid = uid
        self.balance_file = balance_file
    
    def process_if_requested(self):
        """Check Redis flag and fetch balance if requested."""
        bal = self.redis_client.hget(self.uid, "BALANCE")
        
        if bal != "requested":
            return
        
        # Mark as fetched
        settings = {"BALANCE": "fetched"}
        self.redis_client.hset(self.uid, mapping=settings)
        
        # Fetch and save balance
        self._fetch_and_save_balance()
    
    def _fetch_and_save_balance(self):
        """Fetch balance from XTS API and save to CSV."""
        resp = self.xt.get_balance(clientID=self.uid)
        logging.info(f"Response for get_balance: {resp}")
        
        # Make sure we have a dict
        if isinstance(resp, (str, bytes, bytearray)):
            resp = json.loads(resp)
        
        if resp.get("type") == "success" and "result" in resp:
            balance = resp["result"].get("BalanceList", [])
            
            # Parse nested structure and extract only needed fields
            parsed_data = []
            for item in balance:
                try:
                    limit_obj = item.get("limitObject")
                    if isinstance(limit_obj, str):
                        limit_obj = json.loads(limit_obj.replace("'", '"'))  # handle string dict
                    
                    rms = limit_obj.get("RMSSubLimits", {}) if limit_obj else {}
                    parsed_data.append({
                        "cashAvailable": rms.get("cashAvailable"),
                        "netMarginAvailable": rms.get("netMarginAvailable"),
                        "marginUtilized": rms.get("marginUtilized"),
                    })
                except Exception as e:
                    logging.error(f"[ERROR] parsing balance item: {e}")
                    parsed_data.append({
                        "cashAvailable": None,
                        "netMarginAvailable": None,
                        "marginUtilized": None,
                    })
            
            # Convert to DataFrame and save
            df = pd.DataFrame(parsed_data)
            df.to_csv(self.balance_file, index=False)
            logging.info("Balance Request successful — cleaned data written to CSV.")
