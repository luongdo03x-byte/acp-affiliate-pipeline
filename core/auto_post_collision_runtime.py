"""Recover fairly scheduled Auto assignments when a slot becomes stale mid-preparation.

The calendar/fairness runtime snapshots open slots before artifact preparation. A
concurrent target can occupy that slot before the write transaction starts. The
core guard must keep rejecting the occupied slot; this adapter refreshes the
channel's current slot list and retries the same candidate at the next valid
slot instead of exhausting every candidate against the stale snapshot.
"""
from __future__ import annotations

from . import auto_post_scheduler_runtime, auto_scheduler

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_attempt = auto_post_scheduler_runtime._attempt_assignment

    def collision_resilient_attempt(conn, **kwargs):
        channel = kwargs.get("channel")
        requested_slot = kwargs.get("slot")
        now_utc = kwargs.get("now_utc")

        if channel is None or not requested_slot:
            return original_attempt(conn, **kwargs)

        channel_id = channel["id"]

        # The snapshot can already be stale before artifact preparation starts.
        if auto_scheduler.live_slot_occupied(conn, channel_id, requested_slot):
            fresh_slots = auto_scheduler.available_slots(conn, channel, now_utc)
            if not fresh_slots:
                return "skipped"
            kwargs = dict(kwargs)
            kwargs["slot"] = fresh_slots[0]["slot"]
            requested_slot = kwargs["slot"]

        outcome = original_attempt(conn, **kwargs)
        if outcome != "skipped":
            return outcome

        # Retry only when evidence shows the requested slot became occupied.
        # Other skip reasons (quality, duplicate, link/image, etc.) stay skipped.
        if not auto_scheduler.live_slot_occupied(conn, channel_id, requested_slot):
            return outcome

        fresh_slots = auto_scheduler.available_slots(conn, channel, now_utc)
        alternatives = [item for item in fresh_slots if item["slot"] != requested_slot]
        if not alternatives:
            return outcome

        retry_kwargs = dict(kwargs)
        retry_kwargs["slot"] = alternatives[0]["slot"]
        return original_attempt(conn, **retry_kwargs)

    auto_post_scheduler_runtime._attempt_assignment = collision_resilient_attempt
    _INSTALLED = True
