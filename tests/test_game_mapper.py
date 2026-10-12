"""Testes para o mapeador de jogos e heurística de saves (core/game_mapper.py)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from core.game_mapper import GameMapper, parse_vdf_simple, sanitize_game_name


def test_parse_vdf_simple():
    """Valida o parser simples de formato VDF / ACF da Valve."""
    vdf_content = """
    "AppState"
    {
        "appid"       "1091500"
        "Universe"    "1"
        "name"        "Cyberpunk 2077"
        "installdir"  "Cyberpunk 2077"
        "SizeOnDisk"  "75161927680"
    }
    """
    res = parse_vdf_simple(vdf_content)
    assert "AppState" in res
    assert res["AppState"]["name"] == "Cyberpunk 2077"
    assert res["AppState"]["installdir"] == "Cyberpunk 2077"
    assert res["AppState"]["SizeOnDisk"] == "75161927680"


def test_discover_steam_games(tmp_path: Path):
    """Detecta jogos Steam a partir de manifestos .acf sintéticos."""
    steam_root = tmp_path / "SteamLibrary"
    steamapps = steam_root / "steamapps"
    common = steamapps / "common"
    game_dir = common / "Witcher 3"
    game_dir.mkdir(parents=True)
    (game_dir / "witcher3.exe").write_bytes(b"exe")

    acf_file = steamapps / "appmanifest_292030.acf"
    acf_content = """
    "AppState"
    {
        "appid" "292030"
        "name" "The Witcher 3: Wild Hunt"
        "installdir" "Witcher 3"
        "SizeOnDisk" "50000000000"
    }
    """
    acf_file.write_text(acf_content, encoding="utf-8")

    mapper = GameMapper(save_roots=[])
    games = mapper.discover_steam_games([str(steam_root)])

    assert len(games) == 1
    assert games[0].nome == "The Witcher 3: Wild Hunt"
    assert games[0].launcher == "Steam"
    assert "Witcher 3" in games[0].caminho_instalacao


def test_discover_standalone_games(tmp_path: Path):
    """Detecta jogos avulsos / FitGirl / Hydra em D:\\Games."""
    games_root = tmp_path / "Games"
    game_dir = games_root / "Elden Ring"
    game_dir.mkdir(parents=True)

    # Simular executável
    (game_dir / "eldenring.exe").write_bytes(b"exe")
    # Simular arquivo de dados para ter tamanho significativo
    with patch("core.game_mapper.calculate_folder_size_and_mtime", return_value=(15 * 1024 * 1024, 5, 12345.0)):
        mapper = GameMapper(save_roots=[])
        games = mapper.discover_standalone_games([str(games_root)])

        assert len(games) == 1
        assert games[0].nome == "Elden Ring"
        assert games[0].launcher == "FitGirl/Hydra/Avulso"
        assert "eldenring.exe" in games[0].executaveis_principais


def test_find_game_saves_depth_and_confidence(tmp_path: Path):
    """Testa localização de saves em múltiplos locais e atribuição de nível de certeza (Adendo v1.1 req 5)."""
    # 1. Saved Games -> certeza Alta
    saved_games = tmp_path / "Saved Games"
    witcher_save = saved_games / "The Witcher 3"
    witcher_save.mkdir(parents=True)
    (witcher_save / "gamesave.sav").write_bytes(b"save data 100h")

    # 2. Public Documents (emuladores de repack) -> certeza Alta / Média
    public_docs = tmp_path / "Public" / "Documents"
    public_save = public_docs / "Steam" / "CODEX" / "Cyberpunk 2077"
    public_save.mkdir(parents=True)
    (public_save / "manualsave_0.sav").write_bytes(b"cyberpunk data")

    # 3. AppData profunda além do limite de profundidade (max_depth = 2)
    appdata = tmp_path / "AppData" / "Roaming"
    deep_save = appdata / "Nivel1" / "Nivel2" / "Nivel3_MuitoProfundo" / "JogoOculto"
    deep_save.mkdir(parents=True)
    (deep_save / "save.dat").write_bytes(b"deep")

    mapper = GameMapper(
        save_roots=[str(saved_games), str(public_docs), str(appdata)],
        max_save_depth=3,
    )

    # Buscar saves para The Witcher 3
    saves_witcher = mapper.find_game_saves("The Witcher 3: Wild Hunt", ["witcher3.exe"])
    assert len(saves_witcher) >= 1
    assert any(s.nivel_certeza in {"alta", "media"} for s in saves_witcher)

    # Buscar saves para Cyberpunk 2077
    saves_cp = mapper.find_game_saves("Cyberpunk 2077", ["Cyberpunk2077.exe"])
    assert len(saves_cp) >= 1
    assert any("Cyberpunk 2077" in s.caminho_save for s in saves_cp)

    # Buscar jogo que só está no nível 3 (além da profundidade máxima permitida)
    saves_deep = mapper.find_game_saves("JogoOculto", [])
    # Não deve ter varrido níveis além da profundidade configurada
    assert len(saves_deep) == 0
