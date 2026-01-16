# Poozhi Trading System

A custom trading interface built with Streamlit and Python, interacting with the XTS API via a Redis-based agent.

## Prerequisites

-   Python 3.x
-   Redis Server (running on localhost:6379)
-   XTS Connect SDK (installed in the environment)

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install streamlit pandas redis tabulate
    ```

2.  **Configuration**:
    -   Ensure you have a folder named after your UID (e.g., `ITC2766`).
    -   Inside that folder, ensure you have a `config.json` (e.g., `ITC2766.json`) with your XTS credentials.

3.  **Initialize Instrument Master**:
    Run `master.py` to fetch the instrument master and populate the Redis mapping.
    ```bash
    python master.py <UID>
    ```

## Usage

1.  **Start the Agent**:
    The agent listens for requests from the UI.
    ```bash
    python impl.py <UID>
    ```

2.  **Start the UI**:
    Open a new terminal and run the Streamlit app.
    ```bash
    streamlit run ui.py
    ```

## Directory Structure

-   `ui.py`: The frontend interface.
-   `impl.py`: The backend agent logic.
-   `master.py`: Script to initialize instrument data.
-   `agent.md`: Documentation of the agent's architecture.
