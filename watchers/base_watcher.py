"""Base class for level-based watchers."""
import logging
from abc import ABC, abstractmethod
from utils.telegram_notifier import send_telegram



class BaseLevelWatcher(ABC):
    """Abstract base class for level-based order watchers."""
    
    def __init__(self, redis_client, uid, trigger_key, level_key, index_key, prev_spot_key, place_key):
        """Initialize base watcher.
        
        Args:
            redis_client: Redis client instance
            uid: User ID
            trigger_key: Redis key for trigger state
            level_key: Redis key for level value
            index_key: Redis key for index (NIFTY/BANKNIFTY)
            prev_spot_key: Redis key for previous spot price
            place_key: Redis key to trigger order placement
        """
        self.redis_client = redis_client
        self.uid = uid
        self.trigger_key = trigger_key
        self.level_key = level_key
        self.index_key = index_key
        self.prev_spot_key = prev_spot_key
        self.place_key = place_key
    
    def _get_spot_price(self, index):
        """Get spot price from Redis based on index.
        
        Args:
            index: Index name (NIFTY or BANKNIFTY)
            
        Returns:
            float or None: Spot price if available
        """
        if index == "NIFTY":
            raw_spot = self.redis_client.get("NF_SPOT")
        elif index == "BANKNIFTY":
            raw_spot = self.redis_client.get("BN_SPOT")
        else:
            logging.warning(f"[{self.trigger_key}] Unknown index '{index}'; skipping")
            return None
        
        if not raw_spot:
            logging.info(f"[{self.trigger_key}] No spot data for {index}; skipping")
            return None
        
        try:
            return float(raw_spot)
        except Exception as e:
            logging.error(f"[{self.trigger_key}] Could not parse spot '{raw_spot}' for {index}: {e}")
            return None
    
    @abstractmethod
    def _should_trigger(self, prev_spot, current_spot, level):
        """Determine if trigger condition is met.
        
        Args:
            prev_spot: Previous spot price
            current_spot: Current spot price
            level: Trigger level
            
        Returns:
            bool: True if should trigger
        """
        pass
    
    def check_and_trigger(self):
        """Check conditions and trigger order if necessary."""
        try:
            trigger_state = self.redis_client.hget(self.uid, self.trigger_key)
            
            if trigger_state != "waiting":
                return
            
            level = float(self.redis_client.hget(self.uid, self.level_key) or 0)
            index = self.redis_client.hget(self.uid, self.index_key)
            
            # Get current spot price
            spot = self._get_spot_price(index)
            if spot is None:
                return
            
            # Get previous spot price
            prev_raw = self.redis_client.hget(self.uid, self.prev_spot_key)
            prev_spot = float(prev_raw) if prev_raw else None
            
            # Save current spot for next cycle
            self.redis_client.hset(self.uid, self.prev_spot_key, spot)
            
            # If first cycle → no previous value, skip
            if prev_spot is None:
                return
            
            # Check trigger condition
            if self._should_trigger(prev_spot, spot, level):
                logging.info(
                    f"[{self.trigger_key} TRIGGERED] {index}: Prev={prev_spot}, Spot={spot}, Level={level}"
                )
                send_telegram(
                    f"{self.place_key.replace('PLACE_', '')} Triggered 🔔\n"
                    f"Index: {index}\n"
                    f"Prev Spot: {prev_spot}\n"
                    f"Current Spot: {spot}\n"
                    f"Level: {level}\n"
                    f"Executing legs..."
                )
                self.redis_client.hset(self.uid, self.place_key, "requested")
                self.redis_client.hset(self.uid, self.trigger_key, "triggered")
        
        except Exception as e:
            logging.error(f"[{self.trigger_key} ERROR] {e}")
