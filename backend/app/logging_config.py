import logging
import os
import sys

from app.markers import MARKER_LOGGER_NAME


def setup_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Markers always produce records so in-process consumers (the playtest
    # harness, caplog) can capture them regardless of LOG_LEVEL. CC_MARKERS=0
    # only stops them reaching the console.
    marker_log = logging.getLogger(MARKER_LOGGER_NAME)
    marker_log.setLevel(logging.INFO)
    marker_log.propagate = os.environ.get("CC_MARKERS", "1") != "0"
