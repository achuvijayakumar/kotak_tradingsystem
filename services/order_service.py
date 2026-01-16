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
    """Handles all order placement operations using Kotak Neo API."""
    
    def __init__(self, client, redis_client, uid):
        """Initialize order service.
        
        Args:
            client: NeoAPI client instance (authenticated)
            redis_client: Redis client instance
            uid: User ID
        """
        # XTS → KOTAK NEO REPLACEMENT: renamed xt to client
        self.client = client
        self.redis_client = redis_client
        self.uid = uid
    
    def place_single_leg(self, leg):
        """Place one option leg with independent parameters.
        
        Args:
            leg: Dictionary containing leg parameters (Strike, Qty, Side, OptionType, Expiry, tradingSymbol)
            
        Returns:
            dict: Response from Neo API
        """
        # SAFETY CHECK: Ensure client is authenticated
        if self.client is None:
            logging.error("[FATAL] NeoAPI client is None - not authenticated")
            return {"type": "error", "description": "Client not authenticated"}
        
        strike = leg["Strike"]
        qty = int(leg["Qty"])

        exec_type = leg.get("ExecutionType", "MARKET")
        # XTS → KOTAK NEO REPLACEMENT: Neo uses "L" for LIMIT, "MKT" for MARKET
        if exec_type == "LIMIT":
            order_type = "L"
            limit_price = str(leg.get("LimitPrice", 0))
        else:
            order_type = "MKT"
            limit_price = "0"

        side = leg["Side"].upper()  # BUY / SELL
        opt = leg["OptionType"].upper()  # CE / PE
        expiry_raw = leg["Expiry"]  # YYYY-MM-DD
        
        # XTS → KOTAK NEO REPLACEMENT: Use tradingSymbol instead of exchangeInstrumentID
        trading_symbol = leg.get("tradingSymbol")
        
        if not trading_symbol:
            # Fallback: Build from Redis lookup (NEO_INSTR_OPT)
            # XTS → KOTAK NEO REPLACEMENT: Using NEO_INSTR_OPT instead of XTS_INSTR
            redis_key = f"{leg['Index']}_{expiry_raw}_{opt}_{strike}"
            trading_symbol = self.redis_client.hget("NEO_INSTR_OPT", redis_key)
            
        if not trading_symbol:
            logging.error(f"[ERROR] Missing tradingSymbol for leg: {leg}")
            return {"type": "error", "description": "Missing tradingSymbol"}
        
        logging.info(f"Using tradingSymbol = {trading_symbol}")
        logging.info(f"[EXEC] {side} {opt} {strike} {expiry_raw} Qty={qty}")
        
        # XTS → KOTAK NEO REPLACEMENT: Neo place_order call
        try:
            logging.info(f"[ORDER] Placing: symbol={trading_symbol}, side={side}, qty={qty}, type={order_type}")
            
            response = self.client.place_order(
                exchange_segment="nse_fo",
                product="NRML",
                price=limit_price,
                order_type=order_type,
                quantity=str(qty),
                validity="DAY",
                trading_symbol=trading_symbol,
                transaction_type="B" if side == "BUY" else "S",
                amo="NO",
                disclosed_quantity="0",
                market_protection="0",
                pf="N",
                trigger_price="0",
                tag=f"LEG-{int(time.time()*1000)}"
            )
            
            # DEFENSIVE: Log full response for debugging
            logging.info(f"[Neo Response] {json.dumps(response) if isinstance(response, dict) else response}")
            
            # SAFETY CHECK: Handle None response
            if response is None:
                logging.error("[ERROR] Neo API returned None response")
                return {"type": "error", "description": "Neo API returned empty response"}
            
            # SAFETY CHECK: Handle error responses
            if response.get("Error") or response.get("stat") == "Not_Ok":
                error_msg = response.get("Error Message") or response.get("message") or response.get("emsg") or str(response)
                logging.error(f"[ORDER REJECTED] {error_msg}")
                return {"type": "error", "description": error_msg}
            
            # Success path
            order_no = response.get("nOrdNo", "Unknown")
            logging.info(f"[ORDER SUCCESS] Order placed: {order_no}")
            return {"type": "success", "result": response}
                
        except Exception as e:
            logging.error(f"[ORDER EXCEPTION] Failed to place order: {e}", exc_info=True)
            return {"type": "error", "description": str(e)}

    def _execute_squareoff_leg(self, leg):
        try:
            trading_symbol = leg.get("tradingSymbol") or leg.get("TradingSymbol")
            qty = int(leg["Quantity"])
            side = leg["Side"]

            logging.info(
                f"[SQUAREOFF] {side} {qty} {trading_symbol}"
            )

            # XTS → KOTAK NEO REPLACEMENT: Neo place_order for squareoff
            self.client.place_order(
                exchange_segment="nse_fo",
                product="NRML",
                price="0",
                order_type="MKT",
                quantity=str(qty),
                validity="DAY",
                trading_symbol=trading_symbol,
                transaction_type="B" if side == "BUY" else "S",
                amo="NO",
                disclosed_quantity="0",
                market_protection="0",
                pf="N",
                trigger_price="0",
                tag=f"SQOFF-{int(time.time()*1000)}"
            )

            logging.info(f"[SQUAREOFF] SUCCESS {trading_symbol}")
            # ---- TELEGRAM NOTIFICATION ----
            try:
                msg = (
                    "🚨 <b>SQUARE OFF EXECUTED</b>\n\n"
                    f"Symbol: {trading_symbol}\n"
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
                        # XTS → KOTAK NEO REPLACEMENT: Neo uses nOrdNo instead of AppOrderID
                        self.redis_client.hset(self.uid, mapping={
                            "STATUS_SINGLE": "SUCCESS",
                            "MSG_SINGLE": f"Order Placed: {res.get('result', {}).get('nOrdNo', 'Unknown')}"
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
        """Place an equity (nse_cm) order.
        
        Args:
            order_data: Payload from UI via PLACE_EQUITY
        """
        symbol = order_data["symbol"]
        qty = int(order_data["qty"])
        side = order_data["side"].upper()
        product = order_data["product"]
        trading_symbol = order_data.get("tradingSymbol") or symbol
        
        logging.info(f"[EXEC-EQUITY] {side} {symbol} Qty={qty} Product={product}")
        
        # XTS → KOTAK NEO REPLACEMENT: Neo place_order for equity
        try:
            response = self.client.place_order(
                exchange_segment="nse_cm",
                product=product,  # MIS or CNC
                price="0",
                order_type="MKT",
                quantity=str(qty),
                validity="DAY",
                trading_symbol=trading_symbol,
                transaction_type="B" if side == "BUY" else "S",
                amo="NO",
                disclosed_quantity="0",
                market_protection="0",
                pf="N",
                trigger_price="0",
                tag=f"EQ-{int(time.time()*1000)}"
            )
            logging.info(f"[Neo Response] {response}")
            
            if response and not response.get("Error"):
                return {"type": "success", "result": response}
            else:
                return {"type": "error", "description": response.get("Error Message", str(response))}
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
             
        if not raw_order:
            logging.error("Claimed EQUITY_ORDER but payload was empty/None?")
            self.redis_client.hset(self.uid, mapping={"PLACE_EQUITY": "failed_no_payload", "STATUS_EQUITY": "FAILED"})
            return

        self.redis_client.hset(self.uid, mapping={"PLACE_EQUITY": "processing"})
        
        try:
            order_data = json.loads(raw_order)
            
            # Verify state
            if order_data.get("state") != "requested":
                 return
            
            # Execute
            res = self.place_equity_order(order_data)
            
            if res.get("type") == "success":
                # XTS → KOTAK NEO REPLACEMENT: Neo uses nOrdNo instead of AppOrderID
                order_no = res.get('result', {}).get('nOrdNo', 'Unknown')
                msg = f"SUCCESS: Order Placed {order_no}"
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
            # 🔴 EXIT-FIRST / POSITION AWARE LOGIC
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

                    # XTS → KOTAK NEO REPLACEMENT: Using NEO_INSTR_OPT instead of XTS_INSTR
                    trading_sym = self.redis_client.hget("NEO_INSTR_OPT", redis_key)
                    if trading_sym is None:
                        raise ValueError(f"tradingSymbol not found for exit {redis_key}")

                    exit_leg["tradingSymbol"] = trading_sym
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
            # 🟢 NORMAL SELL FLOW
            # ==================================================
            redis_key = (
                f"{leg['Index']}_"
                f"{leg['Expiry']}_"
                f"{leg['OptionType']}_"
                f"{leg['Strike']}"
            )

            # XTS → KOTAK NEO REPLACEMENT: Using NEO_INSTR_OPT instead of XTS_INSTR
            trading_symbol = self.redis_client.hget("NEO_INSTR_OPT", redis_key)
            if trading_symbol is None:
                raise ValueError(f"tradingSymbol not found for {redis_key}")

            leg["tradingSymbol"] = trading_symbol

            res = self.place_single_leg(leg)

            if res.get("type") == "success":
                self.redis_client.hset("NIFTY_OI_SIGNAL", "status", "CONSUMED")

                # 🆕 WRITE LIVE POSITION
                add_position(
                    index=index,
                    direction=new_direction,
                    legs=[leg]
                )

                # XTS → KOTAK NEO REPLACEMENT: Neo uses nOrdNo instead of AppOrderID
                self.redis_client.hset(
                    self.uid,
                    mapping={
                        "STATUS_OI_CROSSOVER": "SUCCESS",
                        "MSG_OI_CROSSOVER": f"Order placed: {res.get('result', {}).get('nOrdNo')}"
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

            # XTS → KOTAK NEO REPLACEMENT: Using NEO_INSTR_OPT instead of XTS_INSTR
            trading_symbol = self.redis_client.hget("NEO_INSTR_OPT", redis_key)
            if trading_symbol is None:
                raise ValueError(f"tradingSymbol not found for {redis_key}")

            leg["tradingSymbol"] = trading_symbol

            res = self.place_single_leg(leg)

            if res.get("type") == "success":
                # Consume BANKNIFTY signal ONLY on success
                self.redis_client.hset("BANKNIFTY_OI_SIGNAL", "status", "CONSUMED")

                # XTS → KOTAK NEO REPLACEMENT: Neo uses nOrdNo instead of AppOrderID
                self.redis_client.hset(
                    self.uid,
                    mapping={
                        "STATUS_BN_OI_CROSSOVER": "SUCCESS",
                        "MSG_BN_OI_CROSSOVER": f"Order placed: {res.get('result', {}).get('nOrdNo')}"
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
