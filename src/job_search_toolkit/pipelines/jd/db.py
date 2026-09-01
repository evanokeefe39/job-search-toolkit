"""Warehouse connection helper (direct-file mode).

DuckDB's file model allows many read-only processes OR one read-write process;
a reader blocks any writer on the same file. The pipeline owns the warehouse
file directly (single-process mode — the default, byte-identical behavior).

The serving mirror (``data/warehouse/serve.db``) that the DuckDB UI queries is
maintained separately by the Quack server (see ``data/_quack/server.py``) and
the ``serve_refresh`` asset; the pipeline itself always opens ``jobs.db``
directly and never routes through Quack. This helper keeps that boundary
explicit by offering only the direct-file connect.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from . import config


def connect(
    db_path: Path | str | None = None,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Open the warehouse database (creating the file if missing).

    Opens ``db_path`` (defaults to the warehouse) directly as a local file.
    """
    path = Path(db_path) if db_path else config.WAREHOUSE_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)
