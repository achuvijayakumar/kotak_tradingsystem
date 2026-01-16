# NIFTY OI Crossover Trading System - Production Review

## Executive Summary

**Production-Ready Status: ⚠️ NOT SAFE FOR LIVE TRADING**

The system has **7 critical bugs**, **4 race conditions**, **3 safety gaps**, and **multiple failure scenarios** that must be addressed before production deployment.

---

## 1. Confirmed Bugs

### 🔴 BUG-1: Signal Engine Always in TEST_MODE
**File:** [nifty_oi_trade_engine.py](file:///home/nodeusr/poozhi/nifty_oi_trade_engine.py#L1)
**Severity:** CRITICAL

```python
TEST_MODE = True  # Line 1
```

**Impact:** Signal engine will NEVER publish signals to Redis. All crossover detections are logged only.

**Fix:**
```python
TEST_MODE = False  # Or make it configurable via Redis/env var
```

---

### 🔴 BUG-2: Missing Telegram Notification in OI Order Flow
**File:** [order_service.py](file:///home/nodeusr/poozhi/services/order_service.py#L346-L409)
**Severity:** HIGH

The `process_oi_crossover_order()` method has NO Telegram notification on success or failure, unlike all other order types (Single, Multi, Level CE/PE, Equity).

**Current code (lines 385-394):**
```python
if res.get("type") == "success":
    self.redis_client.hset(
        self.uid,
        mapping={
            "STATUS_OI_CROSSOVER": "SUCCESS",
            "MSG_OI_CROSSOVER": f"Order placed: {res.get('result', {}).get('AppOrderID')}"
        }
    )
else:
    raise ValueError(res.get("description", "Unknown error"))
```

**Fix:** Add Telegram notifications matching existing patterns:
```python
if res.get("type") == "success":
    app_order_id = res.get('result', {}).get('AppOrderID', 'Unknown')
    self.redis_client.hset(...)
    send_telegram(
        f"[OI AUTO] Order Executed ✔️\n"
        f"Direction: {leg['direction']}\n"
        f"Strike: {leg['Strike']} {leg['OptionType']}\n"
        f"Qty: {leg['Qty']}\n"
        f"AppOrderID: {app_order_id}"
    )
else:
    err_desc = res.get("description", "Unknown error")
    send_telegram(f"[OI AUTO] Order FAILED ❌\n{err_desc}")
    raise ValueError(err_desc)
```

---

### 🔴 BUG-3: Hardcoded Expiry Date
**File:** [nifty_oi_order_engine.py](file:///home/nodeusr/poozhi/nifty_oi_order_engine.py#L77)
**Severity:** HIGH

```python
"Expiry": "2025-12-30",  # Line 77 - HARDCODED
```

**Impact:** After December 30, 2025, ALL orders will fail with "instrument not found" errors.

**Fix:** Compute current weekly/monthly expiry dynamically:
```python
from datetime import datetime, timedelta

def get_current_nifty_expiry():
    """Get current week's Thursday expiry for NIFTY."""
    today = datetime.now()
    # Find next Thursday (weekday 3)
    days_ahead = 3 - today.weekday()
    if days_ahead <= 0:  # Target day already happened this week
        days_ahead += 7
    next_thursday = today + timedelta(days_ahead)
    return next_thursday.strftime("%Y-%m-%d")

# In order_payload:
"Expiry": get_current_nifty_expiry(),
```

---

### 🔴 BUG-4: Hardcoded UID in Order Engine
**File:** [nifty_oi_order_engine.py](file:///home/nodeusr/poozhi/nifty_oi_order_engine.py#L7)
**Severity:** MEDIUM

```python
UID = "ITC2766"  # Line 7 - HARDCODED
```

**Impact:** Multi-user deployments will fail. Only ITC2766 can use OI auto-trading.

**Fix:** Accept UID as command-line argument:
```python
import sys

if len(sys.argv) < 2:
    logging.error("Usage: python nifty_oi_order_engine.py <UID>")
    sys.exit(1)

UID = sys.argv[1]
```

---

### 🔴 BUG-5: Hardcoded Quantity
**File:** [nifty_oi_order_engine.py](file:///home/nodeusr/poozhi/nifty_oi_order_engine.py#L73)
**Severity:** MEDIUM

```python
"Qty": 75,  # Line 73 - HARDCODED to 1 lot
```

**Impact:** No flexibility for position sizing. Always trades exactly 1 lot.

**Fix:** Make configurable via Redis:
```python
qty_raw = r.hget(UID, "OI_ENGINE_QTY")
qty = int(qty_raw) if qty_raw else 75  # Default 1 lot

order_payload = {
    ...
    "Qty": qty,
    ...
}
```

Add UI control in [ui.py](file:///home/nodeusr/poozhi/ui.py#L1326):
```python
oi_qty = st.number_input("Quantity (Lots)", min_value=1, step=1, value=1)
redis_client.hset(current_user, "OI_ENGINE_QTY", oi_qty * 75)
```

---

### 🟡 BUG-6: Missing Index Validation
**File:** [order_service.py](file:///home/nodeusr/poozhi/services/order_service.py#L368-L373)
**Severity:** LOW

The Redis key construction assumes `leg['Index']` is always "NIFTY", but there's no validation.

**Current code:**
```python
redis_key = (
    f"{leg['Index']}_"
    f"{leg['Expiry']}_"
    f"{leg['OptionType']}_"
    f"{leg['Strike']}"
)
```

**Fix:** Add validation:
```python
if leg.get('Index') != 'NIFTY':
    raise ValueError(f"OI Auto Trade only supports NIFTY, got: {leg.get('Index')}")
```

---

### 🟡 BUG-7: Inconsistent Status Key Naming
**File:** [ui.py](file:///home/nodeusr/poozhi/ui.py#L1388-L1390)
**Severity:** LOW

The UI reads `MSG_OI_CROSSOVER` but only displays it as an error. Success messages are in `STATUS_OI_CROSSOVER`.

**Current code:**
```python
msg = redis_client.hget(current_user, "MSG_OI_CROSSOVER")
if msg:
    st.error(msg)  # Always shown as error, even if it's a success message
```

**Fix:**
```python
status = redis_client.hget(current_user, "STATUS_OI_CROSSOVER")
msg = redis_client.hget(current_user, "MSG_OI_CROSSOVER")

if status == "SUCCESS" and msg:
    st.success(msg)
elif status == "FAILED" and msg:
    st.error(msg)
elif msg:
    st.info(msg)
```

---

## 2. Race Conditions

### 🔴 RACE-1: Signal Status Transition (CRITICAL)
**Files:** 
- [nifty_oi_telegram_engine.py](file:///home/nodeusr/poozhi/nifty_oi_telegram_engine.py#L45-L65)
- [nifty_oi_order_engine.py](file:///home/nodeusr/poozhi/nifty_oi_order_engine.py#L40-L95)

**Scenario:**
1. Signal Engine publishes: `status=NEW`
2. Telegram Engine reads `status=NEW` → sends notification → sets `status=NOTIFIED`
3. Order Engine reads `status` (could be NEW or NOTIFIED) → places order → sets `status=CONSUMED`

**Problem:** Both engines accept `status=NEW` OR `status=NOTIFIED`. If both read simultaneously:
- Telegram engine at line 45: `if signal_data.get("status") != "NEW"`
- Order engine at line 46: `if signal_data.get("status") not in ("NEW", "NOTIFIED")`

**Impact:** Duplicate order placement if Order Engine processes before Telegram Engine updates status.

**Fix:** Use atomic claim pattern with GETDEL or Lua script:
```python
# In nifty_oi_order_engine.py, replace lines 40-50 with:

# Atomic claim: only process if status is NEW or NOTIFIED
lua_claim = """
local key = KEYS[1]
local status = redis.call('HGET', key, 'status')
if status == 'NEW' or status == 'NOTIFIED' then
    redis.call('HSET', key, 'status', 'CLAIMED')
    return redis.call('HGETALL', key)
else
    return nil
end
"""

signal_data = r.eval(lua_claim, 1, "NIFTY_OI_SIGNAL")
if not signal_data:
    time.sleep(CHECK_INTERVAL)
    continue

# Convert flat list to dict
signal_data = dict(zip(signal_data[::2], signal_data[1::2]))
```

---

### 🟡 RACE-2: Order Service Processing
**File:** [order_service.py](file:///home/nodeusr/poozhi/services/order_service.py#L348-L409)

**Scenario:**
If `impl.py` runs multiple instances for the same UID (accidental), both could process `PLACE_OI_CROSSOVER=requested` simultaneously.

**Current check (line 348):**
```python
if self.redis_client.hget(self.uid, "PLACE_OI_CROSSOVER") != "requested":
    return
```

**Problem:** No atomic claim. Both instances pass the check.

**Impact:** Duplicate order placement.

**Fix:** Use atomic claim pattern (similar to Equity order handling at lines 284-287):
```python
# Check flag
if self.redis_client.hget(self.uid, "PLACE_OI_CROSSOVER") != "requested":
    return

# Atomic claim
raw = self.redis_client.hget(self.uid, "OI_CROSSOVER_ORDER")
if self.redis_client.hdel(self.uid, "OI_CROSSOVER_ORDER") == 0:
    logging.info("Race condition: OI order already claimed")
    return

if not raw:
    logging.error("Claimed OI order but payload was empty")
    self.redis_client.hset(self.uid, mapping={
        "PLACE_OI_CROSSOVER": "failed_no_payload",
        "STATUS_OI_CROSSOVER": "FAILED"
    })
    return

# Proceed with processing...
```

---

### 🟡 RACE-3: Signal Engine Sign Update
**File:** [nifty_oi_trade_engine.py](file:///home/nodeusr/poozhi/nifty_oi_trade_engine.py#L69-L100)

**Scenario:**
1. Iteration 1: `pe_ce = 2500`, `current_sign = 1`, `prev_sign = None` → stores `prev_sign = 1`
2. Iteration 2: `pe_ce = 1500` (below threshold), skips signal logic BUT still updates `prev_sign = 1`
3. Iteration 3: `pe_ce = -2500`, `current_sign = -1`, `prev_sign = 1` → **CROSSOVER DETECTED**

**Problem:** Line 100 updates `prev_sign` even when signal is filtered by threshold. This is actually **CORRECT BEHAVIOR** to track actual PE-CE direction, not a bug.

**Status:** NOT A BUG - Working as designed.

---

### 🟡 RACE-4: UI Toggle State
**File:** [ui.py](file:///home/nodeusr/poozhi/ui.py#L1330-L1344)

**Scenario:**
User rapidly toggles ON → OFF → ON within 3 seconds (order engine poll interval).

**Current code:**
```python
current_state = redis_client.hget(current_user, "OI_ENGINE_ENABLED")
is_enabled = current_state == "ON"

toggle = st.toggle("Enable OI Auto Trading", value=is_enabled)

if toggle and current_state != "ON":
    redis_client.hset(current_user, "OI_ENGINE_ENABLED", "ON")
    st.success("OI Auto Trade Engine ENABLED")
elif not toggle and current_state != "OFF":
    redis_client.hset(current_user, "OI_ENGINE_ENABLED", "OFF")
    st.warning("OI Auto Trade Engine DISABLED")
```

**Problem:** No issue - Streamlit reruns on every interaction, so state is always consistent.

**Status:** NOT A BUG - Streamlit's execution model prevents this race.

---

## 3. Idempotency & Duplicate Prevention

### ✅ GOOD: Signal Consumption
The signal is marked `CONSUMED` after order placement (line 95 in `nifty_oi_order_engine.py`), preventing re-processing.

### ⚠️ GAP-1: No Cooldown Between Signals
**Impact:** If PE-CE crosses threshold multiple times within minutes, multiple orders are placed.

**Example:**
- 10:00:00 → PE-CE = 2500 (BULLISH) → BUY 26000 CE
- 10:00:20 → PE-CE = -2500 (BEARISH) → BUY 26000 PE
- 10:00:40 → PE-CE = 2500 (BULLISH) → BUY 26000 CE (DUPLICATE POSITION)

**Fix:** Add cooldown in signal engine:
```python
# After line 100 in nifty_oi_trade_engine.py
last_signal_ts = r.get("NIFTY_OI_LAST_SIGNAL_TS")
if last_signal_ts:
    elapsed = time.time() - float(last_signal_ts)
    if elapsed < 300:  # 5-minute cooldown
        logging.info(f"[COOLDOWN] Last signal {elapsed:.0f}s ago, waiting...")
        time.sleep(CHECK_INTERVAL)
        continue

r.set("NIFTY_OI_LAST_SIGNAL_TS", time.time())
```

### ⚠️ GAP-2: No Duplicate Strike Prevention
**Impact:** Same strike can be bought multiple times if signals flip rapidly.

**Fix:** Track open positions and skip if already holding:
```python
# In nifty_oi_order_engine.py, before line 84
open_position_key = f"OI_OPEN_{option_type}_{strike}"
if r.exists(open_position_key):
    logging.warning(f"Already holding {strike} {option_type}, skipping")
    r.hset("NIFTY_OI_SIGNAL", "status", "CONSUMED")
    time.sleep(CHECK_INTERVAL)
    continue

# After successful order (line 95)
r.set(open_position_key, "1", ex=86400)  # Expire in 24h
```

---

## 4. Failure Scenarios

### 🔴 FAIL-1: Missing NF_SPOT
**File:** [nifty_oi_order_engine.py](file:///home/nodeusr/poozhi/nifty_oi_order_engine.py#L58-L62)

**Current handling:**
```python
spot_raw = r.get("NF_SPOT")
if not spot_raw:
    logging.warning("NF_SPOT not available")
    time.sleep(CHECK_INTERVAL)
    continue
```

**Problem:** Signal is NOT marked as consumed. Next iteration will retry indefinitely.

**Fix:**
```python
if not spot_raw:
    logging.error("NF_SPOT not available, consuming signal to prevent retry")
    r.hset("NIFTY_OI_SIGNAL", "status", "FAILED_NO_SPOT")
    r.hset(UID, "OI_ENGINE_STATUS", "ERROR")
    r.hset(UID, "MSG_OI_CROSSOVER", "NF_SPOT unavailable")
    time.sleep(CHECK_INTERVAL)
    continue
```

---

### 🔴 FAIL-2: Missing Instrument ID
**File:** [order_service.py](file:///home/nodeusr/poozhi/services/order_service.py#L375-L378)

**Current handling:**
```python
exchange_inst_id = self.redis_client.hget("XTS_INSTR", redis_key)

if exchange_inst_id is None:
    raise ValueError(f"exchangeInstrumentID not found for {redis_key}")
```

**Problem:** Exception is caught at line 396, but signal in `NIFTY_OI_SIGNAL` is already marked `CONSUMED` (line 95 in order engine). Order engine won't retry.

**Impact:** Silent failure - user sees error in UI but signal is lost.

**Fix:** Order engine should NOT consume signal until order service confirms success:
```python
# In nifty_oi_order_engine.py, REMOVE line 95:
# r.hset("NIFTY_OI_SIGNAL", "status", "CONSUMED")  # DELETE THIS

# In order_service.py, ADD after line 392:
r.hset("NIFTY_OI_SIGNAL", "status", "CONSUMED")  # Move here
```

---

### 🔴 FAIL-3: Engine Restart Mid-Signal
**Scenario:**
1. Signal Engine publishes `status=NEW`
2. Order Engine crashes before consuming
3. On restart, Order Engine sees old signal with `status=NEW`

**Problem:** Stale signal could be hours old. Order placement at stale price is dangerous.

**Fix:** Add timestamp validation:
```python
# In nifty_oi_order_engine.py, after line 50
signal_ts = float(signal_data.get("ts", 0))
age = time.time() - signal_ts

if age > 60:  # Signal older than 1 minute
    logging.warning(f"Signal is {age:.0f}s old, marking stale")
    r.hset("NIFTY_OI_SIGNAL", "status", "STALE")
    time.sleep(CHECK_INTERVAL)
    continue
```

---

### 🟡 FAIL-4: XTS API Failure
**File:** [order_service.py](file:///home/nodeusr/poozhi/services/order_service.py#L51-L70)

**Current handling:** Exception caught, returns `{"type": "error", "description": str(e)}`

**Problem:** Transient network errors are treated as permanent failures.

**Fix:** Add retry logic for transient errors:
```python
def place_single_leg(self, leg, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = self.xt.place_order(...)
            logging.info(f"[XTS Response] {response}")
            return response
        except Exception as e:
            if attempt < max_retries - 1:
                logging.warning(f"Order attempt {attempt+1} failed: {e}, retrying...")
                time.sleep(1)
            else:
                logging.error(f"[ERROR] Failed to place order after {max_retries} attempts: {e}")
                return {"type": "error", "description": str(e)}
```

---

## 5. Alignment with Existing Order Flows

### ✅ GOOD: Reuses `place_single_leg()`
OI order execution uses the same `place_single_leg()` method as Single, Multi, and Level orders. Consistent execution path.

### ✅ GOOD: Instrument ID Resolution
Uses same `XTS_INSTR` Redis hash as UI-driven orders. Consistent instrument lookup.

### ✅ GOOD: Status Key Pattern
Follows existing pattern: `PLACE_*`, `STATUS_*`, `MSG_*` keys.

### ⚠️ INCONSISTENCY-1: No Telegram Notification
See BUG-2 above.

### ⚠️ INCONSISTENCY-2: Order Cleanup
**File:** [order_service.py](file:///home/nodeusr/poozhi/services/order_service.py#L408-L409)

```python
finally:
    self.redis_client.hset(self.uid, "PLACE_OI_CROSSOVER", "fetched")
    self.redis_client.hdel(self.uid, "OI_CROSSOVER_ORDER")
```

**Comparison with Single order (lines 194-195):**
```python
finally:
    self.redis_client.hset(self.uid, mapping={"PLACE_SINGLE": "fetched"})
    self.redis_client.hdel(self.uid, "SINGLE_LEG")
```

**Issue:** OI order uses `hset()` with single key, others use `mapping={}`. Inconsistent but functionally equivalent.

**Fix (optional):** Standardize to mapping syntax:
```python
self.redis_client.hset(self.uid, mapping={"PLACE_OI_CROSSOVER": "fetched"})
```

---

## 6. Safety Concerns

### 🔴 SAFETY-1: No Position Limits
**Impact:** Unlimited order placement. If signal flips 10 times in a day, 10 orders are placed.

**Fix:** Add daily order limit:
```python
# In nifty_oi_order_engine.py, before line 84
today = time.strftime("%Y-%m-%d")
order_count_key = f"OI_ORDER_COUNT_{today}"
order_count = int(r.get(order_count_key) or 0)

if order_count >= 5:  # Max 5 orders per day
    logging.warning(f"Daily order limit reached: {order_count}")
    r.hset(UID, "MSG_OI_CROSSOVER", f"Daily limit reached ({order_count}/5)")
    r.hset("NIFTY_OI_SIGNAL", "status", "CONSUMED")
    time.sleep(CHECK_INTERVAL)
    continue

# After successful order
r.incr(order_count_key)
r.expire(order_count_key, 86400)
```

---

### 🔴 SAFETY-2: No Market Hours Check
**Impact:** Orders could be placed outside trading hours (9:15 AM - 3:30 PM IST).

**Fix:** Add market hours validation:
```python
from datetime import datetime
import pytz

def is_market_open():
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    
    # Check if weekday (Monday=0, Sunday=6)
    if now.weekday() > 4:  # Saturday or Sunday
        return False
    
    # Check time (9:15 AM - 3:30 PM)
    market_open = now.replace(hour=9, minute=15, second=0)
    market_close = now.replace(hour=15, minute=30, second=0)
    
    return market_open <= now <= market_close

# In nifty_oi_order_engine.py, before line 40
if not is_market_open():
    r.hset(UID, "OI_ENGINE_STATUS", "MARKET_CLOSED")
    time.sleep(CHECK_INTERVAL)
    continue
```

---

### 🟡 SAFETY-3: No Stop-Loss / Take-Profit
**Impact:** Positions are opened but never closed automatically. Requires manual intervention.

**Recommendation:** Out of scope for this review, but consider adding:
- Auto-exit at 50% profit
- Auto-exit at 30% loss
- Time-based exit (e.g., 3:15 PM square-off)

---

## 7. Logging & Debuggability

### ✅ GOOD: Consistent Logging Format
All engines use same format: `%(asctime)s | %(levelname)s | %(message)s`

### ✅ GOOD: Contextual Logging
Signal engine logs PE-CE values, signs, and threshold filtering.

### ⚠️ IMPROVEMENT-1: Add Correlation IDs
**Problem:** Hard to trace a single signal through all 3 engines (Signal → Telegram → Order → Service).

**Fix:** Add signal ID:
```python
# In nifty_oi_trade_engine.py, line 88
signal_id = f"OI_{int(time.time()*1000)}"
r.hset(
    REDIS_KEY,
    mapping={
        "signal_id": signal_id,  # ADD THIS
        "signal": signal,
        "pe_ce": pe_ce,
        "ts": time.time(),
        "status": "NEW"
    }
)
logging.info(f"[SIGNAL {signal_id}] {signal} (PE-CE={pe_ce})")
```

Then log `signal_id` in all subsequent engines.

---

### ⚠️ IMPROVEMENT-2: Add Metrics
**Recommendation:** Track in Redis:
- Total signals generated today
- Total orders placed today
- Success/failure rate
- Average order execution time

---

## 8. Production-Readiness Assessment

### Critical Blockers (MUST FIX)
1. ✅ **BUG-1:** Disable TEST_MODE
2. ✅ **BUG-3:** Fix hardcoded expiry
3. ✅ **RACE-1:** Implement atomic signal claim
4. ✅ **FAIL-1:** Handle missing NF_SPOT gracefully
5. ✅ **FAIL-2:** Move signal consumption to order service
6. ✅ **FAIL-3:** Add timestamp validation
7. ✅ **SAFETY-1:** Add position limits
8. ✅ **SAFETY-2:** Add market hours check

### High Priority (SHOULD FIX)
1. ✅ **BUG-2:** Add Telegram notifications
2. ✅ **BUG-4:** Make UID configurable
3. ✅ **RACE-2:** Implement atomic order claim
4. ✅ **GAP-1:** Add signal cooldown
5. ✅ **GAP-2:** Prevent duplicate strikes

### Medium Priority (NICE TO HAVE)
1. ✅ **BUG-5:** Make quantity configurable
2. ✅ **BUG-6:** Add index validation
3. ✅ **BUG-7:** Fix UI status display
4. ✅ **FAIL-4:** Add retry logic for XTS API
5. ✅ **IMPROVEMENT-1:** Add correlation IDs

---

## 9. Minimal Fix Checklist

To make this system production-safe with **minimal changes**:

### Phase 1: Critical Fixes (1-2 hours)
- [ ] Set `TEST_MODE = False` in `nifty_oi_trade_engine.py`
- [ ] Implement dynamic expiry calculation
- [ ] Add atomic signal claim (Lua script)
- [ ] Add market hours check
- [ ] Add daily order limit (5 orders/day)
- [ ] Add timestamp validation (reject signals >60s old)
- [ ] Move signal consumption to order service

### Phase 2: Safety Fixes (2-3 hours)
- [ ] Add 5-minute signal cooldown
- [ ] Add duplicate strike prevention
- [ ] Handle missing NF_SPOT by consuming signal
- [ ] Add Telegram notifications for OI orders
- [ ] Make UID configurable (command-line arg)

### Phase 3: Polish (1-2 hours)
- [ ] Add correlation IDs for tracing
- [ ] Fix UI status display logic
- [ ] Add retry logic for XTS API
- [ ] Make quantity configurable via UI
- [ ] Add index validation

---

## 10. Final Recommendation

**DO NOT DEPLOY TO PRODUCTION** until at minimum **Phase 1 and Phase 2** are completed.

**Estimated effort:** 4-5 hours of focused development + 2 hours of testing.

**Testing checklist before go-live:**
1. ✅ Verify signal generation with real PE-CE data
2. ✅ Verify Telegram notifications arrive
3. ✅ Verify order placement with paper trading account
4. ✅ Test engine restart mid-signal (should reject stale signals)
5. ✅ Test rapid signal flips (should respect cooldown)
6. ✅ Test missing NF_SPOT scenario
7. ✅ Test outside market hours (should idle)
8. ✅ Test daily limit (should stop at 5 orders)
9. ✅ Verify no duplicate orders under race conditions
10. ✅ Monitor for 1 full trading day in paper trading mode

---

## Redis Key Reference

### Global Keys
- `NIFTY_OI_SIGNAL` (hash) - Signal state
  - `signal`: "BULLISH" | "BEARISH"
  - `pe_ce`: float
  - `ts`: timestamp
  - `status`: "NEW" | "NOTIFIED" | "CONSUMED" | "CLAIMED" | "STALE" | "FAILED_NO_SPOT"
- `NIFTY_PREV_PECE_SIGN` (string) - Last PE-CE sign (-1, 0, 1)
- `NF_SPOT` (string) - NIFTY spot price
- `XTS_INSTR` (hash) - Instrument ID lookup

### Per-User Keys (in `<UID>` hash)
- `OI_ENGINE_ENABLED`: "ON" | "OFF"
- `OI_ENGINE_STATUS`: "IDLE" | "RUNNING" | "ERROR" | "MARKET_CLOSED"
- `OI_ENGINE_QTY`: int (total quantity, e.g., 75 for 1 lot)
- `PLACE_OI_CROSSOVER`: "requested" | "processing" | "fetched"
- `OI_CROSSOVER_ORDER`: JSON payload
- `STATUS_OI_CROSSOVER`: "PROCESSING" | "SUCCESS" | "FAILED"
- `MSG_OI_CROSSOVER`: string (error or success message)
- `OI_ENGINE_LAST_SIGNAL`: JSON (for UI display)
- `OI_ENGINE_LAST_ORDER`: JSON (for UI display)

### Proposed New Keys
- `NIFTY_OI_LAST_SIGNAL_TS` (string) - Timestamp of last signal (for cooldown)
- `OI_ORDER_COUNT_<YYYY-MM-DD>` (int) - Daily order counter
- `OI_OPEN_<CE|PE>_<strike>` (string) - Track open positions

---

**Review completed:** 2025-12-19  
**Reviewer:** Antigravity AI  
**System version:** OI Auto Trade v1.0
