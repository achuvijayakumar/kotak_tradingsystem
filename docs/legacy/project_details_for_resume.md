# System Whitepaper: Algorithmic Trading Platform (Poozhi)

## 1. Introduction
**Poozhi** is a production-grade Algorithmic Trading & Execution Platform engineered for the Indian Financial Markets (NSE Derivatives & Equity). It serves as a "Hybrid Command Center," seamlessly blending ultra-low-latency automated strategies with a high-performance manual execution interface.

Unlike standard trading bots, Poozhi is built on a **Micro-Service Architecture** controlled by an event-driven core. This ensures that heavy data processing (like Option Chain analytics) never blocks critical order execution, providing a smooth, institutional-grade experience.

---

## 2. Technical Architecture

### 2.1 The "Decoupled Event Loop" Pattern
The system is architected around a central property: **Non-Blocking Operation**.
*   **The Frontend (Producer)**: Built with **Streamlit**, functioning purely as a "State Creator." When a user clicks "Buy," it does *not* talk to the broker. It writes an **Intent** to a Redis Event Bus.
*   **The Backend (Consumer)**: A centralized orchestrator (`impl.py`) runs an infinite event loop that polls Redis for intents. It claims tasks, executes them via the Symphony XTS API, and writes the status back to Redis.
*   **The Glue**: **Redis** serves as the high-speed Message Broker and State Store, enabling sub-millisecond communication between the UI and the Backend.

### 2.2 Technology Stack
| Layer | Technology | Role |
| :--- | :--- | :--- |
| **Language** | **Python 3.10+** | Core Logic, Async I/O, Type Hinting |
| **Interface** | **Streamlit** | Real-time Dashboard with React-like partial re-rendering |
| **State/Bus** | **Redis** | Ephemeral State, Pub/Sub, Distributed Locks |
| **Time-Series DB** | **QuestDB** | High-throughput storage for Market Data (OI, Spot) |
| **Analytics DB** | **DuckDB / MotherDuck** | Serverless OLAP for FIFO PnL & Trade Accounting |
| **Visualization** | **Plotly / Seaborn** | Real-time Options Payoff & Risk Charts |
| **Broker API** | **Symphony XTS** | Interactive (Orders) & Market Data (LTP/Depth) |

---

## 3. End-to-End Workflow: The Life of a Trade
To understand the system, trace a **Multi-Leg Strategy Order**:

1.  **Intent Creation**:
    *   User configures an "Iron Condor" (4 Legs) in the UI.
    *   User clicks "Place Order".
    *   UI writes JSON payload to Redis Key: `PLACE_MULTI = requested`.
    *   UI displays "Processing..." toast locally.

2.  **Orchestration (The Loop)**:
    *   The `impl.py` Main Loop detects `PLACE_MULTI == requested`.
    *   It delegates to `OrderService.process_multi_order()`.
    *   **Atomic Claim**: Service updates Redis status to `PROCESSING` to lock the task.

3.  **Execution Logic**:
    *   **Validation**: Checks instrument validity against the local Redis Master Map (`XTS_INSTR`).
    *   **Sequence**: Executes **BUY** legs first (to reduce Margin), then **SELL** legs.
    *   **Resilience**: If any leg fails, the system halts and triggers a Telegram Alert immediately.

4.  **Feedback**:
    *   On success, `OrderService` updates Redis: `STATUS_MULTI = SUCCESS`.
    *   UI pulls this new state and turns the "Processing..." toast into a Green Success Banner.
    *   `TelegramNotifier` sends a formatted HTML message to the trader's mobile.

---

## 4. Key Engineering Modules

### 4.1 Custom Order Management System (OMS)
**Problem**: Broker APIs are "dumb pipe" executors. They don't support complex atomic strategies.
**Solution**: A robust `OrderService` (`services/order_service.py`) that acts as a Smart Wrapper.
*   **Atomic Multi-Leg**: Handles 2, 3, or 4 leg strategies with sequential safety logic.
*   **Concurrency Control**: Uses Redis `HDEL` atomic operations to prevent "Double Execution" race conditions across multiple workers.
*   **Service Abstraction**: Logic is split into `BalanceService`, `PositionService`, and `OrderbookService`, allowing independent polling intervals (e.g., Positions every 1s, Balance every 60s).

### 4.2 "OI Crossover" Auto-Trading Engine
**Problem**: Keeping a constant watch on 50+ Option Strikes for trend reversals is impossible for humans.
**Solution**: A fully autonomous agent (`nifty_oi_trade_engine.py`) based on "Smart Money" sentiment.
*   **Logic**: Monitors `Net OI = Put OI - Call OI`. A crossover from Negative to Positive signals a Bullish Trend.
*   **Dynamic Strike Selection**: Real-time querying of Spot Price (`NF_SPOT`) to calculate and trade the ATM strike instantly.
*   **Noise Filtering**: Hysteresis Threshold (e.g., +/- 2000 contracts) filters out sideways market chop.

### 4.3 Financial Engineering: Real-Time Payoff Simulator
**Problem**: Understanding the risk curve of a complex 10-leg portfolio is non-intuitive.
**Solution**: A reversed-engineered Black-Scholes simulator embedded in the dashboard (`ui.py`).
*   **Simulation**: Generates a hypothetical "Spot Price Array" ($S_T$).
*   **Math**: Computes $Max(S_T - K, 0)$ for every option in the portfolio.
*   **Visual**: Renders a **Plotly** chart showing exact Max Profit, Max Loss, and Breakeven Points overlayed on the live market price.

### 4.4 Breakout/Breakdown Watchers (Traps)
**Problem**: Traders miss fast moves while waiting for a level break.
**Solution**: `LevelCEWatcher` / `LevelPEWatcher` (`watchers/`).
*   **Mechanism**: The user sets a Trigger Price. The watcher monitors the feed tick-by-tick.
*   **Action**: `if prev_price < Level and curr_price >= Level`: Immediate Market Order Execution.

---

## 5. Data Engineering & Automation

### 5.1 Master Data Management (Cache Warming)
*   **The Issue**: Resolving "NIFTY 27JAN 26000 CE" to "ExchangeID 45892" takes ~200ms via API. This is too slow.
*   **The Fix**: `instruments.py` pulls the Full Master List (50,000+ rows) at startup. It builds an Optimized Redis Hash Map.
*   **Result**: Symbol Resolution time drops to **microseconds (O(1))**.

### 5.2 The "Offline Analytics" Pipeline
*   **Ingestion**: `order_ingest.py` runs nightly to sync trades to **MotherDuck** (Cloud Data Warehouse).
*   **Deduplication**: Implements logical Upserts to guarantee data integrity.
*   **FIFO Accounting**: `analyze_pnl.py` uses advanced SQL Window Functions (`SUM() OVER PARTITION`) to replicate complex FIFO (First-In-First-Out) PnL accounting purely in the database layer, enabling tax-ready reporting.

---

## 6. Resilience & DevOps

*   **Self-Healing Authentication**: `core/auth.py` implements a "Headless Login" flow. If an API Token expires (403 Forbidden), the agent pauses, autonomously regenerates a token using stored credentials, and resumes execution without a restart.
*   **Centralized Notification Bus**: All exceptions and trade confirmations are routed through `services/telegram_notifier.py`, ensuring the trader is aware of system health even when away from the screen.
*   **Smart Watchlist**: The UI (`watchlist.py`) uses SQL-based Autosuggest to help users find contracts instantly by typing partial names, querying the local QuestDB archives.
