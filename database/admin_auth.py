"""أساليب admin auth (mixins لطبقة البيانات).

نُقلت من ``database/__init__.py`` كما هي.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from database._shared import (
    STATUS_APPROVED,
    STATUS_AWAITING_INFO,
    STATUS_CLOSED_NO_RESPONSE,
    STATUS_CODES,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_REVIEW,
    _IDENTITY_PRIVATE_TABLES,
    _mapping_row_factory,
    _row_to_dict,
    logger,
)


class AdminAuthMixin(object):
    def admin_auth_snapshot(self) -> tuple[list[dict[str, Any]], bool]:
        """Return persisted admin credentials and whether environment tokens are disabled."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM system_state "
                "WHERE key IN ('admin_auth_accounts', 'admin_auth_bootstrap_disabled')"
            ).fetchall()
        state = {str(row["key"]): row["value"] for row in rows}
        raw_accounts = state.get("admin_auth_accounts")
        accounts = json.loads(raw_accounts) if raw_accounts else []
        if not isinstance(accounts, list) or any(
            not isinstance(account, dict) for account in accounts
        ):
            raise ValueError("Invalid persisted admin account data")
        return accounts, state.get("admin_auth_bootstrap_disabled") == "1"

    def save_admin_auth_snapshot(
        self, accounts: list[dict[str, Any]], *, disable_bootstrap: bool = False
    ) -> None:
        with self._lock:
            self._save_admin_auth_snapshot_locked(
                accounts,
                disable_bootstrap=disable_bootstrap,
            )
            self._conn.commit()

    def _save_admin_auth_snapshot_locked(
        self, accounts: list[dict[str, Any]], *, disable_bootstrap: bool
    ) -> None:
        serialized = json.dumps(accounts, ensure_ascii=False, separators=(",", ":"))
        self._conn.execute(
            "INSERT INTO system_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("admin_auth_accounts", serialized),
        )
        if disable_bootstrap:
            self._conn.execute(
                "INSERT INTO system_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("admin_auth_bootstrap_disabled", "1"),
            )

    def mutate_admin_auth_snapshot(
        self,
        mutation: Callable[[list[dict[str, Any]], bool], Any],
        *,
        disable_bootstrap: bool = False,
        audit_event: tuple[
            Optional[int], str, Optional[str], Optional[str]
        ] | None = None,
    ) -> Any:
        """Apply one serialized account update while holding the database lock."""
        with self._lock:
            if audit_event is not None:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    accounts, bootstrap_disabled = self.admin_auth_snapshot()
                    result = mutation(accounts, bootstrap_disabled)
                    if result is False:
                        self._conn.rollback()
                        return result
                    self._save_admin_auth_snapshot_locked(
                        accounts,
                        disable_bootstrap=disable_bootstrap,
                    )
                    self._insert_audit_locked(*audit_event)
                    self._conn.commit()
                    return result
                except BaseException:
                    self._conn.rollback()
                    raise

            accounts, bootstrap_disabled = self.admin_auth_snapshot()
            result = mutation(accounts, bootstrap_disabled)
            if result is not False:
                self.save_admin_auth_snapshot(
                    accounts,
                    disable_bootstrap=disable_bootstrap,
                )
            return result

