"""Testes para o comparador relacional de arquivos C: vs D: (core/comparator.py)."""

from pathlib import Path

import pytest

from core.comparator import RelationalComparator
from core.fingerprint import FastFingerprintEngine
from core.scanner import ScannedFile


def create_scanned_file(path: Path, drive: str = "C:", is_iso: bool = False) -> ScannedFile:
    stat = path.stat()
    return ScannedFile(
        path=str(path),
        size=stat.st_size,
        mtime=stat.st_mtime,
        scan_timestamp="2026-10-11T12:00:00",
        drive=drive,
        is_iso=is_iso,
        extension=path.suffix.lower(),
    )


def test_comparator_duplicates_and_pending(tmp_path: Path):
    """Testa detecção de duplicatas entre C: e D: e identificação de pendentes de backup."""
    dir_c = tmp_path / "DiscoC"
    dir_d = tmp_path / "DiscoD"
    dir_c.mkdir()
    dir_d.mkdir()

    # 1. Arquivo duplicado exatamente entre C e D
    data_comum = b"Dados sincronizados de planilha financeira" * 20
    fc_dup = dir_c / "planilha_2026.xlsx"
    fd_dup = dir_d / "backup_planilha_2026.xlsx"
    fc_dup.write_bytes(data_comum)
    fd_dup.write_bytes(data_comum)

    # 2. Arquivo exclusivo de C (pendente de backup)
    fc_novo = dir_c / "projeto_novo.docx"
    fc_novo.write_bytes(b"Apenas no C")

    # 3. Arquivo exclusivo de D
    fd_outro = dir_d / "antigo_d.bin"
    fd_outro.write_bytes(b"Outro arquivo em D")

    sfc_dup = create_scanned_file(fc_dup, drive="C:")
    sfc_novo = create_scanned_file(fc_novo, drive="C:")
    sfd_dup = create_scanned_file(fd_dup, drive="D:")
    sfd_outro = create_scanned_file(fd_outro, drive="D:")

    engine = FastFingerprintEngine()
    comparator = RelationalComparator(engine)

    res = comparator.compare(
        files_c=[sfc_dup, sfc_novo],
        files_d=[sfd_dup, sfd_outro],
    )

    # Verifica duplicatas
    assert len(res.duplicatas) == 1
    dup = res.duplicatas[0]
    assert dup.caminho_origem == str(fc_dup)
    assert dup.caminho_destino == str(fd_dup)
    assert dup.espaco_recuperavel == fc_dup.stat().st_size
    assert dup.nivel_confianca in {"completo", "amostral"}

    # Verifica pendentes de backup
    assert len(res.pendentes_backup) == 1
    assert res.pendentes_backup[0].caminho == str(fc_novo)


def test_comparator_loose_documents_and_iso_priority(tmp_path: Path):
    """Testa classificação de documentos avulsos e retenção de .iso com prioridade baixa (Adendo v1.1 req 7)."""
    dir_c = tmp_path / "Downloads"
    dir_c.mkdir()

    doc_pdf = dir_c / "contrato.pdf"
    doc_pdf.write_bytes(b"PDF dados")

    iso_file = dir_c / "instalador_windows.iso"
    iso_file.write_bytes(b"ISO image data")

    s_pdf = create_scanned_file(doc_pdf, drive="C:")
    s_iso = create_scanned_file(iso_file, drive="C:", is_iso=True)

    engine = FastFingerprintEngine()
    comparator = RelationalComparator(engine)

    res = comparator.compare(files_c=[s_pdf, s_iso], files_d=[])

    docs = res.documentos_avulsos
    assert len(docs) == 2

    pdf_entry = next(d for d in docs if "contrato.pdf" in d.caminho)
    iso_entry = next(d for d in docs if "instalador_windows.iso" in d.caminho)

    assert pdf_entry.prioridade == "alta"
    # .iso deve ser retido com prioridade 'baixa' (Adendo v1.1 req 7)
    assert iso_entry.prioridade == "baixa"
