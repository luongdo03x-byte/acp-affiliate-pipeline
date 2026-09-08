#!/usr/bin/env python3
"""Verify that each stored Threads credential belongs to its channel row.

This operator diagnostic never prints access tokens or encrypted credentials.
It performs only the read-only Threads ``/me`` request.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from acp.core.crypto import decrypt  # noqa: E402
from acp.core.db import connect  # noqa: E402


PROFILE_URL = "https://graph.threads.net/v1.0/me"


def classify(row, response) -> str:
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.ok:
        identity = "MATCH" if str(data.get("id")) == str(row["external_user_id"]) else "MISMATCH"
        username = str(data.get("username") or "?")
        return f'{row["handle"]}|HTTP_{response.status_code}|oauth=@{username}|{identity}'
    error = data.get("error") if isinstance(data, dict) else {}
    error = error if isinstance(error, dict) else {}
    return (
        f'{row["handle"]}|HTTP_{response.status_code}|ERROR|'
        f'code={error.get("code", "?")}|subcode={error.get("error_subcode", "?")}'
    )


def main() -> int:
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT handle, external_user_id, token_encrypted
               FROM channel
               WHERE platform='threads' AND status='ACTIVE'
               ORDER BY handle"""
        ).fetchall()
        for row in rows:
            try:
                response = requests.get(
                    PROFILE_URL,
                    params={
                        "fields": "id,username",
                        "access_token": decrypt(row["token_encrypted"]),
                    },
                    timeout=20,
                )
                print(classify(row, response))
            except Exception as exc:
                print(f'{row["handle"]}|CHECK_FAILED|{type(exc).__name__}')
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
