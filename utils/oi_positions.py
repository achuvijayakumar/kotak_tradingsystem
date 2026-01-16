import json
import os
from datetime import datetime
from threading import Lock

FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "oi_live_positions.json"
)

_lock = Lock()


def _ensure_file():
    if not os.path.exists(FILE_PATH):
        with open(FILE_PATH, "w") as f:
            json.dump({}, f)


def load_positions():
    _ensure_file()
    with _lock, open(FILE_PATH, "r") as f:
        return json.load(f)


def save_positions(data):
    with _lock, open(FILE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_position(index):
    return load_positions().get(index)


def add_position(index, direction, legs):
    data = load_positions()
    data[index] = {
        "direction": direction,
        "opened_at": datetime.now().isoformat(),
        "naked": True,
        "hedged": False,
        "legs": legs
    }
    save_positions(data)


def remove_position(index):
    data = load_positions()
    if index in data:
        del data[index]
        save_positions(data)
