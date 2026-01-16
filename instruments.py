import json
import sys
import os
from XTSConnect import XTSConnect  # ensure the module is available

# --- Step 1: Ensure UID argument is passed ---
if len(sys.argv) < 2:
    print("[ERROR] UID argument missing.")
    sys.exit(1)

uid = sys.argv[1]  # example: 'achu'

# --- Step 2: Locate the JSON file inside 'uid' folder (relative to this script) ---
base_dir = os.path.dirname(os.path.abspath(__file__))
uid_dir = os.path.join(base_dir, uid)
config_file = os.path.join(base_dir, uid, f"{uid}.json")

# --- Step 3: Read credentials from JSON file ---
try:
    with open(config_file, "r") as file:
        creds = json.load(file)
except FileNotFoundError:
    print(f"[ERROR] Config file not found: {config_file}")
    sys.exit(1)
except json.JSONDecodeError:
    print(f"[ERROR] Invalid JSON format in '{config_file}'")
    sys.exit(1)

# --- Step 4: Extract credentials ---
MARKETDATA_API_KEY = creds.get("MARKETDATA_API_KEY")
MARKETDATA_API_SECRET = creds.get("MARKETDATA_API_SECRET")
MARKETDATA_XTS_API_BASE_URL = creds.get("MARKETDATA_XTS_API_BASE_URL")

# --- Step 5: Validate required fields ---
if not all([MARKETDATA_API_KEY, MARKETDATA_API_SECRET, MARKETDATA_XTS_API_BASE_URL]):
    print("[ERROR] Missing one or more required credentials in config file.")
    sys.exit(1)

# --- Step 6: Initialize XTSConnect ---
print(f"[INFO] Logging in using credentials from {config_file} ...")

xt = XTSConnect(
    MARKETDATA_API_KEY,
    MARKETDATA_API_SECRET,
    "WEBAPI",
    MARKETDATA_XTS_API_BASE_URL
)

print("[SUCCESS] XTSConnect initialized successfully.")

# --- Step 7: Attempt login and print response ---
try:
    resp = xt.marketdata_login()
    print("login response =", resp)
except Exception as e:
    print("[ERROR] Login failed:", e)
    sys.exit(1)

# --- Step 8: Extract token from response ---
token = resp

# --- Step 9: Validate and store token ---
if token and len(token) > 25:  # your length check still works
    token_file = os.path.join(uid_dir, "token.txt")
    with open(token_file, "w") as f:
        f.write(token)
    print(f"[SUCCESS] Token stored at: {token_file}")
else:
    print("[ERROR] Invalid credentials or token too short. Token not saved.")



"""Get Master Instruments Request"""
exchangesegments = [xt.EXCHANGE_NSEFO, xt.EXCHANGE_NSECM]
response = xt.get_master(exchangeSegmentList=exchangesegments)
#print("Master: " + str(response))
# response= xt.get_option_symbol(xt.EXCHANGE_NSEFO, series, symbol, expiryDate, optionType, strikePrice)
# print(response)

import pandas as pd

raw = response["result"]  # your string from API

# Split rows
rows = raw.strip().split("\n")
# Split fields by '|'
data = [row.split("|") for row in rows]

# Header provided by you
header = [
    "ExchangeSegment","ExchangeInstrumentID","InstrumentType","Name","Description","Series",
    "NameWithSeries","InstrumentID","PriceBandHigh","PriceBandLow","FreezeQty","TickSize",
    "LotSize","Multiplier","UnderlyingInstrumentId","UnderlyingIndexName","ContractExpiration",
    "StrikePrice","OptionType","DisplayName","PriceNumerator", "Dummy1","Dummy2"
]

# Create DataFrame
df = pd.DataFrame(data, columns = header)

# Save CSV
#df.to_csv("instrument_data.csv", index=False)
df[["ExchangeInstrumentID", "DisplayName"]].to_csv(
    "instrument_data.csv",
    index=False
)

print(df)
print("\nSaved to instrument_data.csv")


import pandas as pd

# Read only required columns
df = pd.read_csv("instrument_data.csv", usecols=["ExchangeInstrumentID", "DisplayName"])

# Filter using .str.startswith()
filtered = df[
    df["DisplayName"].str.startswith(("NIFTY ", "BANKNIFTY"), na=False)
]

filtered.to_csv("instr.csv", index=False)

import pandas as pd
from datetime import datetime
import redis

# Redis connection
redis_client = redis.StrictRedis(host="localhost", port=6379, db=0)

# -------- Read CSV --------
df = pd.read_csv("instr.csv", usecols=["ExchangeInstrumentID", "DisplayName"])


# -------- Transform function --------
def transform_display_name(name: str) -> str:
    # expected format: "NIFTY 23DEC2025 CE 31250"
    parts = name.split()
    if len(parts) != 4:
        return name  # fallback for unexpected cases

    index, date_raw, opt_type, strike = parts
    parsed_date = datetime.strptime(date_raw, "%d%b%Y").strftime("%Y-%m-%d")

    return f"{index}_{parsed_date}_{opt_type}_{strike}"


# Apply transformation
df["transformed"] = df["DisplayName"].apply(transform_display_name)


# -------- Clear existing Redis hash --------
redis_client.delete("XTS_INSTR")


# -------- Store transformed -> instrumentID --------
mapping = {
    row["transformed"]: str(row["ExchangeInstrumentID"])
    for _, row in df.iterrows()
}

redis_client.hset("XTS_INSTR", mapping=mapping)

print("Stored in Redis XTS_INSTR:")
for k, v in mapping.items():
    print(k, "=>", v)

# -------------------------------------------------------------------
# NEW: Process NSECM (Equity) Instruments
# -------------------------------------------------------------------

# Re-create DataFrame from the original 'data' list which contains BOTH NSEFO and NSECM
df_all = pd.DataFrame(data, columns=header)

# Filter for NSECM and Series EQ
# ExchangeSegment: "NSECM" usually corresponds to ID 1 (based on XTS SDK), but let's check explicit field if available.
# Actually, 'ExchangeSegment' is the first column. "NSEFO" is usually 2, "NSECM" is 1.
# Or we can just use Series='EQ' which is specific to Equity.

df_equity = df_all[
    (df_all["Series"] == "EQ")
].copy()

# Select columns
# Name -> Symbol (e.g. RELIANCE)
# ExchangeInstrumentID -> ID

# Prepare mapping: SYMBOL -> ExchangeInstrumentID
equity_mapping = {
    row["Name"]: str(row["ExchangeInstrumentID"]) 
    for _, row in df_equity.iterrows()
}

# Store in separate Redis key
redis_client.delete("XTS_INSTR_EQ")
if equity_mapping:
    redis_client.hset("XTS_INSTR_EQ", mapping=equity_mapping)
    print("\n[SUCCESS] Stored NSECM (EQ) keys in Redis XTS_INSTR_EQ")
    # print first 5 items
    from itertools import islice
    for k, v in islice(equity_mapping.items(), 5):
        print(f"  {k} -> {v}")
else:
    print("\n[WARNING] No NSECM (EQ) instruments found.")

