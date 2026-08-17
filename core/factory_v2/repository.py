"""Persistence boundary for Account Factory V2."""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Mapping

from core.db import transaction

_ACTIVE_JOB_STATES = ("LEASED", "RUNNING", "WAITING_HUMAN", "RECOVERING")


def _dict(row):
    return dict(row) if row is not None else None


def _insert(conn, table: str, row: Mapping[str, Any]) -> None:
    cols = list(row)
    placeholders = ",".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
        tuple(row[c] for c in cols),
    )


class FactoryRepository:
    def __init__(self, conn):
        self.conn = conn

    def create_batch(self, row: Mapping[str, Any]) -> dict:
        _insert(self.conn, "factory_batch", row)
        return self.get_batch(row["id"])

    def get_batch(self, batch_id: str) -> dict | None:
        return _dict(self.conn.execute(
            "SELECT * FROM factory_batch WHERE id=?", (batch_id,)
        ).fetchone())

    def latest_batch(self) -> dict | None:
        return _dict(self.conn.execute(
            """SELECT * FROM factory_batch
               WHERE status IN ('READY','RUNNING','PAUSED')
               ORDER BY created_at DESC, id DESC
               LIMIT 1"""
        ).fetchone())

    def insert_accounts(self, rows: Iterable[Mapping[str, Any]]) -> None:
        rows = list(rows)
        if not rows:
            return
        if self.conn.in_transaction:
            for row in rows:
                _insert(self.conn, "factory_account", row)
            return
        with transaction(self.conn):
            for row in rows:
                _insert(self.conn, "factory_account", row)

    def get_account(self, account_id: str) -> dict | None:
        return _dict(self.conn.execute(
            "SELECT * FROM factory_account WHERE id=?", (account_id,)
        ).fetchone())

    def list_accounts(self, batch_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM factory_account WHERE batch_id=? ORDER BY sequence", (batch_id,)
        ).fetchall()]

    def query_accounts(self, *, batch_id: str | None = None, stage: str | None = None) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if batch_id:
            clauses.append("batch_id=?")
            params.append(batch_id)
        if stage:
            clauses.append("stage=?")
            params.append(stage)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM factory_account {where} ORDER BY batch_id, sequence",
            tuple(params),
        ).fetchall()]

    def update_account_stage(
        self,
        account_id: str,
        *,
        stage: str,
        last_safe_stage: str,
        updated_at: str,
        error_code: str | None = None,
        error_message: str | None = None,
        completed_at: str | None = None,
    ) -> dict:
        self.conn.execute(
            """UPDATE factory_account
               SET stage=?, last_safe_stage=?, updated_at=?,
                   last_error_code=?, last_error_message=?,
                   completed_at=COALESCE(?, completed_at)
               WHERE id=?""",
            (stage, last_safe_stage, updated_at, error_code, error_message, completed_at, account_id),
        )
        return self.get_account(account_id)

    def insert_worker(self, row: Mapping[str, Any]) -> dict:
        _insert(self.conn, "factory_worker", row)
        return self.get_worker(row["id"])

    def get_worker(self, worker_id: str) -> dict | None:
        return _dict(self.conn.execute(
            "SELECT * FROM factory_worker WHERE id=?", (worker_id,)
        ).fetchone())

    def get_worker_by_device_id(self, device_id: str) -> dict | None:
        return _dict(self.conn.execute(
            "SELECT * FROM factory_worker WHERE device_id=?", (device_id,)
        ).fetchone())

    def list_workers(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM factory_worker ORDER BY id"
        ).fetchall()]

    def update_worker_fields(self, worker_id: str, **values) -> dict:
        if not values:
            return self.get_worker(worker_id)
        allowed = {
            "runner_type", "avd_name", "adb_serial", "device_id", "device_name",
            "state", "current_account_id", "current_job_id", "pid", "started_at",
            "last_heartbeat_at", "last_progress_at", "processed_count", "recovery_count",
            "estimated_ram_mb", "current_ram_mb", "current_cpu_percent", "draining",
            "last_error",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported worker fields: {sorted(unknown)}")
        assignments = ",".join(f"{name}=?" for name in values)
        cursor = self.conn.execute(
            f"UPDATE factory_worker SET {assignments} WHERE id=?",
            (*values.values(), worker_id),
        )
        if cursor.rowcount == 0:
            return None
        return self.get_worker(worker_id)

    def upsert_worker_heartbeat(self, worker_id: str, **values) -> dict:
        return self.update_worker_fields(worker_id, **values)

    def create_job_lease(self, row: Mapping[str, Any]) -> dict:
        try:
            with transaction(self.conn):
                _insert(self.conn, "factory_job", row)
        except sqlite3.IntegrityError as exc:
            raise ValueError("account already has an active job lease") from exc
        return _dict(self.conn.execute("SELECT * FROM factory_job WHERE id=?", (row["id"],)).fetchone())

    def get_active_job_for_account(self, account_id: str) -> dict | None:
        placeholders = ",".join("?" for _ in _ACTIVE_JOB_STATES)
        return _dict(self.conn.execute(
            f"SELECT * FROM factory_job WHERE account_id=? AND state IN ({placeholders}) ORDER BY leased_at DESC LIMIT 1",
            (account_id, *_ACTIVE_JOB_STATES),
        ).fetchone())

    def create_runner_command(self, row: Mapping[str, Any]) -> dict:
        try:
            _insert(self.conn, "factory_runner_command", row)
        except sqlite3.IntegrityError:
            existing = self.get_runner_command(row["id"])
            if existing is not None:
                return existing
            raise
        return self.get_runner_command(row["id"])

    def get_runner_command(self, command_id: str) -> dict | None:
        return _dict(self.conn.execute(
            "SELECT * FROM factory_runner_command WHERE id=?", (command_id,)
        ).fetchone())

    def claim_next_runner_command(self, worker_id: str, *, delivered_at: str) -> dict | None:
        with transaction(self.conn):
            row = self.conn.execute(
                """SELECT * FROM factory_runner_command
                   WHERE worker_id=? AND status='QUEUED'
                   ORDER BY created_at, id
                   LIMIT 1""",
                (worker_id,),
            ).fetchone()
            if row is None:
                return None
            self.conn.execute(
                """UPDATE factory_runner_command
                   SET status='DELIVERED', delivered_at=?
                   WHERE id=? AND status='QUEUED'""",
                (delivered_at, row["id"]),
            )
        return self.get_runner_command(row["id"])

    def complete_runner_command(
        self,
        worker_id: str,
        command_id: str,
        *,
        status: str,
        result_json: str,
        completed_at: str,
    ) -> dict:
        status = str(status).upper()
        if status not in {"COMPLETED", "FAILED"}:
            raise ValueError("runner command result status must be COMPLETED or FAILED")
        row = self.get_runner_command(command_id)
        if row is None or row["worker_id"] != worker_id:
            raise KeyError(command_id)
        if row["status"] in {"COMPLETED", "FAILED"}:
            return row
        if row["status"] not in {"QUEUED", "DELIVERED"}:
            raise ValueError(f"runner command cannot complete from {row['status']}")
        self.conn.execute(
            """UPDATE factory_runner_command
               SET status=?, result_json=?, completed_at=?
               WHERE id=?""",
            (status, result_json, completed_at, command_id),
        )
        return self.get_runner_command(command_id)

    def create_checkpoint(self, row: Mapping[str, Any]) -> dict:
        _insert(self.conn, "factory_checkpoint", row)
        return self.get_checkpoint(row["id"])

    def get_checkpoint(self, checkpoint_id: str) -> dict | None:
        return _dict(self.conn.execute(
            "SELECT * FROM factory_checkpoint WHERE id=?", (checkpoint_id,)
        ).fetchone())

    def list_checkpoints(
        self,
        *,
        batch_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if batch_id:
            clauses.append("batch_id=?")
            params.append(batch_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM factory_checkpoint {where} ORDER BY created_at, id",
            tuple(params),
        ).fetchall()]

    def resolve_checkpoint(
        self,
        checkpoint_id: str,
        *,
        resolved_at: str,
        resolution: str,
        resolved_by_device_id: str | None = None,
    ) -> dict | None:
        self.conn.execute(
            """UPDATE factory_checkpoint
               SET status='RESOLVED', resolved_at=?, resolution=?, resolved_by_device_id=?
               WHERE id=?""",
            (resolved_at, resolution, resolved_by_device_id, checkpoint_id),
        )
        return self.get_checkpoint(checkpoint_id)

    def insert_resource_sample(self, row: Mapping[str, Any]) -> dict:
        _insert(self.conn, "factory_resource_sample", row)
        sample_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return _dict(self.conn.execute(
            "SELECT * FROM factory_resource_sample WHERE id=?", (sample_id,)
        ).fetchone())

    def latest_resource_sample(self) -> dict | None:
        return _dict(self.conn.execute(
            "SELECT * FROM factory_resource_sample ORDER BY timestamp DESC, id DESC LIMIT 1"
        ).fetchone())
