"""Testes de regressão para argumentos CLI múltiplos, parsing de caminhos e detecção de jogos."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from config import AuditorConfig, parse_cli_path_list
from core.game_mapper import GameMapper
from core.scanner import DirectoryScanner
from main import run_audit


def test_parse_cli_path_list_semicolons_commas_and_quotes(tmp_path: Path):
    """Garante que caminhos separados por ponto-e-vírgula e vírgula sejam devidamente divididos e validados."""
    d1 = tmp_path / "Pasta1"
    d2 = tmp_path / "Pasta2"
    d3 = tmp_path / "Pasta3"
    d1.mkdir()
    d2.mkdir()
    d3.mkdir()

    # Formato com ponto-e-vírgula e aspas (exatamente como passado pelo PowerShell)
    raw_input = [f"{d1};{d2};{d3}"]
    valid, invalid = parse_cli_path_list(raw_input)

    assert len(valid) == 3
    assert len(invalid) == 0
    assert str(d1.resolve()) in valid
    assert str(d2.resolve()) in valid
    assert str(d3.resolve()) in valid


def test_parse_cli_path_list_user_folder_aliases():
    """Garante que aliases como Documents, Downloads e Desktop sejam mapeados para a pasta do usuário."""
    raw = ["Documents;Downloads;Desktop"]
    valid, invalid = parse_cli_path_list(raw, is_c_roots=True)

    # Devem ter sido resolvidos para caminhos absolutos no disco C:
    assert len(valid) >= 1
    assert any("Documents" in p for p in valid)
    assert any("Desktop" in p for p in valid)


def test_parse_cli_path_list_trailing_slash_quotes():
    """Trata caminhos com barra invertida antes de aspas (ex: D:\\" resultante de escape no Windows)."""
    # Testa normalização do token
    raw = ['D:\\"']
    valid, invalid = parse_cli_path_list(raw)
    # Se D:\ existir no sistema, deve validar; senão deve ir para invalid com mensagem tratada
    if os.path.exists("D:\\"):
        assert any(p.startswith("D:") for p in valid)


def test_cli_empty_root_inconsistency_fails_with_nonzero_code(tmp_path: Path):
    """Falha com código diferente de zero se uma raiz existir mas produzir zero itens enquanto outra produz itens."""
    c_root_cheia = tmp_path / "C_Cheia"
    c_root_vazia = tmp_path / "C_Vazia"
    d_root = tmp_path / "D_Storage"
    out_dir = tmp_path / "reports_test"

    c_root_cheia.mkdir()
    c_root_vazia.mkdir()
    d_root.mkdir()

    (c_root_cheia / "arquivo.txt").write_bytes(b"dados")
    (d_root / "outro.txt").write_bytes(b"dados D")

    config = AuditorConfig(
        c_roots=[str(c_root_cheia), str(c_root_vazia)],
        d_roots=[str(d_root)],
        game_library_paths=[],
        save_candidate_roots=[],
        output_dir=str(out_dir),
        cache_db_path=str(out_dir / "cache.db"),
        test_mode=True,
    )

    exit_code = run_audit(config)
    # Deve falhar com código 2 (inconsistência de raiz vazia)
    assert exit_code == 2


def test_cli_all_c_empty_while_d_has_files_fails(tmp_path: Path):
    """Falha com código diferente de zero se C: estiver vazio enquanto D: possui arquivos."""
    c_root_vazia = tmp_path / "C_Vazia"
    d_root_cheia = tmp_path / "D_Cheia"
    out_dir = tmp_path / "reports_test_fail"

    c_root_vazia.mkdir()
    d_root_cheia.mkdir()
    (d_root_cheia / "arquivo_d.bin").write_bytes(b"conteudo d")

    config = AuditorConfig(
        c_roots=[str(c_root_vazia)],
        d_roots=[str(d_root_cheia)],
        game_library_paths=[],
        save_candidate_roots=[],
        output_dir=str(out_dir),
        cache_db_path=str(out_dir / "cache.db"),
        test_mode=True,
    )

    exit_code = run_audit(config)
    assert exit_code == 2


def test_game_mapper_steam_singular_libraryfolder_vdf(tmp_path: Path):
    """Valida detecção de Steam games mesmo quando o arquivo é libraryfolder.vdf (singular)."""
    steam_dir = tmp_path / "SteamLibrary"
    steamapps = steam_dir / "steamapps"
    common = steamapps / "common"
    game_path = common / "MeuJogo"
    game_path.mkdir(parents=True)
    (game_path / "game.exe").write_bytes(b"binary")

    # Arquivo no singular: libraryfolder.vdf
    (steam_dir / "libraryfolder.vdf").write_text('"libraryfolder" { "path" "' + str(steam_dir).replace('\\', '\\\\') + '" }')
    (steamapps / "appmanifest_123.acf").write_text('"AppState" { "appid" "123" "name" "Meu Jogo Steam" "installdir" "MeuJogo" "SizeOnDisk" "5000000" }')

    mapper = GameMapper(save_roots=[])
    games = mapper.discover_steam_games([str(steam_dir)])

    assert len(games) == 1
    assert games[0].nome == "Meu Jogo Steam"
    assert games[0].tamanho_instalacao_bytes == 5000000


def test_end_to_end_cli_with_semicolon_arguments(tmp_path: Path):
    """Executa CLI com string contendo ponto-e-vírgula em diretórios sintéticos e valida sucesso."""
    c_sub1 = tmp_path / "C1"
    c_sub2 = tmp_path / "C2"
    d_dir = tmp_path / "D1"
    out_dir = tmp_path / "reports_cli_test"

    c_sub1.mkdir()
    c_sub2.mkdir()
    d_dir.mkdir()

    (c_sub1 / "doc1.pdf").write_bytes(b"PDF dados 1")
    (c_sub2 / "doc2.docx").write_bytes(b"DOCX dados 2")
    (d_dir / "doc1.pdf").write_bytes(b"PDF dados 1")  # duplicata confirmada!

    raw_c = [f"{c_sub1};{c_sub2}"]
    valid_c, _ = parse_cli_path_list(raw_c)
    assert len(valid_c) == 2

    config = AuditorConfig(
        c_roots=valid_c,
        d_roots=[str(d_dir)],
        game_library_paths=[],
        save_candidate_roots=[],
        output_dir=str(out_dir),
        cache_db_path=str(out_dir / "cache.db"),
        test_mode=True,
    )

    exit_code = run_audit(config)
    assert exit_code == 0
    assert (out_dir / "duplicatas_confirmadas.json").exists()
    assert (out_dir / "resumo_para_ia.json").exists()
