import streamlit as st
import pandas as pd
import redis
import time
import os 
HOST = "http://localhost:9000"
import json
import duckdb
import requests
from watchlist import banknifty_watchlist, nifty_watchlist
from utils.telegram_notifier import send_telegram

# ---------------------------------
# MotherDuck connection (GLOBAL)
# ---------------------------------
MD_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbWFpbCI6ImFjaHV2aWpheWFrdW1hcjk4QGdtYWlsLmNvbSIsIm1kUmVnaW9uIjoiYXdzLWV1LWNlbnRyYWwtMSIsInNlc3Npb24iOiJhY2h1dmlqYXlha3VtYXI5OC5nbWFpbC5jb20iLCJwYXQiOiJRM0NjaHVPa0o4RHNMdWRMM3Q5UWNMVjFPVm5CZ2V0cmxOMTFSNFE2OEdJIiwidXNlcklkIjoiNjRhODM3MzMtM2M1OC00MTcyLTkyM2UtNDZmNjNjODQ1NmMyIiwiaXNzIjoibWRfcGF0IiwicmVhZE9ubHkiOmZhbHNlLCJ0b2tlblR5cGUiOiJyZWFkX3dyaXRlIiwiaWF0IjoxNzY0MjM4NzA5fQ.QgX5-G1F8kKipmk9PZHoEeqxiLcpU3bIclmqTkzX9C4"
md_con = duckdb.connect(f"md:?token={MD_TOKEN}")

#paths
base_dir = os.path.dirname(os.path.abspath(__file__))

redis_client = redis.StrictRedis(host='localhost', port=6379, db=0, decode_responses=True)

NIFTY_LOT_SIZE = 65
BANKNIFTY_LOT_SIZE = 30

def render_leg_input(col, key_suffix):
    """
    Renders inputs for a single leg in the given column.
    Returns a dictionary with the current values of the inputs.
    """
    with col:
        index = st.selectbox("Instrument", ["NIFTY", "BANKNIFTY"], key=f"index_{key_suffix}")
        lots = st.number_input("Lots", min_value=1, step=1, key=f"lots_{key_suffix}")
        
        lot_size = NIFTY_LOT_SIZE if index == "NIFTY" else BANKNIFTY_LOT_SIZE
        qty = lots * lot_size
        
        side = st.selectbox("Side", [" ", "BUY", "SELL"], key=f"side_{key_suffix}")
        expiry = st.selectbox("Date",["2026-01-27"], key=f"exp_{key_suffix}")
        strike = st.number_input("Strike Price", min_value=100,value=26000, step=50, key=f"strike_{key_suffix}")
        option_type = st.selectbox("Option Type", ["CE", "PE"], key=f"opt_{key_suffix}")
        
        return {
            "Index": index,
            "OrderType": "NRML",
            "Qty": qty,
            "Side": side,
            "Expiry": expiry, # Keep as date object, convert later
            "Strike": strike,
            "OptionType": option_type,
        }
#-------------------------------------------------------------

#-------------------------------------------------------------
# [CACHE SYMBOL]
@st.cache_data(ttl=3600)
def resolve_symbol(symbol):
    """
    Convert CSV symbols into actual QuestDB symbols.
    Supports:
      - BANKNIFTY options
      - NIFTY options (nearest weekly)
      - BANKNIFTY futures
      - NIFTY futures
    """
    s = symbol.upper().strip().replace("  ", " ")
    parts = s.split()

    # Already QuestDB format
    if " " not in s:
        return s

    # --------------------------------------------------
    # FUTURES: INDEX DDMMMYYYY  →  INDEXYYMONFUT
    # --------------------------------------------------
    if len(parts) == 2:
        idx, expiry = parts
        yy = expiry[-2:]       # 25
        mon = expiry[2:5]      # DEC
        return f"{idx}{yy}{mon}FUT"

    # --------------------------------------------------
    # OPTIONS: INDEX DDMMMYYYY CE/PE STRIKE
    # --------------------------------------------------
    if len(parts) < 4:
        return None

    idx, expiry, opt, strike = parts
    yy = expiry[-2:]

    # BANKNIFTY options (monthly)
    if idx == "BANKNIFTY":
        mon = expiry[2:5]
        pattern = f"{idx}{yy}{mon}%{strike}{opt}"
        table = "BankNiftyOI"

    # NIFTY options (nearest weekly)
    else:
        pattern = f"{idx}{yy}%{strike}{opt}"
        table = "NiftyOI"

    sql = f"""
        select tradingsymbol
        from {table}
        where tradingsymbol like '{pattern}'
        latest on ts partition by tradingsymbol;
    """

    r = requests.get(HOST + "/exec", params={"query": sql}).json()
    df = pd.DataFrame.from_dict(r.get("dataset", []))

    return df.iloc[0, 0] if not df.empty else None



def fetch_ltp(symbol):
    sym = resolve_symbol(symbol)
    if not sym:
        return None

    table = "BankNiftyOI" if sym.startswith("BANKNIFTY") else "NiftyOI"

    sql = f"""
        select last_price
        from {table}
        where tradingsymbol = '{sym}'
        latest on ts partition by tradingsymbol;
    """

    try:
        r = requests.get(HOST + "/exec", params={"query": sql}).json()
        df = pd.DataFrame.from_dict(r.get("dataset", []))
        return float(df.iloc[0,0]) if not df.empty else None
    except:
        return None


def fetch_ltp_bulk(symbols: list[str]) -> dict[str, float]:
    # [BATCH LTP FIX]
    if not symbols:
        return {}

    # Map original -> resolved
    orig_to_res = {}
    bn_syms = set()
    nf_syms = set()

    for s in symbols:
        rs = resolve_symbol(s)
        orig_to_res[s] = rs
        if rs:
            if rs.startswith("BANKNIFTY"):
                bn_syms.add(rs)
            else:
                nf_syms.add(rs)

    rs_prices = {}

    for table, sym_set in [("BankNiftyOI", bn_syms), ("NiftyOI", nf_syms)]:
        if not sym_set:
            continue
        
        quoted = ",".join(f"'{x}'" for x in sym_set)
        sql = f"""
            select tradingsymbol, last_price
            from {table}
            where tradingsymbol in ({quoted})
            latest on ts partition by tradingsymbol;
        """

        try:
            r = requests.get(HOST + "/exec", params={"query": sql}).json()
            df_res = pd.DataFrame.from_dict(r.get("dataset", []))
            if not df_res.empty:
                for _, row in df_res.iterrows():
                    # row[0] = tradingsymbol, row[1] = last_price
                    rs_prices[row[0]] = float(row[1])
        except Exception as e:
            print("[BATCH LTP ERROR]", e)

    # Map results back to original symbols
    results = {}
    for s in symbols:
        rs = orig_to_res.get(s)
        results[s] = rs_prices.get(rs)

    return results

#-------------------------------------------------------------
def resolve_instrument_id(redis_client, leg):
    """Build redis key and fetch ExchangeInstrumentID"""

    index = leg["Index"]                   # NIFTY / BANKNIFTY
    expiry = leg["Expiry"]                # YYYY-MM-DD
    option_type = leg["OptionType"]       # CE / PE
    strike = leg["Strike"]                # int or str

    redis_key = f"{index}_{expiry}_{option_type}_{strike}"
    

    inst_id = redis_client.hget("XTS_INSTR", redis_key)

    if inst_id is None:
        return None

    return int(inst_id)
#-------------------------------------------------------------

if "user" not in st.session_state:
    #st.session_state.user = None
    name = st.text_input("UID", value="")
    if st.button("Save"):
        st.session_state.user = name
        st.success(f"Saved: {name}")

        # NEW — Telegram notify UID selection
        #send_telegram(f"👤 <b>UID Selected</b>\nUser: {name}")
        
        st.rerun()
else:    
    st.write(f"welcome {st.session_state.user}")
    current_user = st.session_state.user



    tabs= st.tabs([ "Multi","Single","Positions","BreakTrades","Orders","Watchlist","Reports","Redis", "Utils", "Equity","OI Auto"])

    with tabs[0]:
        # Place Multiple Trade
        col1, col2, col3, col4 = st.columns(4)
        
        leg1 = render_leg_input(col1, "m_1")
        leg2 = render_leg_input(col2, "m_2")
        leg3 = render_leg_input(col3, "m_3")
        leg4 = render_leg_input(col4, "m_4")

        if st.button("Place Order", key="multi_order_btn"):
            # Clear old keys - ONLY relevant ones
            redis_client.hdel(current_user, "MULTI_LEGS")
            redis_client.hdel(current_user, "STATUS_MULTI")
            redis_client.hdel(current_user, "MSG_MULTI")
            
            raw_legs = [leg1, leg2, leg3, leg4]
            
            # Filter valid legs and format expiry
            valid_legs = []
            all_resolved = True
            for leg in raw_legs:
                if leg["Side"] and leg["Side"].strip() != "":
                    # Create a copy to avoid modifying the original dict if needed elsewhere
                    l = leg.copy()

                    
                    # Resolve ID
                    inst_id = resolve_instrument_id(redis_client, l)
                    if inst_id:
                        l["exchangeInstrumentID"] = inst_id
                        valid_legs.append(l)
                    else:
                        st.error(f"Instrument not found for {l['Index']} {l['Expiry']} {l['OptionType']} {l['Strike']}")
                        all_resolved = False
                        break
            
            if valid_legs and all_resolved:
                redis_client.hset(
                    current_user,
                    mapping={
                        "PLACE_MULTI": "requested",
                        "MULTI_LEGS": json.dumps(valid_legs),
                        "STATUS_MULTI": "PROCESSING"
                    }
                )
                
                with st.spinner("Processing Order..."):
                    timeout = 10
                    start_time = time.time()
                    while time.time() - start_time < timeout:
                        status = redis_client.hget(current_user, "STATUS_MULTI")
                        if status in ["SUCCESS", "FAILED"]:
                            msg = redis_client.hget(current_user, "MSG_MULTI")
                            if status == "SUCCESS":
                                st.success(f"Order Success: {msg}")
                            else:
                                st.error(f"Order Failed: {msg}")
                            break
                        time.sleep(0.5)
                    else:
                        st.warning("Order Timeout: Agent did not respond in time.")

            elif not valid_legs and all_resolved:
                    st.warning("No valid legs to submit.")   


        with tabs[1]:
            #st.subheader("Place Trade") 

            #Dropdown for NIFTY / BANKNIFTY
            index_choice = st.selectbox(" ",["NIFTY", "BANKNIFTY"])
        
            # #Order Type Selector (MIS / NRML)
            # order_type = st.radio("",["NRML", "MIS"],horizontal=True)
            order_type = "NRML"
            st.write(f"Order Type: {order_type}")

            col1, col2 = st.columns(2)
            with col1:
                #Quantity entry
                lots = st.number_input("Lots", min_value=1, step=1)
                qty = lots * (NIFTY_LOT_SIZE if index_choice == "NIFTY" else BANKNIFTY_LOT_SIZE)

            with col2:
                #Expiry Date Selector
                expiry_str = st.selectbox("ExpDate",["2026-01-27"])

            #Strike Price Selector
            default_strike = 26000 if index_choice == "NIFTY" else 55000
            strike = st.number_input("Strike Price",min_value=100,value=default_strike,step=50)

            # #Option Type Selector (CE / PE)
            # option_type = st.radio("",["CE", "PE"],horizontal=True)

            # #Execution Type
            # exec_type = st.radio("",["MARKET", "LIMIT"],horizontal=True)

            col1, col2 = st.columns(2)
            with col1:
                option_type = st.radio("Option Type", ["CE", "PE"], horizontal=True)
            with col2:
                exec_type = st.radio("Execution Type", ["MARKET", "LIMIT"], horizontal=True)

            #Limit Price (only for LIMIT)
            limit_price = None
            if exec_type == "LIMIT": limit_price = st.number_input("Limit Price", min_value=0.05, step=0.05, format="%.2f")

            col_buy, col_sell = st.columns(2)

            buy_clicked = col_buy.button("BUY")
            sell_clicked = col_sell.button("SELL")

            if buy_clicked or sell_clicked:
                side = "BUY" if buy_clicked else "SELL"

                # clear old legs 
                redis_client.hdel(current_user, "SINGLE_LEG")
                redis_client.hdel(current_user, "STATUS_SINGLE")
                redis_client.hdel(current_user, "MSG_SINGLE")

                settings = {
                    "Index": index_choice,
                    "OrderType": order_type,
                    "Qty": qty,
                    "Side": side,
                    "Expiry": expiry_str,
                    "Strike": strike,
                    "OptionType": option_type,
                    "ExecutionType": exec_type,
                }
                if exec_type == "LIMIT":
                    if not limit_price or limit_price <= 0:
                        st.error("Please enter a valid limit price")
                        st.stop()
                    settings["LimitPrice"] = float(limit_price)

                # Resolve Instrument ID
                inst_id = resolve_instrument_id(redis_client, settings)
                if inst_id:
                    settings["exchangeInstrumentID"] = inst_id
                    
                    # Wrap the single leg inside a list to keep the structure identical to multi-leg
                    single_leg_list = [settings]

                    redis_client.hset(
                        current_user,
                        mapping={
                            "PLACE_SINGLE": "requested",
                            "SINGLE_LEG": json.dumps(single_leg_list),
                            "STATUS_SINGLE": "PROCESSING"
                        }
                    )

                    with st.spinner("Processing Order..."):
                        timeout = 10
                        start_time = time.time()
                        while time.time() - start_time < timeout:
                            status = redis_client.hget(current_user, "STATUS_SINGLE")
                            if status in ["SUCCESS", "FAILED"]:
                                msg = redis_client.hget(current_user, "MSG_SINGLE")
                                if status == "SUCCESS":
                                    st.success(f"Order Success: {msg}")
                                else:
                                    st.error(f"Order Failed: {msg}")
                                break
                            time.sleep(0.5)
                        else:
                            st.warning("Order Timeout: Agent did not respond in time.")
                else:
                    st.error(f"Instrument not found for {index_choice} {expiry_str} {option_type} {strike}")
    


    # [FRAGMENT ADD]
    @st.fragment
    def render_ltp_fragment(filtered):
        refresh = st.button("Refresh LTP")

        df_view = filtered.copy()     
        # ---------------------------------------
        # Fetch LTP + Compute P/L
        # ---------------------------------------
        # ---------------------------------------
        # Fetch LTP + Compute P/L
        # ---------------------------------------
        cached_ltp = st.session_state.get("netwise_ltp")
        cached_pnl = st.session_state.get("netwise_pnl")
        cached_symbols = st.session_state.get("netwise_symbols", [])

        current_symbols = df_view["TradingSymbol"].tolist()

        needs_compute = (
            refresh
            or cached_ltp is None
            or cached_pnl is None
            or cached_symbols != current_symbols
        )

        if needs_compute:
            print(f"[UI] Computing PnL for {len(current_symbols)} symbols... (Refresh={refresh})")
            ltps = []
            pnls = []

            # [BATCH LTP]
            bulk_prices = fetch_ltp_bulk(current_symbols)

            for _, row in df_view.iterrows():
                sym = row["TradingSymbol"]
                qty = int(row["Quantity"])
                buy_p = float(row["Buy Price"])
                sell_p = float(row["Sell Price"])

                # Fetch LTP using the new resolver logic
                # ltp = fetch_ltp(sym) # [BATCH LTP FIX]
                ltp = bulk_prices.get(sym)
                ltps.append(ltp)

                if ltp is None:
                    pnls.append(None)
                    continue

                # Long
                if qty > 0:
                    avg = buy_p if buy_p > 0 else sell_p
                    pnl = (ltp - avg) * qty

                # Short
                else:
                    # For short positions, if there is a Sell Price use it, else Buy Price (unlikely for pure short but handling edge case)
                    avg = sell_p if sell_p > 0 else buy_p
                    pnl = (avg - ltp) * abs(qty)

                pnls.append(pnl)

            # [FRAGMENT ADD] Persist computed values
            st.session_state["netwise_ltp"] = [round(x, 2) if x is not None else None for x in ltps]
            st.session_state["netwise_pnl"] = [round(x, 2) if x is not None else None for x in pnls]
            st.session_state["netwise_symbols"] = current_symbols

        df_view["LTP"] = st.session_state.get("netwise_ltp")
        df_view["P/L"] = st.session_state.get("netwise_pnl")

        # ---------------------------------------
        # Styling
        # ---------------------------------------
        def color_quantity(val):
            if val < 0:
                return "color: red;"
            if val > 0:
                return "color: green;"
            return ""

        def color_pnl(val):
            if pd.isna(val):
                return ""
            if val < 0:
                return "color: red;"
            if val > 0:
                return "color: green;"
            return ""

        # [NULL-SAFETY FIX]
        df_view[["LTP", "P/L"]] = df_view[["LTP", "P/L"]].apply(pd.to_numeric, errors="coerce")
        # styled = (
        #     df_view.style
        #     .applymap(color_quantity, subset=["Quantity"])
        #     .applymap(color_pnl,     subset=["P/L"])
        #     .format({"LTP": "{:.2f}", "P/L": "{:.2f}"})
        # )

        # Ensure Payoff is the rightmost column
        if "Payoff" in df_view.columns:
            cols = [c for c in df_view.columns if c != "Payoff"] + ["Payoff"]
            df_view = df_view[cols]

        edited = st.data_editor(
            df_view,
            hide_index=True,
            width="stretch",
            disabled=[c for c in df_view.columns 
                if c not in ("Exit", "Payoff")
            ],
            column_config={
                "ExchangeInstrumentId": None
            },
            key="netwise_editor"
        )
        st.session_state["netwise_edited_df"] = edited


        selected = edited[edited["Exit"] == True]

        if not selected.empty:
            st.divider()

            if st.button(f"Square Off {len(selected)} Position(s)", type="primary"):
                st.session_state["confirm_squareoff"] = True

        if st.session_state.get("confirm_squareoff"):
            st.warning("This will exit selected positions at MARKET price.")

            c1, c2 = st.columns(2)
            if c1.button("Confirm Square Off", type="primary"):
                enqueue_squareoff_request(selected)
                st.session_state["confirm_squareoff"] = False
                st.success("Square-off request submitted.")
                st.rerun()

            if c2.button("Cancel"):
                st.session_state["confirm_squareoff"] = False

        # ---------------------------------------
        # Metrics (Total Quantity + MTM)
        # ---------------------------------------
        total_qty = df_view["Quantity"].sum()
        total_mtm = df_view["P/L"].fillna(0).sum()

        # ---------------------------------------
        # Metrics (Total Quantity + MTM)
        # ---------------------------------------
        total_qty = df_view["Quantity"].sum()
        total_mtm = df_view["P/L"].fillna(0).sum()

        c1, c2 = st.columns(2)
        c1.metric("Total Quantity", total_qty)
        mtm_color = "green" if total_mtm > 0 else "red" if total_mtm < 0 else "gray"

        c2.markdown(
            f"""
            <div style="text-align:center">
                <div style="font-size:14px; color:#6b7280;">Total MTM (P/L)</div>
                <div style="font-size:34px; font-weight:400; color:{mtm_color};">
                    {total_mtm:,.2f}
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

        # =================================================================
        # [FRAGMENT HELPER FUNCTIONS moved here for scope]
        # =================================================================
        import numpy as np
        import matplotlib.pyplot as plt
        import seaborn as sns
        import re
        import json
        import plotly.graph_objects as go

        def extract_strike_and_type(symbol):
            """Extract last integer as strike and CE/PE from trading symbol."""
            m = re.search(r"(\d+)$", str(symbol).strip())
            strike = int(m.group(1)) if m else None
            opt = "CE" if "CE" in str(symbol) else ("PE" if "PE" in str(symbol) else None)
            return strike, opt

        def compute_leg_payoff(spot_range, strike, option_type, premium, qty_sign, lots=1):
            """Return payoff array for single leg."""
            if option_type == "CE":
                intrinsic = np.maximum(spot_range - strike, 0)
            else:
                intrinsic = np.maximum(strike - spot_range, 0)
            return (intrinsic - premium) * qty_sign

        def build_combined_payoff(df_legs, spot_min=None, spot_max=None, points=600):
            """Build combined payoff over a spot grid for provided legs dataframe."""
            strikes = []
            for s in df_legs["TradingSymbol"].tolist():
                stk, _ = extract_strike_and_type(s)
                if stk:
                    strikes.append(stk)
            if not strikes:
                spot_min = 0 if spot_min is None else spot_min
                spot_max = 100000 if spot_max is None else spot_max
            else:
                mn = min(strikes) - 1500
                mx = max(strikes) + 1500
                spot_min = mn if spot_min is None else spot_min
                spot_max = mx if spot_max is None else spot_max

            S = np.linspace(spot_min, spot_max, points)
            total = np.zeros_like(S)

            for _, row in df_legs.iterrows():
                sym = row["TradingSymbol"]
                qty = int(row["Quantity"])
                strike, opt = extract_strike_and_type(sym)
                if strike is None or opt is None:
                    continue

                buy_p = float(row.get("Buy Price") or 0)
                sell_p = float(row.get("Sell Price") or 0)

                if buy_p > 0 and sell_p == 0:
                    premium = buy_p
                    qty_sign = 1  # long
                elif sell_p > 0 and buy_p == 0:
                    premium = sell_p
                    qty_sign = -1  # short
                elif buy_p > 0 and sell_p > 0:
                    premium = abs(buy_p - sell_p)
                    qty_sign = 1 if qty > 0 else -1
                else:
                    continue

                leg_payoff = compute_leg_payoff(S, strike, opt, premium, qty_sign) * abs(qty)
                total += leg_payoff

            return S, total

        def enqueue_squareoff_request(df_selected):
            legs = []
            for _, row in df_selected.iterrows():
                qty = int(row["Quantity"])
                if qty == 0:
                    continue
                legs.append({
                    "exchangeInstrumentID": int(row["ExchangeInstrumentId"]),
                    "Quantity": abs(qty),
                    "Side": "SELL" if qty > 0 else "BUY",
                    "TradingSymbol": row["TradingSymbol"]
                })

            if not legs:
                return

            redis_client.hset(
                current_user,
                mapping={
                    "SQUAREOFF_REQUEST": json.dumps(legs),
                    "SQUAREOFF_STATUS": "REQUESTED"
                }
            )

        def plot_payoff_plotly(S, total):
            max_profit = np.max(total)
            max_profit_x = S[np.argmax(total)]
            max_loss = np.min(total)
            max_loss_x = S[np.argmin(total)]

            sign_changes = np.where(np.sign(total[:-1]) != np.sign(total[1:]))[0]
            breakevens = [(S[i] + S[i+1]) / 2 for i in sign_changes]

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=S, y=total, mode="lines",
                line=dict(color="#00E676", width=4), name="Payoff",
                hovertemplate="Spot: %{x}<br>P/L: %{y}"))
            fig.add_trace(go.Scatter(x=S, y=np.where(total > 0, total, 0),
                fill='tozeroy', mode='none', fillcolor='rgba(0, 230, 118, 0.15)', name='Profit Zone'))
            fig.add_trace(go.Scatter(x=S, y=np.where(total < 0, total, 0),
                fill='tozeroy', mode='none', fillcolor='rgba(255, 82, 82, 0.15)', name='Loss Zone'))
            
            # Markers
            fig.add_trace(go.Scatter(x=[max_profit_x], y=[max_profit],
                mode='markers+text', marker=dict(color="#00E676", size=10),
                text=[f"Max Profit<br>{max_profit:.0f}"], textposition="top center", name="Max Profit"))
            fig.add_trace(go.Scatter(x=[max_loss_x], y=[max_loss],
                mode='markers+text', marker=dict(color="#FF5252", size=10),
                text=[f"Max Loss<br>{max_loss:.0f}"], textposition="bottom center", name="Max Loss"))

            for be in breakevens:
                fig.add_shape(type="line", x0=be, x1=be, y0=min(total), y1=max(total),
                    line=dict(color="#42A5F5", width=2, dash="dot"))
                fig.add_annotation(x=be, y=0, text=f"BE {be:.1f}", showarrow=False,
                    font=dict(color="#42A5F5", size=12))

            fig.update_layout(template="plotly_dark", title="Options Payoff Diagram",
                xaxis_title="Underlying Price at Expiry", yaxis_title="Profit / Loss",
                xaxis=dict(tickformat=".0f"), yaxis=dict(tickformat=".0f"),
                hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=40, r=40, t=60, b=40), height=500)
            
            st.plotly_chart(fig, width='stretch')

        # --- Payoff selection via table checkbox ---
        edited_df = st.session_state.get("netwise_edited_df")

        if edited_df is not None:
             payoff_df = edited_df[edited_df["Payoff"] == True]
             if not payoff_df.empty:
                 S, total = build_combined_payoff(payoff_df)
                 c1, c2 = st.columns(2)
                 c1.metric("Estimated Max Profit", f"{np.max(total):.2f}")
                 c2.metric("Estimated Max Loss", f"{np.min(total):.2f}")
                 plot_payoff_plotly(S, total)
             else:
                 st.info("Select at least one position to view Payoff Chart.")


    with tabs[2]:
        #st.subheader("Netwise Position")

        # Button to fetch position
        if st.button("Fetch Net Position"):
            settings = {
                    "POSITION": "requested",
                }
            redis_client.hset(current_user, mapping=settings)
            st.session_state["load_position"] = True
            time.sleep(1)

        if st.session_state.get("load_position"): 
            #path
            position_file = os.path.join(base_dir, current_user, "positions.csv")
            # Read the CSV into a DataFrame
            try:
                df = pd.read_csv(position_file)
                # st.write("Positions Loaded:", len(df))
                # st.write(df.head())
                df = df[df["Quantity"] != 0]
                df["OptionType"] = df["TradingSymbol"].str.extract(r"(CE|PE)")

            # --- FILTERS ON TOP ---
                col1, col2 = st.columns([1, 1])

                option_type = col1.radio(
                    "",
                    ("All", "CE", "PE"),
                    horizontal=True
                )
                index_type = col2.radio(
                    "",
                    ("All", "NIFTY", "BANKNIFTY"),
                    horizontal=True
                )

                # --- APPLY FILTERS ---
                filtered = df.copy()

                # CE/PE filter
                if option_type != "All":
                    filtered = filtered[filtered["OptionType"] == option_type]

                # Index filter (NIFTY / BANKNIFTY)
                if index_type != "All":
                    filtered = filtered[
                        filtered["TradingSymbol"].astype(str).str.startswith(index_type)
                    ]


                # --- SHOW FINAL TABLE ---   
                wanted_cols = ["TradingSymbol", "BuyAveragePrice", "SellAveragePrice", "Quantity","ExchangeInstrumentId"]
                filtered = filtered[wanted_cols]
                filtered = filtered.rename(columns={
                    "BuyAveragePrice": "Buy Price",
                    "SellAveragePrice": "Sell Price",
                })

                # ---- EXIT SELECTION COLUMN ----
                filtered = filtered.copy()
                filtered["Exit"] = False

                # Move Exit to first column
                cols = ["Exit"] + [c for c in filtered.columns if c != "Exit"]
                filtered = filtered[cols]

                # ---- PAYOFF SELECTION COLUMN (for payoff chart) ----
                filtered["Payoff"] = True   # default: all selected

                cols = [c for c in filtered.columns if c != "Payoff"] + ["Payoff"]
                filtered = filtered[cols]

                # [FRAGMENT ADD]
                if "Payoff" not in filtered.columns:
                    filtered["Payoff"] = True
                render_ltp_fragment(filtered)

                print("[SUCCESS] Positions loaded successfully.")

                print("[SUCCESS] Positions loaded successfully.")
            except Exception as e:
                print(f"[ERROR] Failed to read positions file: {e}") 
                st.error(f"Stack trace: {e}")   
       
    with tabs[3]:
        #st.subheader("BreakTrades")
        subtab_ce, subtab_pe = st.tabs(["BreakOut", "BreakDown"])

    #---------#Level CE Trade------------
    with subtab_ce:
        colA, colB = st.columns([1,1])

        with colA:
            selected_index = st.radio("Index", ["NIFTY", "BANKNIFTY"], key="level_ce_index", horizontal=True)
            #Read current spot from Redis
            if selected_index == "NIFTY":
                current_spot = redis_client.get("NF_SPOT")
            else:
                current_spot = redis_client.get("BN_SPOT")

            st.info(f"Current {selected_index} Spot: {current_spot}")

            default_level = 26000 
        with colB:    
            level_setter = st.number_input("Place Order When Index Is Above", min_value=1, step=1,value=default_level, key="level_setter1")
            
        col1, col2, col3, col4 = st.columns(4)
        leg1 = render_leg_input(col1, "t2_1")
        leg2 = render_leg_input(col2, "t2_2")
        leg3 = render_leg_input(col3, "t2_3")
        leg4 = render_leg_input(col4, "t2_4")

        if st.button("Place Order", key="t2_multi_order"):
            # Clear old keys - ONLY relevant ones
            redis_client.hdel(current_user, "LEVEL_CE")
            redis_client.hdel(current_user, "STATUS_LEVEL_CE")
            redis_client.hdel(current_user, "MSG_LEVEL_CE")
            
            
            raw_legs = [leg1, leg2, leg3, leg4]
            valid_legs = []
            all_resolved = True

            for leg in raw_legs:
                if leg["Side"] and leg["Side"].strip() != "":
                    l = leg.copy()
                    l["Expiry"] = l["Expiry"]
                    
                    # Resolve ID
                    inst_id = resolve_instrument_id(redis_client, l)
                    if inst_id:
                        l["exchangeInstrumentID"] = inst_id
                        valid_legs.append(l)
                    else:
                        st.error(f"Instrument not found for {l['Index']} {l['Expiry']} {l['OptionType']} {l['Strike']}")
                        all_resolved = False
                        break
            
            if valid_legs and all_resolved:
                # NEW — store trigger metadata
                redis_client.hset(current_user, "LEVEL_CE_TRIGGER", "waiting")
                redis_client.hset(current_user, "LEVEL_CE_LEVEL", level_setter)
                redis_client.hset(current_user, "LEVEL_CE_INDEX", selected_index)

                # #store legs
                # redis_client.hset(
                #     current_user,
                #     mapping={
                #         "LEVEL_CE": json.dumps(valid_legs),
                #         "STATUS_LEVEL_CE": "WAITING"
                #     }
                # )

                # st.success(f"LEVEL CE set at level {level_setter} for {selected_index}. Waiting for breakout...")
                
                redis_client.hset(
                    current_user,
                    mapping={
                        "LEVEL_CE": json.dumps(valid_legs),
                        "STATUS_LEVEL_CE": "WAITING"
                    }
                )
                st.success(f"LEVEL CE set at level {level_setter} for {selected_index}. Waiting for breakout...")
                st.info("LEVEL CE order armed. It will execute automatically when the index crosses your level.")

            elif not valid_legs and all_resolved:
                st.warning("No valid legs to submit.")

#---------#Level PE Trade------------
    with subtab_pe:
        colA, colB = st.columns([1,1])

        with colA:
            selected_index = st.radio("Index", ["NIFTY", "BANKNIFTY"], key="level_pe_index", horizontal=True)
            if selected_index == "NIFTY":
                current_spot = redis_client.get("NF_SPOT")
            else:
                current_spot = redis_client.get("BN_SPOT")

            st.info(f"Current {selected_index} Spot: {current_spot}")
            default_level = 26000

        with colB:
            level_setter = st.number_input("Place Order When Index Is Below", min_value=1, step=1, value=default_level, key="level_setter2")
        
        col1, col2, col3, col4 = st.columns(4)
        leg1 = render_leg_input(col1, "t3_1")
        leg2 = render_leg_input(col2, "t3_2")
        leg3 = render_leg_input(col3, "t3_3")
        leg4 = render_leg_input(col4, "t3_4")

        if st.button("Place Order", key="t3_multi_order"):
            # Clear old keys - ONLY relevant ones
            redis_client.hdel(current_user, "LEVEL_PE")
            redis_client.hdel(current_user, "STATUS_LEVEL_PE")
            redis_client.hdel(current_user, "MSG_LEVEL_PE")
                               
            raw_legs = [leg1, leg2, leg3, leg4]
            valid_legs = []
            all_resolved = True
            
            for leg in raw_legs:
                if leg["Side"] and leg["Side"].strip() != "":
                    l = leg.copy()
                    l["Expiry"] = l["Expiry"]
                    
                    # Resolve ID
                    inst_id = resolve_instrument_id(redis_client, l)
                    if inst_id:
                        l["exchangeInstrumentID"] = inst_id
                        valid_legs.append(l)
                    else:
                        st.error(f"Instrument not found for {l['Index']} {l['Expiry']} {l['OptionType']} {l['Strike']}")
                        all_resolved = False
                        break
            
            if valid_legs and all_resolved:
                # NEW — store trigger metadata
                redis_client.hset(current_user, "LEVEL_PE_TRIGGER", "waiting")
                redis_client.hset(current_user, "LEVEL_PE_LEVEL", level_setter)
                redis_client.hset(current_user, "LEVEL_PE_INDEX", selected_index)

                # # Store final legs
                # redis_client.hset(
                #     current_user,
                #     mapping={
                #         "LEVEL_PE": json.dumps(valid_legs),
                #         "STATUS_LEVEL_PE": "WAITING"
                #     }
                # )
                #st.success(f"LEVEL PE set at level {level_setter} for {selected_index}. Waiting for breakdown...")
                redis_client.hset(
                    current_user,
                    mapping={
                        "LEVEL_PE": json.dumps(valid_legs),
                        "STATUS_LEVEL_PE": "WAITING"
                    }
                )
                st.success(f"LEVEL PE set at level {level_setter} for {selected_index}. Waiting for breakdown...")
                st.info("LEVEL PE order armed. It will execute automatically when the index crosses your level.")

            elif not valid_legs and all_resolved:
                st.warning("No valid legs to submit.")


    with tabs[4]:
        #st.subheader("OrderBook")

        # Button to fetch order book
        if st.button("Fetch OrderBook"):
            settings = {
                "ORDERBOOK": "requested",
            }
            redis_client.hset(current_user, mapping=settings)

            time.sleep(2)

            # Path
            orderbook_file = os.path.join(base_dir, current_user, "orderbook.csv")

            # Read CSV
            try:
                df = pd.read_csv(orderbook_file)
                print("[SUCCESS] OrderBook loaded successfully.")

                st.session_state["orderbook_df"] = df
                
            except Exception as e:
                print(f"[ERROR] Failed to read Orderbook file: {e}")
                df = None

        df = st.session_state.get("orderbook_df")

        # =====================================================================
        # ⭐ APPLY FILTERS ONLY IF DATA EXISTS
        # =====================================================================
        if df is not None:
        
            # ⭐ Extract CE/PE
            df["OptionType"] = df["TradingSymbol"].str.extract(r"(CE|PE)")

            # ⭐ FILTER BAR (Radio on left, Qty on right)
            col1, col2 = st.columns([1, 1])

            option_type = col1.radio(
                "Option Type",
                ("All", "CE", "PE"),
                horizontal=True,
                key="orderbook_option_filter"
            )
            index_type = col2.radio(
                "Index",
                ("All", "NIFTY", "BANKNIFTY"),
                horizontal=True,
                key="orderbook_index_filter"
            )


            # ⭐ APPLY FILTERS
            filtered = df.copy()

            # Filter CE/PE
            if option_type != "All":
                filtered = filtered[filtered["OptionType"] == option_type]

            # Filter Index (NIFTY / BANKNIFTY)
            if index_type != "All":
                filtered = filtered[filtered["TradingSymbol"].astype(str).str.contains(index_type)]

            filtered = filtered.rename(columns={"BuyAveragePrice": "Buy Price","SellAveragePrice" : "Sell Price", })

            # ⭐ Sort by latest first
            if "ExchangeTransactTime" in filtered.columns: filtered = filtered.sort_values(by="ExchangeTransactTime", ascending=False)
            
            filtered["OrderStatus"] = filtered["OrderStatus"].astype(str).str.upper()
            # ================================
            # ⭐ SPLIT FILLED vs FAILED
            # ================================
            filled_df = filtered[filtered["OrderStatus"] == "FILLED"]
            failed_df = filtered[filtered["OrderStatus"] != "FILLED"]

        
            # ⭐ FILLED ORDERS (MAIN TABLE)
            st.dataframe(filled_df, hide_index=True)

            # ⭐ FAILED / PENDING ORDERS
            if not failed_df.empty:
                st.subheader("Failed / Pending Orders")
                st.dataframe(failed_df, hide_index=True)

    with tabs[5]:
        #st.subheader("Watchlist")

        subtab1, subtab2 = st.tabs(["Nifty", "BankNifty"])

        with subtab1:
            nifty_watchlist()

        with subtab2:
            banknifty_watchlist()
             

    with tabs[6]:
        #st.subheader("📊 Reports")

        # Sync latest data to MotherDuck
        col_sync, col_space = st.columns([1, 4])
        with col_sync:
            if st.button("🔄 Sync Data"):
                try:
                    from order_ingest import push_orderbook
                    with st.spinner("Syncing data..."):
                        push_orderbook(current_user, base_dir)
                    st.success("Sync Complete")
                    time.sleep(1) # Give user time to see success message
                    st.rerun()
                except Exception as e:
                    st.error(f"Data Sync Failed: {e}")

        table_name = f"trading.orderbook_{current_user}"

        report_type = st.radio(
            "Select Report",
            ["Ungrouped", "Closed Positions (PnL)"], horizontal=True)

        limit = 100

        # -----------------------------------------------
        # Ungrouped (direct MotherDuck data) — CLEAN version with data_editor
        # -----------------------------------------------
        if report_type == "Ungrouped":
            try:
                df = md_con.execute(f"""
                    SELECT *
                    FROM {table_name}
                    WHERE GroupName = 'Ungrouped'
                    ORDER BY OrderGeneratedDateTime DESC
                    LIMIT {limit}
                """).fetchdf()

                if df.empty:
                    st.info("No trades found.")
                else:
                    # Ensure GroupName exists
                    if "GroupName" not in df.columns:
                        df["GroupName"] = "Ungrouped"
                    else:
                        df["GroupName"] = df["GroupName"].fillna("Ungrouped")
                    # Move GroupName column to the leftmost
                    cols = ["GroupName"] + [c for c in df.columns if c != "GroupName"]
                    df = df[cols]

                    st.write(" Edit group assignments directly in the table")

                    editable_df = st.data_editor(
                        df,
                        hide_index=True,
                        width='stretch',
                    )

                    if st.button("Save group assignments"):
                        try:
                            updates = 0
                            for _, row in editable_df.iterrows():
                                app_id = int(row["AppOrderID"])
                                grp = row["GroupName"]

                                # If user leaves empty OR selects None → Treat as UNGROUPED
                                if grp in (None, "", "None") or pd.isna(grp):
                                    md_con.execute(
                                        f"UPDATE {table_name} SET GroupName = 'Ungrouped' WHERE AppOrderID = {app_id}"
                                    )
                                else:
                                    md_con.execute(
                                        f"UPDATE {table_name} SET GroupName = '{grp}' WHERE AppOrderID = {app_id}"
                                    )

                                updates += 1

                            st.success(f"Updated {updates} group assignments.")

                        except Exception as e:
                            st.error(f"Failed to update groups: {e}")

            except Exception as e:
                st.error(f"Error: {e}")


        # -----------------------------------------------
        # CLOSED POSITIONS (FIFO P&L)
        # -----------------------------------------------
        elif report_type == "Closed Positions (PnL)":
            try:
                # Cache groups in session_state to avoid re-querying on every interaction
                if "cached_pnl_groups" not in st.session_state:
                    groups_df = md_con.execute(f"""
                        SELECT DISTINCT GroupName 
                        FROM {table_name}
                        WHERE GroupName IS NOT NULL 
                        AND GroupName <> 'Ungrouped'
                        ORDER BY GroupName
                    """).fetchdf()
                    st.session_state.cached_pnl_groups = sorted(groups_df["GroupName"].dropna().tolist())
                
                groups = st.session_state.cached_pnl_groups
                
                # Button to refresh groups list if user added new groups
                if st.button("🔄 Refresh Groups", key="refresh_groups_btn"):
                    del st.session_state["cached_pnl_groups"]
                    st.rerun()
                
                if not groups:
                    st.info("No groups available. Assign groups in Ungrouped section.")
                    st.stop()
                # Multi-select UI (without form - forms have issues with multiselect state)
                selected_groups = st.multiselect("Select Groups", groups, key="pnl_group_selector")
                apply = st.button("Apply Filter", key="apply_pnl_filter")

                if apply or selected_groups:  # Show results if groups are selected
                    if not selected_groups:
                        st.info("No groups selected. Please select at least one group.")
                    else:
                        # Build SQL for multi-group filtering
                        group_list_sql = ",".join([f"'{g}'" for g in selected_groups])
                        group_filter_sql = f"AND GroupName IN ({group_list_sql})"
                        
                        # FIFO pairing: partitioned by BOTH TradingSymbol AND GroupName
                        # This ensures buy/sell pairs within the same group are matched correctly
                        sql = f"""
                            WITH base AS (
                                SELECT 
                                    TradingSymbol,
                                    OrderSide,
                                    OrderQuantity AS qty,
                                    OrderAverageTradedPrice AS price,
                                    OrderGeneratedDateTime,
                                    GroupName,
                                    ROW_NUMBER() OVER(PARTITION BY GroupName, TradingSymbol, OrderSide ORDER BY OrderGeneratedDateTime) as rn
                                FROM {table_name}
                                WHERE OrderStatus = 'Filled'
                                {group_filter_sql}
                            ),
                            buys AS (
                                SELECT 
                                    *, 
                                    SUM(qty) OVER(PARTITION BY GroupName, TradingSymbol ORDER BY OrderGeneratedDateTime, rn) AS cum_buy,
                                    SUM(qty) OVER(PARTITION BY GroupName, TradingSymbol ORDER BY OrderGeneratedDateTime, rn) - qty AS buy_start
                                FROM base WHERE OrderSide = 'BUY'
                            ),
                            sells AS (
                                SELECT 
                                    *, 
                                    SUM(qty) OVER(PARTITION BY GroupName, TradingSymbol ORDER BY OrderGeneratedDateTime, rn) AS cum_sell,
                                    SUM(qty) OVER(PARTITION BY GroupName, TradingSymbol ORDER BY OrderGeneratedDateTime, rn) - qty AS sell_start
                                FROM base WHERE OrderSide = 'SELL'
                            ),
                            paired AS (
                                SELECT 
                                    b.GroupName,
                                    b.TradingSymbol,
                                    b.price AS BuyPrice,
                                    s.price AS SellPrice,
                                    -- Intersection of [b_start, b_end] and [s_start, s_end]
                                    (LEAST(b.cum_buy, s.cum_sell) - GREATEST(b.buy_start, s.sell_start)) AS ClosedQty,
                                    b.OrderGeneratedDateTime AS BuyTime,
                                    s.OrderGeneratedDateTime AS SellTime,
                                    (s.price - b.price) * (LEAST(b.cum_buy, s.cum_sell) - GREATEST(b.buy_start, s.sell_start)) AS PnL
                                FROM buys b
                                JOIN sells s
                                ON b.TradingSymbol = s.TradingSymbol
                                AND b.GroupName = s.GroupName
                                -- Overlap condition: Start of one is before End of other
                                AND b.buy_start < s.cum_sell
                                AND s.sell_start < b.cum_buy
                            )
                            SELECT *
                            FROM paired
                            WHERE ClosedQty > 0
                            ORDER BY GroupName, BuyTime DESC
                            LIMIT {limit};
                        """

                        df = md_con.execute(sql).fetchdf()

                        if df.empty:
                            st.info("No closed positions found for the selected groups.")
                        else:
                            st.dataframe(df, hide_index=True, width='stretch')
                            st.metric("Total Realised P&L", f"{df['PnL'].sum():,.2f}")

            except Exception as e:
                st.error(f"Error: {e}")


    with tabs[7]:
        #redis datas
        if st.button("Refresh"):
            st.rerun()

        #st.subheader("🔌 Redis Orders (Raw hget View)")

        def h(key):
            val = redis_client.hget(current_user, key)
            return val if val is not None else "-"

        col1, col2, col3, col4 = st.columns(4)
        # -------------------- SINGLE LEG --------------------
        with col1:
            st.write(" 🎯 Single Leg")
            st.write("**Status:**", h("STATUS_SINGLE"))
            st.write("**Data:**")
            st.write(h("SINGLE_LEG"))


        # -------------------- MULTI LEGS --------------------
        with col2:
            st.write("📦 Multi-Legs")
            st.write("**Status:**", h("STATUS_MULTI"))
            st.write("**Data:**")
            st.write(h("MULTI_LEGS"))



        # -------------------- LEVEL CE ----------------------
        with col3:
            st.write("📈 BreakOut")
            status_ce =h("STATUS_LEVEL_CE")
            st.write("**Status:**", status_ce)
            st.write("**Data:**")
            st.write(h("LEVEL_CE"))

            if status_ce == "WAITING":
                if st.button("❌ Cancel BreakOut Order", key="cancel_level_ce"):
                    redis_client.hset(current_user, "STATUS_LEVEL_CE", "CANCELLED")
                    redis_client.hset(current_user, "LEVEL_CE_TRIGGER", "cancelled")

                    redis_client.hdel(current_user, "LEVEL_CE")
                    redis_client.hdel(current_user, "LEVEL_CE_LEVEL")
                    redis_client.hdel(current_user, "LEVEL_CE_INDEX")

                    st.success("BreakOut order cancelled safely.")
                    st.rerun()
            else:
                st.button("❌ Cancel BreakOut Order", disabled=True)


        # -------------------- LEVEL PE ----------------------
        with col4:
            st.write(" 📉 BreakDown")
            status_pe =h("STATUS_LEVEL_PE")
            st.write("**Status:**", status_pe)
            st.write("**Data:**")
            st.write(h("LEVEL_PE"))

            if status_pe == "WAITING":
                if st.button("❌ Cancel BreakDown Order", key="cancel_level_pe"):
                    redis_client.hset(current_user, "STATUS_LEVEL_PE", "CANCELLED")
                    redis_client.hset(current_user, "LEVEL_PE_TRIGGER", "cancelled")

                    redis_client.hdel(current_user, "LEVEL_PE")
                    redis_client.hdel(current_user, "LEVEL_PE_LEVEL")
                    redis_client.hdel(current_user, "LEVEL_PE_INDEX")

                    st.success("BreakDown order cancelled safely.")
                    st.rerun()
            else:
                st.button("❌ Cancel BreakDown Order", disabled=True)

        st.divider()
        st.write("🤖 OI Auto Trade (Redis)")
        #------------------------------------------------
        # ---- Last Signal ----
        st.write("📡 Last OI Signal")
        last_signal = redis_client.hget(current_user, "OI_ENGINE_LAST_SIGNAL")
        if last_signal:
            st.json(json.loads(last_signal))
        else:
            st.write("No signal yet")

        st.divider()

        # ---- Last Order ----
        st.write("📦 Last OI Auto Order")
        last_order = redis_client.hget(current_user, "OI_ENGINE_LAST_ORDER")
        if last_order:
            st.json(json.loads(last_order))
        else:
            st.write("No order generated yet")

        st.divider()

        # ---- Message / Error ----
        msg = redis_client.hget(current_user, "MSG_OI_CROSSOVER")
        if msg:
            st.error(msg)

#----------------------------------------------------------------------
    with tabs[8]:
        st.subheader("💰 Balance") 
        

        # Button to fetch Balance
        if st.button("Fetch Balance"):
            settings = {
                    "BALANCE": "requested",
                }
            redis_client.hset(current_user, mapping=settings)
            
            time.sleep(3)
                #path
            balance_file = os.path.join(base_dir, current_user, "balance.csv")
            # Read the CSV into a DataFrame
            try:
                df = pd.read_csv(balance_file)
                df= df.rename(columns={"cashAvailable": "Cash Available","netMarginAvailable" : "Net Margin Available", "marginUtilized" : "Margin Utilized" })
                st.dataframe(df,  hide_index=True)
                print("[SUCCESS] Balance loaded successfully.")
            except Exception as e:
                print(f"[ERROR] Failed to read Balance file: {e}")
            # else:
            #     st.info(f"Balance is already '{Bal}'.")

        st.divider() 

        # ============================
        # 🔐 LOGIN / SESSION SECTION
        # ============================
        st.subheader("Login")

        if st.button("Check Session Status"):
            redis_client.hdel(current_user, "SESSION_STATUS")
            redis_client.hdel(current_user, "SESSION_MESSAGE")
            
            redis_client.hset(
                current_user,
                mapping={
                    "CHECK_SESSION": "requested",
                    "SESSION_STATUS": "PROCESSING"
                }
            )
            
            with st.spinner("Checking Session Status..."):
                timeout = 10
                start_time = time.time()
                while time.time() - start_time < timeout:
                    status = redis_client.hget(current_user, "SESSION_STATUS")
                    if status in ["ACTIVE", "EXPIRED"]:
                        msg = redis_client.hget(current_user, "SESSION_MESSAGE")
                        if status == "ACTIVE":
                            st.success(f"🟢 {msg}")
                        else:
                            st.error(f"🔴 XTS Session is EXPIRED / INVALID: {msg}")
                        break
                    time.sleep(0.5)
                else:
                    st.warning("Session Check Timeout: Agent did not respond in time.")

        st.write("Generate a new XTS token using the agent.")
        if st.button("Generate Token"):
            redis_client.hdel(current_user, "LOGIN_STATUS")
            redis_client.hdel(current_user, "LOGIN_MESSAGE")
            
            redis_client.hset(
                current_user,
                mapping={
                    "LOGIN": "requested",
                    "LOGIN_STATUS": "PROCESSING"
                }
            )
            
            with st.spinner("Generating Token..."):
                timeout = 15
                start_time = time.time()
                while time.time() - start_time < timeout:
                    status = redis_client.hget(current_user, "LOGIN_STATUS")
                    if status in ["SUCCESS", "FAILED"]:
                        msg = redis_client.hget(current_user, "LOGIN_MESSAGE")
                        if status == "SUCCESS":
                            st.success(f"Login Success: {msg}")
                        else:
                            st.error(f"Login Failed: {msg}")
                        break
                    time.sleep(0.5)
                else:
                    st.warning("Login Timeout: Agent did not respond in time.")
#---------------------------------------------------------------------------------

    with tabs[9]:
        #st.subheader("Equity")

        # Inputs
        eq_col1, eq_col2 = st.columns(2)
        
        with eq_col1:
            eq_symbol = st.text_input("Symbol (e.g. RELIANCE)", value="").upper().strip()
            eq_qty = st.number_input("Quantity", min_value=1, step=1, key="eq_qty")
        
        with eq_col2:
            eq_side = st.selectbox("Side", ["BUY", "SELL"], key="eq_side")
            eq_product = st.selectbox("Product Type", ["CNC", "MIS"], key="eq_product")
            
        st.info("Order Type is fixed to MARKET")
        
        if st.button("Place Equity Order"):
            if not eq_symbol:
                st.error("Please enter a symbol.")
            else:
                # Validation: Check directly in Redis XTS_INSTR_EQ
                exch_inst_id = redis_client.hget("XTS_INSTR_EQ", eq_symbol)
                
                if not exch_inst_id:
                    st.error(f"Symbol '{eq_symbol}' not found in XTS_INSTR_EQ. Please run instruments.py to update master.")
                else:
                    # Construct Payload
                    payload = {
                        "state": "requested",
                        "symbol": eq_symbol,
                        "exchangeInstrumentID": int(exch_inst_id),
                        "side": eq_side,
                        "qty": int(eq_qty),
                        "product": eq_product,
                        "order_type": "MARKET"
                    }
                    
                    # Per-User Hash Write
                    # 1. Clear old status/data
                    redis_client.hdel(current_user, "EQUITY_ORDER") 
                    redis_client.hdel(current_user, "STATUS_EQUITY")
                    
                    # 2. Set new request
                    redis_client.hset(
                        current_user,
                        mapping={
                            "PLACE_EQUITY": "requested",
                            "EQUITY_ORDER": json.dumps(payload),
                            "STATUS_EQUITY": "PROCESSING"
                        }
                    )
                    
                    st.success(f"Equity Order Requested for {eq_symbol}. ID: {exch_inst_id}")
                    
                    # Spinner waiting for STATUS_EQUITY in user hash
                    with st.spinner("Processing Equity Order..."):
                         timeout = 5
                         start = time.time()
                         while time.time() - start < timeout:
                             status = redis_client.hget(current_user, "STATUS_EQUITY")
                             # We expect SUCCESS or FAILED or PROCESSING. 
                             # If it stays PROCESSING, we wait.
                             # If it changes to SUCCESS/FAILED, we show it.
                             
                             if status and status != "PROCESSING":
                                 if "SUCCESS" in status:
                                     st.success(f"Execution: {status}")
                                 else:
                                     st.error(f"Execution: {status}")
                                 # Optional: clear status or leave it for history
                                 break
                             time.sleep(0.2)

#-----------------------------------------------------------------------------------------

    with tabs[10]:
        # -------------------------------
        # OI AUTO TRADE TAB
        # -------------------------------
        subtab1, subtab2 = st.tabs(["NIFTY", "BANKNIFTY"])
    with subtab1:
        #st.write("🤖 NIFTY OI Auto Trade Engine")
        # ---- Engine ON / OFF ----
        current_state = redis_client.hget(current_user, "OI_ENGINE_ENABLED")

        if current_state is None:
            redis_client.hset(current_user, "OI_ENGINE_ENABLED", "OFF")
            current_state = "OFF"

        is_enabled = current_state == "ON"

        toggle = st.toggle(
            "Enable NIFTY OI Auto Trading",
            value=is_enabled,key="nf_oi_toggle"
        )

        if toggle and current_state != "ON":
            redis_client.hset(current_user, "OI_ENGINE_ENABLED", "ON")
            st.success("NIFTY OI Auto Trade Engine ENABLED")

        elif not toggle and current_state != "OFF":
            redis_client.hset(current_user, "OI_ENGINE_ENABLED", "OFF")
            st.warning("NIFTY OI Auto Trade Engine DISABLED")

        st.divider()

        # ---- Lot Settings ----
        current_lots = int(
            redis_client.hget(current_user, "OI_NIFTY_LOTS") or 1
        )

        lots = st.number_input(
            "NIFTY OI Lots",
            min_value=1,
            step=1,
            value=current_lots,
            key="oi_nf_lots"
        )

        if lots != current_lots:
            redis_client.hset(current_user, "OI_NIFTY_LOTS", lots)
            st.success(f"NIFTY OI lot size set to {lots}")

        
        # ---- Engine Status ----
        col1, col2 = st.columns(2)

        with col1:
            status = redis_client.hget(current_user, "OI_ENGINE_STATUS") or "UNKNOWN"
            st.metric("Engine Status", status)

        with col2:
            order_status = redis_client.hget(current_user, "STATUS_OI_CROSSOVER") or "-"
            st.metric("Last Order Status", order_status)

        st.divider()

        # ---- Market Context ----
        spot = redis_client.get("NF_SPOT")
        st.info(f"NIFTY Spot (NF_SPOT): {spot}")

        st.divider()

        # ---- Last Signal ----
        st.write("📡 Last OI Signal")
        last_signal = redis_client.hget(current_user, "OI_ENGINE_LAST_SIGNAL")
        if last_signal:
            st.json(json.loads(last_signal))
        else:
            st.write("No signal yet")

        st.divider()

        # ---- Last Order ----
        st.write("📦 Last OI Auto Order")
        last_order = redis_client.hget(current_user, "OI_ENGINE_LAST_ORDER")
        if last_order:
            st.json(json.loads(last_order))
        else:
            st.write("No order generated yet")

        st.divider()

        # ---- Message / Error ----
        msg = redis_client.hget(current_user, "MSG_OI_CROSSOVER")
        if msg:
            st.error(msg)


    with subtab2:
        #st.write("🤖 BANKNIFTY OI Auto Trade Engine")

        current_state = redis_client.hget(current_user, "BN_OI_ENGINE_ENABLED")

        if current_state is None:
            redis_client.hset(current_user, "BN_OI_ENGINE_ENABLED", "OFF")
            current_state = "OFF"

        is_enabled = current_state == "ON"

        toggle = st.toggle(
            "Enable BANKNIFTY OI Auto Trading",
            value=is_enabled,
            key="bn_oi_toggle"
        )

        if toggle and current_state != "ON":
            redis_client.hset(current_user, "BN_OI_ENGINE_ENABLED", "ON")
            st.success("BANKNIFTY OI Auto Trade Engine ENABLED")

        elif not toggle and current_state != "OFF":
            redis_client.hset(current_user, "BN_OI_ENGINE_ENABLED", "OFF")
            st.warning("BANKNIFTY OI Auto Trade Engine DISABLED")

        st.divider()

        # ---- Lot Settings ----
        current_lots = int(
            redis_client.hget(current_user, "OI_BANKNIFTY_LOTS") or 1
        )

        lots = st.number_input(
            "BANKNIFTY OI Lots",
            min_value=1,
            step=1,
            value=current_lots,
            key="oi_bn_lots"
        )

        if lots != current_lots:
            redis_client.hset(current_user, "OI_BANKNIFTY_LOTS", lots)
            st.success(f"BANKNIFTY OI lot size set to {lots}")

        col1, col2 = st.columns(2)
        with col1:
            status = redis_client.hget(current_user, "BN_OI_ENGINE_STATUS") or "UNKNOWN"
            st.metric("Engine Status", status)

        with col2:
            order_status = redis_client.hget(current_user, "STATUS_BN_OI_CROSSOVER") or "-"
            st.metric("Last Order Status", order_status)

        st.divider()

        spot = redis_client.get("BN_SPOT")
        st.info(f"BANKNIFTY Spot (BN_SPOT): {spot}")

        st.divider()

        st.write("📡 Last OI Signal")
        last_signal = redis_client.hget(current_user, "BN_OI_ENGINE_LAST_SIGNAL")
        if last_signal:
            st.json(json.loads(last_signal))
        else:
            st.write("No signal yet")

        st.divider()

        st.write("📦 Last OI Auto Order")
        last_order = redis_client.hget(current_user, "BN_OI_ENGINE_LAST_ORDER")
        if last_order:
            st.json(json.loads(last_order))
        else:
            st.write("No order generated yet")

        st.divider()

        msg = redis_client.hget(current_user, "MSG_BN_OI_CROSSOVER")
        if msg:
            st.error(msg)

