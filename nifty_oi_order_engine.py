import time
import json
import logging
import redis
import math
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_SIGN_FILE = os.path.join(BASE_DIR, "oi_last_pece_sign.json")


def load_last_sign():
    if not os.path.exists(LAST_SIGN_FILE):
        return {}
    with open(LAST_SIGN_FILE, "r") as f:
        return json.load(f)


def save_last_sign(data):
    with open(LAST_SIGN_FILE, "w") as f:
        json.dump(data, f, indent=2)

UID = "ITC2766"          # <-- start hardcoded, UI will control later
CHECK_INTERVAL = 3

NIFTY_LOT_SIZE = 60
BANKNIFTY_LOT_SIZE = 25

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# def round_itm_strike(spot: float, direction: str) -> int:
#     if direction == "BULLISH":
#         return int(math.floor(spot / 100) * 100)
#     else:
#         return int(math.ceil(spot / 100) * 100)

def round_otm_strike(spot: float, direction: str) -> int:
    if direction == "BULLISH":
        # SELL PE below spot
        return int(math.floor(spot / 100) * 100)
    else:
        # SELL CE above spot
        return int(math.ceil(spot / 100) * 100)

def main():
    logging.info("OI Order Engine started")

    while True:
        try:
            # ---- Engine ON/OFF ----
            enabled = r.hget(UID, "OI_ENGINE_ENABLED")
            if enabled != "ON":
                r.hset(UID, "OI_ENGINE_STATUS", "IDLE")
            else:
                r.hset(UID, "OI_ENGINE_STATUS", "RUNNING")

                # ---- Read signal ----
                signal_data = r.hgetall("NIFTY_OI_SIGNAL")
                if not signal_data:
                    pass  # Just skip to next check

                elif signal_data.get("status") not in ("NEW", "NOTIFIED"):
                    pass
                
                else: 
                    direction = signal_data.get("signal")
                    pe_ce = float(signal_data.get("pe_ce"))

                    # ---- Read and validate NF_SPOT ----
                    spot_raw = r.get("NF_SPOT")
                    try:
                        if not spot_raw:
                            raise ValueError("NF_SPOT key missing or empty")
                        spot = float(spot_raw)
                    except (ValueError, TypeError) as e:
                        # Fail fast: mark signal as failed, set error state
                        logging.error(f"NF_SPOT validation failed: {e}")
                        r.hset("NIFTY_OI_SIGNAL", "status", "FAILED_NO_SPOT")
                        r.hset(UID, "OI_ENGINE_STATUS", "ERROR")
                        r.hset(UID, "MSG_OI_CROSSOVER", "NF_SPOT not available")
                    else:
                        # strike = round_itm_strike(spot, direction)
                        # option_type = "CE" if direction == "BULLISH" else "PE"
                        strike = round_otm_strike(spot, direction)
                        option_type = "PE" if direction == "BULLISH" else "CE"

                        lots = int(r.hget(UID, "OI_NIFTY_LOTS") or 1)
                        qty = lots * NIFTY_LOT_SIZE

                        # ---- Build order payload ----
                        order_payload = {
                            "Index": "NIFTY",
                            "OrderType": "NRML",
                            "Qty": qty,
                            "Side": "SELL",
                            "Expiry": "2026-01-27",
                            "Strike": strike,
                            "OptionType": option_type,
                            "strategy": "OI_CROSSOVER",
                            "spot": spot,
                            "pe_ce": pe_ce,
                            "direction": direction
                        }

                        # ---- Publish order intent ----
                        r.hset(
                            UID,
                            mapping={
                                "PLACE_OI_CROSSOVER": "requested",
                                "OI_CROSSOVER_ORDER": json.dumps(order_payload),
                                "STATUS_OI_CROSSOVER": "PROCESSING"
                            }
                        )

                        # ---- Store last signal/order for UI ----
                        r.hset(UID, "OI_ENGINE_LAST_SIGNAL", json.dumps(signal_data))
                        r.hset(UID, "OI_ENGINE_LAST_ORDER", json.dumps(order_payload))

                        # NOTE: Signal consumption moved to order_service.py (only on success)

                        logging.info(
                            f"[OI ORDER] {direction} → SELL {strike} {option_type} | Spot={spot}"
                        )

            # ===============================
            # BANKNIFTY OI ORDER EXECUTION
            # ===============================
            enabled_bn = r.hget(UID, "BN_OI_ENGINE_ENABLED")
            if enabled_bn != "ON":
                r.hset(UID, "BN_OI_ENGINE_STATUS", "IDLE")
            else:
                r.hset(UID, "BN_OI_ENGINE_STATUS", "RUNNING")

                signal_data_bn = r.hgetall("BANKNIFTY_OI_SIGNAL")
                if not signal_data_bn:
                    pass

                elif signal_data_bn.get("status") not in ("NEW", "NOTIFIED"):
                    pass
                
                else:
                    direction_bn = signal_data_bn.get("signal")
                    pe_ce_bn = float(signal_data_bn.get("pe_ce"))

                    spot_raw = r.get("BN_SPOT")
                    try:
                        if not spot_raw:
                            raise ValueError("BN_SPOT key missing or empty")
                        spot_bn = float(spot_raw)
                    except (ValueError, TypeError) as e:
                        logging.error(f"BN_SPOT validation failed: {e}")
                        r.hset("BANKNIFTY_OI_SIGNAL", "status", "FAILED_NO_SPOT")
                        r.hset(UID, "BN_OI_ENGINE_STATUS", "ERROR")
                        r.hset(UID, "MSG_BN_OI_CROSSOVER", "BN_SPOT not available")
                    else:
                        strike = round_otm_strike(spot_bn, direction_bn)
                        option_type = "PE" if direction_bn == "BULLISH" else "CE"

                        lots_bn = int(r.hget(UID, "OI_BANKNIFTY_LOTS") or 1)
                        qty_bn = lots_bn * BANKNIFTY_LOT_SIZE

                        order_payload = {
                            "Index": "BANKNIFTY",
                            "OrderType": "NRML",
                            "Qty": qty_bn,
                            "Side": "SELL",
                            "Expiry": "2026-01-27",
                            "Strike": strike,
                            "OptionType": option_type,
                            "strategy": "OI_CROSSOVER",
                            "spot": spot_bn,
                            "pe_ce": pe_ce_bn,
                            "direction": direction_bn
                        }

                        r.hset(
                            UID,
                            mapping={
                                "PLACE_BN_OI_CROSSOVER": "requested",
                                "BN_OI_CROSSOVER_ORDER": json.dumps(order_payload),
                                "STATUS_BN_OI_CROSSOVER": "PROCESSING"
                            }
                        )

                        r.hset(UID, "BN_OI_ENGINE_LAST_SIGNAL", json.dumps(signal_data_bn))
                        r.hset(UID, "BN_OI_ENGINE_LAST_ORDER", json.dumps(order_payload))

                        logging.info(
                            f"[BN OI ORDER] {direction_bn} → SELL {strike} {option_type} | Spot={spot_bn}"
                        )


        except Exception as e:
            logging.exception("OI Order Engine error")
            r.hset(UID, "OI_ENGINE_STATUS", "ERROR")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
