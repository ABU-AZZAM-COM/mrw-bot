"""Compatibility entry point for the hosted FastAPI application."""
from importlib import import_module as _import_module
import sys as _sys

_module = _import_module("app.space_app")
_sys.modules[__name__] = _module

if __name__ == "__main__":
    _module.main()
