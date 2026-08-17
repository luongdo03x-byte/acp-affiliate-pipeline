"""Lease-based account scheduler for Account Factory V2."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets

from core.db import transaction, ulid

from .models import AccountStage

_ACTIVE_JOB_STATES = ("LEASED", "RUNNING", "WAITING_HUMAN", "RECOVERING")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Scheduler:
    def __init__(self, repository, service, *, lease_seconds: int = 120, live_heartbeat_seconds: int = 60):
        self.repo = repository
        self.service = service
        self.lease_seconds = max(30, int(lease_seconds))
        self.live_heartbeat_seconds = max(15, int(live_heartbeat_seconds))

    def assign_next(self, worker_id: str) -> dict | None:
        conn = self.repo.conn
        now_dt = _utc_now()
        with transaction(conn):
            worker = conn.execute(
                "SELECT * FROM factory_worker WHERE id=?", (worker_id,)
            ).fetchone()
            if worker is None or worker["state"] != "READY" or worker["draining"]:
                return None

            placeholders = ",".join("?" for _ in _ACTIVE_JOB_STATES)
            account = conn.execute(
                f"""SELECT a.*
                    FROM factory_account a
                    JOIN factory_batch b ON b.id=a.batch_id
                    WHERE b.status IN ('READY','RUNNING')
                      AND a.stage IN ('PROFILE_READY','RETRY_PENDING')
                      AND a.current_job_id IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM factory_job j
                          WHERE j.account_id=a.id AND j.state IN ({placeholders})
                      )
                    ORDER BY a.batch_id, a.sequence
                    LIMIT 1""",
                _ACTIVE_JOB_STATES,
            ).fetchone()
            if account is None:
                return None

            job_id = ulid()
            leased_at = _iso(now_dt)
            lease_expires_at = _iso(now_dt + timedelta(seconds=self.lease_seconds))
            conn.execute(
                """INSERT INTO factory_job
                   (id,account_id,worker_id,lease_token,state,desired_action,command_id,
                    leased_at,lease_expires_at,heartbeat_at,attempt,started_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id, account["id"], worker_id, secrets.token_urlsafe(24), "RUNNING",
                    "PREPARE_INSTAGRAM", ulid(), leased_at, lease_expires_at,
                    leased_at, 1, leased_at,
                ),
            )
            self.service.transition_account(account["id"], AccountStage.AVD_ASSIGNED)
            conn.execute(
                """UPDATE factory_account
                   SET assigned_worker_id=?, current_job_id=?, updated_at=?
                   WHERE id=?""",
                (worker_id, job_id, leased_at, account["id"]),
            )
            conn.execute(
                """UPDATE factory_worker
                   SET state='RUNNING', current_account_id=?, current_job_id=?, last_progress_at=?
                   WHERE id=?""",
                (account["id"], job_id, leased_at, worker_id),
            )
        return dict(conn.execute("SELECT * FROM factory_job WHERE id=?", (job_id,)).fetchone())

    def release_job(self, job_id: str, final_state: str) -> None:
        conn = self.repo.conn
        finished_at = _iso(_utc_now())
        final_state = str(final_state).upper()
        with transaction(conn):
            job = conn.execute("SELECT * FROM factory_job WHERE id=?", (job_id,)).fetchone()
            if job is None:
                return
            account = conn.execute("SELECT * FROM factory_account WHERE id=?", (job["account_id"],)).fetchone()
            conn.execute(
                "UPDATE factory_job SET state=?, finished_at=? WHERE id=?",
                (final_state, finished_at, job_id),
            )
            if account is not None and account["current_job_id"] == job_id:
                if final_state != "COMPLETED" and account["stage"] not in {"ACP_ACTIVE", "DISABLED"}:
                    try:
                        self.service.transition_account(account["id"], AccountStage.RETRY_PENDING)
                    except ValueError:
                        pass
                conn.execute(
                    """UPDATE factory_account
                       SET assigned_worker_id=NULL, current_job_id=NULL, updated_at=?
                       WHERE id=?""",
                    (finished_at, account["id"]),
                )
            conn.execute(
                """UPDATE factory_worker
                   SET state='READY', current_account_id=NULL, current_job_id=NULL,
                       processed_count=processed_count + CASE WHEN ?='COMPLETED' THEN 1 ELSE 0 END,
                       last_progress_at=?
                   WHERE id=? AND current_job_id=?""",
                (final_state, finished_at, job["worker_id"], job_id),
            )

    def reconcile_expired_leases(self, now_iso: str) -> list[str]:
        conn = self.repo.conn
        now_dt = _parse_iso(now_iso)
        if now_dt is None:
            raise ValueError("now_iso must be ISO-8601")
        placeholders = ",".join("?" for _ in _ACTIVE_JOB_STATES)
        rows = conn.execute(
            f"""SELECT j.*, w.last_heartbeat_at, w.state AS worker_state
                FROM factory_job j
                LEFT JOIN factory_worker w ON w.id=j.worker_id
                WHERE j.state IN ({placeholders}) AND j.lease_expires_at < ?
                ORDER BY j.leased_at""",
            (*_ACTIVE_JOB_STATES, now_iso),
        ).fetchall()
        reconciled: list[str] = []
        for row in rows:
            heartbeat = _parse_iso(row["last_heartbeat_at"])
            heartbeat_live = heartbeat is not None and (now_dt - heartbeat) <= timedelta(seconds=self.live_heartbeat_seconds)
            human_ambiguous = row["state"] == "WAITING_HUMAN" or row["worker_state"] == "WAITING_HUMAN"
            with transaction(conn):
                if heartbeat_live or human_ambiguous:
                    extended = _iso(now_dt + timedelta(seconds=self.live_heartbeat_seconds))
                    conn.execute(
                        "UPDATE factory_job SET state='RECOVERING', lease_expires_at=? WHERE id=?",
                        (extended, row["id"]),
                    )
                    conn.execute(
                        "UPDATE factory_worker SET state='RECOVERING' WHERE id=?",
                        (row["worker_id"],),
                    )
                else:
                    conn.execute(
                        "UPDATE factory_job SET state='EXPIRED', finished_at=? WHERE id=?",
                        (now_iso, row["id"]),
                    )
                    account = self.repo.get_account(row["account_id"])
                    if account is not None:
                        target = AccountStage.NEEDS_CONFIRMATION if human_ambiguous else AccountStage.RETRY_PENDING
                        try:
                            self.service.transition_account(account["id"], target, error_code="WORKER_TIMEOUT", error_message="Worker lease expired")
                        except ValueError:
                            pass
                        conn.execute(
                            """UPDATE factory_account
                               SET assigned_worker_id=NULL, current_job_id=NULL, updated_at=?
                               WHERE id=?""",
                            (now_iso, row["account_id"]),
                        )
                    conn.execute(
                        """UPDATE factory_worker
                           SET state='RECOVERING', current_account_id=NULL, current_job_id=NULL,
                               recovery_count=recovery_count+1
                           WHERE id=?""",
                        (row["worker_id"],),
                    )
            reconciled.append(row["id"])
        return reconciled
