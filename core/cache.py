"""Índice incremental baseado em SQLite para persistência de hashes e metadados.

Atende ao Adendo v1.1 requisito 8:
Garante que novas varreduras não recalculam hash de arquivos inalterados (mtime e tamanho idênticos).
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Optional, Tuple


class HashCache:
    """Gerenciador do banco de dados SQLite para cache incremental de hashes."""

    def __init__(self, db_path: str = "reports/storage_auditor_cache.db") -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, timeout=30.0)
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA synchronous = NORMAL;")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_hashes (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    sample_hash TEXT,
                    full_hash TEXT,
                    confidence TEXT NOT NULL,
                    last_scanned REAL NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_file_hashes_lookup
                ON file_hashes (path, size, mtime);
                """
            )

    def get_cached(
        self, path: str, size: int, mtime: float
    ) -> Optional[Tuple[Optional[str], Optional[str], str]]:
        """Recupera hashes cacheados se o tamanho e o mtime coincidirem exatamente.

        Retorna:
            (sample_hash, full_hash, confidence) se encontrado e válido; senão None.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT sample_hash, full_hash, confidence
            FROM file_hashes
            WHERE path = ? AND size = ? AND ABS(mtime - ?) < 0.001
            """,
            (path, size, mtime),
        )
        row = cursor.fetchone()
        if row:
            return (row[0], row[1], row[2])
        return None

    def put_cached(
        self,
        path: str,
        size: int,
        mtime: float,
        sample_hash: Optional[str],
        full_hash: Optional[str],
        confidence: str,
    ) -> None:
        """Salva ou atualiza os hashes cacheados de um arquivo."""
        conn = self._get_connection()
        now = time.time()
        with conn:
            conn.execute(
                """
                INSERT INTO file_hashes (path, size, mtime, sample_hash, full_hash, confidence, last_scanned)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size = excluded.size,
                    mtime = excluded.mtime,
                    sample_hash = excluded.sample_hash,
                    full_hash = excluded.full_hash,
                    confidence = excluded.confidence,
                    last_scanned = excluded.last_scanned;
                """,
                (path, size, mtime, sample_hash, full_hash, confidence, now),
            )

    def close(self) -> None:
        """Fecha a conexão com o banco de dados se aberta."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> HashCache:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
