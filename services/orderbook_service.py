"""Orderbook service for fetching and saving orderbook data."""
import logging
import json
import pandas as pd

try:
    from order_ingest import push_orderbook
    DUCKDB_AVAILABLE = True
except ImportError as e:
    logging.error(f"[ERROR] Could not import order_ingest/duckdb: {e}. Data syncing will be disabled.")
    DUCKDB_AVAILABLE = False


class OrderbookService:
    """Handles orderbook fetching and saving."""
    
    def __init__(self, xt, redis_client, uid, orderbook_file, base_dir):
        """Initialize orderbook service.
        
        Args:
            xt: XTS client instance
            redis_client: Redis client instance
            uid: User ID
            orderbook_file: Path to orderbook CSV file
            base_dir: Base directory for the application
        """
        self.xt = xt
        self.redis_client = redis_client
        self.uid = uid
        self.orderbook_file = orderbook_file
        self.base_dir = base_dir
    
    def process_if_requested(self):
        """Check Redis flag and fetch orderbook if requested."""
        order_book = self.redis_client.hget(self.uid, "ORDERBOOK")
        
        if order_book != "requested":
            return
        
        # Mark as fetched
        settings = {"ORDERBOOK": "fetched"}
        self.redis_client.hset(self.uid, mapping=settings)
        
        # Fetch and save orderbook
        self._fetch_and_save_orderbook()
    
    def _fetch_and_save_orderbook(self):
        """Fetch orderbook from XTS API and save to CSV."""
        response = self.xt.get_order_book(clientID=self.uid)
        logging.info(f"Order Book: {response}")
        
        # Make sure we have a dict
        if isinstance(response, (str, bytes, bytearray)):
            response = json.loads(response)
        
        if response.get("type") == "success" and "result" in response:
            orderbook = response["result"]
            df = pd.DataFrame(orderbook)
            wanted_cols = [
                "AppOrderID",
                "TradingSymbol",
                "OptionType",
                "OrderSide",
                "OrderQuantity",
                "OrderStatus",
                "OrderAverageTradedPrice",
                "OrderGeneratedDateTime",
                "ExchangeTransactTime",
            ]
            
            df = df[[col for col in wanted_cols if col in df.columns]]
            # Save DataFrame to CSV
            df.to_csv(self.orderbook_file, index=False)
            logging.info("OrderBook Request successful")
            
            if DUCKDB_AVAILABLE:
                push_orderbook(self.uid, self.base_dir)
        else:
            logging.warning("OrderBook Request not successful")
