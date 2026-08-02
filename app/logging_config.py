import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "tech_content_agent"


def configure_logging(level: str = "INFO", log_file: str = "data/agent.log") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(resolved_level)
    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    logger.propagate = False
    return logger
