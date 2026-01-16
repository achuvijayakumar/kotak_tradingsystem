"""Redis helper functions and utilities."""
import redis


def get_redis_client():
    """Create and return a Redis client connected to localhost."""
    return redis.StrictRedis(host='localhost', port=6379, db=0, decode_responses=True)


def check_flag(redis_client, uid, flag_name, expected_value="requested"):
    """Check if a Redis flag matches the expected value.
    
    Args:
        redis_client: Redis client instance
        uid: User ID
        flag_name: Name of the flag to check
        expected_value: Expected value (default: "requested")
        
    Returns:
        bool: True if flag matches expected value
    """
    return redis_client.hget(uid, flag_name) == expected_value


def set_status(redis_client, uid, status_key, status_value, msg_key=None, msg_value=None):
    """Set status in Redis, optionally with a message.
    
    Args:
        redis_client: Redis client instance
        uid: User ID
        status_key: Key for status field
        status_value: Value to set for status
        msg_key: Optional message key
        msg_value: Optional message value
    """
    mapping = {status_key: status_value}
    if msg_key and msg_value:
        mapping[msg_key] = msg_value
    redis_client.hset(uid, mapping=mapping)
