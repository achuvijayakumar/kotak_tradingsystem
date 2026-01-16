import duckdb
import pandas as pd
import os
import logging

# Configure logging (will inherit from impl.py if called from there)
logging.basicConfig(level=logging.INFO)

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbWFpbCI6ImFjaHV2aWpheWFrdW1hcjk4QGdtYWlsLmNvbSIsIm1kUmVnaW9uIjoiYXdzLWV1LWNlbnRyYWwtMSIsInNlc3Npb24iOiJhY2h1dmlqYXlha3VtYXI5OC5nbWFpbC5jb20iLCJwYXQiOiJRM0NjaHVPa0o4RHNMdWRMM3Q5UWNMVjFPVm5CZ2V0cmxOMTFSNFE2OEdJIiwidXNlcklkIjoiNjRhODM3MzMtM2M1OC00MTcyLTkyM2UtNDZmNjNjODQ1NmMyIiwiaXNzIjoibWRfcGF0IiwicmVhZE9ubHkiOmZhbHNlLCJ0b2tlblR5cGUiOiJyZWFkX3dyaXRlIiwiaWF0IjoxNzY0MjM4NzA5fQ.QgX5-G1F8kKipmk9PZHoEeqxiLcpU3bIclmqTkzX9C4"
con = duckdb.connect(f"md:?token={TOKEN}")

def push_orderbook(uid: str, base_dir: str):
    csv_path = os.path.join(base_dir, uid, "orderbook.csv")

    if not os.path.exists(csv_path):
        logging.warning(f"[WARN] No orderbook.csv for {uid}")
        return

    try:
        df = pd.read_csv(csv_path)

        # Skip if empty
        if df.empty:
            logging.info(f"[MD] Orderbook empty for UID {uid}")
            return

        # Apply required filters
        df = df[df["OrderStatus"] == "Filled"]

        if df.empty:
            logging.info(f"[MD] No filled orders for UID {uid}")
            return

        # Ensure timestamps
        try:
            df["OrderGeneratedDateTime"] = pd.to_datetime(df["OrderGeneratedDateTime"], dayfirst=True)
            df["ExchangeTransactTime"] = pd.to_datetime(df["ExchangeTransactTime"], dayfirst=True)
        except Exception as e:
            logging.warning(f"[WARN] Timestamp parsing failed for {uid}: {e}")
            return


        
        # Per-user table name
        table_name = f"trading.orderbook_{uid}"

         # Create table if missing
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                AppOrderID               BIGINT,
                TradingSymbol            VARCHAR,
                OrderSide                VARCHAR,
                OrderQuantity            BIGINT,
                OrderStatus              VARCHAR,
                OrderAverageTradedPrice  DOUBLE,
                OrderGeneratedDateTime   TIMESTAMP,
                ExchangeTransactTime     TIMESTAMP,
                GroupName                VARCHAR
            );
        """)

        # Deduplicate using AppOrderID
        csv_ids = tuple(df["AppOrderID"].unique().tolist())

        if csv_ids:
            existing = con.execute(
                f"SELECT AppOrderID FROM {table_name} WHERE AppOrderID IN {csv_ids}"
            ).fetchdf()

            existing_ids = set(existing["AppOrderID"]) if not existing.empty else set()
            df = df[~df["AppOrderID"].isin(existing_ids)]

        if df.empty:
            logging.info(f"[MD] No new orders for {uid}")
            return
        
        # If CSV does not have GroupName, add default
        if "GroupName" not in df.columns:
            df["GroupName"] = "Ungrouped"
        else:
            df["GroupName"] = df["GroupName"].fillna("Ungrouped")

        # Ensure all required columns exist and are in the exact order as the table schema
        required_columns = [
            "AppOrderID",
            "TradingSymbol",
            "OrderSide",
            "OrderQuantity",
            "OrderStatus",
            "OrderAverageTradedPrice",
            "OrderGeneratedDateTime",
            "ExchangeTransactTime",
            "GroupName"
        ]
        
        # Check if all required columns exist
        missing_cols = [col for col in required_columns if col not in df.columns]
        if missing_cols:
            logging.error(f"[ERROR] Missing columns in orderbook.csv: {missing_cols}")
            return
        
        # Reorder DataFrame columns to match table schema
        df = df[required_columns]

        con.execute(f"INSERT INTO {table_name} SELECT * FROM df")

        logging.info(f"[MD] Inserted {len(df)} rows into {table_name}")

    except Exception as e:
        logging.error(f"[ERROR] Ingestion failed for {uid}: {e}")



# import duckdb
# con = duckdb.connect("md:?token={TOKEN}")

# print(con.execute("SHOW TABLES IN trading").fetchdf())
