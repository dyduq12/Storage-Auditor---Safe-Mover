"""Comparador relacional de arquivos entre C: e D:.

Identifica:
1. Duplicatas confirmadas entre C: e D: com nível de confiança (amostral ou completo).
2. Arquivos de C: pendentes de backup para D:.
3. Documentos avulsos em Downloads/Desktop com prioridade e retenção de arquivos .iso.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from config import DOCUMENT_EXTENSIONS, ISO_EXTENSIONS
from core.fingerprint import FastFingerprintEngine, FingerprintResult
from core.scanner import ScannedFile

logger = logging.getLogger("storage_auditor.comparator")


@dataclass
class DuplicateMatch:
    """Representação de um par de arquivos duplicados entre C: e D:."""

    caminho_origem: str  # Arquivo em C:
    caminho_destino: str  # Arquivo em D:
    tamanho_bytes: int
    hash_amostral: Optional[str]
    hash_completo: Optional[str]
    espaco_recuperavel: int
    nivel_confianca: str  # "completo" ou "amostral" (Adendo v1.1 requisito 1)
    mtime_origem: float
    mtime_destino: float


@dataclass
class PendingBackupFile:
    """Arquivo em C: que ainda não possui cópia correspondente em D:."""

    caminho: str
    tamanho_bytes: int
    mtime: float
    extensao: str
    is_iso: bool


@dataclass
class LooseDocumentFile:
    """Documento avulso identificado em pastas como Downloads e Desktop."""

    caminho: str
    tamanho_bytes: int
    mtime: float
    extensao: str
    prioridade: str  # "alta", "media" ou "baixa" (.iso marcado como baixa prioridade - Adendo v1.1 requisito 7)
    pasta_origem: str


@dataclass
class ComparisonResult:
    """Resultado completo da comparação relacional entre as unidades."""

    duplicatas: List[DuplicateMatch] = field(default_factory=list)
    pendentes_backup: List[PendingBackupFile] = field(default_factory=list)
    documentos_avulsos: List[LooseDocumentFile] = field(default_factory=list)
    espaco_total_recuperavel_bytes: int = 0
    tamanho_total_pendente_bytes: int = 0


class RelationalComparator:
    """Motor de cruzamento relacional e análise de redundância."""

    def __init__(self, fingerprint_engine: FastFingerprintEngine) -> None:
        self.fp_engine = fingerprint_engine

    def compare(
        self,
        files_c: List[ScannedFile],
        files_d: List[ScannedFile],
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> ComparisonResult:
        """Compara listas de arquivos escaneados de C: e D:."""
        result = ComparisonResult()

        # 1. Mapeamento de arquivos avulsos e documentos importantes em C:
        for f in files_c:
            ext = f.extension.lower()
            is_doc = ext in DOCUMENT_EXTENSIONS
            is_iso = ext in ISO_EXTENSIONS or f.is_iso

            if is_doc or is_iso:
                parent_name = os.path.basename(os.path.dirname(f.path))
                # Prioridade: .iso -> baixa prioridade (Adendo v1.1 requisito 7); Docs -> alta prioridade
                if is_iso:
                    prio = "baixa"
                elif ext in {".pdf", ".docx", ".xlsx", ".pptx", ".txt"}:
                    prio = "alta"
                else:
                    prio = "media"

                result.documentos_avulsos.append(
                    LooseDocumentFile(
                        caminho=f.path,
                        tamanho_bytes=f.size,
                        mtime=f.mtime,
                        extensao=ext,
                        prioridade=prio,
                        pasta_origem=parent_name,
                    )
                )

        # 2. Agrupamento por tamanho em bytes para C: e D:
        # Estágio 1: Arquivos de C: cujo tamanho não existe em D: são pendentes de backup imediatos!
        map_d_by_size: Dict[int, List[ScannedFile]] = {}
        for fd in files_d:
            map_d_by_size.setdefault(fd.size, []).append(fd)

        map_c_by_size: Dict[int, List[ScannedFile]] = {}
        for fc in files_c:
            map_c_by_size.setdefault(fc.size, []).append(fc)

        matched_c_paths: Set[str] = set()

        all_candidate_sizes = [s for s in map_c_by_size.keys() if s in map_d_by_size]
        total_sizes_to_check = len(all_candidate_sizes)

        # 3. Cruzamento para tamanhos coincidentes
        for idx, size_val in enumerate(all_candidate_sizes):
            if cancel_event and cancel_event.is_set():
                break

            if progress_callback and (idx % 20 == 0 or idx == total_sizes_to_check - 1):
                progress_callback("Comparando impressões digitais", idx, total_sizes_to_check)

            c_candidates = map_c_by_size[size_val]
            d_candidates = map_d_by_size[size_val]

            # Obter ou calcular fingerprints para candidatos de D
            d_fps: List[FingerprintResult] = []
            for d_file in d_candidates:
                d_fp = self.fp_engine.fingerprint_file(d_file, cancel_event=cancel_event)
                if d_fp.sample_hash:
                    d_fps.append(d_fp)

            if not d_fps:
                continue

            # Comparar com candidatos de C
            for c_file in c_candidates:
                if cancel_event and cancel_event.is_set():
                    break

                c_fp = self.fp_engine.fingerprint_file(c_file, cancel_event=cancel_event)
                if not c_fp.sample_hash:
                    continue

                # Busca correspondência em D com o mesmo sample_hash
                matching_d = next(
                    (d for d in d_fps if d.sample_hash == c_fp.sample_hash), None
                )

                if matching_d:
                    matched_c_paths.add(c_file.path)

                    # Nível de confiança: "completo" ou "amostral" (Adendo v1.1 requisito 1)
                    confidence = "completo" if (
                        c_fp.confidence == "completo" and matching_d.confidence == "completo"
                    ) else "amostral"

                    dup = DuplicateMatch(
                        caminho_origem=c_file.path,
                        caminho_destino=matching_d.path,
                        tamanho_bytes=size_val,
                        hash_amostral=c_fp.sample_hash,
                        hash_completo=c_fp.full_hash or matching_d.full_hash,
                        espaco_recuperavel=size_val,
                        nivel_confianca=confidence,
                        mtime_origem=c_file.mtime,
                        mtime_destino=matching_d.mtime,
                    )
                    result.duplicatas.append(dup)
                    result.espaco_total_recuperavel_bytes += size_val

        # 4. Arquivos de C: que NÃO tiveram correspondência em D: são pendentes de backup
        for fc in files_c:
            if fc.path not in matched_c_paths:
                result.pendentes_backup.append(
                    PendingBackupFile(
                        caminho=fc.path,
                        tamanho_bytes=fc.size,
                        mtime=fc.mtime,
                        extensao=fc.extension,
                        is_iso=fc.is_iso,
                    )
                )
                result.tamanho_total_pendente_bytes += fc.size

        return result
