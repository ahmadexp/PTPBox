#!/usr/bin/env python3
"""Small SQLite/WAL ring store shared by the PHC collector and web agent."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


MAX_ROWS = 20_000
MAX_AGE_SECONDS = 1_200.0


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=2.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=2000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS phc_samples (
            observed_at REAL NOT NULL,
            sample_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS phc_samples_observed_at ON phc_samples(observed_at)"
    )
    return connection


def append_sample(
    path: Path,
    sample: dict[str, Any],
    temperatures: dict[str, float] | None = None,
    *,
    max_rows: int = MAX_ROWS,
    max_age_seconds: float = MAX_AGE_SECONDS,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    observed_at = float(sample["observed_at"])
    payload = json.dumps(
        {"sample": sample, "temperatures": temperatures or {}},
        separators=(",", ":"),
    )
    with _connect(path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO phc_samples(observed_at, sample_id, payload) VALUES(?, ?, ?)",
            (observed_at, str(sample["sample_id"]), payload),
        )
        connection.execute(
            "DELETE FROM phc_samples WHERE observed_at < ?",
            (observed_at - max_age_seconds,),
        )
        connection.execute(
            """
            DELETE FROM phc_samples
            WHERE sample_id IN (
                SELECT sample_id FROM phc_samples
                ORDER BY observed_at DESC LIMIT -1 OFFSET ?
            )
            """,
            (max_rows,),
        )


def read_records(
    path: Path,
    history_seconds: float = 120.0,
    since: float | None = None,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    cutoff = time.time() - max(5.0, min(float(history_seconds), MAX_AGE_SECONDS))
    lower_bound = max(cutoff, float(since)) if since is not None else cutoff
    try:
        with _connect(path) as connection:
            rows = connection.execute(
                "SELECT payload FROM phc_samples WHERE observed_at > ? ORDER BY observed_at",
                (lower_bound,),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return []
    records: list[dict[str, Any]] = []
    for (payload,) in rows:
        try:
            record = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and isinstance(record.get("sample"), dict):
            records.append(record)
    return records


def collector_quality(path: Path, requested_rate_hz: float, history_seconds: float = 30.0) -> dict[str, Any]:
    records = read_records(path, history_seconds)
    timestamps = [float(record["sample"]["observed_at"]) for record in records]
    deltas = [
        right - left
        for left, right in zip(timestamps, timestamps[1:])
        if right > left
    ]
    expected_period = 1.0 / max(0.1, requested_rate_hz)
    coverage = max(expected_period, timestamps[-1] - timestamps[0]) if len(timestamps) > 1 else 0.0
    achieved = (len(timestamps) - 1) / coverage if coverage else 0.0
    gap_threshold = expected_period * 2.5
    return {
        "source": "external-collector",
        "requested_rate_hz": requested_rate_hz,
        "achieved_rate_hz": achieved,
        "sample_count": len(timestamps),
        "gap_count": sum(delta > gap_threshold for delta in deltas),
        "largest_gap_s": max(deltas, default=0.0),
        "last_sample_age_s": max(0.0, time.time() - timestamps[-1]) if timestamps else None,
        "healthy": bool(timestamps) and achieved >= requested_rate_hz * 0.8,
    }
