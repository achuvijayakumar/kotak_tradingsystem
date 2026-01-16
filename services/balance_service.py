"""Balance service for fetching and saving balance data."""
import logging
import json
import pandas as pd


class BalanceService:
    """Handles balance fetching and saving."""
    
    def __init__(self, client, redis_client, uid, balance_file):
        """Initialize balance service.
        
        Args:
            client: NeoAPI client instance (authenticated)
            redis_client: Redis client instance
            uid: User ID
            balance_file: Path to balance CSV file
        """
        # XTS → KOTAK NEO REPLACEMENT: renamed xt to client
        self.client = client
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
        """Fetch balance from Neo API and save to CSV."""
        # SAFETY CHECK: Ensure client is authenticated
        if self.client is None:
            logging.error("[FATAL] NeoAPI client is None - cannot fetch balance")
            return
        
        # XTS → KOTAK NEO REPLACEMENT: xt.get_balance() → client.limits()
        try:
            resp = self.client.limits()
            logging.info(f"Response for limits: {json.dumps(resp) if isinstance(resp, dict) else resp}")
        except Exception as e:
            logging.error(f"[BALANCE ERROR] Failed to fetch limits: {e}", exc_info=True)
            return
        
        # Make sure we have a dict
        if isinstance(resp, (str, bytes, bytearray)):
            resp = json.loads(resp)
        
        # XTS → KOTAK NEO REPLACEMENT: Neo limits() returns different structure
        # Neo response: {"stat": "Ok", "data": {"...margin fields..."}}
        if resp and resp.get("stat") == "Ok" and "data" in resp:
            data = resp.get("data", {})
            
            # Parse Neo margin structure
            parsed_data = []
            try:
                # Neo returns flat margin data
                parsed_data.append({
                    "cashAvailable": data.get("cash", data.get("availablecash")),
                    "netMarginAvailable": data.get("net", data.get("marginused")),
                    "marginUtilized": data.get("marginused", data.get("utiliseddebits")),
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
        else:
            logging.warning(f"Balance Request not successful: {resp}")
