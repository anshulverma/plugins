#!/usr/bin/env python3
"""queue.db migration runner for the distributed test-fix harness.

Additive-only, online migrations keyed on `PRAGMA user_version` (see ADR 0001,
plan Slice 1). Idempotent: re-running applies only migrations newer than the
DB's current user_version. Opens the DB with the harness PRAGMAs and runs
`PRAGMA integrity_check` on open, aborting on corruption.

Usage:
    migrate.py <db_path>          # apply pending migrations, print what ran
    migrate.py <db_path> --check  # open + integrity_check + report version only
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Ordered (version, sql_file, description). Migration 1 is the full initial
# schema. Later schema changes append (2, ...), (3, ...) here as additive-only
# SQL files under this directory; never edit a landed migration.
MIGRATIONS = [
    (1, os.path.join(HERE, "schema.sql"), "initial schema"),
]


def connect(db_path: str) -> sqlite3.Connection:
    """Open with the harness PRAGMAs and verify integrity before use."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    row = conn.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        conn.close()
        raise RuntimeError(f"integrity_check failed for {db_path}: {row}")
    return conn


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(db_path: str) -> list[int]:
    """Apply every migration whose version exceeds the DB's user_version.

    Returns the list of versions actually applied (empty when up to date).
    """
    conn = connect(db_path)
    try:
        current = _user_version(conn)
        applied: list[int] = []
        for version, sql_file, description in MIGRATIONS:
            if version <= current:
                continue
            with open(sql_file, "r") as fh:
                sql = fh.read()
            # executescript commits any open txn and runs the multi-statement
            # DDL (including PRAGMAs in schema.sql, which are idempotent).
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at, description) "
                "VALUES (?, ?, ?)",
                (version, int(time.time()), description),
            )
            # PRAGMA does not accept bound params; version is a trusted int.
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.commit()
            applied.append(version)
        return applied
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(prog="migrate.py")
    p.add_argument("db_path")
    p.add_argument("--check", action="store_true", help="integrity_check + version only")
    args = p.parse_args()

    if args.check:
        conn = connect(args.db_path)
        try:
            print(f"ok user_version={_user_version(conn)}")
        finally:
            conn.close()
        return 0

    applied = apply_migrations(args.db_path)
    print(f"applied={applied}" if applied else "up-to-date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
