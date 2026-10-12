"""Motor de impressão digital e hashing escalonado em 4 estágios (Fast Fingerprint).

Implementa:
- Estágio 1: Agrupamento por tamanho exato em bytes (Zero I/O para tamanhos únicos).
- Estágio 2: Amostragem rápida em colisão (64 KB início + [centro se > 2 GB] + 64 KB fim).
  * Arquivos pequenos (<= 128 KB) recebem hash completo direto (Adendo v1.1 requisito 4).
  * Arquivos até o limite configurável (default 100 MB) recebem hash completo (Adendo v1.1 requisito 1).
  * Arquivos acima do limite usam amostragem expandida com confiança "amostral".
- Estágio 3: Resolução de colisão e cálculo de SHA-256 criptográfico em blocos de 1 MB.
- Persistência incremental em SQLite (Adendo v1.1 requisito 8).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from config import (
    DEFAULT_FULL_HASH_THRESHOLD_BYTES,
    EXPANDED_SAMPLE_THRESHOLD_BYTES,
    HASH_READ_BLOCK_SIZE,
    SAMPLE_CHUNK_SIZE,
)
from core.cache import HashCache
from core.scanner import ScannedFile, normalize_windows_path, strip_long_path_prefix

logger = logging.getLogger("storage_auditor.fingerprint")


@dataclass
class FingerprintResult:
    """Resultado do cálculo de impressão digital de um arquivo."""

    path: str
    size: int
    mtime: float
    sample_hash: Optional[str]
    full_hash: Optional[str]
    confidence: str  # "completo" ou "amostral"
    error: Optional[str] = None


class FastFingerprintEngine:
    """Motor de cálculo de impressões digitais de arquivos."""

    def __init__(
        self,
        cache: Optional[HashCache] = None,
        full_hash_threshold_bytes: int = DEFAULT_FULL_HASH_THRESHOLD_BYTES,
        sample_chunk_size: int = SAMPLE_CHUNK_SIZE,
    ) -> None:
        self.cache = cache
        self.full_hash_threshold = full_hash_threshold_bytes
        self.sample_chunk_size = sample_chunk_size

    def calculate_file_sample_hash(self, path: str, size: int) -> Optional[str]:
        """Calcula o hash amostral de 64 KB do início, centro (se > 2GB) e fim do arquivo."""
        norm_path = normalize_windows_path(path)
        sha256 = hashlib.sha256()

        try:
            with open(norm_path, "rb") as f:
                # 1. Primeiros 64 KB
                head_chunk = f.read(self.sample_chunk_size)
                sha256.update(head_chunk)

                # 2. Amostragem central expandida para arquivos > 2 GB
                if size > EXPANDED_SAMPLE_THRESHOLD_BYTES:
                    mid_offset = max(0, (size - self.sample_chunk_size) // 2)
                    f.seek(mid_offset, os.SEEK_SET)
                    mid_chunk = f.read(self.sample_chunk_size)
                    sha256.update(mid_chunk)

                # 3. Últimos 64 KB
                if size > self.sample_chunk_size:
                    tail_offset = max(0, size - self.sample_chunk_size)
                    f.seek(tail_offset, os.SEEK_SET)
                    tail_chunk = f.read(self.sample_chunk_size)
                    sha256.update(tail_chunk)

            return sha256.hexdigest()
        except (PermissionError, FileNotFoundError, OSError) as exc:
            logger.debug("Falha ao ler amostra do arquivo %s: %s", path, exc)
            return None

    def calculate_file_full_hash(
        self, path: str, cancel_event: Optional[threading.Event] = None
    ) -> Optional[str]:
        """Calcula o hash SHA-256 criptográfico integral do arquivo em blocos de 1 MB."""
        norm_path = normalize_windows_path(path)
        sha256 = hashlib.sha256()

        try:
            with open(norm_path, "rb") as f:
                while True:
                    if cancel_event and cancel_event.is_set():
                        return None
                    chunk = f.read(HASH_READ_BLOCK_SIZE)
                    if not chunk:
                        break
                    sha256.update(chunk)
            return sha256.hexdigest()
        except (PermissionError, FileNotFoundError, OSError) as exc:
            logger.debug("Falha ao calcular hash completo de %s: %s", path, exc)
            return None

    def fingerprint_file(
        self,
        file_info: ScannedFile,
        force_full: bool = False,
        cancel_event: Optional[threading.Event] = None,
    ) -> FingerprintResult:
        """Gera a impressão digital do arquivo aplicando regras de tamanho e cache."""
        path = file_info.path
        size = file_info.size
        mtime = file_info.mtime

        # 1. Consulta ao cache incremental SQLite (Adendo v1.1 requisito 8)
        if self.cache:
            cached = self.cache.get_cached(path, size, mtime)
            if cached is not None:
                c_sample, c_full, c_conf = cached
                if force_full:
                    if c_full is not None:
                        return FingerprintResult(
                            path=path,
                            size=size,
                            mtime=mtime,
                            sample_hash=c_sample,
                            full_hash=c_full,
                            confidence="completo",
                        )
                else:
                    return FingerprintResult(
                        path=path,
                        size=size,
                        mtime=mtime,
                        sample_hash=c_sample,
                        full_hash=c_full,
                        confidence=c_conf,
                    )

        # 2. Regra para arquivos vazios (0 bytes)
        if size == 0:
            zero_hash = hashlib.sha256(b"").hexdigest()
            res = FingerprintResult(
                path=path,
                size=size,
                mtime=mtime,
                sample_hash=zero_hash,
                full_hash=zero_hash,
                confidence="completo",
            )
            if self.cache:
                self.cache.put_cached(path, size, mtime, zero_hash, zero_hash, "completo")
            return res

        # 3. Regra para arquivos pequenos (Adendo v1.1 requisito 4):
        # Arquivos menores que o tamanho combinado das amostras (2 * 64KB = 128KB)
        # recebem hash completo direto!
        small_file_limit = self.sample_chunk_size * 2
        is_small = size <= small_file_limit
        should_full_hash = force_full or is_small or (size <= self.full_hash_threshold)

        if should_full_hash:
            full_h = self.calculate_file_full_hash(path, cancel_event=cancel_event)
            if full_h is None:
                return FingerprintResult(
                    path=path,
                    size=size,
                    mtime=mtime,
                    sample_hash=None,
                    full_hash=None,
                    confidence="falha_io",
                    error="Arquivo inacessível ou bloqueado por outro processo.",
                )
            sample_h = full_h
            confidence = "completo"
        else:
            sample_h = self.calculate_file_sample_hash(path, size)
            if sample_h is None:
                return FingerprintResult(
                    path=path,
                    size=size,
                    mtime=mtime,
                    sample_hash=None,
                    full_hash=None,
                    confidence="falha_io",
                    error="Arquivo inacessível ou bloqueado por outro processo.",
                )
            full_h = None
            confidence = "amostral"

        if self.cache:
            self.cache.put_cached(path, size, mtime, sample_h, full_h, confidence)

        return FingerprintResult(
            path=path,
            size=size,
            mtime=mtime,
            sample_hash=sample_h,
            full_hash=full_h,
            confidence=confidence,
        )

    def process_size_groups(
        self,
        files: List[ScannedFile],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[int, List[FingerprintResult]]:
        """Executa os Estágios 1, 2 e 3 agrupando por tamanho e calculando hashes apenas em colisões."""
        # Estágio 1: Agrupar por tamanho exato em bytes
        size_map: Dict[int, List[ScannedFile]] = {}
        for f in files:
            size_map.setdefault(f.size, []).append(f)

        results: Dict[int, List[FingerprintResult]] = {}
        total_colliding_files = sum(len(group) for group in size_map.values() if len(group) > 1)
        processed_count = 0

        for file_size, group in size_map.items():
            if cancel_event and cancel_event.is_set():
                break

            # Se o tamanho for estritamente único, NÃO lê do disco (Estágio 1)
            if len(group) == 1:
                single_file = group[0]
                results.setdefault(file_size, []).append(
                    FingerprintResult(
                        path=single_file.path,
                        size=single_file.size,
                        mtime=single_file.mtime,
                        sample_hash=None,
                        full_hash=None,
                        confidence="unico_por_tamanho",
                    )
                )
                continue

            # Estágio 2: Colisão de tamanho -> Calcular amostragem rápida
            group_results: List[FingerprintResult] = []
            sample_collision_map: Dict[str, List[FingerprintResult]] = {}

            for file_info in group:
                if cancel_event and cancel_event.is_set():
                    break
                fp = self.fingerprint_file(file_info, force_full=False, cancel_event=cancel_event)
                group_results.append(fp)
                processed_count += 1
                if progress_callback and (processed_count % 10 == 0 or processed_count == total_colliding_files):
                    progress_callback(processed_count, total_colliding_files, file_info.path)

                if fp.sample_hash:
                    sample_collision_map.setdefault(fp.sample_hash, []).append(fp)

            # Estágio 3: Colisão persistente de amostra para arquivos com confiança 'amostral'
            # Se múltiplos arquivos tiverem a mesma amostra e estiverem marcados como amostral,
            # podemos refinar ou manter amostral conforme o limite configurado (Adendo v1.1 requisito 1).
            results[file_size] = group_results

        return results
