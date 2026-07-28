#!/usr/bin/env python3
"""Slice 1 acceptance test for the queue.db schema + migration runner.

Dependency-free (plain asserts). Run: `python3 test_schema.py` (exit 0 = pass).
Covers the plan's Slice 1 acceptance: create DB, apply migrations twice
(idempotent), insert a row, reopen and read it back, integrity_check == ok,
and the generated repro_rate column.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import migrate


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="tfh_schema_")
    db = os.path.join(tmpdir, "queue.db")

    # First apply creates the schema at version 1.
    assert migrate.apply_migrations(db) == [1], "first apply should run migration 1"
    # Second apply is a no-op (idempotent).
    assert migrate.apply_migrations(db) == [], "second apply should be a no-op"

    conn = migrate.connect(db)
    try:
        assert migrate._user_version(conn) == 1, "user_version should be 1"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

        # schema_migrations recorded the applied version.
        vers = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        assert vers == [1], f"schema_migrations should have [1], got {vers}"

        # Insert into a Phase-0 table.
        conn.execute(
            "INSERT INTO test_issue(issue_id, target, test_name, category) "
            "VALUES (?, ?, ?, ?)",
            ("156166394", "//torcheval/tests/metrics/image:test_image_test_psnr",
             "test_psnr_with_random_data", "FLAKY"),
        )
        # Exercise the generated repro_rate column on `tests`.
        conn.execute(
            "INSERT INTO tests(test_target, test_case, issue_ids, category, "
            "status, repro_runs, repro_total) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("//t:test", "case_a", "[\"156166394\"]", "FLAKY", "pending", 1, 5),
        )
        conn.commit()
    finally:
        conn.close()

    # Reopen with a fresh connection and read back.
    conn2 = migrate.connect(db)
    try:
        cat = conn2.execute(
            "SELECT category FROM test_issue WHERE issue_id=?", ("156166394",)
        ).fetchone()
        assert cat is not None and cat[0] == "FLAKY", "row should persist across reopen"
        rate = conn2.execute(
            "SELECT repro_rate FROM tests WHERE test_case=?", ("case_a",)
        ).fetchone()[0]
        assert abs(rate - 0.2) < 1e-9, f"generated repro_rate should be 0.2, got {rate}"
    finally:
        conn2.close()

    print("PASS test_schema: create + idempotent re-apply + insert + reopen + integrity ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
