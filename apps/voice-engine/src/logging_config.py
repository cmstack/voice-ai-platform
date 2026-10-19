"""Loguru setup. Loguru (and Pipecat, which logs through it) defaults to DEBUG, which writes full
system prompts, including caller names, phone numbers and answers, to the logs. Default to INFO."""

import sys

from loguru import logger


def configure(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level.upper(), backtrace=False, diagnose=False)
