#!/usr/bin/env python3
"""Issue a cloud pairing code on the controller, using its existing database.

Usage: ACP_DB=/absolute/path/to/database .venv/bin/python factory_pairing.py
Never accepts the operator API key, Threads credentials, or master key.
"""
import os
import sqlite3
from pathlib import Path

from core.factory_v2.pairing import create_pairing_code


def main():
    path = Path(os.environ.get("ACP_DB", ""))
    if not path.is_absolute() or not path.is_file():
        raise SystemExit("ACP_DB phải trỏ đến database đang có của Factory Controller")
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
    try:
        # Guard against accidentally pairing to an unrelated SQLite file.
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='factory_account'").fetchone():
            raise SystemExit("Database chưa có Factory schema; khởi động controller trước")
        print("Mã ghép đôi dùng một lần, hết hạn sau 10 phút:")
        print(create_pairing_code(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
