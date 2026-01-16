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
    
    def __init__(self, client, redis_client, uid, orderbook_file, base_dir):
        """Initialize orderbook service.
        
        Args:
            client: NeoAPI client instance (authenticated)
            redis_client: Redis client instance
            uid: User ID
            orderbook_file: Path to orderbook CSV file
            base_dir: Base directory for the application
        """
        # XTS → KOTAK NEO REPLACEMENT: renamed xt to client
        self.client = client
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
        """Fetch orderbook from Neo API and save to CSV."""
        # SAFETY CHECK: Ensure client is authenticated
        if self.client is None:
            logging.error("[FATAL] NeoAPI client is None - cannot fetch orderbook")
            return
        
        # XTS → KOTAK NEO REPLACEMENT: xt.get_order_book() → client.order_report()
        try:
            response = self.client.order_report()
            logging.info(f"Order Book: {json.dumps(response) if isinstance(response, dict) else response}")
        except Exception as e:
            logging.error(f"[ORDERBOOK ERROR] Failed to fetch orders: {e}", exc_info=True)
            return
        
        # Make sure we have a dict
        if isinstance(response, (str, bytes, bytearray)):
            response = json.loads(response)
        
        # XTS → KOTAK NEO REPLACEMENT: Neo order_report() returns different structure
        # Neo response: {"stat": "Ok", "data": [...]}
        if response and response.get("stat") == "Ok" and "data" in response:
            orderbook = response.get("data", [])
            df = pd.DataFrame(orderbook)
            
            # XTS → KOTAK NEO REPLACEMENT: Map Neo fields to expected column names
            # Neo fields: nOrdNo, trdSym, optTp, trnsTp, qty, ordSt, avgPrc, ordDtTm, exTm
            column_mapping = {
                "nOrdNo": "AppOrderID",
                "trdSym": "TradingSymbol",
                "optTp": "OptionType",
                "trnsTp": "OrderSide",
                "qty": "OrderQuantity",
                "ordSt": "OrderStatus",
                "avgPrc": "OrderAverageTradedPrice",
                "ordDtTm": "OrderGeneratedDateTime",
                "exTm": "ExchangeTransactTime",
            }
            
            # Rename columns that exist
            df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})
            
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
            logging.warning(f"OrderBook Request not successful: {response}")
