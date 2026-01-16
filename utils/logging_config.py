"""Logging configuration for the trading application."""
import logging
import sys


def setup_logging():
    """Configure logging with file and console handlers."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("agent.log"),
            logging.StreamHandler(sys.stdout)
        ]
    )
