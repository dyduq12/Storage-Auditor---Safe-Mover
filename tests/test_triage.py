"""Suíte de testes automatizados para a camada de Triagem e Agrupamento por Pasta (Fase 3: Triage Engine).

Cobre:
- Agregação hierárquica por pasta e propagação bottom-up de arquivos e bytes.
- Estado inicial 100% não selecionado (UNDECIDED / Revisar depois).
- Herança de decisões pai -> descendentes.
- Precedência de decisão específica (subpasta explícita prevalece sobre o pai).
- Cálculo exato e reativo de contagens e bytes.
- Resiliência a grupos vazios.
- Filtros por categoria, texto e decisão.
- Sugestão técnica de regeneráveis (exclusão sob demanda, nunca silenciosa).
- Integração com ActionPlanGenerator (apenas itens INCLUDE entram no plano).
- Garantia de isolamento: nenhum arquivo real é lido ou modificado no sistema de arquivos.
"""

import os
from typing import Any, Dict, List

import pytest

from core.action_plan import ActionPlanGenerator
from core.triage import (
    CATEGORY_LABELS,
    FolderGroup,
    TriageCategory,
    TriageDecision,
    TriageManager,
    classify_file_category,
)


def test_categorization_rules():
    """Valida a classificação correta de arquivos em categorias isoladas."""
    # Regenerável
    assert classify_file_category("C:\\proj\\node_modules\\react\\index.js") == TriageCategory.REGENERAVEL
    assert classify_file_category("C:\\proj\\app\\build\\intermediates\\dex.jar") == TriageCategory.REGENERAVEL
    assert classify_file_category("C:\\proj\\__pycache__\\mod.cpython-310.pyc") == TriageCategory.REGENERAVEL
    assert classify_file_category("C:\\proj\\bundle.js.map") == TriageCategory.REGENERAVEL

    # Git
    assert classify_file_category("C:\\proj\\.git\\objects\\ab\\12345") == TriageCategory.GIT_REPO

    # Jogos e Launchers
    assert classify_file_category("C:\\Users\\User\\Documents\\Rockstar Games\\GTA V\\Profiles\\save.dat") == TriageCategory.GAMES_LAUNCHERS
    assert classify_file_category("C:\\Users\\User\\Documents\\My Games\\Far Cry\\save.bin") == TriageCategory.GAMES_LAUNCHERS

    # Anotações e Obsidian
    assert classify_file_category("C:\\Users\\User\\Documents\\Bad notes\\Diario.md") == TriageCategory.NOTES_KNOWLEDGE
    assert classify_file_category("C:\\Users\\User\\Documents\\Vault\\.obsidian\\workspace.json") == TriageCategory.NOTES_KNOWLEDGE

    # Fotos e Imagens
    assert classify_file_category("C:\\Fotos\\viagem\\IMG_001.JPG") == TriageCategory.FOTOS_IMAGENS
    assert classify_file_category("C:\\Fotos\\RAW\\photo.cr2") == TriageCategory.FOTOS_IMAGENS
    assert classify_file_category("C:\\Fotos\\design.psd") == TriageCategory.FOTOS_IMAGENS

    # Compactados
    assert classify_file_category("C:\\Downloads\\archive.zip") == TriageCategory.COMPACTADOS
    assert classify_file_category("C:\\Downloads\\pack.7z") == TriageCategory.COMPACTADOS

    # Documentos
    assert classify_file_category("C:\\Docs\\relatorio.pdf") == TriageCategory.DOCUMENTOS
    assert classify_file_category("C:\\Docs\\planilha.xlsx") == TriageCategory.DOCUMENTOS

    # Código Fonte / Projetos
    assert classify_file_category("C:\\proj\\src\\main.py") == TriageCategory.PROJETOS_DEV
    assert classify_file_category("C:\\proj\\src\\app.tsx") == TriageCategory.PROJETOS_DEV


def test_hierarchy_building_and_aggregation():
    """Valida construção hierárquica e propagação de arquivos e bytes."""
    items = [
        {"caminho": "C:\\Work\\Project\\src\\app.py", "tamanho_bytes": 1000},
        {"caminho": "C:\\Work\\Project\\src\\utils.py", "tamanho_bytes": 2000},
        {"caminho": "C:\\Work\\Project\\docs\\readme.md", "tamanho_bytes": 500},
        {"caminho": "C:\\Work\\Notes\\todo.txt", "tamanho_bytes": 300},
    ]

    manager = TriageManager(items)
    metrics = manager.get_summary_metrics()

    assert metrics["total_files"] == 4
    assert metrics["total_bytes"] == 3800

    # Verifica nó intermediário C:\Work\Project\src
    src_key = os.path.normpath("C:\\Work\\Project\\src")
    assert src_key in manager.folders
    src_grp = manager.folders[src_key]
    assert src_grp.direct_files == 2
    assert src_grp.total_files == 2
    assert src_grp.total_bytes == 3000

    # Verifica nó pai C:\Work\Project (deve acumular src e docs)
    proj_key = os.path.normpath("C:\\Work\\Project")
    assert proj_key in manager.folders
    proj_grp = manager.folders[proj_key]
    assert proj_grp.total_files == 3
    assert proj_grp.total_bytes == 3500


def test_initial_state_100_percent_undecided():
    """Garante que a triagem inicia 100% como 'Revisar Depois' sem pré-seleções."""
    items = [
        {"caminho": "C:\\Folder1\\file1.txt", "tamanho_bytes": 100},
        {"caminho": "C:\\Folder2\\file2.txt", "tamanho_bytes": 200},
    ]
    manager = TriageManager(items)
    metrics = manager.get_summary_metrics()

    assert metrics["included_files"] == 0
    assert metrics["included_bytes"] == 0
    assert metrics["excluded_files"] == 0
    assert metrics["excluded_bytes"] == 0
    assert metrics["undecided_files"] == 2
    assert metrics["undecided_bytes"] == 300

    for path, grp in manager.folders.items():
        assert grp.explicit_decision is None
        dec, _, is_exp = manager.resolve_folder_decision(path)
        assert dec == TriageDecision.UNDECIDED
        assert not is_exp


def test_group_decision_and_inheritance():
    """Testa marcação de grupo e propagação aos descendentes."""
    items = [
        {"caminho": "C:\\Base\\Sub1\\a.txt", "tamanho_bytes": 100},
        {"caminho": "C:\\Base\\Sub1\\Deep\\b.txt", "tamanho_bytes": 200},
        {"caminho": "C:\\Base\\Sub2\\c.txt", "tamanho_bytes": 300},
    ]
    manager = TriageManager(items)

    # Marca C:\Base\Sub1 como INCLUDE com herança para descendentes
    sub1_path = os.path.normpath("C:\\Base\\Sub1")
    manager.set_decision(sub1_path, TriageDecision.INCLUDE, apply_to_children=True)

    # a.txt e b.txt devem resolver como INCLUDE
    dec_a, orig_a, is_exp_a = manager.resolve_file_decision("C:\\Base\\Sub1\\a.txt")
    assert dec_a == TriageDecision.INCLUDE
    assert orig_a == sub1_path
    assert is_exp_a is True

    dec_b, orig_b, is_exp_b = manager.resolve_file_decision("C:\\Base\\Sub1\\Deep\\b.txt")
    assert dec_b == TriageDecision.INCLUDE
    assert orig_b == sub1_path
    assert is_exp_b is False  # herdado do ancestral Sub1

    # c.txt em Sub2 deve permanecer UNDECIDED
    dec_c, _, _ = manager.resolve_file_decision("C:\\Base\\Sub2\\c.txt")
    assert dec_c == TriageDecision.UNDECIDED

    metrics = manager.get_summary_metrics()
    assert metrics["included_files"] == 2
    assert metrics["included_bytes"] == 300
    assert metrics["undecided_files"] == 1
    assert metrics["undecided_bytes"] == 300


def test_specific_subfolder_precedence_over_parent():
    """Garante que uma decisão explícita em uma subpasta prevalece sobre a do pai."""
    items = [
        {"caminho": "C:\\Main\\general.txt", "tamanho_bytes": 100},
        {"caminho": "C:\\Main\\IgnoreMe\\junk.tmp", "tamanho_bytes": 500},
    ]
    manager = TriageManager(items)

    main_path = os.path.normpath("C:\\Main")
    ignore_path = os.path.normpath("C:\\Main\\IgnoreMe")

    # Pai inclui tudo
    manager.set_decision(main_path, TriageDecision.INCLUDE, apply_to_children=True)
    # Filho explícito exclui
    manager.set_decision(ignore_path, TriageDecision.EXCLUDE, apply_to_children=True)

    dec_gen, _, _ = manager.resolve_file_decision("C:\\Main\\general.txt")
    dec_junk, orig_junk, is_exp_junk = manager.resolve_file_decision("C:\\Main\\IgnoreMe\\junk.tmp")

    assert dec_gen == TriageDecision.INCLUDE
    assert dec_junk == TriageDecision.EXCLUDE
    assert orig_junk == ignore_path
    assert is_exp_junk is True

    metrics = manager.get_summary_metrics()
    assert metrics["included_files"] == 1
    assert metrics["included_bytes"] == 100
    assert metrics["excluded_files"] == 1
    assert metrics["excluded_bytes"] == 500
    assert metrics["undecided_files"] == 0


def test_empty_pending_list_resilience():
    """Valida comportamento robusto com lista vazia."""
    manager = TriageManager([])
    metrics = manager.get_summary_metrics()
    assert metrics["total_files"] == 0
    assert metrics["total_bytes"] == 0
    assert len(manager.get_filtered_groups()) == 0
    assert len(manager.get_included_items()) == 0


def test_search_and_filters():
    """Valida filtros por texto, categoria e decisão."""
    items = [
        {"caminho": "C:\\Photos\\pic1.jpg", "tamanho_bytes": 1000},
        {"caminho": "C:\\Code\\script.py", "tamanho_bytes": 500},
        {"caminho": "C:\\Trash\\node_modules\\pkg\\index.js", "tamanho_bytes": 200},
    ]
    manager = TriageManager(items)

    # 1. Busca por texto
    res_search = manager.get_filtered_groups(search_text="Photos")
    assert any("Photos" in g.path for g in res_search)
    assert not any("Trash" in g.path for g in res_search)

    # 2. Filtro por Categoria
    res_cat = manager.get_filtered_groups(category_filter=TriageCategory.REGENERAVEL)
    assert any("node_modules" in g.path for g in res_cat)

    # 3. Filtro por Decisão
    photos_path = os.path.normpath("C:\\Photos")
    manager.set_decision(photos_path, TriageDecision.INCLUDE)

    res_dec = manager.get_filtered_groups(decision_filter=TriageDecision.INCLUDE)
    assert any("Photos" in g.path for g in res_dec)
    assert not any("Code" in g.path for g in res_dec)


def test_regenerable_suggestion_explicit_activation_only():
    """Valida que sugestões para regeneráveis existem mas exigem ativação explícita."""
    items = [
        {"caminho": "C:\\Project\\node_modules\\lib\\a.js", "tamanho_bytes": 500},
        {"caminho": "C:\\Project\\src\\main.py", "tamanho_bytes": 1000},
    ]
    manager = TriageManager(items)

    # Antes da sugestão, tudo é UNDECIDED
    assert manager.get_summary_metrics()["undecided_files"] == 2
    assert manager.get_summary_metrics()["excluded_files"] == 0

    # Aplica sugestão sob demanda do usuário
    count = manager.apply_regenerable_suggestions()
    assert count >= 1

    # node_modules agora deve estar EXCLUDE, mas src continua UNDECIDED
    dec_js, _, _ = manager.resolve_file_decision("C:\\Project\\node_modules\\lib\\a.js")
    dec_py, _, _ = manager.resolve_file_decision("C:\\Project\\src\\main.py")

    assert dec_js == TriageDecision.EXCLUDE
    assert dec_py == TriageDecision.UNDECIDED


def test_triage_integration_with_action_plan_generator(tmp_path):
    """Valida que o ActionPlanGenerator aceita apenas itens com decisão INCLUDE."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    import json
    pen_data = {
        "itens": [
            {"caminho": str(tmp_path / "c_root" / "keep" / "save.pdf"), "tamanho_bytes": 100, "mtime": 1600000000.0},
            {"caminho": str(tmp_path / "c_root" / "junk" / "temp.log"), "tamanho_bytes": 200, "mtime": 1600000000.0},
            {"caminho": str(tmp_path / "c_root" / "skip" / "wait.txt"), "tamanho_bytes": 300, "mtime": 1600000000.0},
        ]
    }
    with open(reports_dir / "pendentes_de_backup_para_d.json", "w", encoding="utf-8") as f:
        json.dump(pen_data, f)
    with open(reports_dir / "documentos_avulsos.json", "w", encoding="utf-8") as f:
        json.dump({"itens": []}, f)

    manager = TriageManager(pen_data["itens"])

    # Usuário marca APENAS a pasta 'keep' como INCLUDE
    keep_path = str(tmp_path / "c_root" / "keep")
    junk_path = str(tmp_path / "c_root" / "junk")
    manager.set_decision(keep_path, TriageDecision.INCLUDE)
    manager.set_decision(junk_path, TriageDecision.EXCLUDE)
    # 'skip' permanece UNDECIDED

    generator = ActionPlanGenerator(
        reports_dir=str(reports_dir),
        target_base_dir=str(tmp_path / "target_d"),
    )

    plan = generator.generate_plan(
        include_pending=True,
        include_documents=False,
        triage_manager=manager,
    )

    # O plano DEVE conter EXATAMENTE 1 ordem (save.pdf)
    assert len(plan.ordens) == 1
    assert "save.pdf" in plan.ordens[0].source_path
    assert plan.totais["total_ordens"] == 1
    assert plan.totais["tamanho_total_bytes"] == 100


def test_isolation_guarantee_no_real_files_touched(tmp_path):
    """Garante que a triagem e planejamento não escrevem nos diretórios de origem."""
    src_file = tmp_path / "source.txt"
    src_file.write_text("ORIGINAL CONTENT", encoding="utf-8")
    mtime_before = src_file.stat().st_mtime

    items = [{"caminho": str(src_file), "tamanho_bytes": 16, "mtime": mtime_before}]
    manager = TriageManager(items)
    manager.set_decision(str(tmp_path), TriageDecision.INCLUDE)

    # Verifica que o arquivo de origem está 100% inalterado
    assert src_file.read_text(encoding="utf-8") == "ORIGINAL CONTENT"
    assert src_file.stat().st_mtime == mtime_before


def test_regression_real_reports_groups_loading():
    """Teste de regressão com reports reais: valida carregamento e integridade dos grupos."""
    if not os.path.exists("reports/pendentes_de_backup_para_d.json"):
        pytest.skip("reports/pendentes_de_backup_para_d.json não encontrado para teste de regressão real.")

    from gui.data_loader import AuditDataLoader

    loader = AuditDataLoader(reports_dir="reports")
    assert loader.load_from_directory() is True
    assert len(loader.pendentes) == 83977

    manager = TriageManager(loader.pendentes)

    # 1. Modo top_level: raízes principais de usuário em ordem previsível
    roots = manager.get_root_groups()
    assert len(roots) >= 5, f"Esperava pelo menos 5 pastas principais de usuário, obteve {len(roots)}"

    # Ordem previsível: Desktop, Downloads, Documents, Pictures, Videos
    root_names = [r.name.lower() for r in roots]
    expected_order_subset = ["desktop", "downloads", "documents", "pictures", "videos"]
    filtered_roots = [name for name in root_names if name in expected_order_subset]
    assert filtered_roots == expected_order_subset, f"Ordem das pastas raízes incorreta: {filtered_roots}"

    # Nomes amigáveis em português
    assert any("Área de Trabalho (Desktop)" in r.friendly_display_name for r in roots)
    assert any("Downloads" in r.friendly_display_name for r in roots)
    assert any("Documentos (Documents)" in r.friendly_display_name for r in roots)

    # Verifica que subpastas estão aninhadas sob suas respectivas raízes (não achatadas)
    doc_root = next(r for r in roots if r.name.lower() == "documents")
    doc_children = manager.get_child_folders(doc_root.path)
    assert any("Rockstar Games" in c.path for c in doc_children), "Pasta Rockstar Games deve estar aninhada sob Documents"
    assert any("Bad notes" in c.path for c in doc_children), "Pasta Bad notes deve estar aninhada sob Documents"

    desk_root = next(r for r in roots if r.name.lower() == "desktop")
    desk_children = manager.get_child_folders(desk_root.path)
    assert any("gg" in c.path for c in desk_children), "Pasta gg deve estar aninhada sob Desktop"

    # Verifica que a soma total de arquivos cobrindo direct_files cobre 100% dos 83.977
    total_direct_all_nodes = sum(grp.direct_files for grp in manager.folders.values())
    assert total_direct_all_nodes == 83977

    # Verifica estado inicial: 100% Revisar Depois
    metrics = manager.get_summary_metrics()
    assert metrics["total_files"] == 83977
    assert metrics["undecided_files"] == 83977
    assert metrics["included_files"] == 0
    assert metrics["excluded_files"] == 0

    # 2. Busca hierárquica em profundidade
    search_rockstar = manager.get_filtered_groups(search_text="Rockstar")
    assert len(search_rockstar) >= 1
    assert any("Rockstar Games" in g.path for g in search_rockstar)

    search_notes = manager.get_filtered_groups(search_text="Bad notes")
    assert len(search_notes) >= 1

    search_gg = manager.get_filtered_groups(search_text="gg")
    assert len(search_gg) >= 1

    # 3. Modo all (todas as subpastas)
    groups_all = manager.get_filtered_groups(group_mode="all")
    assert len(groups_all) > 5000, f"Deveria retornar milhares de subpastas, retornou {len(groups_all)}"


def test_regression_gui_treeview_population_and_interaction():
    """Teste de regressão da interface gráfica: geometria 1920x1080 (>= 20 linhas visíveis),
    árvore recolhida inicialmente, status no cabeçalho e interação."""
    try:
        import customtkinter as ctk
        root = ctk.CTk()
        root.geometry("1920x1080")
    except Exception as e:
        pytest.skip(f"Ambiente gráfico/Tcl indisponível para Tkinter: {e}")
    from gui.data_loader import AuditDataLoader
    from gui.views.tab_safe_mover import SafeMoverView

    loader = AuditDataLoader(reports_dir="reports")
    if not loader.load_from_directory():
        loader.pendentes = [
            {"caminho": "C:\\Users\\User\\Documents\\Rockstar Games\\GTA V\\save.dat", "tamanho_bytes": 5000, "mtime": 1600000000.0},
            {"caminho": "C:\\Users\\User\\Documents\\Bad notes\\Diario.md", "tamanho_bytes": 1000, "mtime": 1600000000.0},
            {"caminho": "C:\\Users\\User\\Desktop\\gg\\foto.jpg", "tamanho_bytes": 2000, "mtime": 1600000000.0},
        ]
        loader.is_loaded = True

    view = SafeMoverView(root, data_loader=loader)
    view.pack(fill="both", expand=True)
    root.update()

    view.refresh_view()
    root.update()

    # 1. Mede altura real do Treeview e calcula linhas visíveis (>= 20 linhas)
    tree_height = view.tree_groups.winfo_height()
    row_height = 22
    visible_lines = tree_height // row_height
    assert visible_lines >= 20, f"Treeview altura={tree_height}px dá apenas {visible_lines} linhas visíveis (esperado >= 20 em 1920x1080)"

    # 2. Raízes principais na primeira tela e subpastas recolhidas inicialmente
    root_items = view.tree_groups.get_children("")
    assert len(root_items) > 0, "A Treeview de grupos está vazia após refresh_view!"
    for r_item in root_items:
        # Garante que as raízes iniciam recolhidas
        assert view.tree_groups.item(r_item, "open") == False, f"Nó raiz {r_item} deveria iniciar recolhido (open=False)"

    first_item = root_items[0]
    folder_label = view.tree_groups.item(first_item, "text")
    values = view.tree_groups.item(first_item, "values")
    tags = view.tree_groups.item(first_item, "tags")

    assert folder_label != "", "Rótulo amigável do nó não pode ser vazio"
    assert len(values) >= 5, f"Treeview esperava ao menos 5 colunas de dados, obteve {len(values)}"
    assert "status_undecided" in tags, f"Estado inicial deveria ser status_undecided, obteve {tags}"
    assert "[⬜]" in folder_label, "Checkbox visual inicial deve ser [⬜]"

    # Rótulo de status no cabeçalho
    status_text = view.lbl_table_status.cget("text")
    assert "Pastas principais:" in status_text
    assert "Arquivos cobertos:" in status_text

    # 3. Interação de seleção e marcação
    view.tree_groups.selection_set(first_item)
    view.tree_groups.focus(first_item)
    view._on_group_selected(None)
    view._apply_decision_to_selected(TriageDecision.INCLUDE)
    root.update()

    updated_tags = view.tree_groups.item(first_item, "tags")
    assert "status_included" in updated_tags, f"Tag deveria ter atualizado para status_included, obteve {updated_tags}"
    assert "[☑]" in view.tree_groups.item(first_item, "text"), "Checkbox visual deve ser [☑] para INCLUDE"

    # Duplo clique ou alternância cicla para EXCLUDE
    view._cycle_folder_decision(first_item)
    root.update()
    assert "status_excluded" in view.tree_groups.item(first_item, "tags")
    assert "[✖]" in view.tree_groups.item(first_item, "text")

    # Arquivos na aba secundária
    file_children = view.tree_files.get_children()
    assert len(file_children) > 0, "A aba de arquivos deveria conter arquivos após selecionar a pasta"

    root.destroy()


def test_regression_gui_json_error_fallback():
    """Valida que erro de carregamento ou falta de dados exibe mensagem visível no cabeçalho."""
    try:
        import customtkinter as ctk
        root = ctk.CTk()
        root.geometry("1000x600")
    except Exception as e:
        pytest.skip(f"Ambiente gráfico/Tcl indisponível para Tkinter: {e}")
    from gui.data_loader import AuditDataLoader
    from gui.views.tab_safe_mover import SafeMoverView

    loader = AuditDataLoader(reports_dir="caminho_inexistente_para_erro")
    loader.load_from_directory()

    view = SafeMoverView(root, data_loader=loader, reports_dir="caminho_inexistente_para_erro")
    view.pack(fill="both", expand=True)
    root.update()

    status_text = view.lbl_table_status.cget("text")
    assert "⚠️" in status_text or "Falha" in status_text or "Nenhum arquivo" in status_text
    assert len(view.tree_groups.get_children()) == 0

    root.destroy()

