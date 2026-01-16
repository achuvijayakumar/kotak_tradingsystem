"""Level PE watcher for detecting downward price crosses."""
from watchers.base_watcher import BaseLevelWatcher


class LevelPEWatcher(BaseLevelWatcher):
    """Watches for spot price crossing DOWN below a level (for PE orders)."""
    
    def __init__(self, redis_client, uid):
        """Initialize PE watcher.
        
        Args:
            redis_client: Redis client instance
            uid: User ID
        """
        super().__init__(
            redis_client=redis_client,
            uid=uid,
            trigger_key="LEVEL_PE_TRIGGER",
            level_key="LEVEL_PE_LEVEL",
            index_key="LEVEL_PE_INDEX",
            prev_spot_key="LEVEL_PE_PREV_SPOT",
            place_key="PLACE_LEVEL_PE"
        )
    
    def _should_trigger(self, prev_spot, current_spot, level):
        """Check if price crossed DOWN below level.
        
        Args:
            prev_spot: Previous spot price
            current_spot: Current spot price
            level: Trigger level
            
        Returns:
            bool: True if crossed down
        """
        return (prev_spot > level) and (current_spot <= level)
