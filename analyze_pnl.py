import duckdb
import pandas as pd

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbWFpbCI6ImFjaHV2aWpheWFrdW1hcjk4QGdtYWlsLmNvbSIsIm1kUmVnaW9uIjoiYXdzLWV1LWNlbnRyYWwtMSIsInNlc3Npb24iOiJhY2h1dmlqYXlha3VtYXI5OC5nbWFpbC5jb20iLCJwYXQiOiJRM0NjaHVPa0o4RHNMdWRMM3Q5UWNMVjFPVm5CZ2V0cmxOMTFSNFE2OEdJIiwidXNlcklkIjoiNjRhODM3MzMtM2M1OC00MTcyLTkyM2UtNDZmNjNjODQ1NmMyIiwiaXNzIjoibWRfcGF0IiwicmVhZE9ubHkiOmZhbHNlLCJ0b2tlblR5cGUiOiJyZWFkX3dyaXRlIiwiaWF0IjoxNzY0MjM4NzA5fQ.QgX5-G1F8kKipmk9PZHoEeqxiLcpU3bIclmqTkzX9C4"
con = duckdb.connect(f"md:?token={TOKEN}")

table_name = "trading.orderbook_ITC2766"
limit = 100

print("=" * 80)
print("ANALYZING CLOSED POSITIONS P&L GROUPING")
print("=" * 80)

# Test both groups
for group_choice in ["Group1", "Group2"]:
    print(f"\n{'=' * 80}")
    print(f"GROUP: {group_choice}")
    print(f"{'=' * 80}")
    
    group_filter_sql = f"AND GroupName = '{group_choice}'"
    
    sql = f"""
        WITH base AS (
            SELECT 
                TradingSymbol,
                OrderSide,
                OrderQuantity AS qty,
                OrderAverageTradedPrice AS price,
                OrderGeneratedDateTime,
                GroupName
            FROM {table_name}
            WHERE OrderStatus = 'Filled'
            {group_filter_sql}
            ORDER BY OrderGeneratedDateTime
        ),
        buys AS (
            SELECT *, SUM(qty) OVER(PARTITION BY TradingSymbol ORDER BY OrderGeneratedDateTime) AS cum_buy
            FROM base WHERE OrderSide = 'BUY'
        ),
        sells AS (
            SELECT *, SUM(qty) OVER(PARTITION BY TradingSymbol ORDER BY OrderGeneratedDateTime) AS cum_sell
            FROM base WHERE OrderSide = 'SELL'
        ),
        paired AS (
            SELECT 
                b.TradingSymbol,
                b.price AS BuyPrice,
                s.price AS SellPrice,
                LEAST(b.qty, s.qty) AS ClosedQty,
                b.OrderGeneratedDateTime AS BuyTime,
                s.OrderGeneratedDateTime AS SellTime,
                (s.price - b.price) * LEAST(b.qty, s.qty) AS PnL
            FROM buys b
            JOIN sells s
            ON b.TradingSymbol = s.TradingSymbol
            AND b.cum_buy >= s.cum_sell - s.qty
            AND b.cum_buy - b.qty < s.cum_sell
        )
        SELECT *
        FROM paired
        ORDER BY BuyTime DESC
        LIMIT {limit};
    """
    
    df = con.execute(sql).fetchdf()
    
    if df.empty:
        print(f"No closed positions found for {group_choice}")
    else:
        print(f"\nClosed Positions for {group_choice}:")
        print(df.to_string(index=False))
        print(f"\n{'=' * 80}")
        print(f"Total Realised P&L for {group_choice}: {df['PnL'].sum():,.2f}")
        print(f"{'=' * 80}")

# Also show raw data for verification
print(f"\n\n{'=' * 80}")
print("RAW DATA FROM DATABASE (for verification)")
print(f"{'=' * 80}")
raw_sql = f"""
    SELECT 
        AppOrderID,
        TradingSymbol,
        OrderSide,
        OrderQuantity,
        OrderAverageTradedPrice,
        OrderGeneratedDateTime,
        GroupName
    FROM {table_name}
    WHERE OrderStatus = 'Filled'
    ORDER BY GroupName, OrderGeneratedDateTime
"""
raw_df = con.execute(raw_sql).fetchdf()
print(raw_df.to_string(index=False))

# Manual calculation verification
print(f"\n\n{'=' * 80}")
print("MANUAL P&L VERIFICATION")
print(f"{'=' * 80}")

trades = [
    {"symbol": "NIFTY25DEC26000CE", "buy": 122.5, "sell": 135.8, "qty": 75, "group": "Group1"},
    {"symbol": "BANKNIFTY25DEC55000CE", "buy": 210.3, "sell": 188.4, "qty": 35, "group": "Group2"},
    {"symbol": "NIFTY25DEC26200PE", "buy": 142.1, "sell": 98.7, "qty": 75, "group": "Group1"},
    {"symbol": "BANKNIFTY25DEC55200PE", "buy": 176.9, "sell": 201.6, "qty": 35, "group": "Group2"},
    {"symbol": "NIFTY25DEC26400CE", "buy": 115.0, "sell": 158.3, "qty": 75, "group": "Group1"},
]

group1_pnl = 0
group2_pnl = 0

for trade in trades:
    pnl = (trade["sell"] - trade["buy"]) * trade["qty"]
    print(f"{trade['group']} | {trade['symbol']:25} | Buy: {trade['buy']:6.1f} | Sell: {trade['sell']:6.1f} | Qty: {trade['qty']:3} | P&L: {pnl:8.2f}")
    
    if trade["group"] == "Group1":
        group1_pnl += pnl
    else:
        group2_pnl += pnl

print(f"\n{'=' * 80}")
print(f"Group1 Total P&L (Manual): {group1_pnl:,.2f}")
print(f"Group2 Total P&L (Manual): {group2_pnl:,.2f}")
print(f"Overall Total P&L (Manual): {group1_pnl + group2_pnl:,.2f}")
print(f"{'=' * 80}")
