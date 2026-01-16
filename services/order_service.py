"""Order service for handling all order placement logic."""
import logging
import json
import time
from utils.telegram_notifier import send_telegram
from utils.oi_positions import (
    get_position,
    add_position,
    remove_position
)




class OrderService:
    """Handles all order placement operations."""
    
    def __init__(self, xt, redis_client, uid):
        """Initialize order service.
        
        Args:
            xt: XTS client instance
            redis_client: Redis client instance
            uid: User ID
        """
        self.xt = xt
        self.redis_client = redis_client
        self.uid = uid
    
    def place_single_leg(self, leg):
        """Place one option leg with independent parameters.
        
        Args:
            leg: Dictionary containing leg parameters (Strike, Qty, Side, OptionType, Expiry, exchangeInstrumentID)
            
        Returns:
            dict: Response from XTS API
        """
        strike = leg["Strike"]
        qty = int(leg["Qty"])

        exec_type = leg.get("ExecutionType", "MARKET")
        if exec_type == "LIMIT":
            order_type = self.xt.ORDER_TYPE_LIMIT
            limit_price = float(leg.get("LimitPrice", 0))
        else:
            order_type = self.xt.ORDER_TYPE_MARKET
            limit_price = 0

        side = leg["Side"].upper()  # BUY / SELL
        opt = leg["OptionType"].upper()  # CE / PE
        expiry_raw = leg["Expiry"]  # YYYY-MM-DD
        expiry = expiry_raw  # YYYYMMDD
        
        # Exchange Instrument ID is now passed from UI
        exchange_inst_id = leg.get("exchangeInstrumentID")
        
        if not exchange_inst_id:
            logging.error(f"[ERROR] Missing exchangeInstrumentID for leg: {leg}")
            return {"type": "error", "description": "Missing exchangeInstrumentID"}
        
        logging.info(f"Using ExchangeInstrumentID = {exchange_inst_id}")
        logging.info(f"[EXEC] {side} {opt} {strike} {expiry} Qty={qty}")
        
        # XTS Place Order Call
        try:
            response = self.xt.place_order(
                exchangeSegment=self.xt.EXCHANGE_NSEFO,
                exchangeInstrumentID=exchange_inst_id,
                productType=self.xt.PRODUCT_NRML,
                orderType=order_type,
                orderSide=self.xt.TRANSACTION_TYPE_BUY if side == "BUY" else self.xt.TRANSACTION_TYPE_SELL,
                timeInForce=self.xt.VALIDITY_DAY,
                disclosedQuantity=0,
                orderQuantity=qty,
                limitPrice=limit_price,
                stopPrice=0,
                orderUniqueIdentifier=f"LEG-{int(time.time()*1000)}",
                clientID=self.uid
            )
            logging.info(f"[XTS Response] {response}")
            return response
        except Exception as e:
            logging.error(f"[ERROR] Failed to place order: {e}")
            return {"type": "error", "description": str(e)}

    def _execute_squareoff_leg(self, leg):
        try:
            exch_id = int(leg["exchangeInstrumentID"])
            qty = int(leg["Quantity"])
            side = leg["Side"]

            logging.info(
                f"[SQUAREOFF] {side} {qty} {leg.get('TradingSymbol', '')}"
            )

            self.xt.place_order(
                exchangeSegment=self.xt.EXCHANGE_NSEFO,
                exchangeInstrumentID=exch_id,
                productType=self.xt.PRODUCT_NRML,
                orderType=self.xt.ORDER_TYPE_MARKET,
                orderSide=(
                    self.xt.TRANSACTION_TYPE_BUY
                    if side == "BUY"
                    else self.xt.TRANSACTION_TYPE_SELL
                ),
                timeInForce=self.xt.VALIDITY_DAY,
                disclosedQuantity=0,
                orderQuantity=qty,
                limitPrice=0,
                stopPrice=0,
                orderUniqueIdentifier=f"SQOFF-{int(time.time()*1000)}",
                clientID=self.uid
            )

            logging.info(f"[SQUAREOFF] SUCCESS {exch_id}")
            # ---- TELEGRAM NOTIFICATION ----
            try:
                msg = (
                    "🚨 <b>SQUARE OFF EXECUTED</b>\n\n"
                    f"Symbol: {leg.get('TradingSymbol','')}\n"
                    f"Side: {side}\n"
                    f"Quantity: {qty}\n"
                )
                send_telegram(msg)
            except Exception as e:
                logging.error(f"[TELEGRAM ERROR] Square-off notify failed: {e}")

        except Exception as e:
            logging.error(
                f"[SQUAREOFF ERROR] {leg} → {e}",
                exc_info=True
            )

    def _process_multi_leg_order(self, order_key, flag_key, status_key, msg_key):
        """Process multi-leg order from Redis.
        
        Args:
            order_key: Redis key for order legs
            flag_key: Redis key for processing flag
            status_key: Redis key for status
            msg_key: Redis key for message
        """
        # Mark as processing
        self.redis_client.hset(self.uid, mapping={flag_key: "processing", status_key: "PROCESSING"})
        
        # Load legs JSON from Redis
        legs_raw = self.redis_client.hget(self.uid, order_key)
        if not legs_raw:
            logging.error(f"[ERROR] No {order_key} found in Redis for multi-order")
            self.redis_client.hset(self.uid, mapping={status_key: "FAILED", msg_key: f"No {order_key} found"})
            return
        
        try:
            legs = json.loads(legs_raw)
            
            logging.info(f"[INFO] Received Legs from {order_key}:")
            for leg in legs:
                logging.info(leg)
            
            # BUY legs first → SELL legs later
            buy_legs = [leg for leg in legs if leg["Side"].upper() == "BUY"]
            sell_legs = [leg for leg in legs if leg["Side"].upper() == "SELL"]
            
            results = []
            
            logging.info("\n[INFO] Executing BUY Legs:")
            for leg in buy_legs:
                res = self.place_single_leg(leg)
                results.append(res)
            
            # Wait 1 second before SELL legs
            time.sleep(1)
            
            logging.info("\n[INFO] Executing SELL Legs:")
            for leg in sell_legs:
                res = self.place_single_leg(leg)
                results.append(res)
            
            # Check if any failed
            errors = [r for r in results if r.get("type") != "success"]
            if errors:
                msg = f"Completed with {len(errors)} errors. First error: {errors[0].get('description', 'Unknown')}"
                self.redis_client.hset(self.uid, mapping={status_key: "FAILED", msg_key: msg})
                send_telegram(f"Multi-Leg Order FAILED ❌\nReason: {msg}")

            else:
                self.redis_client.hset(self.uid, mapping={status_key: "SUCCESS", msg_key: "All legs placed successfully"})
                send_telegram(f"Multi-Leg Order Executed ✔️ ({order_key})")

        
        except Exception as e:
            logging.error(f"Error processing multi legs: {e}")
            self.redis_client.hset(self.uid, mapping={status_key: "FAILED", msg_key: str(e)})
            send_telegram(f"Multi-Leg Order FAILED ❌\nException: {str(e)}")
        finally:
            # Mark as fetched/done
            self.redis_client.hset(self.uid, mapping={flag_key: "fetched"})
            # Clear legs to prevent re-execution
            self.redis_client.hdel(self.uid, order_key)
    
    def process_single_order(self):
        """Process single leg order if requested."""
        if self.redis_client.hget(self.uid, "PLACE_SINGLE") != "requested":
            return
        
        # Set status to processing
        self.redis_client.hset(self.uid, mapping={"PLACE_SINGLE": "processing", "STATUS_SINGLE": "PROCESSING"})
        
        try:
            # Load the list containing 1 leg
            single_leg_json = self.redis_client.hget(self.uid, "SINGLE_LEG")
            if not single_leg_json:
                logging.warning("[SINGLE] No SINGLE_LEG data found.")
                self.redis_client.hset(self.uid, mapping={"STATUS_SINGLE": "FAILED", "MSG_SINGLE": "No data found"})
            else:
                leg_list = json.loads(single_leg_json)
                
                if not leg_list:
                    logging.warning("[SINGLE] No leg found inside SINGLE_LEG list.")
                    self.redis_client.hset(self.uid, mapping={"STATUS_SINGLE": "FAILED", "MSG_SINGLE": "No leg found"})
                else:
                    leg = leg_list[0]  # the only leg
                    logging.info(f"[SINGLE] Executing: {leg}")
                    
                    res = self.place_single_leg(leg)
                    
                    if res.get("type") == "success":
                        self.redis_client.hset(self.uid, mapping={
                            "STATUS_SINGLE": "SUCCESS",
                            "MSG_SINGLE": f"Order Placed: {res.get('result', {}).get('AppOrderID', 'Unknown')}"
                        })
                        send_telegram(
                            f"Order Executed ✔️\n"
                            f"Type: Single\n"
                            f"Symbol: {leg['Index']} {leg['Strike']}{leg['OptionType']}\n"
                            f"Side: {leg['Side']}\n"
                            f"Qty: {leg['Qty']}"
                        )

                    else:
                        self.redis_client.hset(self.uid, mapping={
                            "STATUS_SINGLE": "FAILED",
                            "MSG_SINGLE": res.get("description", "Unknown Error")
                        })
                        send_telegram(
                            f"Order FAILED ❌\n"
                            f"{res.get('description')}"
                        )

        
        except Exception as e:
            logging.error(f"Error processing single leg: {e}")
            self.redis_client.hset(self.uid, mapping={"STATUS_SINGLE": "FAILED", "MSG_SINGLE": str(e)})
        finally:
            # Mark as fetched
            self.redis_client.hset(self.uid, mapping={"PLACE_SINGLE": "fetched"})
            self.redis_client.hdel(self.uid, "SINGLE_LEG")
    
    def process_multi_order(self):
        """Process multi-leg order if requested."""
        if self.redis_client.hget(self.uid, "PLACE_MULTI") == "requested":
            self._process_multi_leg_order(
                order_key="MULTI_LEGS",
                flag_key="PLACE_MULTI",
                status_key="STATUS_MULTI",
                msg_key="MSG_MULTI"
            )
    
    def process_level_ce_order(self):
        """Process level CE order if requested."""
        if self.redis_client.hget(self.uid, "PLACE_LEVEL_CE") == "requested":
            self._process_multi_leg_order(
                order_key="LEVEL_CE",
                flag_key="PLACE_LEVEL_CE",
                status_key="STATUS_LEVEL_CE",
                msg_key="MSG_LEVEL_CE"
            )
            # Cleanup CE trigger metadata
            self.redis_client.hdel(self.uid, "LEVEL_CE_TRIGGER")
            self.redis_client.hdel(self.uid, "LEVEL_CE_LEVEL")
            self.redis_client.hdel(self.uid, "LEVEL_CE_INDEX")
    
    def process_level_pe_order(self):
        """Process level PE order if requested."""
        if self.redis_client.hget(self.uid, "PLACE_LEVEL_PE") == "requested":
            self._process_multi_leg_order(
                order_key="LEVEL_PE",
                flag_key="PLACE_LEVEL_PE",
                status_key="STATUS_LEVEL_PE",
                msg_key="MSG_LEVEL_PE"
            )
            # Cleanup PE trigger metadata
            self.redis_client.hdel(self.uid, "LEVEL_PE_TRIGGER")
            self.redis_client.hdel(self.uid, "LEVEL_PE_LEVEL")
            self.redis_client.hdel(self.uid, "LEVEL_PE_TRIGGER")
            self.redis_client.hdel(self.uid, "LEVEL_PE_LEVEL")
            self.redis_client.hdel(self.uid, "LEVEL_PE_INDEX")
    
    def place_equity_order(self, order_data):
        """Place an equity (NSECM) order.
        
        Args:
            order_data: Payload from UI via PLACE_EQUITY
        """
        symbol = order_data["symbol"]
        qty = int(order_data["qty"])
        side = order_data["side"].upper()
        product = order_data["product"]
        exchange_inst_id = order_data["exchangeInstrumentID"]
        
        logging.info(f"[EXEC-EQUITY] {side} {symbol} Qty={qty} Product={product}")
        
        try:
            response = self.xt.place_order(
                exchangeSegment=self.xt.EXCHANGE_NSECM,
                exchangeInstrumentID=exchange_inst_id,
                productType=self.xt.PRODUCT_MIS if product == "MIS" else "CNC",
                orderType=self.xt.ORDER_TYPE_MARKET,
                orderSide=self.xt.TRANSACTION_TYPE_BUY if side == "BUY" else self.xt.TRANSACTION_TYPE_SELL,
                timeInForce=self.xt.VALIDITY_DAY,
                disclosedQuantity=0,
                orderQuantity=qty,
                limitPrice=0,
                stopPrice=0,
                orderUniqueIdentifier=f"EQ-{int(time.time()*1000)}",
                clientID=self.uid
            )
            logging.info(f"[XTS Response] {response}")
            return response
        except Exception as e:
            logging.error(f"[ERROR] Failed to place equity order: {e}")
            return {"type": "error", "description": str(e)}

    def process_equity_order(self):
        """Process equity order from Redis per-user hash (PLACE_EQUITY field)."""
        # Check if requested
        status_flag = self.redis_client.hget(self.uid, "PLACE_EQUITY")
        if status_flag != "requested":
            return
        
        # Fetch payload
        raw_order = self.redis_client.hget(self.uid, "EQUITY_ORDER")
        
        # Atomic Claim: Delete the EQUITY_ORDER field from the user hash.
        # hdel returns number of fields deleted. If 0, it was already taken.
        if self.redis_client.hdel(self.uid, "EQUITY_ORDER") == 0:
             # If return 0, it means the field didn 't exist (already claimed).
             logging.info("Race condition detected: failed to claim EQUITY_ORDER (already deleted/claimed).")
             return
             
        # If we are here, we successfully deleted the key.
        # But wait, do we have 'raw_order'?
        # If raw_order was None, hdel would likely be 0 too.
        # if raw_order was not None, and hdel succeeded, we are good.
        # Use raw_order.
        
        if not raw_order:
            # Edge case: key existed when we hdel'd? No, if it didn't exist hdel is 0.
            # So if hdel is 1, raw_order *should* be there unless we failed to read it properly?
            # Or if it was set to empty string?
            # Let's assume if hdel==1, we "won" the right to process.
            # But we need the data.
            # If we acted on "requested" flag, we assume data is there.
            logging.error("Claimed EQUITY_ORDER but payload was empty/None?")
            # Restore status?
            self.redis_client.hset(self.uid, mapping={"PLACE_EQUITY": "failed_no_payload", "STATUS_EQUITY": "FAILED"})
            return

        self.redis_client.hset(self.uid, mapping={"PLACE_EQUITY": "processing"})
        
        try:
            order_data = json.loads(raw_order)
            
            # Verify state
            if order_data.get("state") != "requested":
                 # Technically we claimed it, but state is wrong. Abort.
                 return
            
            # Execute
            res = self.place_equity_order(order_data)
            
            if res.get("type") == "success":
                app_order_id = res.get('result', {}).get('AppOrderID', 'Unknown')
                msg = f"SUCCESS: Order Placed {app_order_id}"
                self.redis_client.hset(self.uid, mapping={"STATUS_EQUITY": msg})
                
                # Notify Telegram
                symbol = order_data.get("symbol", "UNKNOWN")
                side = order_data.get("side", "UNKNOWN")
                qty = order_data.get("qty", 0)
                product = order_data.get("product", "UNKNOWN")
                
                send_telegram(f"[EQUITY] {side} {symbol} Qty={qty} ({product}) – SUCCESS")
            else:
                err_desc = res.get("description", "Unknown Error")
                msg = f"FAILED: {err_desc}"
                # Update status in hash
                self.redis_client.hset(self.uid, mapping={"STATUS_EQUITY": msg})
                send_telegram(f"[EQUITY] Order FAILED ❌\n{msg}")

        except Exception as e:
            logging.error(f"Error processing equity order: {e}")
            self.redis_client.hset(self.uid, mapping={"STATUS_EQUITY": f"FAILED: {str(e)}"})
        finally:
            # Set fetched
            self.redis_client.hset(self.uid, mapping={"PLACE_EQUITY": "fetched"})

    def process_oi_crossover_order(self):
        """Process OI crossover auto trade."""
        if self.redis_client.hget(self.uid, "PLACE_OI_CROSSOVER") != "requested":
            return

        self.redis_client.hset(
            self.uid,
            mapping={
                "PLACE_OI_CROSSOVER": "processing",
                "STATUS_OI_CROSSOVER": "PROCESSING"
            }
        )

        try:
            raw = self.redis_client.hget(self.uid, "OI_CROSSOVER_ORDER")
            if not raw:
                raise ValueError("OI_CROSSOVER_ORDER missing")

            leg = json.loads(raw)
            index = leg["Index"]
            new_direction = leg["direction"]

            logging.info(f"[OI AUTO] Executing: {leg}")

            # ==================================================
            # 🔴 EXIT-FIRST / POSITION AWARE LOGIC (NEW)
            # ==================================================
            live_pos = get_position(index)

            if live_pos:
                existing_direction = live_pos["direction"]

                # Same direction → ignore signal
                if existing_direction == new_direction:
                    self.redis_client.hset("NIFTY_OI_SIGNAL", "status", "CONSUMED")
                    self.redis_client.hset(
                        self.uid,
                        mapping={
                            "STATUS_OI_CROSSOVER": "IGNORED",
                            "MSG_OI_CROSSOVER": "Same direction position already exists"
                        }
                    )
                    return

                # Opposite direction → EXIT FIRST
                exit_legs = []
                for old_leg in live_pos["legs"]:
                    exit_leg = old_leg.copy()
                    exit_leg["Side"] = "BUY" if old_leg["Side"] == "SELL" else "SELL"

                    redis_key = (
                        f"{exit_leg['Index']}_"
                        f"{exit_leg['Expiry']}_"
                        f"{exit_leg['OptionType']}_"
                        f"{exit_leg['Strike']}"
                    )

                    exch_id = self.redis_client.hget("XTS_INSTR", redis_key)
                    if exch_id is None:
                        raise ValueError(f"exchangeInstrumentID not found for exit {redis_key}")

                    exit_leg["exchangeInstrumentID"] = int(exch_id)
                    exit_legs.append(exit_leg)

                # Fire EXIT as multi-leg
                self.redis_client.hset(
                    self.uid,
                    mapping={
                        "PLACE_MULTI": "requested",
                        "MULTI_LEGS": json.dumps(exit_legs),
                        "STATUS_MULTI": "PROCESSING"
                    }
                )

                # Block until exit completes
                timeout = 15
                start = time.time()
                while time.time() - start < timeout:
                    status = self.redis_client.hget(self.uid, "STATUS_MULTI")
                    if status == "SUCCESS":
                        remove_position(index)
                        break
                    if status == "FAILED":
                        raise ValueError("Exit failed. Aborting new entry.")
                    time.sleep(0.5)

            # ==================================================
            # 🟢 NORMAL SELL FLOW (UNCHANGED)
            # ==================================================
            redis_key = (
                f"{leg['Index']}_"
                f"{leg['Expiry']}_"
                f"{leg['OptionType']}_"
                f"{leg['Strike']}"
            )

            exchange_inst_id = self.redis_client.hget("XTS_INSTR", redis_key)
            if exchange_inst_id is None:
                raise ValueError(f"exchangeInstrumentID not found for {redis_key}")

            leg["exchangeInstrumentID"] = int(exchange_inst_id)

            res = self.place_single_leg(leg)

            if res.get("type") == "success":
                self.redis_client.hset("NIFTY_OI_SIGNAL", "status", "CONSUMED")

                # 🆕 WRITE LIVE POSITION
                add_position(
                    index=index,
                    direction=new_direction,
                    legs=[leg]
                )

                self.redis_client.hset(
                    self.uid,
                    mapping={
                        "STATUS_OI_CROSSOVER": "SUCCESS",
                        "MSG_OI_CROSSOVER": f"Order placed: {res.get('result', {}).get('AppOrderID')}"
                    }
                )
                send_telegram(
                    f"[OI AUTO] ✔️ {leg['direction']} {leg['Strike']}{leg['OptionType']}"
                )

            else:
                send_telegram(f"[OI AUTO] ❌ Failed: {res.get('description')}")
                raise ValueError(res.get("description", "Unknown error"))

        except Exception as e:
            logging.error(f"[OI AUTO ERROR] {e}")
            self.redis_client.hset(
                self.uid,
                mapping={
                    "STATUS_OI_CROSSOVER": "FAILED",
                    "MSG_OI_CROSSOVER": str(e)
                }
            )

        finally:
            self.redis_client.hset(self.uid, "PLACE_OI_CROSSOVER", "fetched")
            self.redis_client.hdel(self.uid, "OI_CROSSOVER_ORDER")

    def process_bn_oi_crossover_order(self):
        """Process BANKNIFTY OI crossover auto trade."""
        if self.redis_client.hget(self.uid, "PLACE_BN_OI_CROSSOVER") != "requested":
            return

        self.redis_client.hset(
            self.uid,
            mapping={
                "PLACE_BN_OI_CROSSOVER": "processing",
                "STATUS_BN_OI_CROSSOVER": "PROCESSING"
            }
        )

        try:
            raw = self.redis_client.hget(self.uid, "BN_OI_CROSSOVER_ORDER")
            if not raw:
                raise ValueError("BN_OI_CROSSOVER_ORDER missing")

            leg = json.loads(raw)

            logging.info(f"[BN OI AUTO] Executing: {leg}")

            redis_key = (
                f"{leg['Index']}_"
                f"{leg['Expiry']}_"
                f"{leg['OptionType']}_"
                f"{leg['Strike']}"
            )

            exchange_inst_id = self.redis_client.hget("XTS_INSTR", redis_key)
            if exchange_inst_id is None:
                raise ValueError(f"exchangeInstrumentID not found for {redis_key}")

            leg["exchangeInstrumentID"] = int(exchange_inst_id)

            res = self.place_single_leg(leg)

            if res.get("type") == "success":
                # Consume BANKNIFTY signal ONLY on success
                self.redis_client.hset("BANKNIFTY_OI_SIGNAL", "status", "CONSUMED")

                self.redis_client.hset(
                    self.uid,
                    mapping={
                        "STATUS_BN_OI_CROSSOVER": "SUCCESS",
                        "MSG_BN_OI_CROSSOVER": f"Order placed: {res.get('result', {}).get('AppOrderID')}"
                    }
                )
                send_telegram(
                    f"[BN OI AUTO] ✔️ {leg['direction']} {leg['Strike']}{leg['OptionType']}"
                )
            else:
                send_telegram(f"[BN OI AUTO] ❌ Failed: {res.get('description')}")
                raise ValueError(res.get("description", "Unknown error"))

        except Exception as e:
            logging.error(f"[BN OI AUTO ERROR] {e}")
            self.redis_client.hset(
                self.uid,
                mapping={
                    "STATUS_BN_OI_CROSSOVER": "FAILED",
                    "MSG_BN_OI_CROSSOVER": str(e)
                }
            )

        finally:
            # Cleanup so it never re-fires
            self.redis_client.hset(self.uid, "PLACE_BN_OI_CROSSOVER", "fetched")
            self.redis_client.hdel(self.uid, "BN_OI_CROSSOVER_ORDER")

    
    def process_squareoff(self):
        status = self.redis_client.hget(self.uid, "SQUAREOFF_STATUS")
        if status != "REQUESTED":
            return

        self.redis_client.hset(self.uid, "SQUAREOFF_STATUS", "PROCESSING")

        raw = self.redis_client.hget(self.uid, "SQUAREOFF_REQUEST")
        if not raw:
            self.redis_client.hset(self.uid, "SQUAREOFF_STATUS", "FAILED")
            return

        legs = json.loads(raw)

        for leg in legs:
            self._execute_squareoff_leg(leg)

        self.redis_client.hset(self.uid, "SQUAREOFF_STATUS", "DONE")
        self.redis_client.hdel(self.uid, "SQUAREOFF_REQUEST")

    def process_all(self):
        """Process all order types."""
        self.process_single_order()
        self.process_multi_order()
        self.process_level_ce_order()
        self.process_level_pe_order()
        self.process_equity_order()
        self.process_oi_crossover_order()
        self.process_bn_oi_crossover_order()
        self.process_squareoff()

