import pandas as pd
import redis
import sys
import os
import json
from XTSConnect import XTSConnect
import time
try:
    from order_ingest import push_orderbook
    DUCKDB_AVAILABLE = True
except ImportError as e:
    logging.error(f"[ERROR] Could not import order_ingest/duckdb: {e}. Data syncing will be disabled.")
    DUCKDB_AVAILABLE = False


# Connect to local Redis database
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("agent.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

# Connect to local Redis database
redis_client = redis.StrictRedis(host='localhost', port=6379, db=0, decode_responses=True)

# Ensure a UID argument is passed when running the script
if len(sys.argv) < 2:
    logging.error("[ERROR] UID argument missing.")
    sys.exit(1)

# Read the UID from command-line arguments
uid = sys.argv[1]

#paths
base_dir = os.path.dirname(os.path.abspath(__file__))
token_file = os.path.join(base_dir, uid, "token.txt")
config_file = os.path.join(base_dir, uid, f"{uid}.json")
position_file = os.path.join(base_dir, uid, "positions.csv")
balance_file = os.path.join(base_dir, uid, "balance.csv")
orderbook_file = os.path.join(base_dir, uid, "orderbook.csv")


# Read the token
try:
    with open(token_file, "r") as f:
        token = f.read().strip()
        logging.info(f"Token read successfully for {uid}")
except FileNotFoundError:
    logging.error(f"[ERROR] Token file not found for {uid}")
    sys.exit(1)
except Exception as e:
    logging.error(f"[ERROR] Could not read token: {e}")
    sys.exit(1)

if len(token)<25 :
    logging.error("Invalid Token")
    exit()
    

# --- Step 3: Read credentials from JSON file ---
try:
    with open(config_file, "r") as file:
        creds = json.load(file)
except FileNotFoundError:
    logging.error(f"[ERROR] Config file not found: {config_file}")
    sys.exit(1)
except json.JSONDecodeError:
    logging.error(f"[ERROR] Invalid JSON format in '{config_file}'")
    sys.exit(1)

# --- Step 4: Extract credentials ---
INTERACTIVE_API_KEY = creds.get("INTERACTIVE_API_KEY")
INTERACTIVE_API_SECRET = creds.get("INTERACTIVE_API_SECRET")
INTERACTIVE_XTS_API_BASE_URL = creds.get("INTERACTIVE_XTS_API_BASE_URL")

# --- Step 5: Validate required fields ---
if not all([INTERACTIVE_API_KEY, INTERACTIVE_API_SECRET, INTERACTIVE_XTS_API_BASE_URL]):
    logging.error("[ERROR] Missing one or more required credentials in config file.")
    sys.exit(1)

# --- Step 6: Initialize XTSConnect ---
logging.info(f"[INFO] Logging in using credentials from {config_file} ...")

xt = XTSConnect(
    INTERACTIVE_API_KEY,
    INTERACTIVE_API_SECRET,
    "WEBAPI",
    INTERACTIVE_XTS_API_BASE_URL
)

logging.info("[SUCCESS] XTSConnect initialized successfully.")

xt._set_common_variables(token, isInvestorClient=True)

# ---- PLACE ORDER SECTION ----

# -------------------------------------------------------------
# Function: place a single leg
# -------------------------------------------------------------
def place_single_leg(xt, uid, leg):
    """Place one option leg with independent parameters."""

    strike      = leg["Strike"]
    qty         = int(leg["Qty"])
    side        = leg["Side"].upper()          # BUY / SELL
    opt         = leg["OptionType"].upper()    # CE / PE
    expiry_raw  = leg["Expiry"]                # YYYY-MM-DD
    expiry      = expiry_raw   # YYYYMMDD

    # Exchange Instrument ID is now passed from UI
    exchange_inst_id = leg.get("exchangeInstrumentID")
    
    if not exchange_inst_id:
        logging.error(f"[ERROR] Missing exchangeInstrumentID for leg: {leg}")
        return {"type": "error", "description": "Missing exchangeInstrumentID"}

    logging.info(f"Using ExchangeInstrumentID = {exchange_inst_id}")

    logging.info(f"[EXEC] {side} {opt} {strike} {expiry} Qty={qty}")

    #---------------- XTS Place Order Call ----------------
    try:
        response = xt.place_order(
            exchangeSegment       = xt.EXCHANGE_NSEFO,
            exchangeInstrumentID  = exchange_inst_id,
            productType           = xt.PRODUCT_NRML,
            orderType             = xt.ORDER_TYPE_MARKET,
            orderSide             = xt.TRANSACTION_TYPE_BUY if side == "BUY" else xt.TRANSACTION_TYPE_SELL,
            timeInForce           = xt.VALIDITY_DAY,
            disclosedQuantity     = 0,
            orderQuantity         = qty,
            limitPrice            = 0,
            stopPrice             = 0,
            orderUniqueIdentifier = f"LEG-{int(time.time()*1000)}",
            clientID              = uid
        )
        logging.info(f"[XTS Response] {response}")
        return response
    except Exception as e:
        logging.error(f"[ERROR] Failed to place order: {e}")
        return {"type": "error", "description": str(e)}

# -------------------------------------------------------------
# Function : Multi Place Order 
# -------------------------------------------------------------
def process_multi_leg_order(xt, redis_client, uid, order_key, flag_key, status_key, msg_key):
    """Reads multi-leg order from Redis, sorts legs, executes BUY before SELL."""

    # Mark as processing
    redis_client.hset(uid, mapping={flag_key: "processing", status_key: "PROCESSING"})

    # Load legs JSON from Redis
    legs_raw = redis_client.hget(uid, order_key)
    if not legs_raw:
        logging.error(f"[ERROR] No {order_key} found in Redis for multi-order")
        redis_client.hset(uid, mapping={status_key: "FAILED", msg_key: f"No {order_key} found"})
        return

    try:
        legs = json.loads(legs_raw)

        logging.info(f"[INFO] Received Legs from {order_key}:")
        for leg in legs:
            logging.info(leg)

        # BUY legs first → SELL legs later
        buy_legs  = [leg for leg in legs if leg["Side"].upper() == "BUY"]
        sell_legs = [leg for leg in legs if leg["Side"].upper() == "SELL"]

        results = []

        logging.info("\n[INFO] Executing BUY Legs:")
        for leg in buy_legs:
            res = place_single_leg(xt, uid, leg)
            results.append(res)

        # Wait 1 second before SELL legs
        time.sleep(1)

        logging.info("\n[INFO] Executing SELL Legs:")
        for leg in sell_legs:
            res = place_single_leg(xt, uid, leg)
            results.append(res)
        
        # Check if any failed
        errors = [r for r in results if r.get("type") != "success"]
        if errors:
            msg = f"Completed with {len(errors)} errors. First error: {errors[0].get('description', 'Unknown')}"
            redis_client.hset(uid, mapping={status_key: "FAILED", msg_key: msg})
        else:
            redis_client.hset(uid, mapping={status_key: "SUCCESS", msg_key: "All legs placed successfully"})

    except Exception as e:
        logging.error(f"Error processing multi legs: {e}")
        redis_client.hset(uid, mapping={status_key: "FAILED", msg_key: str(e)})
    finally:
        # Mark as fetched/done
        redis_client.hset(uid, mapping={flag_key: "fetched"})
        # Clear legs to prevent re-execution
        redis_client.hdel(uid, order_key)

# -------------------------------------------------------------
# Function : Login Handler
# -------------------------------------------------------------
def process_login_request(xt, redis_client, uid):
    """Handles login request from Redis."""
    logging.info("[LOGIN] Processing login request...")
    redis_client.hset(uid, mapping={"LOGIN": "processing", "LOGIN_STATUS": "PROCESSING"})
    
    try:
        xt = XTSConnect(
    INTERACTIVE_API_KEY,
    INTERACTIVE_API_SECRET,
    "WEBAPI",
    INTERACTIVE_XTS_API_BASE_URL
)
        # Perform login
        resp = xt.interactive_login()
        logging.info(f"[LOGIN] Response: {resp}")
        
        if resp and len(resp) > 25: # Simple validation for token
             # Save token
            with open(token_file, "w") as f:
                f.write(resp)
            logging.info(f"[SUCCESS] Token stored at: {token_file}")
            
            # Update current session
            xt._set_common_variables(resp, isInvestorClient=True)
            
            redis_client.hset(uid, mapping={"LOGIN_STATUS": "SUCCESS", "LOGIN_MESSAGE": "Token generated successfully"})
        else:
             redis_client.hset(uid, mapping={"LOGIN_STATUS": "FAILED", "LOGIN_MESSAGE": "Invalid token received"})

    except Exception as e:
        logging.error(f"[LOGIN] Error: {e}")
        redis_client.hset(uid, mapping={"LOGIN_STATUS": "FAILED", "LOGIN_MESSAGE": str(e)})
    finally:
        redis_client.hset(uid, mapping={"LOGIN": "fetched"})


while True:
    try:
        # ---- LOGIN SECTION ----
        login_flag = redis_client.hget(uid, "LOGIN")
        if login_flag == "requested":
            process_login_request(xt, redis_client, uid)

        # ---- BALANCE SECTION ----
        # Get the value of "Balance" field from Redis for this UID
        bal = redis_client.hget(uid, "BALANCE")
        # logging.info(f"BalanceFlag is {bal}")

        # If "Balance" is marked as 'requested', update it to 'fetched'
        if bal == "requested":
            settings = {"BALANCE": "fetched"}
            redis_client.hset(uid, mapping=settings)
            resp = xt.get_balance(clientID=uid)
            logging.info(f"Response for get_balance: {resp}")
            

            # make sure we have a dict
            if isinstance(resp, (str, bytes, bytearray)):
                resp = json.loads(resp)

            if resp.get("type") == "success" and "result" in resp:
                balance = resp["result"].get("BalanceList", [])
        
                # Parse nested structure and extract only needed fields
                parsed_data = []
                for item in balance:
                    try:
                        limit_obj = item.get("limitObject")
                        if isinstance(limit_obj, str):
                            limit_obj = json.loads(limit_obj.replace("'", '"'))  # handle string dict
                        
                        rms = limit_obj.get("RMSSubLimits", {}) if limit_obj else {}
                        parsed_data.append({
                            "cashAvailable": rms.get("cashAvailable"),
                            "netMarginAvailable": rms.get("netMarginAvailable"),
                            "marginUtilized": rms.get("marginUtilized"),
                        })
                    except Exception as e:
                        logging.error(f"[ERROR] parsing balance item: {e}")
                        parsed_data.append({
                            "cashAvailable": None,
                            "netMarginAvailable": None,
                            "marginUtilized": None,
                        })
                
                # Convert to DataFrame and save
                df = pd.DataFrame(parsed_data)
                df.to_csv(balance_file, index=False)
                logging.info("Balance Request successful — cleaned data written to CSV.")



        # ---- NETWISE POSITION SECTION ----
        # Get the value of "position" field from Redis for this UID
        net_position = redis_client.hget(uid, "POSITION")
        # logging.info(f"Net Position Flag is {net_position}")

        # If "position" is 'requested', update it to 'fetched'
        if net_position == "requested":
            settings = {"POSITION": "fetched"}
            redis_client.hset(uid, mapping=settings)

            response = xt.get_position_netwise(clientID= uid)
            logging.info(f"Position by Net: {response}")

            resp = response  # whatever you got

            # make sure we have a dict
            if isinstance(resp, (str, bytes, bytearray)):
                resp = json.loads(resp)

            if resp.get("type") == "success" and "result" in resp:
                positions = resp["result"].get("positionList", [])
                df = pd.DataFrame(positions)
                # Save DataFrame to CSV
                df.to_csv(position_file, index=False)
                logging.info("Position Request successful")
            else:
                logging.warning("Position Request not successful")
        

        # ---- ORDER BOOK SECTION ----
        # Get the value of "OrderBook" field from Redis for this UID
        order_book = redis_client.hget(uid, "ORDERBOOK")
        # logging.info(f"Order Books Flag is {order_book}")

        # If "OrderBook" is 'requested', update it to 'fetched'
        if order_book == "requested":
            settings = {"ORDERBOOK": "fetched"}
            redis_client.hset(uid, mapping=settings)

            response = xt.get_order_book(clientID= uid)
            logging.info(f"Order Book: {response}")
            
            # make sure we have a dict
            if isinstance(response, (str, bytes, bytearray)):
                response = json.loads(response)

            if response.get("type") == "success" and "result" in response:
                orderbook = response["result"]
                #get("result", [])
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
                df.to_csv(orderbook_file, index=False)
                logging.info("OrderBook Request successful")

                if DUCKDB_AVAILABLE:
                    push_orderbook(uid, base_dir)
                
            else:
                logging.warning("OrderBook Request not successful")

        # -------------------------------------------------------------
        # Single Order Handler (Independent)
        # -------------------------------------------------------------
        if redis_client.hget(uid, "PLACE_SINGLE") == "requested":
            
            # Set status to processing
            redis_client.hset(uid, mapping={"PLACE_SINGLE": "processing", "STATUS_SINGLE": "PROCESSING"})

            try:
                # Load the list containing 1 leg
                single_leg_json = redis_client.hget(uid, "SINGLE_LEG")
                if not single_leg_json:
                    logging.warning("[SINGLE] No SINGLE_LEG data found.")
                    redis_client.hset(uid, mapping={"STATUS_SINGLE": "FAILED", "MSG_SINGLE": "No data found"})
                else:
                    leg_list = json.loads(single_leg_json)

                    if not leg_list:
                        logging.warning("[SINGLE] No leg found inside SINGLE_LEG list.")
                        redis_client.hset(uid, mapping={"STATUS_SINGLE": "FAILED", "MSG_SINGLE": "No leg found"})
                    else:
                        leg = leg_list[0]   # the only leg
                        logging.info(f"[SINGLE] Executing: {leg}")

                        res = place_single_leg(xt, uid, leg)
                        
                        if res.get("type") == "success":
                            redis_client.hset(uid, mapping={"STATUS_SINGLE": "SUCCESS", "MSG_SINGLE": f"Order Placed: {res.get('result', {}).get('AppOrderID', 'Unknown')}"})
                        else:
                            redis_client.hset(uid, mapping={"STATUS_SINGLE": "FAILED", "MSG_SINGLE": res.get("description", "Unknown Error")})

            except Exception as e:
                logging.error(f"Error processing single leg: {e}")
                redis_client.hset(uid, mapping={"STATUS_SINGLE": "FAILED", "MSG_SINGLE": str(e)})
            finally:
                # Mark as fetched
                redis_client.hset(uid, mapping={"PLACE_SINGLE": "fetched"})
                redis_client.hdel(uid, "SINGLE_LEG")


        # -------------------------------------------------------------
        # Multi Order Handler (Independent)
        # -------------------------------------------------------------
        if redis_client.hget(uid, "PLACE_MULTI") == "requested":
            process_multi_leg_order(
                xt, redis_client, uid, 
                order_key="MULTI_LEGS", 
                flag_key="PLACE_MULTI", 
                status_key="STATUS_MULTI", 
                msg_key="MSG_MULTI"
            )
        # -------------------------------------------------------------
        # NEW: LEVEL CE WATCHER (auto-trigger)
        # -------------------------------------------------------------
        try:
            trigger_state = redis_client.hget(uid, "LEVEL_CE_TRIGGER")

            if trigger_state == "waiting":

                level = float(redis_client.hget(uid, "LEVEL_CE_LEVEL") or 0)
                index = redis_client.hget(uid, "LEVEL_CE_INDEX")

                # Select spot based on index
                if index == "NIFTY":
                    raw_spot = redis_client.get("NF_SPOT")
                elif index == "BANKNIFTY":
                    raw_spot = redis_client.get("BN_SPOT")
                else:
                    logging.warning(f"[LEVEL CE WATCH] Unknown index '{index}'; skipping")
                    continue

                if not raw_spot:
                    logging.info(f"[LEVEL CE WATCH] No spot data for {index}; skipping")
                    continue

                try:
                    spot = float(raw_spot)
                except Exception as e:
                    logging.error(f"[LEVEL CE WATCH] Could not parse spot '{raw_spot}' for {index}: {e}")
                    continue

                # --- NEW: previous spot tracking ---
                prev_raw = redis_client.hget(uid, "LEVEL_CE_PREV_SPOT")
                prev_spot = float(prev_raw) if prev_raw else None

                # Save current spot for next cycle (important!)
                redis_client.hset(uid, "LEVEL_CE_PREV_SPOT", spot)

                # If first cycle → no previous value, skip
                if prev_spot is None:
                    continue

                # --- actual CROSS UP logic ---
                crossed_up = (prev_spot < level) and (spot >= level)

                if crossed_up:
                    logging.info(f"[LEVEL CE TRIGGERED] {index} Crossed UP: Prev={prev_spot}, Spot={spot}, Level={level}")

                    redis_client.hset(uid, "PLACE_LEVEL_CE", "requested")
                    redis_client.hset(uid, "LEVEL_CE_TRIGGER", "triggered")

        except Exception as e:
            logging.error(f"[LEVEL CE WATCH ERROR] {e}")


        # -------------------------------------------------------------
        # Level CE Handler (Independent)
        # -------------------------------------------------------------
        if redis_client.hget(uid, "PLACE_LEVEL_CE") == "requested":
            process_multi_leg_order(
                xt, redis_client, uid, 
                order_key="LEVEL_CE", 
                flag_key="PLACE_LEVEL_CE", 
                status_key="STATUS_LEVEL_CE", 
                msg_key="MSG_LEVEL_CE"
            )
            # Cleanup CE trigger metadata
            redis_client.hdel(uid, "LEVEL_CE_TRIGGER")
            redis_client.hdel(uid, "LEVEL_CE_LEVEL")
            redis_client.hdel(uid, "LEVEL_CE_INDEX")

        # -------------------------------------------------------------
        # NEW: LEVEL PE WATCHER (auto-trigger when spot <= level)
        # -------------------------------------------------------------
        try:
            trigger_state = redis_client.hget(uid, "LEVEL_PE_TRIGGER")

            if trigger_state == "waiting":

                level = float(redis_client.hget(uid, "LEVEL_PE_LEVEL") or 0)
                index = redis_client.hget(uid, "LEVEL_PE_INDEX")

                # Select spot based on index
                if index == "NIFTY":
                    raw_spot = redis_client.get("NF_SPOT")
                elif index == "BANKNIFTY":
                    raw_spot = redis_client.get("BN_SPOT")
                else:
                    logging.warning(f"[LEVEL PE WATCH] Unknown index '{index}'; skipping")
                    continue

                if not raw_spot:
                    logging.info(f"[LEVEL PE WATCH] No spot data for {index}; skipping")
                    continue

                try:
                    spot = float(raw_spot)
                except Exception as e:
                    logging.error(f"[LEVEL PE WATCH] Could not parse spot '{raw_spot}' for {index}: {e}")
                    continue

                # --- NEW: read previous spot ---
                prev_raw = redis_client.hget(uid, "LEVEL_PE_PREV_SPOT")
                prev_spot = float(prev_raw) if prev_raw else None

                # save current spot for next iteration
                redis_client.hset(uid, "LEVEL_PE_PREV_SPOT", spot)

                # first cycle → no previous value available
                if prev_spot is None:
                    continue

                # --- CROSS DOWN logic for PE ---
                crossed_down = (prev_spot > level) and (spot <= level)

                if crossed_down:
                    logging.info(
                        f"[LEVEL PE TRIGGERED] {index} Crossed DOWN: Prev={prev_spot}, Spot={spot}, Level={level}"
                    )

                    redis_client.hset(uid, "PLACE_LEVEL_PE", "requested")
                    redis_client.hset(uid, "LEVEL_PE_TRIGGER", "triggered")

        except Exception as e:
            logging.error(f"[LEVEL PE WATCH ERROR] {e}")


        # -------------------------------------------------------------
        # Level PE Handler (Independent)
        # -------------------------------------------------------------
        if redis_client.hget(uid, "PLACE_LEVEL_PE") == "requested":
            process_multi_leg_order(
                xt, redis_client, uid, 
                order_key="LEVEL_PE", 
                flag_key="PLACE_LEVEL_PE", 
                status_key="STATUS_LEVEL_PE", 
                msg_key="MSG_LEVEL_PE"
            )
            # Cleanup PE trigger metadata
            redis_client.hdel(uid, "LEVEL_PE_TRIGGER")
            redis_client.hdel(uid, "LEVEL_PE_LEVEL")
            redis_client.hdel(uid, "LEVEL_PE_INDEX")


        time.sleep(3)

    except Exception as e:
        logging.error(f"Critical error in main loop: {e}")
        time.sleep(5) # Wait before retrying to avoid tight loop on persistent error



    



