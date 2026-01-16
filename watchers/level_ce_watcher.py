"""Level CE watcher for detecting upward price crosses."""
from watchers.base_watcher import BaseLevelWatcher


class LevelCEWatcher(BaseLevelWatcher):
    """Watches for spot price crossing UP above a level (for CE orders)."""
    
    def __init__(self, redis_client, uid):
        """Initialize CE watcher.
        
        Args:
            redis_client: Redis client instance
            uid: User ID
        """
        super().__init__(
            redis_client=redis_client,
            uid=uid,
            trigger_key="LEVEL_CE_TRIGGER",
            level_key="LEVEL_CE_LEVEL",
            index_key="LEVEL_CE_INDEX",
            prev_spot_key="LEVEL_CE_PREV_SPOT",
            place_key="PLACE_LEVEL_CE"
        )
    
    def _should_trigger(self, prev_spot, current_spot, level):
        """Check if price crossed UP above level.
        
        Args:
            prev_spot: Previous spot price
            current_spot: Current spot price
            level: Trigger level
            
        Returns:
            bool: True if crossed up
        """
        return (prev_spot < level) and (current_spot >= level)
