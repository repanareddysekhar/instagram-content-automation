import logging


LOGGER_NAME = "tech_content_agent"


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(resolved_level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        logger.addHandler(handler)
    logger.propagate = False
    return logger
