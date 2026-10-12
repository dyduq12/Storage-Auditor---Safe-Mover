"""Testes para o exportador JSON e conformidade com o Adendo v1.1 (exporters/json_exporter.py)."""

import json
from pathlib import Path

import pytest

from core.comparator import ComparisonResult, DuplicateMatch, LooseDocumentFile, PendingBackupFile
from core.game_mapper import GameInventoryItem, GameSaveInfo
from core.scanner import ScannedFile
from exporters.json_exporter import JsonReportExporter


def test_json_export_all_five_files_and_ascii_keys(tmp_path: Path):
    """Gera os 5 relatórios e valida: schema_version, chaves ASCII sem acento e estrutura."""
    exporter = JsonReportExporter(output_dir=str(tmp_path), schema_version="1.1.0")

    comp_result = ComparisonResult(
        duplicatas=[
            DuplicateMatch(
                caminho_origem="C:\\Users\\User\\Documents\\planilha.xlsx",
                caminho_destino="D:\\Backup\\planilha.xlsx",
                tamanho_bytes=50000,
                hash_amostral="a" * 64,
                hash_completo="a" * 64,
                espaco_recuperavel=50000,
                nivel_confianca="completo",
                mtime_origem=1700000000.0,
                mtime_destino=1700000000.0,
            )
        ],
        pendentes_backup=[
            PendingBackupFile(
                caminho="C:\\Users\\User\\Desktop\\foto.png",
                tamanho_bytes=120000,
                mtime=1700000000.0,
                extensao=".png",
                is_iso=False,
            )
        ],
        documentos_avulsos=[
            LooseDocumentFile(
                caminho="C:\\Users\\User\\Downloads\\instalador.iso",
                tamanho_bytes=2000000,
                mtime=1700000000.0,
                extensao=".iso",
                prioridade="baixa",
                pasta_origem="Downloads",
            )
        ],
        espaco_total_recuperavel_bytes=50000,
        tamanho_total_pendente_bytes=120000,
    )

    games = [
        GameInventoryItem(
            nome="The Witcher 3",
            launcher="Steam",
            caminho_instalacao="D:\\Games\\Witcher3",
            tamanho_instalacao_bytes=50000000000,
            saves_em_c=[
                GameSaveInfo(
                    caminho_save="C:\\Users\\User\\Documents\\The Witcher 3",
                    tamanho_bytes=1500000,
                    quantidade_arquivos=12,
                    nivel_certeza="alta",
                    mtime=1700000000.0,
                )
            ],
            tamanho_total_saves_bytes=1500000,
        )
    ]

    files_c = [
        ScannedFile(
            path="C:\\Users\\User\\Documents\\planilha.xlsx",
            size=50000,
            mtime=1700000000.0,
            scan_timestamp="2026-10-11T12:00:00",
            drive="C:",
            is_iso=False,
            extension=".xlsx",
        )
    ]
    files_d = [
        ScannedFile(
            path="D:\\Backup\\planilha.xlsx",
            size=50000,
            mtime=1700000000.0,
            scan_timestamp="2026-10-11T12:00:00",
            drive="D:",
            is_iso=False,
            extension=".xlsx",
        )
    ]

    timestamp = "2026-10-11T12:00:00"
    exported = exporter.export_all(
        files_c=files_c,
        files_d=files_d,
        comp_result=comp_result,
        games=games,
        timestamp=timestamp,
    )

    expected_files = [
        "duplicatas_confirmadas.json",
        "pendentes_de_backup_para_d.json",
        "inventario_jogos_e_saves.json",
        "documentos_avulsos.json",
        "resumo_para_ia.json",
    ]

    for fname in expected_files:
        fpath = tmp_path / fname
        assert fpath.exists(), f"Arquivo {fname} não foi gerado!"

        with open(fpath, "r", encoding="utf-8") as jf:
            data = json.load(jf)

        # Requisito 7: schema_version em todos os arquivos
        assert data.get("schema_version") == "1.1.0"
        assert "timestamp_varredura" in data

        # Requisito 7: Todas as chaves devem ser ASCII sem acento
        def check_ascii_keys(d):
            if isinstance(d, dict):
                for k, v in d.items():
                    assert k.isascii(), f"Chave com caractere não-ASCII detectada: {k}"
                    assert not any(c in "áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ" for c in k), f"Chave com acento: {k}"
                    check_ascii_keys(v)
            elif isinstance(d, list):
                for item in d:
                    check_ascii_keys(item)

        check_ascii_keys(data)

    # Verificar conteúdo do resumo_para_ia.json
    with open(tmp_path / "resumo_para_ia.json", "r", encoding="utf-8") as rf:
        resumo = json.load(rf)

    assert "totais" in resumo
    assert resumo["totais"]["total_duplicatas"] == 1
    assert resumo["totais"]["espaco_recuperavel_bytes"] == 50000
    assert "maiores_duplicatas" in resumo
    assert "jogos_com_maior_tamanho_e_saves" in resumo
    assert len(resumo["jogos_com_maior_tamanho_e_saves"]) == 1
