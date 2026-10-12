"""Testes automatizados do exportador Excel (openpyxl) - Fase 2."""

import json
import os
import openpyxl
import pytest

from exporters.excel_exporter import ExcelReportExporter, format_bytes, format_timestamp, classify_save_status


def test_format_helpers():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1024 * 1024 * 50) == "50.00 MB"
    assert format_bytes(1024 * 1024 * 1024 * 2) == "2.00 GB"
    assert format_bytes(None) == "0 B"

    assert format_timestamp(1670251472.0) == "2022-12-05 11:44:32"
    assert "2026-10-12" in format_timestamp("2026-10-12T00:11:56.927820")
    assert format_timestamp(None) == ""


def test_classify_save_status():
    shared = {"C:\\Users\\Public\\Documents\\Steam": 2}
    status1 = classify_save_status("C:\\Users\\Public\\Documents\\Steam", "OPERATOR", shared)
    assert "Vínculo Heurístico — Revisar" in status1
    assert "compartilhada" in status1

    status2 = classify_save_status("C:\\Users\\Eduardo\\Documents\\LEGO", "LEGO MARVEL Super Heroes", {})
    assert status2 == "Legítimo / Provável"

    status3 = classify_save_status("C:\\Users\\Eduardo\\Documents\\fonts\\Edition", "Dying Light 2: Reloaded Edition", {})
    assert "Revisar" in status3


def test_excel_export_synthetic_data(tmp_path):
    output_dir = str(tmp_path / "reports_test")
    exporter = ExcelReportExporter(output_dir=output_dir)

    resumo = {
        "schema_version": "1.1.0",
        "timestamp_varredura": "2026-10-12T00:00:00",
        "totais": {
            "arquivos_varridos_c": 10,
            "arquivos_varridos_d": 20,
            "total_duplicatas": 2,
            "espaco_recuperavel_bytes": 1000,
            "total_pendentes_backup": 5,
            "tamanho_pendentes_backup_bytes": 5000,
            "total_documentos_avulsos": 1,
            "tamanho_documentos_avulsos_bytes": 200,
            "total_jogos_detectados": 1,
            "tamanho_total_jogos_bytes": 10000,
            "total_saves_detectados": 1,
            "tamanho_total_saves_bytes": 300,
        },
    }

    duplicatas = {
        "itens": [
            {
                "caminho_origem": "C:\\test\\a.txt",
                "caminho_destino": "D:\\test\\a.txt",
                "tamanho_bytes": 500,
                "espaco_recuperavel": 500,
                "nivel_confianca": "completo",
                "hash_amostral": "abc",
                "hash_completo": "abc",
                "mtime_origem": 1600000000.0,
                "mtime_destino": 1600000000.0,
            },
            {
                "caminho_origem": "C:\\test\\big.iso",
                "caminho_destino": "D:\\test\\big.iso",
                "tamanho_bytes": 500000000,
                "espaco_recuperavel": 500000000,
                "nivel_confianca": "amostral",
                "hash_amostral": "def",
                "hash_completo": None,
                "mtime_origem": 1600000000.0,
                "mtime_destino": 1600000000.0,
            },
        ]
    }

    pendentes = {
        "itens": [
            {
                "caminho": "C:\\test\\b.txt",
                "tamanho_bytes": 1000,
                "mtime": 1600000000.0,
                "extensao": ".txt",
                "is_iso": False,
            }
        ]
    }

    documentos = {
        "itens": [
            {
                "caminho": "C:\\test\\relatorio.pdf",
                "pasta_origem": "Documents",
                "tamanho_bytes": 200,
                "extensao": ".pdf",
                "prioridade": "alta",
                "mtime": 1600000000.0,
            }
        ]
    }

    inventario = {
        "jogos": [
            {
                "nome": "Super Game",
                "launcher": "Steam",
                "caminho_instalacao": "D:\\SteamLibrary\\SuperGame",
                "tamanho_instalacao_bytes": 10000,
                "saves_em_c": [
                    {
                        "caminho_save": "C:\\Users\\User\\Documents\\SuperGame",
                        "tamanho_bytes": 300,
                        "quantidade_arquivos": 3,
                        "nivel_certeza": "alta",
                        "mtime": 1600000000.0,
                    }
                ],
            }
        ]
    }

    excel_path = exporter.export(
        resumo_data=resumo,
        duplicatas_data=duplicatas,
        pendentes_data=pendentes,
        documentos_data=documentos,
        inventario_data=inventario,
        output_filename="test_output.xlsx",
    )

    assert os.path.exists(excel_path)

    # Inspeciona o arquivo gerado
    wb = openpyxl.load_workbook(excel_path)
    sheet_names = wb.sheetnames
    expected_sheets = ["Resumo", "Duplicatas", "Pendentes de Backup", "Documentos Avulsos", "Jogos e Saves"]
    for s in expected_sheets:
        assert s in sheet_names, f"Aba '{s}' não encontrada no arquivo Excel"

    # Valida aba Resumo
    ws_resumo = wb["Resumo"]
    assert ws_resumo["A1"].value == "STORAGE AUDITOR & SAFE MOVER — RESUMO EXECUTIVO"
    assert ws_resumo.auto_filter.ref is not None

    # Valida aba Duplicatas
    ws_dup = wb["Duplicatas"]
    assert ws_dup.freeze_panes == "A2"
    assert ws_dup.auto_filter.ref is not None
    assert ws_dup.cell(row=2, column=1).value == "C:\\test\\a.txt"
    assert ws_dup.cell(row=3, column=6).value == "AMOSTRAL"

    # Valida aba Pendentes
    ws_pen = wb["Pendentes de Backup"]
    assert ws_pen.freeze_panes == "A2"
    assert ws_pen.auto_filter.ref is not None

    # Valida aba Jogos e Saves
    ws_jogos = wb["Jogos e Saves"]
    assert ws_jogos.freeze_panes == "A2"
    assert ws_jogos.cell(row=2, column=1).value == "Super Game"
    assert ws_jogos.cell(row=2, column=11).value == "Legítimo / Provável"


def test_excel_export_from_real_reports(tmp_path):
    """Testa exportação lendo a pasta reports/ existente do repositório."""
    if not os.path.exists("reports/resumo_para_ia.json"):
        pytest.skip("reports/resumo_para_ia.json não existe no ambiente")

    output_dir = str(tmp_path / "real_excel_test")
    exporter = ExcelReportExporter(output_dir=output_dir)
    excel_path = exporter.export_from_reports_dir(
        reports_dir="reports",
        output_filename="auditoria_real.xlsx",
        max_items_per_sheet=200,
    )

    assert os.path.exists(excel_path)
    assert os.path.getsize(excel_path) > 1000

    wb = openpyxl.load_workbook(excel_path, read_only=True)
    expected_sheets = ["Resumo", "Duplicatas", "Pendentes de Backup", "Documentos Avulsos", "Jogos e Saves"]
    for s in expected_sheets:
        assert s in wb.sheetnames
