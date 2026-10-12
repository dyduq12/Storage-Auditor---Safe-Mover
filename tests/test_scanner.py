"""Testes para o motor de varredura NTFS (core/scanner.py)."""

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.scanner import DirectoryScanner, IgnoredItem, is_cloud_offline_file


def test_scanner_basic_and_filters(tmp_path: Path):
    """Verifica indexação normal e descarte de extensões do Estágio 0 (mantendo .iso)."""
    (tmp_path / "doc.pdf").write_bytes(b"pdf data")
    (tmp_path / "jogo.iso").write_bytes(b"iso image data")  # .iso deve ser indexado!
    (tmp_path / "temp.tmp").write_bytes(b"temporary data")  # .tmp deve ser ignorado!
    (tmp_path / "biblioteca.dll").write_bytes(b"dll binary")  # .dll deve ser ignorado!

    sub = tmp_path / "subpasta"
    sub.mkdir()
    (sub / "texto.txt").write_bytes(b"texto puro")

    scanner = DirectoryScanner()
    valid_files, ignored, errors = scanner.scan_directories([str(tmp_path)])

    valid_paths = [f.path for f in valid_files]
    assert any("doc.pdf" in p for p in valid_paths)
    assert any("jogo.iso" in p for p in valid_paths)
    assert any("texto.txt" in p for p in valid_paths)

    # .tmp e .dll devem estar na lista de ignorados
    ignored_paths = [i.path for i in ignored]
    assert any("temp.tmp" in p for p in ignored_paths)
    assert any("biblioteca.dll" in p for p in ignored_paths)

    # Nenhum arquivo temporário deve estar nos válidos
    assert not any("temp.tmp" in p for p in valid_paths)
    assert not any("biblioteca.dll" in p for p in valid_paths)


def test_permission_error_resilience(tmp_path: Path):
    """Pasta sem permissão / erro de I/O não derruba a varredura (continua e registra log)."""
    (tmp_path / "visivel.txt").write_bytes(b"visivel")
    bloqueado = tmp_path / "pasta_bloqueada"
    bloqueado.mkdir()
    (bloqueado / "secreto.txt").write_bytes(b"secreto")

    original_scandir = os.scandir

    def mocked_scandir(path):
        if "pasta_bloqueada" in str(path):
            raise PermissionError("Acesso negado simulado pelo Windows.")
        return original_scandir(path)

    with patch("os.scandir", side_effect=mocked_scandir):
        scanner = DirectoryScanner()
        valid_files, ignored, errors = scanner.scan_directories([str(tmp_path)])

    # O arquivo visível foi indexado com sucesso
    valid_names = [os.path.basename(f.path) for f in valid_files]
    assert "visivel.txt" in valid_names

    # O erro foi capturado sem quebrar o processo
    assert len(errors) >= 1
    assert any("Acesso negado" in err for err in errors)


def test_cloud_offline_file_detection(tmp_path: Path):
    """Arquivos de nuvem com atributo offline/recall não são lidos e são ignorados (Adendo v1.1 req 3)."""
    arquivo_nuvem = tmp_path / "arquivo_onedrive.docx"
    arquivo_nuvem.write_bytes(b"stub")

    # Simular atributo FILE_ATTRIBUTE_OFFLINE (0x1000)
    with patch("core.scanner.get_windows_attributes", return_value=0x1000):
        assert is_cloud_offline_file(str(arquivo_nuvem)) is True

        scanner = DirectoryScanner()
        valid, ignored, errors = scanner.scan_directories([str(tmp_path)])

        # Deve estar nos ignorados com motivo cloud_offline
        assert not any("arquivo_onedrive.docx" in f.path for f in valid)
        cloud_ignored = [i for i in ignored if i.reason == "cloud_offline"]
        assert len(cloud_ignored) >= 1
        assert any("arquivo_onedrive.docx" in i.path for i in cloud_ignored)


def test_cancellation(tmp_path: Path):
    """Cancelamento limpo interrompe a varredura imediatamente."""
    for i in range(20):
        (tmp_path / f"arq_{i}.txt").write_bytes(b"conteudo")

    cancel_event = threading.Event()
    cancel_event.set()  # Cancela logo de início

    scanner = DirectoryScanner()
    valid, _, _ = scanner.scan_directories([str(tmp_path)], cancel_event=cancel_event)

    assert len(valid) == 0


def test_excluded_library_paths(tmp_path: Path):
    """Pastas de bibliotecas de jogos no D: são excluídas da varredura de arquivos (Adendo v1.1 req 2)."""
    user_files = tmp_path / "MeusDocumentos"
    user_files.mkdir()
    (user_files / "planilha.xlsx").write_bytes(b"dados")

    games_lib = tmp_path / "Games"
    games_lib.mkdir()
    (games_lib / "game_asset.bin").write_bytes(b"textura")

    scanner = DirectoryScanner(exclude_paths=[str(games_lib)])
    valid, ignored, _ = scanner.scan_directories([str(tmp_path)])

    valid_paths = [f.path for f in valid]
    assert any("planilha.xlsx" in p for p in valid_paths)
    assert not any("game_asset.bin" in p for p in valid_paths)
    assert any(i.reason == "excluded_library_path" for i in ignored)
