"""Compatibility import for time interval utilities."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("services.merge_intervals")
