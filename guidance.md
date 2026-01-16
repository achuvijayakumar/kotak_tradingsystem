✅ Final Migration Blueprint

XTS Symphony → Kotak Neo API (v2)
Method: Kotak README + Antigravity
State: Fresh infra, no legacy users, no Redis carryover

1️⃣ What stays, what dies (non-negotiable)
✅ KEEP (these are solid)

UI (Streamlit) → Redis command bus

Agent loop (while True polling Redis)

Order semantics:

SINGLE_LEG, MULTI_LEGS

LEVEL_CE / LEVEL_PE

OI engine keys

Per-UID Redis hashes

❌ DELETE / IGNORE COMPLETELY

XTS login/session concepts

XTS instrument IDs

XTS Redis keys (XTS_INSTR*)

Old VPS Redis dump

Old users

You are rebooting the broker layer, not the system.

2️⃣ New mental model (important shift)

Kotak Neo is NOT session-centric like XTS.

From README behavior:

Quotes can work without login

Trading requires TOTP → MPIN → trade token

Token lifecycle is opaque → treat client as refreshable

👉 Conclusion
Do not design “session status” logic like XTS.
Design “login capability available / not available”.

3️⃣ Redis schema (final, broker-agnostic)
Global (shared)
NEO_INSTR_OPT        # options instrument master
NEO_INSTR_EQ         # equity instrument master
NF_SPOT
BN_SPOT

Per-user hash (UID)
NEO_LOGIN_REQUEST
NEO_LOGIN_STATUS
NEO_LOGIN_MESSAGE

PLACE_SINGLE
STATUS_SINGLE
MSG_SINGLE

PLACE_MULTI
STATUS_MULTI
MSG_MULTI

LEVEL_CE
LEVEL_CE_LEVEL
LEVEL_CE_INDEX
STATUS_LEVEL_CE

LEVEL_PE
LEVEL_PE_LEVEL
LEVEL_PE_INDEX
STATUS_LEVEL_PE


👉 Your UI already matches this pattern — only key names change.

4️⃣ Authentication: exact flow you must implement

Based strictly on README 

readme

🔐 Neo login flow (Agent side)
UI → Redis → Agent


UI sets:

redis.hset(uid, "NEO_LOGIN_REQUEST", "requested")


Agent does:

client = NeoAPI(
    environment="prod",
    consumer_key=CFG["consumer_key"]
)

client.totp_login(
    mobile_number=CFG["mobile"],
    ucc=CFG["ucc"],
    totp=CFG["totp"]
)

client.totp_validate(mpin=CFG["mpin"])


On success:

redis.hset(uid, "NEO_LOGIN_STATUS", "READY")


❗ No session polling.
❗ No expiry guessing.
If order fails → force re-login.

5️⃣ Instrument master ingestion (do this BEFORE UI)

This is the most critical step.

Source of truth (from README)
client.scrip_master(exchange_segment="nse_fo")
client.search_scrip(...)

What you must build

A one-time bootstrap script:

neo_instruments.py

Logic

Download nse_fo scrip master

Filter:

NIFTY

BANKNIFTY

Normalize Redis keys:

NIFTY_2026-01-27_CE_26000
BANKNIFTY_2026-01-27_PE_55000


Store:

redis.hset("NEO_INSTR_OPT", key, instrument_token)

UI change (minimal)

Replace:

redis.hget("XTS_INSTR", key)


with:

redis.hget("NEO_INSTR_OPT", key)


Nothing else.

6️⃣ Order placement adapter (core swap)
Mapping table (READ THIS CAREFULLY)
Your System	Kotak Neo
Side	transaction_type = B / S
Qty	quantity (FULL LOT QTY, not lots)
NRML	product="NRML"
MARKET	order_type="MKT"
LIMIT	order_type="L"
Instrument ID	trading_symbol (preferred)
Example (options market order)
client.place_order(
    exchange_segment="nse_fo",
    product="NRML",
    price="0",
    order_type="MKT",
    quantity=qty,
    validity="DAY",
    trading_symbol=tsym,
    transaction_type="B",
    amo="NO"
)


👉 Use trading_symbol, not instrument_token, for FO orders unless forced.

7️⃣ Multi-leg & Level trades (no redesign)

Good news:
Your Redis choreography already supports this perfectly.

Only change:

Executor function

Instrument lookup source

Level CE / PE:

Spot comes from Neo quotes

Trigger logic stays identical

8️⃣ Spot price engine (NF_SPOT / BN_SPOT)

From README:

client.quotes(...)
client.subscribe(...)

Phase 1 (safe)

Poll quotes every 1–2 sec

Update Redis:

NF_SPOT
BN_SPOT

Phase 2 (later)

WebSocket subscribe to index feed

Do NOT block migration on websockets.

9️⃣ Order book, positions, balance

Direct 1:1 mapping from README:

Feature	Neo API
Orderbook	order_report()
Trades	trade_report()
Positions	positions()
Holdings	holdings()
Balance	limits()

Your CSV writers stay.
Only data source changes.

🔥 Golden rules (don’t violate)

❌ Never reuse XTS instrument IDs

❌ Never assume session validity

❌ Never mix Redis old keys

✅ Always treat Neo client as disposable

✅ Force re-login on any auth error

✅ What Antigravity should do first (task list)

Exact order:

Create NeoAuthService

Create NeoInstrumentBuilder

Replace instrument Redis keys

Implement NeoOrderExecutor

Test:

single leg

multi leg

equity

THEN enable Level / OI engines