import streamlit as st
import pandas as pd
import requests
import json
import os

HOST = "http://localhost:9000"

BN_FILE = "bn_watchlist.json"
NF_FILE = "nf_watchlist.json"


# --------------------------------------------------
# Load / Save JSON
# --------------------------------------------------
def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                content = f.read().strip()
                if content == "":
                    return []  # empty file
                return json.loads(content)
        except json.JSONDecodeError:
            return []  # corrupted file
    return []



def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


# --------------------------------------------------
# QuestDB Query Wrapper
# --------------------------------------------------
def getData(sql):
    try:
        r = requests.get(HOST + "/exec", params={"query": sql}).json()
        return pd.DataFrame.from_dict(r["dataset"])
    except:
        return pd.DataFrame()


# --------------------------------------------------
# BANKNIFTY AUTOSUGGEST
# --------------------------------------------------
def bn_symbol_suggestions(prefix):
    sql = f"""
        select tradingsymbol
        from BankNiftyOI
        where tradingsymbol like '%{prefix}%'
        latest on ts partition by tradingsymbol;
    """
    df = getData(sql)
    if df.empty:
        return []
    return df.iloc[:, 0].tolist()


# --------------------------------------------------
# NIFTY AUTOSUGGEST
# --------------------------------------------------
def nf_symbol_suggestions(prefix):
    sql = f"""
        select tradingsymbol
        from NiftyOI
        where tradingsymbol like '%{prefix}%'
        latest on ts partition by tradingsymbol;
    """
    df = getData(sql)
    if df.empty:
        return []
    return df.iloc[:, 0].tolist()


# --------------------------------------------------
# Last Price Fetchers
# --------------------------------------------------
def bn_last_price(sym):
    sql = f"""
        select last_price
        from BankNiftyOI
        where tradingsymbol = '{sym}'
        latest on ts partition by tradingsymbol;
    """
    df = getData(sql)
    if df.empty:
        return None
    return df.iloc[0, 0]


def nf_last_price(sym):
    sql = f"""
        select last_price
        from NiftyOI
        where tradingsymbol = '{sym}'
        latest on ts partition by tradingsymbol;
    """
    df = getData(sql)
    if df.empty:
        return None
    return df.iloc[0, 0]


# --------------------------------------------------
# BANKNIFTY WATCHLIST
# --------------------------------------------------
def banknifty_watchlist():

    if "bn_watchlist" not in st.session_state:
        st.session_state["bn_watchlist"] = load_json(BN_FILE)

    col1, col2 = st.columns([2, 1])
    with col1:
        text = st.text_input("BankNifty Search").strip()

    suggestions = bn_symbol_suggestions(text) if len(text) >= 2 else []

    with col2:
        if suggestions:
            pick = st.selectbox("Strike", ["-- Select --"] + suggestions, key="bn_suggestions")
            if pick != "-- Select --":
                if pick not in st.session_state["bn_watchlist"]:
                    st.session_state["bn_watchlist"].append(pick)
                    save_json(BN_FILE, st.session_state["bn_watchlist"])
        else:
            st.selectbox("Strike", ["-- No matches --"], key="bn_no_match")

    st.text("My Watchlist")
    if st.session_state["bn_watchlist"]:
        if st.button("Refresh Prices"):
            pass

        for sym in st.session_state["bn_watchlist"]:
            col1, col2, col3 = st.columns([5, 2, 1])
            col1.write(sym)
            col2.write(bn_last_price(sym))
            if col3.button("❌", key=f"bn_del_{sym}"):
                st.session_state["bn_watchlist"].remove(sym)
                save_json(BN_FILE, st.session_state["bn_watchlist"])
                st.rerun()
    else:
        st.info("Watchlist is empty.")


# --------------------------------------------------
# NIFTY WATCHLIST
# --------------------------------------------------
def nifty_watchlist():

    if "nf_watchlist" not in st.session_state:
        st.session_state["nf_watchlist"] = load_json(NF_FILE)

    col1, col2 = st.columns([2, 1])
    with col1:
        text = st.text_input("Nifty Search").strip()

    suggestions = nf_symbol_suggestions(text) if len(text) >= 2 else []

    with col2:
        if suggestions:
            pick = st.selectbox("Strike", ["-- Select --"] + suggestions, key="nf_suggestions")
            if pick != "-- Select --":
                if pick not in st.session_state["nf_watchlist"]:
                    st.session_state["nf_watchlist"].append(pick)
                    save_json(NF_FILE, st.session_state["nf_watchlist"])
        else:
            st.selectbox("Strike", ["-- No matches --"], key="nf_no_match")

    st.text("My Watchlist")
    if st.session_state["nf_watchlist"]:
        if st.button("Refresh Prices", key="nf_refresh"):
            pass

        for sym in st.session_state["nf_watchlist"]:
            col1, col2, col3 = st.columns([5, 2, 1])
            col1.write(sym)
            col2.write(nf_last_price(sym))
            if col3.button("❌", key=f"nf_del_{sym}"):
                st.session_state["nf_watchlist"].remove(sym)
                save_json(NF_FILE, st.session_state["nf_watchlist"])
                st.rerun()
    else:
        st.info("Watchlist is empty.")


# --------------------------------------------------
# MAIN ENTRY
# --------------------------------------------------
def main():
    st.subheader("Watchlist")

    tab1, tab2 = st.tabs(["BankNifty", "Nifty"])

    with tab1:
        banknifty_watchlist()

    with tab2:
        nifty_watchlist()


if __name__ == "__main__":
    main()
