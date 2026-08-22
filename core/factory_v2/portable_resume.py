"""Bounded reconciliation for imported Account Factory state.

This module intentionally delegates durable state transitions to the existing
scheduler and OAuth activation services.  It never starts the controller loop
and never clears operator-owned OAuth retry gates.
"""
from __future__ import annotations

from .activation import FactoryActivationService
from .repository import FactoryRepository
from .scheduler import Scheduler
from .service import FactoryService


def reconcile_for_portable_resume(conn, now_iso: str) -> dict[str, int | str]:
    """Reconcile stale imported state once, without starting new work."""
    repo = FactoryRepository(conn)
    service = FactoryService(repo)
    scheduler = Scheduler(repo, service)

    reconciled_jobs = scheduler.reconcile_expired_leases(now_iso)

    connecting = conn.execute(
        """SELECT id
           FROM factory_account
           WHERE stage='ACP_CONNECTING'
             AND oauth_session_id IS NOT NULL
           ORDER BY id"""
    ).fetchall()

    oauth_reconciled = 0
    if connecting:
        activation = FactoryActivationService(conn)
        for row in connecting:
            account_id = row["id"] if hasattr(row, "keys") else row[0]
            activation.reconcile(account_id)
            oauth_reconciled += 1

    gated_row = conn.execute(
        """SELECT COUNT(*)
           FROM factory_account
           WHERE stage='RETRY_PENDING'
             AND last_safe_stage='THREADS_CREATED'
             AND last_error_code='OAUTH_FAILED'"""
    ).fetchone()
    oauth_gated = int(gated_row[0] if gated_row is not None else 0)

    return {
        "leases_reconciled": len(reconciled_jobs),
        "oauth_reconciled": oauth_reconciled,
        "oauth_gated": oauth_gated,
    }
