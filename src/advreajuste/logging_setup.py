from pathlib import Path
from loguru import logger
from rich.console import Console
from rich.logging import RichHandler

from .config import settings


def setup_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    logger.remove()
    logger.add(
        "logs/pipeline_{time:YYYY-MM-DD}.log",
        rotation="50 MB",
        retention="1 year",
        compression="zip",
        serialize=True,
        level="DEBUG",
        enqueue=True,
    )
    logger.add(
        RichHandler(console=Console(stderr=True), rich_tracebacks=True, markup=True),
        level=settings.log_level,
        format="{message}",
    )
