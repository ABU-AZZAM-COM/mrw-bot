"""Compatibility entry point for the bot runtime."""
from importlib import import_module as _import_module
import os as _os
import sys as _sys

_module = _import_module("app.main")
_sys.modules[__name__] = _module

if __name__ == "__main__":
    _sys.modules["main"] = _module
    if _os.getenv("PORT"):
        from app.space_app import main as run_web_service

        run_web_service()
    else:
        _module.main()
