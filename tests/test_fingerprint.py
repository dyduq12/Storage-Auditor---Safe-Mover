"""Testes para o motor de impressão digital (core/fingerprint.py) e cache (core/cache.py)."""

import os
from pathlib import Path

import pytest

from core.cache import HashCache
from core.fingerprint import FastFingerprintEngine
from core.scanner import ScannedFile


def create_scanned_file_mock(path: Path) -> ScannedFile:
    stat = path.stat()
    return ScannedFile(
        path=str(path),
        size=stat.st_size,
        mtime=stat.st_mtime,
        scan_timestamp="2026-10-11T00:00:00",
        drive="C:",
        is_iso=False,
        extension=path.suffix.lower(),
    )


def test_exact_duplicate(tmp_path: Path):
    """Duplicata exata: mesmo tamanho e mesmo conteúdo devem gerar mesmos hashes."""
    f1 = tmp_path / "arquivo1.bin"
    f2 = tmp_path / "arquivo2.bin"

    data = b"Conteudo exatamente identico para duplicata." * 100
    f1.write_bytes(data)
    f2.write_bytes(data)

    engine = FastFingerprintEngine()
    sf1 = create_scanned_file_mock(f1)
    sf2 = create_scanned_file_mock(f2)

    res1 = engine.fingerprint_file(sf1)
    res2 = engine.fingerprint_file(sf2)

    assert res1.sample_hash == res2.sample_hash
    assert res1.full_hash == res2.full_hash
    assert res1.confidence == "completo"
    assert res2.confidence == "completo"


def test_same_size_different_content(tmp_path: Path):
    """Mesmo tamanho com conteúdo diferente: colisão de tamanho resolvida por hash diferente."""
    f1 = tmp_path / "diff1.bin"
    f2 = tmp_path / "diff2.bin"

    # Criar 2 arquivos com exatamente 200.000 bytes
    size = 200_000
    f1.write_bytes(b"A" * size)
    f2.write_bytes(b"B" * size)

    engine = FastFingerprintEngine(full_hash_threshold_bytes=500_000)
    sf1 = create_scanned_file_mock(f1)
    sf2 = create_scanned_file_mock(f2)

    res1 = engine.fingerprint_file(sf1)
    res2 = engine.fingerprint_file(sf2)

    assert res1.size == res2.size
    assert res1.sample_hash != res2.sample_hash
    assert res1.full_hash != res2.full_hash


def test_same_content_different_name(tmp_path: Path):
    """Mesmo conteúdo com nome e caminho diferentes: detectados como duplicatas."""
    sub_a = tmp_path / "pasta_a"
    sub_b = tmp_path / "pasta_b"
    sub_a.mkdir()
    sub_b.mkdir()

    f1 = sub_a / "relatorio_final_v1.pdf"
    f2 = sub_b / "copia_de_backup_2026.pdf"

    content = b"PDF-1.4 Dados essenciais de faturamento 2026" * 50
    f1.write_bytes(content)
    f2.write_bytes(content)

    engine = FastFingerprintEngine()
    sf1 = create_scanned_file_mock(f1)
    sf2 = create_scanned_file_mock(f2)

    res1 = engine.fingerprint_file(sf1)
    res2 = engine.fingerprint_file(sf2)

    assert res1.sample_hash == res2.sample_hash
    assert res1.confidence == "completo"


def test_small_file_direct_full_hash(tmp_path: Path):
    """Arquivo pequeno (<= 128 KB): recebe hash completo direto com confiança 'completo' (Adendo v1.1 req 4)."""
    f = tmp_path / "pequeno.txt"
    f.write_bytes(b"Apenas algumas palavras em arquivo pequeno.")

    engine = FastFingerprintEngine(sample_chunk_size=64 * 1024)
    sf = create_scanned_file_mock(f)

    res = engine.fingerprint_file(sf)
    assert res.confidence == "completo"
    assert res.full_hash is not None
    assert res.sample_hash == res.full_hash


def test_large_file_sample_hash(tmp_path: Path):
    """Arquivo acima do limite: gera amostragem expandida marcada como 'amostral' (Adendo v1.1 req 1)."""
    f = tmp_path / "grande.bin"
    # Criar arquivo de 500 KB com limite configurado em 200 KB
    f.write_bytes(os.urandom(500 * 1024))

    engine = FastFingerprintEngine(
        full_hash_threshold_bytes=200 * 1024,
        sample_chunk_size=16 * 1024,
    )
    sf = create_scanned_file_mock(f)

    res = engine.fingerprint_file(sf)
    assert res.confidence == "amostral"
    assert res.sample_hash is not None
    assert res.full_hash is None


def test_sqlite_cache_persistence(tmp_path: Path):
    """Cache SQLite incremental: evita re-leitura de arquivo inalterado (Adendo v1.1 req 8)."""
    db_path = tmp_path / "test_cache.db"
    f = tmp_path / "documento.pdf"
    f.write_bytes(b"Conteudo importante indexado em cache.")

    sf = create_scanned_file_mock(f)

    with HashCache(str(db_path)) as cache:
        engine1 = FastFingerprintEngine(cache=cache)
        res1 = engine1.fingerprint_file(sf)

        # Checar se gravou no banco
        cached = cache.get_cached(sf.path, sf.size, sf.mtime)
        assert cached is not None
        cached_sample, cached_full, cached_conf = cached
        assert cached_sample == res1.sample_hash
        assert cached_conf == res1.confidence

        # Segunda chamada com novo engine deve retornar do cache
        engine2 = FastFingerprintEngine(cache=cache)
        res2 = engine2.fingerprint_file(sf)
        assert res2.sample_hash == res1.sample_hash
        assert res2.confidence == res1.confidence
