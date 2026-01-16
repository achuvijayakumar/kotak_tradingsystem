"""Position service for fetching and saving position data."""
import logging
import json
import pandas as pd


class PositionService:
    """Handles position fetching and saving."""
    
    def __init__(self, client, redis_client, uid, position_file):
        """Initialize position service.
        
        Args:
            client: NeoAPI client instance (authenticated)
            redis_client: Redis client instance
            uid: User ID
            position_file: Path to position CSV file
        """
        # XTS → KOTAK NEO REPLACEMENT: renamed xt to client
        self.client = client
        self.redis_client = redis_client
        self.uid = uid
        self.position_file = position_file
    
    def process_if_requested(self):
        """Check Redis flag and fetch positions if requested."""
        net_position = self.redis_client.hget(self.uid, "POSITION")
        
        if net_position != "requested":
            return
        
        # Mark as fetched
        settings = {"POSITION": "fetched"}
        self.redis_client.hset(self.uid, mapping=settings)
        
        # Fetch and save positions
        self._fetch_and_save_positions()
    
    def _fetch_and_save_positions(self):
        """Fetch positions from Neo API and save to CSV."""
        # SAFETY CHECK: Ensure client is authenticated
        if self.client is None:
            logging.error("[FATAL] NeoAPI client is None - cannot fetch positions")
            return

        # 🔴 Clear old positions immediately
        pd.DataFrame().to_csv(self.position_file, index=False)

        # XTS → KOTAK NEO REPLACEMENT: xt.get_position_netwise() → client.positions()
        try:
            response = self.client.positions()
            logging.info(f"Position response: {json.dumps(response) if isinstance(response, dict) else response}")
        except Exception as e:
            logging.error(f"[POSITIONS ERROR] Failed to fetch positions: {e}", exc_info=True)
            return
        
        resp = response
        
        # Make sure we have a dict
        if isinstance(resp, (str, bytes, bytearray)):
            resp = json.loads(resp)
        
        # XTS → KOTAK NEO REPLACEMENT: Neo returns different structure
        # Neo response: {"stat": "Ok", "stCode": 200, "data": [...]}
        if resp and resp.get("stat") == "Ok" and "data" in resp:
            positions = resp.get("data", [])
            df = pd.DataFrame(positions)

            # 🔴 MINIMAL FIX: remove exited positions
            # XTS → KOTAK NEO REPLACEMENT: Neo uses "flQty" for quantity
            if not df.empty and "flQty" in df.columns:
                df = df[df["flQty"].astype(int) != 0]
            elif not df.empty and "Quantity" in df.columns:
                df = df[df["Quantity"] != 0]

            # Save DataFrame to CSV
            df.to_csv(self.position_file, index=False)
            logging.info("Position Request successful")
        else:
            logging.warning(f"Position Request not successful: {resp}")
