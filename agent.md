# Trading Agent Documentation

## Overview
The Trading Agent is a background process (currently implemented in `impl.py`) that interacts with the XTS API to execute trades and fetch data requested by the UI (`ui.py`). The communication between the UI and the Agent is mediated by a Redis database.

## Architecture
-   **Frontend (`ui.py`)**: A Streamlit application that allows users to view data (positions, balance, orderbook) and place orders. It writes requests to Redis.
-   **Backend (`impl.py`)**: A Python script that polls Redis for requests, executes them using the XTS Connect SDK, and writes the results back to Redis or CSV files.
-   **Data Store (Redis)**: Acts as the message broker.
-   **File System**: Used for persisting large datasets like positions, balance, and orderbook (CSV files).

## Redis Communication Protocol

The system uses a Hash in Redis keyed by the user's UID (e.g., `ITC2766`).

### 1. Balance
-   **Request**: UI sets `BALANCE` field to `"requested"`.
-   **Response**: Agent fetches balance, writes to `balance.csv`, and sets `BALANCE` field to `"fetched"`.

### 2. Net Positions
-   **Request**: UI sets `POSITION` field to `"requested"`.
-   **Response**: Agent fetches positions, writes to `positions.csv`, and sets `POSITION` field to `"fetched"`.

### 3. Order Book
-   **Request**: UI sets `ORDERBOOK` field to `"requested"`.
-   **Response**: Agent fetches order book, writes to `orderbook.csv`, and sets `ORDERBOOK` field to `"fetched"`.

### 4. Place Order (Single Leg)
-   **Request**:
    -   UI sets `SINGLE_LEG` field to a JSON string containing a list with one leg object.
    -   UI sets `PLACEORDER` field to `"requested"`.
-   **Response**: Agent reads `SINGLE_LEG`, places the order, and sets `PLACEORDER` field to `"fetched"`.

### 5. Place Order (Multi Leg)
-   **Request**:
    -   UI sets `MULTI_LEGS` field to a JSON string containing a list of leg objects.
    -   UI sets `PLACEORDER` field to `"requested"`.
-   **Response**: Agent reads `MULTI_LEGS`, places orders (BUYs first, then SELLs), and sets `PLACEORDER` field to `"fetched"`.

### Leg Object Structure
```json
{
    "Index": "NIFTY",
    "OrderType": "NRML",
    "Qty": 75,
    "Side": "BUY",
    "Expiry": "2025-12-23",
    "Strike": 24000,
    "OptionType": "CE"
}
```

## Instrument Master
The agent uses a Redis Hash `XTS_INSTR` to map human-readable symbols (e.g., `NIFTY_2025-12-23_CE_24000`) to XTS Exchange Instrument IDs. This mapping is populated by `master.py`.
