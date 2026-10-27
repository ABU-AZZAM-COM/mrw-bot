"""Compatibility import for the Telegram bot wrapper."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("bot.tracking_bot")
