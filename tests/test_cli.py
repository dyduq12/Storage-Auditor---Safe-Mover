"""Testes de ponta a ponta para a execução via linha de comando (main.py)."""

import os
from pathlib import Path

import pytest

from config import AuditorConfig
from main import run_audit


def test_cli_run_audit_end_to_end(tmp_path: Path):
    """Executa o pipeline completo do Motor 1 com diretórios sintéticos e valida relatórios."""
    c_root = tmp_path / "C_User"
    c_root.mkdir()
    d_root = tmp_path / "D_Storage"
    d_root.mkdir()
    out_dir = tmp_path / "output_reports"

    # Criar duplicata entre C e D
    conteudo_comum = b"Arquivo duplicado com informacoes valiosas." * 10
    (c_root / "documento.pdf").write_bytes(conteudo_comum)
    (d_root / "documento_copia.pdf").write_bytes(conteudo_comum)

    # Arquivo exclusivo de C
    (c_root / "exclusivo_c.txt").write_bytes(b"Apenas em C")

    # Arquivo .iso em C (deve ser documento avulso prioridade baixa)
    (c_root / "disco.iso").write_bytes(b"Imagem ISO sintetica")

    config = AuditorConfig(
        c_roots=[str(c_root)],
        d_roots=[str(d_root)],
        game_library_paths=[],
        save_candidate_roots=[],
        output_dir=str(out_dir),
        cache_db_path=str(out_dir / "cache.db"),
        full_hash_threshold_bytes=10 * 1024 * 1024,
        test_mode=True,
        verbose=True,
    )

    exit_code = run_audit(config)
    assert exit_code == 0

    # Verifica se os 5 arquivos JSON e o banco SQLite foram criados
    assert (out_dir / "duplicatas_confirmadas.json").exists()
    assert (out_dir / "pendentes_de_backup_para_d.json").exists()
    assert (out_dir / "inventario_jogos_e_saves.json").exists()
    assert (out_dir / "documentos_avulsos.json").exists()
    assert (out_dir / "resumo_para_ia.json").exists()
    assert (out_dir / "cache.db").exists()
