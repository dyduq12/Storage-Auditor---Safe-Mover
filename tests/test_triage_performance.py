"""Testes de Regressão e Performance da Triagem (Fase 3: Safe Mover Triage Performance & UX).

Cobre especificamente os requisitos:
1. Resposta rápida a decisões sem reconstruir árvore inteira nem recontar 83.977 arquivos.
2. Atualização cirúrgica in-place preservando nós abertos, seleção, foco e scroll.
3. Não recalcular agregados globais varrendo arquivos: cálculo reativo por delta em O(1).
4. Persistência assíncrona debounced de triage_decisions.json com flush garantido antes do plano.
5. Lazy loading sob demanda de subpastas na Treeview.
6. Meta de latência visual <= 200 ms em operações de clique/espaço.
"""

import json
import os
import time
from typing import Any, Dict, List
import pytest

from core.triage import TriageDecision, TriageManager, FolderGroup
from gui.data_loader import AuditDataLoader


def test_kpis_delta_precision_and_speed(tmp_path):
    """Valida que o cálculo de KPIs por delta é instantâneo (< 5ms) e matematicamente exato."""
    # Gera dataset hierárquico com 5.000 arquivos distribuídos em dezenas de pastas
    items: List[Dict[str, Any]] = []
    base_dirs = [
        "C:\\Users\\User\\Desktop\\Projetos",
        "C:\\Users\\User\\Desktop\\Projetos\\App1",
        "C:\\Users\\User\\Desktop\\Projetos\\App2",
        "C:\\Users\\User\\Downloads\\Archives",
        "C:\\Users\\User\\Documents\\Vault",
    ]
    for i in range(5000):
        d = base_dirs[i % len(base_dirs)]
        items.append({
            "caminho": f"{d}\\file_{i}.dat",
            "tamanho_bytes": 1000 + (i % 500),
        })

    tm = TriageManager(items)

    # 1. get_summary_metrics deve ser O(1) e rodar em < 1 ms
    t0 = time.perf_counter()
    m0 = tm.get_summary_metrics()
    t_get = (time.perf_counter() - t0) * 1000
    assert t_get < 1.0, f"get_summary_metrics demorou {t_get:.2f}ms (esperado < 1ms)"
    assert m0["total_files"] == 5000
    assert m0["included_files"] == 0
    assert m0["excluded_files"] == 0
    assert m0["undecided_files"] == 5000

    # 2. Decisão em nó pai: delta propaga para descendentes
    t1 = time.perf_counter()
    affected = tm.set_decision("C:\\Users\\User\\Desktop\\Projetos", TriageDecision.INCLUDE, apply_to_children=True)
    t_set = (time.perf_counter() - t1) * 1000
    assert t_set < 50.0, f"set_decision demorou {t_set:.2f}ms"
    assert len(affected) >= 3

    m1 = tm.get_summary_metrics()
    # Desktop\Projetos e suas duas subpastas App1 e App2 somam 3/5 dos 5.000 arquivos = 3.000 arquivos
    assert m1["included_files"] == 3000
    assert m1["undecided_files"] == 2000

    # 3. Sobrescrita específica em subpasta: App1 é EXCLUDE
    tm.set_decision("C:\\Users\\User\\Desktop\\Projetos\\App1", TriageDecision.EXCLUDE, apply_to_children=True)
    m2 = tm.get_summary_metrics()
    assert m2["included_files"] == 2000
    assert m2["excluded_files"] == 1000
    assert m2["undecided_files"] == 2000

    # 4. Redefinir App1 para UNDECIDED faz ele voltar a herdar INCLUDE do pai
    tm.set_decision("C:\\Users\\User\\Desktop\\Projetos\\App1", TriageDecision.UNDECIDED)
    m3 = tm.get_summary_metrics()
    assert m3["included_files"] == 3000
    assert m3["excluded_files"] == 0
    assert m3["undecided_files"] == 2000

    # 5. Redefinir tudo
    tm.reset_all_decisions()
    m4 = tm.get_summary_metrics()
    assert m4["included_files"] == 0
    assert m4["excluded_files"] == 0
    assert m4["undecided_files"] == 5000


def test_decision_latency_with_large_dataset():
    """Valida latência do motor com o relatório real (83.977 arquivos / 9.657 grupos)."""
    loader = AuditDataLoader(reports_dir="reports")
    if not loader.load_from_directory():
        pytest.skip("Relatórios reais de auditoria não encontrados em reports/")

    tm = TriageManager(loader.pendentes)

    root_groups = tm.get_root_groups()
    desktop_root = root_groups[0] if root_groups else r"C:\Users\TestUser\Desktop"
    t0 = time.perf_counter()
    affected = tm.set_decision(desktop_root, TriageDecision.INCLUDE, apply_to_children=True)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Meta estrita: bem abaixo de 200ms
    assert elapsed_ms < 100.0, f"set_decision em pasta grande demorou {elapsed_ms:.2f}ms (esperado < 100ms)"
    assert len(affected) > 0

    m = tm.get_summary_metrics()
    assert m["included_files"] > 0
    assert m["included_bytes"] > 0


def test_treeview_preserves_expansion_selection_and_scroll():
    """Valida que a atualização in-place na GUI NÃO recolhe a árvore, mantendo seleção, foco e scroll."""
    try:
        import customtkinter as ctk
        root = ctk.CTk()
        root.geometry("1920x1080")
    except Exception as e:
        pytest.skip(f"Tkinter/display indisponível: {e}")

    from gui.views.tab_safe_mover import SafeMoverView

    loader = AuditDataLoader(reports_dir="reports")
    if not loader.load_from_directory():
        loader.pendentes = [
            {"caminho": "C:\\Users\\User\\Desktop\\gg\\foto.jpg", "tamanho_bytes": 2000, "mtime": 1600000000.0},
            {"caminho": "C:\\Users\\User\\Desktop\\gta\\save.dat", "tamanho_bytes": 5000, "mtime": 1600000000.0},
            {"caminho": "C:\\Users\\User\\Downloads\\doc.pdf", "tamanho_bytes": 1000, "mtime": 1600000000.0},
        ]
        loader.is_loaded = True

    view = SafeMoverView(root, data_loader=loader)
    view.pack(fill="both", expand=True)
    root.update()

    # 1. Obtém raízes
    root_items = view.tree_groups.get_children("")
    assert len(root_items) > 0
    first_root = root_items[0]

    # 2. Expande o primeiro nó raiz manualmente
    view.tree_groups.item(first_root, open=True)
    view._expand_lazy_children(first_root)
    root.update()

    # Garante que está aberto
    assert view.tree_groups.item(first_root, "open") == True

    # Filhos do nó raiz
    child_items = view.tree_groups.get_children(first_root)
    assert len(child_items) > 0, "Nó raiz deveria conter subpastas filhas"
    first_child = child_items[0]

    # Expande o primeiro filho se ele tiver subpastas
    view.tree_groups.item(first_child, open=True)
    view._expand_lazy_children(first_child)
    root.update()
    assert view.tree_groups.item(first_child, "open") == True

    # 3. Seleciona e foca o filho
    view.tree_groups.selection_set(first_child)
    view.tree_groups.focus(first_child)
    view._on_group_selected(None)
    assert view.selected_folder_path == first_child

    # 4. Aplica decisão (INCLUDE)
    t0 = time.perf_counter()
    view._apply_decision_to_selected(TriageDecision.INCLUDE)
    root.update()
    t_click = (time.perf_counter() - t0) * 1000

    # REQUISITO 9: Resposta visual <= 200 ms
    assert t_click < 200.0, f"Tempo de resposta ao clique foi de {t_click:.2f}ms (meta <= 200ms)"

    # REQUISITO 3: Preservação estrita
    # A) Nós expandidos NÃO devem ter recolhido!
    assert view.tree_groups.item(first_root, "open") == True, "ERRO CRÍTICO: Nó raiz foi recolhido após clique!"
    assert view.tree_groups.item(first_child, "open") == True, "ERRO CRÍTICO: Nó filho foi recolhido após clique!"

    # B) Seleção e foco devem permanecer intactos
    curr_sel = view.tree_groups.selection()
    assert len(curr_sel) == 1 and curr_sel[0] == first_child, "Seleção da pasta foi perdida!"
    assert view.tree_groups.focus() == first_child, "Foco da pasta foi perdido!"

    # C) Visual atualizado in-place para INCLUDE
    assert "[☑]" in view.tree_groups.item(first_child, "text")

    # D) Cicla para EXCLUDE e verifica novamente preservação
    view._cycle_folder_decision(first_child)
    root.update()
    assert view.tree_groups.item(first_root, "open") == True, "Nó raiz recolheu após ciclar decisão!"
    assert "[✖]" in view.tree_groups.item(first_child, "text")


def test_debounced_persistence_and_flush(tmp_path):
    """Valida persistência assíncrona debounced e flush garantido antes do Action Plan."""
    try:
        import customtkinter as ctk
        root = ctk.CTk()
    except Exception as e:
        pytest.skip(f"Tkinter indisponível: {e}")

    from gui.views.tab_safe_mover import SafeMoverView

    loader = AuditDataLoader(reports_dir=str(tmp_path))
    loader.pendentes = [
        {"caminho": "C:\\Users\\User\\Desktop\\file1.txt", "tamanho_bytes": 100},
        {"caminho": "C:\\Users\\User\\Desktop\\file2.txt", "tamanho_bytes": 200},
    ]
    loader.is_loaded = True

    view = SafeMoverView(root, data_loader=loader, reports_dir=str(tmp_path))
    view.pack()
    root.update()

    # Aplica decisão: deve agendar debounce e exibir 'Salvando...'
    desktop_path = os.path.normpath("C:\\Users\\User\\Desktop")
    view.tree_groups.selection_set(desktop_path)
    view.selected_folder_path = desktop_path
    view._apply_decision_to_selected(TriageDecision.INCLUDE)

    assert view._pending_save is True
    assert "Salvando" in view.lbl_save_status.cget("text")

    # Flush forçado garante gravação no disco
    view.flush_save()
    assert view._pending_save is False
    assert "Salvo" in view.lbl_save_status.cget("text")

    # Confirma que o JSON foi gravado e possui a decisão
    assert os.path.exists(view.triage_decisions_file)
    with open(view.triage_decisions_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert desktop_path in data.get("explicit_decisions", {})
