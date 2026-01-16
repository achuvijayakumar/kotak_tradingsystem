# daily_orderbook_job.py
import logging

from core.config import load_config, load_token, get_file_paths
from core.auth import AuthService
from services.orderbook_service import OrderbookService
from utils.redis_helper import get_redis_client


def run(uid: str):
    # load configs
    config = load_config(uid)
    token = load_token(uid)
    paths = get_file_paths(uid)

    redis_client = get_redis_client()

    # auth
    auth = AuthService(config, redis_client, uid)
    auth.initialize_client(token)
    xt = auth.get_xt_client()

    # orderbook service
    orderbook_service = OrderbookService(
        xt=xt,
        redis_client=redis_client,
        uid=uid,
        orderbook_file=paths["orderbook"],
        base_dir=paths["base_dir"],
    )

    # DIRECT call (no redis flags)
    orderbook_service._fetch_and_save_orderbook()

    logging.info(f"[DAILY JOB] Orderbook synced for {uid}")

    # Push to MotherDuck
    try:
        from order_ingest import push_orderbook
        push_orderbook(uid, paths["base_dir"])
        logging.info(f"[DAILY JOB] MotherDuck upload complete for {uid}")
    except Exception as e:
        logging.error(f"[DAILY JOB] MotherDuck upload failed: {e}")


if __name__ == "__main__":
    run("ITC2766")   # change UID if needed
