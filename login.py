import json
import sys
import os
from XTSConnect import XTSConnect  # ensure the module is available
from utils.telegram_notifier import send_telegram


# --- Step 1: Ensure UID argument is passed ---
if len(sys.argv) < 2:
    print("[ERROR] UID argument missing.")
    sys.exit(1)

uid = sys.argv[1]  # example: 'achu'

# --- Step 2: Locate the JSON file inside 'uid' folder (relative to this script) ---
base_dir = os.path.dirname(os.path.abspath(__file__))
uid_dir = os.path.join(base_dir, uid)
config_file = os.path.join(base_dir, uid, f"{uid}.json")

# --- Step 3: Read credentials from JSON file ---
try:
    with open(config_file, "r") as file:
        creds = json.load(file)
except FileNotFoundError:
    print(f"[ERROR] Config file not found: {config_file}")
    sys.exit(1)
except json.JSONDecodeError:
    print(f"[ERROR] Invalid JSON format in '{config_file}'")
    sys.exit(1)

# --- Step 4: Extract credentials ---
INTERACTIVE_API_KEY = creds.get("INTERACTIVE_API_KEY")
INTERACTIVE_API_SECRET = creds.get("INTERACTIVE_API_SECRET")
INTERACTIVE_XTS_API_BASE_URL = creds.get("INTERACTIVE_XTS_API_BASE_URL")

# --- Step 5: Validate required fields ---
if not all([INTERACTIVE_API_KEY, INTERACTIVE_API_SECRET, INTERACTIVE_XTS_API_BASE_URL]):
    print("[ERROR] Missing one or more required credentials in config file.")
    sys.exit(1)

# --- Step 6: Initialize XTSConnect ---
print(f"[INFO] Logging in using credentials from {config_file} ...")

xt = XTSConnect(
    INTERACTIVE_API_KEY,
    INTERACTIVE_API_SECRET,
    "WEBAPI",
    INTERACTIVE_XTS_API_BASE_URL
)

print("[SUCCESS] XTSConnect initialized successfully.")

# --- Step 7: Attempt login and print response ---
try:
    resp = xt.interactive_login()
    print("login response =", resp)

    send_telegram(
    f"🔐 <b>Login Successful</b>\n"
    f"UID: {uid}\n"
    f"Session token refreshed."
    )

except Exception as e:
    print("[ERROR] Login failed:", e)
    send_telegram(
        f"❌ <b>Login Failed</b>\n"
        f"UID: {uid}\n"
        f"Reason: {str(e)}"
    )
    sys.exit(1)

# --- Step 8: Extract token from response ---
token = resp

# --- Step 9: Validate and store token ---
if token and len(token) > 25:  # your length check still works
    token_file = os.path.join(uid_dir, "token.txt")
    with open(token_file, "w") as f:
        f.write(token)
    print(f"[SUCCESS] Token stored at: {token_file}")
else:
    print("[ERROR] Invalid credentials or token too short. Token not saved.")
