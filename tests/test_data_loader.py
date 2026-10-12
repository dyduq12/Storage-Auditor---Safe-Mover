"""Testes automatizados do carregador de dados e paginação (gui/data_loader.py) - Fase 2."""

import os
import pytest

from gui.data_loader import AuditDataLoader


def test_data_loader_empty_dir(tmp_path):
    loader = AuditDataLoader(reports_dir=str(tmp_path / "vazio"))
    success = loader.load_from_directory()
    assert not success
    assert not loader.is_loaded
    assert loader.last_load_error is not None


def test_data_loader_synthetic(tmp_path):
    reports_dir = tmp_path / "reports_mock"
    reports_dir.mkdir()

    import json
    with open(reports_dir / "resumo_para_ia.json", "w", encoding="utf-8") as f:
        json.dump({"schema_version": "1.1.0", "totais": {"total_duplicatas": 2}}, f)

    with open(reports_dir / "duplicatas_confirmadas.json", "w", encoding="utf-8") as f:
        json.dump({
            "itens": [
                {"caminho_origem": "C:\\a.txt", "caminho_destino": "D:\\a.txt", "nivel_confianca": "completo", "espaco_recuperavel": 100},
                {"caminho_origem": "C:\\b.iso", "caminho_destino": "D:\\b.iso", "nivel_confianca": "amostral", "espaco_recuperavel": 200},
            ]
        }, f)

    loader = AuditDataLoader(reports_dir=str(reports_dir))
    assert loader.load_from_directory()
    assert loader.is_loaded

    # Paginação sem filtro
    page_items, total, total_pages = loader.get_duplicates_page(page=1, page_size=1)
    assert len(page_items) == 1
    assert total == 2
    assert total_pages == 2

    # Filtro por confiança
    amostral_items, total_am, _ = loader.get_duplicates_page(page=1, page_size=10, confidence_filter="amostral")
    assert len(amostral_items) == 1
    assert amostral_items[0]["caminho_origem"] == "C:\\b.iso"
    assert total_am == 1

    # Busca por texto
    search_items, total_s, _ = loader.get_duplicates_page(page=1, page_size=10, search="b.iso")
    assert len(search_items) == 1
    assert total_s == 1


def test_data_loader_real_reports():
    if not os.path.exists("reports/resumo_para_ia.json"):
        pytest.skip("reports/ não encontrado")

    loader = AuditDataLoader(reports_dir="reports")
    assert loader.load_from_directory()
    assert loader.is_loaded

    metrics = loader.get_summary_metrics()
    exp_duplicatas = loader.resumo.get("totais", {}).get("total_duplicatas", len(loader.duplicatas))
    exp_pendentes = loader.resumo.get("totais", {}).get("total_pendentes_backup", len(loader.pendentes))
    exp_documentos = loader.resumo.get("totais", {}).get("total_documentos_avulsos", len(loader.documentos))
    exp_jogos = loader.resumo.get("totais", {}).get("total_jogos_detectados", len(loader.jogos))
    exp_saves = loader.resumo.get("totais", {}).get("total_saves_detectados", len(loader.saves_flat))

    assert metrics["total_duplicatas"] == exp_duplicatas
    assert metrics["total_pendentes"] == exp_pendentes
    assert metrics["total_documentos"] == exp_documentos
    assert metrics["total_jogos"] == exp_jogos
    assert metrics["total_saves"] == exp_saves

    # Teste de paginação de duplicatas em dados reais
    page_1, total_dup, total_pages_dup = loader.get_duplicates_page(page=1, page_size=100)
    assert len(page_1) == 100
    assert total_dup == exp_duplicatas
    assert total_pages_dup == (exp_duplicatas + 99) // 100

    # Teste de filtro amostral
    amostrais, total_am, _ = loader.get_duplicates_page(page=1, page_size=100, confidence_filter="amostral")
    assert total_am >= 2
    assert len(amostrais) >= 2

    # Teste de pendentes
    page_pen, total_pen, total_pages_pen = loader.get_pending_page(page=1, page_size=100)
    assert len(page_pen) == 100
    assert total_pen == exp_pendentes
    assert total_pages_pen == (exp_pendentes + 99) // 100

    # Teste de documentos
    docs_p1, total_doc, total_pages_doc = loader.get_documents_page(page=1, page_size=50)
    assert len(docs_p1) == 50
    assert total_doc == exp_documentos
    assert total_pages_doc == (exp_documentos + 49) // 50

    # Teste de saves e marcação de suspeitos
    saves = loader.get_saves_list()
    assert len(saves) == exp_saves
    suspeitos = loader.get_saves_list(suspect_only=True)
    assert len(suspeitos) > 0
    # Confirma que Public\Documents\Steam é marcado como suspeito
    steam_shared = [s for s in suspeitos if "Public\\Documents\\Steam" in s["caminho_save"]]
    assert len(steam_shared) >= 2
