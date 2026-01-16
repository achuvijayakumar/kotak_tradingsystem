"""Position service for fetching and saving position data."""
import logging
import json
import pandas as pd


class PositionService:
    """Handles position fetching and saving."""
    
    def __init__(self, xt, redis_client, uid, position_file):
        """Initialize position service.
        
        Args:
            xt: XTS client instance
            redis_client: Redis client instance
            uid: User ID
            position_file: Path to position CSV file
        """
        self.xt = xt
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
        """Fetch positions from XTS API and save to CSV."""

        # 🔴 Clear old positions immediately
        pd.DataFrame().to_csv(self.position_file, index=False)

        response = self.xt.get_position_netwise(clientID=self.uid)
        logging.info(f"Position by Net: {response}")
        
        resp = response
        
        # Make sure we have a dict
        if isinstance(resp, (str, bytes, bytearray)):
            resp = json.loads(resp)
        
        if resp.get("type") == "success" and "result" in resp:
            positions = resp["result"].get("positionList", [])
            df = pd.DataFrame(positions)

            # 🔴 MINIMAL FIX: remove exited positions
            if not df.empty and "Quantity" in df.columns:
                df = df[df["Quantity"] != 0]

            # Save DataFrame to CSV
            df.to_csv(self.position_file, index=False)
            logging.info("Position Request successful")
        else:
            logging.warning("Position Request not successful")
