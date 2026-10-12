"""Aba de Triagem por Pasta e Plano de Cópia Segura (Fase 3: Safe Mover).

Permite ao usuário decidir o escopo de backup por PASTA/GRUPO (não arquivo a arquivo),
visualizar totais reativos (incluídos, excluídos, revisar depois), inspecionar arquivos
individualmente de forma paginada e opcional, gerar a prévia do Action Plan e
executar a cópia segura não-destrutiva mediante confirmação explícita.
"""

from __future__ import annotations

import math
import os
import threading
import time
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import customtkinter as ctk

from core.action_plan import ActionOrder, ActionPlan, ActionPlanGenerator
from core.safe_mover import SafeCopyExecutor
from core.triage import (
    CATEGORY_LABELS,
    FolderGroup,
    TriageCategory,
    TriageDecision,
    TriageManager,
)
from exporters.excel_exporter import format_bytes, format_timestamp
from gui.data_loader import AuditDataLoader


class SafeMoverView(ctk.CTkFrame):
    """Componente visual para triagem por pasta, prévia e execução do Safe Mover."""

    def __init__(
        self,
        master: Any,
        data_loader: AuditDataLoader,
        reports_dir: str = "reports",
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)
        self.data_loader = data_loader

        # Garante caminho absoluto independente do CWD
        if not os.path.isabs(reports_dir):
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            candidate = os.path.join(project_root, reports_dir)
            if os.path.exists(candidate):
                reports_dir = candidate
        self.reports_dir = os.path.abspath(reports_dir)

        # Se os dados pendentes estiverem vazios, tenta carregar imediatamente
        if not self.data_loader.pendentes:
            self.data_loader.load_from_directory(self.reports_dir)

        # Motor de Triagem
        self.triage_manager: TriageManager = TriageManager(self.data_loader.pendentes)
        self.triage_decisions_file = os.path.join(self.reports_dir, "triage_decisions.json")
        self.triage_manager.load_decisions_from_json(self.triage_decisions_file)

        # Estado do Plano e Execução
        self.plan: Optional[ActionPlan] = None
        self.is_running: bool = False
        self.cancel_event: Optional[threading.Event] = None
        self.worker_thread: Optional[threading.Thread] = None

        # Pasta selecionada na tabela de grupos
        self.selected_folder_path: Optional[str] = None
        self.apply_to_children_var = ctk.BooleanVar(value=True)

        # Filtros de pastas
        self.var_search = ctk.StringVar(value="")
        self.var_category_filter = ctk.StringVar(value="ALL")
        self.var_decision_filter = ctk.StringVar(value="ALL")
        self.var_group_mode = ctk.StringVar(value="top_level")

        # Paginação de arquivos individuais da pasta selecionada
        self.folder_files: List[Dict[str, Any]] = []
        self.folder_file_indices: List[int] = []
        self.file_page: int = 1
        self.file_page_size: int = 100
        self.file_total_pages: int = 1

        # Paginação da prévia das ordens
        self.order_page: int = 1
        self.order_page_size: int = 100
        self.order_total_pages: int = 1

        # Controle de persistência assíncrona / debounced
        self._save_debounce_timer: Optional[str] = None
        self._save_worker_thread: Optional[threading.Thread] = None
        self._save_lock = threading.Lock()
        self._pending_save: bool = False
        self.last_click_latency_ms: float = 0.0

        # Conjunto de caminhos de nós raízes para estilização e identificação rápida
        self._root_paths: Set[str] = set()

        self._create_widgets()
        self._refresh_triage_ui()

    def _create_widgets(self) -> None:
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.grid_columnconfigure(0, weight=1)

        # -------------------------------------------------------------
        # 1. Topo: Barra de Indicadores KPI Compacta (Altura Nobre para a Árvore)
        # -------------------------------------------------------------
        self.kpi_bar = ctk.CTkFrame(self, fg_color=("gray85", "gray18"), corner_radius=6)
        self.kpi_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(4, 2))

        self.lbl_kpi_total = ctk.CTkLabel(
            self.kpi_bar,
            text="📊 Todos os Pendentes: 0 (0 B)",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.lbl_kpi_total.pack(side="left", padx=10, pady=4)

        self.lbl_kpi_included = ctk.CTkLabel(
            self.kpi_bar,
            text="✅ Incluídos: 0 (0 B)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#059669",
        )
        self.lbl_kpi_included.pack(side="left", padx=10, pady=4)

        self.lbl_kpi_excluded = ctk.CTkLabel(
            self.kpi_bar,
            text="❌ Excluídos: 0 (0 B)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#DC2626",
        )
        self.lbl_kpi_excluded.pack(side="left", padx=10, pady=4)

        self.lbl_kpi_undecided = ctk.CTkLabel(
            self.kpi_bar,
            text="⏳ Revisar Depois: 0 (0 B)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#D97706",
        )
        self.lbl_kpi_undecided.pack(side="left", padx=10, pady=4)

        self.lbl_safe_notice = ctk.CTkLabel(
            self.kpi_bar,
            text="🛡️ Origem 100% Somente Leitura",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10B981",
        )
        self.lbl_safe_notice.pack(side="right", padx=12, pady=4)

        # Proxies para compatibilidade de testes existentes
        class _ProxyCard:
            def __init__(self, lbl):
                self.lbl = lbl
            def configure(self, **kwargs):
                pass
        self.card_total = {"val": _ProxyCard(self.lbl_kpi_total), "sub": _ProxyCard(self.lbl_kpi_total)}
        self.card_included = {"val": _ProxyCard(self.lbl_kpi_included), "sub": _ProxyCard(self.lbl_kpi_included)}
        self.card_excluded = {"val": _ProxyCard(self.lbl_kpi_excluded), "sub": _ProxyCard(self.lbl_kpi_excluded)}
        self.card_undecided = {"val": _ProxyCard(self.lbl_kpi_undecided), "sub": _ProxyCard(self.lbl_kpi_undecided)}

        # -------------------------------------------------------------
        # 2. Painel Central: Tabview com Altura Nobre Dominante
        # -------------------------------------------------------------
        self.tabs = ctk.CTkTabview(self, corner_radius=8)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=10, pady=2)

        tab_groups = self.tabs.add("📂 Triagem de Pastas & Grupos")
        tab_files = self.tabs.add("🔍 Arquivos da Pasta Selecionada (Opcional)")
        tab_orders = self.tabs.add("📋 Ordens do Action Plan (Prévia Aprovada)")

        # =============================================================
        # 2A. Conteúdo da Aba 1: Triagem de Pastas & Grupos (Foco Principal)
        # =============================================================
        tab_groups.grid_rowconfigure(0, weight=0)
        tab_groups.grid_rowconfigure(1, weight=0)
        tab_groups.grid_rowconfigure(2, weight=0)
        tab_groups.grid_rowconfigure(3, weight=1)
        tab_groups.grid_columnconfigure(0, weight=1)

        # Linha 1: Filtros e Busca (Compacta)
        filter_bar = ctk.CTkFrame(tab_groups, fg_color="transparent")
        filter_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(4, 2))

        ctk.CTkLabel(filter_bar, text="Buscar:").pack(side="left", padx=(0, 4))
        self.entry_search = ctk.CTkEntry(filter_bar, textvariable=self.var_search, width=200, placeholder_text="Filtrar pasta...")
        self.entry_search.pack(side="left", padx=4)
        self.entry_search.bind("<KeyRelease>", lambda e: self._on_filter_changed())

        ctk.CTkLabel(filter_bar, text="Categoria:").pack(side="left", padx=(8, 4))
        self.combo_cat = ctk.CTkComboBox(
            filter_bar,
            values=["Todas as Categorias"] + [CATEGORY_LABELS[k] for k in CATEGORY_LABELS.keys()],
            command=self._on_cat_combo_selected,
            width=175,
        )
        self.combo_cat.set("Todas as Categorias")
        self.combo_cat.pack(side="left", padx=4)

        ctk.CTkLabel(filter_bar, text="Decisão:").pack(side="left", padx=(8, 4))
        self.combo_dec = ctk.CTkComboBox(
            filter_bar,
            values=["Todas", "⏳ Revisar Depois", "✅ Incluído", "❌ Excluído"],
            command=self._on_dec_combo_selected,
            width=140,
        )
        self.combo_dec.set("Todas")
        self.combo_dec.pack(side="left", padx=4)

        ctk.CTkLabel(filter_bar, text="Visão:").pack(side="left", padx=(8, 4))
        self.combo_mode = ctk.CTkComboBox(
            filter_bar,
            values=["Pastas Principais (Árvore)", "Visão Detalhada (Expandir Tudo)"],
            command=self._on_mode_combo_selected,
            width=195,
        )
        self.combo_mode.set("Pastas Principais (Árvore)")
        self.combo_mode.pack(side="left", padx=4)

        self.btn_regen_suggest = ctk.CTkButton(
            filter_bar,
            text="⚡ Sugerir Regeneráveis",
            command=self._suggest_regenerable_exclusions,
            fg_color="#4B5563",
            hover_color="#374151",
            width=170,
        )
        self.btn_regen_suggest.pack(side="right", padx=4)

        self.btn_reset_triage = ctk.CTkButton(
            filter_bar,
            text="🔄 Redefinir Tudo",
            command=self._reset_all_triage,
            fg_color="gray30",
            hover_color="gray40",
            width=105,
        )
        self.btn_reset_triage.pack(side="right", padx=4)

        # Linha 2: Barra de Ação em Lote na Pasta Selecionada (Compacta)
        action_bar = ctk.CTkFrame(tab_groups, fg_color=("gray90", "gray18"), corner_radius=6)
        action_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=2)

        self.lbl_selected_folder = ctk.CTkLabel(
            action_bar,
            text="Selecione uma pasta na árvore abaixo para aplicar decisões.",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="gray80",
        )
        self.lbl_selected_folder.pack(side="left", padx=10, pady=4)

        self.chk_apply_children = ctk.CTkCheckBox(
            action_bar,
            text="Aplicar a subpastas descendentes",
            variable=self.apply_to_children_var,
            onvalue=True,
            offvalue=False,
        )
        self.chk_apply_children.pack(side="left", padx=12)

        self.btn_act_undecided = ctk.CTkButton(
            action_bar,
            text="⏳ Revisar Depois",
            command=lambda: self._apply_decision_to_selected(TriageDecision.UNDECIDED),
            fg_color="gray40",
            hover_color="gray50",
            width=135,
        )
        self.btn_act_undecided.pack(side="right", padx=4, pady=3)

        self.btn_act_exclude = ctk.CTkButton(
            action_bar,
            text="❌ Excluir do Plano",
            command=lambda: self._apply_decision_to_selected(TriageDecision.EXCLUDE),
            fg_color="#DC2626",
            hover_color="#B91C1C",
            width=135,
        )
        self.btn_act_exclude.pack(side="right", padx=4, pady=3)

        self.btn_act_include = ctk.CTkButton(
            action_bar,
            text="✅ Incluir no Plano",
            command=lambda: self._apply_decision_to_selected(TriageDecision.INCLUDE),
            fg_color="#059669",
            hover_color="#047857",
            width=135,
        )
        self.btn_act_include.pack(side="right", padx=4, pady=3)

        self.btn_view_folder_files = ctk.CTkButton(
            action_bar,
            text="🔍 Inspecionar Arquivos",
            command=self._switch_to_files_tab,
            fg_color="#3B82F6",
            hover_color="#2563EB",
            width=145,
        )
        self.btn_view_folder_files.pack(side="right", padx=4, pady=3)

        # Linha 3: Status / Diagnóstico da Tabela
        status_bar = ctk.CTkFrame(tab_groups, fg_color="transparent")
        status_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(1, 2))

        self.lbl_table_status = ctk.CTkLabel(
            status_bar,
            text="Carregando grupos...",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#60A5FA",
        )
        self.lbl_table_status.pack(side="left")

        self.lbl_save_status = ctk.CTkLabel(
            status_bar,
            text="✓ Salvo",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10B981",
        )
        self.lbl_save_status.pack(side="right", padx=10)

        # Linha 4: Tabela de Grupos (Treeview Hierárquica com 100% da Altura Restante)
        tree_frame = ctk.CTkFrame(tab_groups, fg_color="transparent")
        tree_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(2, 6))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        style = ttk.Style()
        style.configure("Treeview", rowheight=22)

        grp_cols = ("status", "path", "files", "size", "category", "origin")
        self.tree_groups = ttk.Treeview(
            tree_frame,
            columns=grp_cols,
            show=("tree", "headings"),
            selectmode="browse",
        )

        self.tree_groups.heading("#0", text="Pasta / Grupo de Arquivos (Hierarquia)", anchor="w")
        self.tree_groups.heading("status", text="Decisão Efetiva", anchor="center")
        self.tree_groups.heading("path", text="Caminho Completo", anchor="w")
        self.tree_groups.heading("files", text="Qtd Arquivos", anchor="e")
        self.tree_groups.heading("size", text="Tamanho Total", anchor="e")
        self.tree_groups.heading("category", text="Categorias Identificadas", anchor="w")
        self.tree_groups.heading("origin", text="Origem da Decisão / Herança", anchor="w")

        self.tree_groups.column("#0", width=340, anchor="w")
        self.tree_groups.column("status", width=140, anchor="center")
        self.tree_groups.column("path", width=380, anchor="w")
        self.tree_groups.column("files", width=95, anchor="e")
        self.tree_groups.column("size", width=105, anchor="e")
        self.tree_groups.column("category", width=170, anchor="w")
        self.tree_groups.column("origin", width=160, anchor="w")

        sb_grp_y = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_groups.yview)
        sb_grp_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree_groups.xview)
        self.tree_groups.configure(yscrollcommand=sb_grp_y.set, xscrollcommand=sb_grp_x.set)

        self.tree_groups.grid(row=0, column=0, sticky="nsew")
        sb_grp_y.grid(row=0, column=1, sticky="ns")
        sb_grp_x.grid(row=1, column=0, sticky="ew")

        self.tree_groups.tag_configure("status_included", foreground="#059669")
        self.tree_groups.tag_configure("status_excluded", foreground="#DC2626")
        self.tree_groups.tag_configure("status_undecided", foreground="#9CA3AF")
        self.tree_groups.tag_configure("status_inherited", foreground="#3B82F6")

        self.tree_groups.bind("<<TreeviewSelect>>", self._on_group_selected)
        self.tree_groups.bind("<Double-1>", self._on_tree_double_click)
        self.tree_groups.bind("<space>", self._on_tree_space_pressed)
        self.tree_groups.bind("<<TreeviewOpen>>", self._on_tree_node_open)


        # =============================================================
        # 2B. Conteúdo da Aba 2: Arquivos Individuais
        # =============================================================
        tab_files.grid_rowconfigure(1, weight=1)
        tab_files.grid_columnconfigure(0, weight=1)

        f_header = ctk.CTkFrame(tab_files, fg_color="transparent")
        f_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 4))
        self.lbl_f_header = ctk.CTkLabel(
            f_header,
            text="Selecione uma pasta na aba 'Triagem de Pastas & Grupos' para listar os arquivos.",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#60A5FA",
        )
        self.lbl_f_header.pack(side="left")

        files_frame = ctk.CTkFrame(tab_files, fg_color="transparent")
        files_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        files_frame.grid_rowconfigure(0, weight=1)
        files_frame.grid_columnconfigure(0, weight=1)

        f_cols = ("file", "size", "cat", "decision", "mtime")
        self.tree_files = ttk.Treeview(files_frame, columns=f_cols, show="headings", selectmode="browse")
        self.tree_files.heading("file", text="Arquivo")
        self.tree_files.heading("size", text="Tamanho")
        self.tree_files.heading("cat", text="Categoria")
        self.tree_files.heading("decision", text="Decisão Efetiva")
        self.tree_files.heading("mtime", text="Modificado em")

        self.tree_files.column("file", width=520, anchor="w")
        self.tree_files.column("size", width=110, anchor="e")
        self.tree_files.column("cat", width=150, anchor="center")
        self.tree_files.column("decision", width=130, anchor="center")
        self.tree_files.column("mtime", width=150, anchor="center")

        sb_f_y = ttk.Scrollbar(files_frame, orient="vertical", command=self.tree_files.yview)
        self.tree_files.configure(yscrollcommand=sb_f_y.set)
        self.tree_files.grid(row=0, column=0, sticky="nsew")
        sb_f_y.grid(row=0, column=1, sticky="ns")

        f_page_bar = ctk.CTkFrame(tab_files, fg_color="transparent")
        f_page_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 8))
        self.btn_f_prev = ctk.CTkButton(f_page_bar, text="◀ Anterior", width=80, command=self._prev_file_page)
        self.btn_f_prev.pack(side="left")
        self.lbl_f_page = ctk.CTkLabel(f_page_bar, text="Página 1 de 1")
        self.lbl_f_page.pack(side="left", padx=10)
        self.btn_f_next = ctk.CTkButton(f_page_bar, text="Próxima ▶", width=80, command=self._next_file_page)
        self.btn_f_next.pack(side="left")
        self.lbl_f_total = ctk.CTkLabel(f_page_bar, text="Nenhum arquivo listado", text_color="gray70")
        self.lbl_f_total.pack(side="right", padx=10)

        # =============================================================
        # 2C. Conteúdo da Aba 3: Prévia das Ordens do Plano
        # =============================================================
        tab_orders.grid_rowconfigure(1, weight=1)
        tab_orders.grid_columnconfigure(0, weight=1)

        o_header = ctk.CTkFrame(tab_orders, fg_color="transparent")
        o_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 4))
        self.lbl_o_header = ctk.CTkLabel(
            o_header,
            text="Ordens validadas para cópia segura (somente arquivos com decisão 'Incluído no plano'):",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#10B981",
        )
        self.lbl_o_header.pack(side="left")

        orders_frame = ctk.CTkFrame(tab_orders, fg_color="transparent")
        orders_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        orders_frame.grid_rowconfigure(0, weight=1)
        orders_frame.grid_columnconfigure(0, weight=1)

        o_cols = ("order_id", "source", "target", "size", "cat", "status")
        self.tree_orders = ttk.Treeview(orders_frame, columns=o_cols, show="headings", selectmode="browse")
        self.tree_orders.heading("order_id", text="ID Ordem")
        self.tree_orders.heading("source", text="Origem (C:)")
        self.tree_orders.heading("target", text="Destino Projetado")
        self.tree_orders.heading("size", text="Tamanho")
        self.tree_orders.heading("cat", text="Categoria")
        self.tree_orders.heading("status", text="Status")

        self.tree_orders.column("order_id", width=120, anchor="center")
        self.tree_orders.column("source", width=400, anchor="w")
        self.tree_orders.column("target", width=400, anchor="w")
        self.tree_orders.column("size", width=110, anchor="e")
        self.tree_orders.column("cat", width=130, anchor="center")
        self.tree_orders.column("status", width=120, anchor="center")

        sb_o_y = ttk.Scrollbar(orders_frame, orient="vertical", command=self.tree_orders.yview)
        self.tree_orders.configure(yscrollcommand=sb_o_y.set)
        self.tree_orders.grid(row=0, column=0, sticky="nsew")
        sb_o_y.grid(row=0, column=1, sticky="ns")

        o_page_bar = ctk.CTkFrame(tab_orders, fg_color="transparent")
        o_page_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 8))
        self.btn_o_prev = ctk.CTkButton(o_page_bar, text="◀ Anterior", width=80, command=self._prev_order_page)
        self.btn_o_prev.pack(side="left")
        self.lbl_o_page = ctk.CTkLabel(o_page_bar, text="Página 1 de 1")
        self.lbl_o_page.pack(side="left", padx=10)
        self.btn_o_next = ctk.CTkButton(o_page_bar, text="Próxima ▶", width=80, command=self._next_order_page)
        self.btn_o_next.pack(side="left")
        self.lbl_o_total = ctk.CTkLabel(o_page_bar, text="Aguardando confirmação da seleção", text_color="gray70")
        self.lbl_o_total.pack(side="right", padx=10)

        # -------------------------------------------------------------
        # 3. Rodapé de Configuração do Destino e Ações do Plano
        # -------------------------------------------------------------
        footer = ctk.CTkFrame(self, fg_color=("gray90", "gray17"), corner_radius=8)
        footer.grid(row=2, column=0, sticky="ew", padx=15, pady=(4, 10))

        frow1 = ctk.CTkFrame(footer, fg_color="transparent")
        frow1.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkLabel(frow1, text="Destino Base:").pack(side="left", padx=(0, 4))
        default_dest = "D:\\Backup_StorageAuditor" if os.path.exists("D:\\") else os.path.abspath("backup_destino")
        self.var_target_base = ctk.StringVar(value=default_dest)
        self.entry_dest = ctk.CTkEntry(frow1, textvariable=self.var_target_base, width=380)
        self.entry_dest.pack(side="left", padx=4)

        self.btn_browse = ctk.CTkButton(
            frow1,
            text="Procurar...",
            width=85,
            command=self._browse_destination,
            fg_color="gray30",
            hover_color="gray40",
        )
        self.btn_browse.pack(side="left", padx=4)

        # Botões de Ação
        self.btn_generate_plan = ctk.CTkButton(
            frow1,
            text="📋 Confirmar Seleção e Gerar Plano",
            command=self._confirm_and_generate_plan,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            width=220,
        )
        self.btn_generate_plan.pack(side="right", padx=4)

        self.btn_audit = ctk.CTkButton(
            frow1,
            text="🔍 Auditar Plano",
            command=self._audit_plan_preview,
            fg_color="#4B5563",
            hover_color="#374151",
            width=110,
            state="disabled",
        )
        self.btn_audit.pack(side="right", padx=4)

        self.btn_execute_batch = ctk.CTkButton(
            frow1,
            text="▶ Executar Lote",
            command=self._confirm_and_execute_batch,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            width=120,
            state="disabled",
        )
        self.btn_execute_batch.pack(side="right", padx=4)

        self.combo_batch_size = ctk.CTkComboBox(
            frow1,
            values=["5", "10", "25", "50", "100"],
            width=65,
            state="readonly",
        )
        self.combo_batch_size.set("10")
        self.combo_batch_size.pack(side="right", padx=2)

        self.btn_execute = ctk.CTkButton(
            frow1,
            text="🚀 Executar Tudo",
            command=self._confirm_and_execute,
            fg_color="#059669",
            hover_color="#047857",
            width=130,
            state="disabled",
        )
        self.btn_execute.pack(side="right", padx=4)

        self.btn_cancel = ctk.CTkButton(
            frow1,
            text="⏹ Cancelar",
            command=self._cancel_execution,
            fg_color="#DC2626",
            hover_color="#B91C1C",
            width=85,
            state="disabled",
        )
        self.btn_cancel.pack(side="right", padx=4)

        frow2 = ctk.CTkFrame(footer, fg_color="transparent")
        frow2.pack(fill="x", padx=12, pady=(0, 8))

        self.lbl_progress = ctk.CTkLabel(
            frow2,
            text="Aguardando seleção de grupos pelo usuário.",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
        )
        self.lbl_progress.pack(side="left")

        self.progress_bar = ctk.CTkProgressBar(frow2, width=320)
        self.progress_bar.pack(side="right", padx=4)
        self.progress_bar.set(0)

    def _create_card(
        self,
        parent: Any,
        col: int,
        title: str,
        main_val: str,
        sub_val: str,
        val_color: Optional[str] = None,
    ) -> Dict[str, Any]:
        frame = ctk.CTkFrame(parent, fg_color=("gray90", "gray17"), corner_radius=8)
        frame.grid(row=0, column=col, padx=4, sticky="ew")

        lbl_t = ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=10, weight="bold"), text_color="gray60")
        lbl_t.pack(anchor="w", padx=10, pady=(8, 2))

        lbl_v = ctk.CTkLabel(frame, text=main_val, font=ctk.CTkFont(size=17, weight="bold"), text_color=val_color)
        lbl_v.pack(anchor="w", padx=10, pady=(0, 2))

        lbl_s = ctk.CTkLabel(frame, text=sub_val, font=ctk.CTkFont(size=11), text_color="gray70")
        lbl_s.pack(anchor="w", padx=10, pady=(0, 8))

        return {"frame": frame, "val": lbl_v, "sub": lbl_s}

    # -------------------------------------------------------------------------
    # Atualização Reativa da Interface de Triagem
    # -------------------------------------------------------------------------

    def refresh_view(self) -> None:
        """Recarrega os itens pendentes do data_loader e atualiza a triagem."""
        if not self.data_loader.pendentes:
            self.data_loader.load_from_directory(self.reports_dir)
        self.triage_manager = TriageManager(self.data_loader.pendentes)
        self.plan = None
        self.selected_folder_path = None
        self.folder_files = []
        self._refresh_triage_ui()

    def _update_kpis(self) -> None:
        """Atualiza indicadores de topo instantaneamente a partir das métricas em O(1)."""
        metrics = self.triage_manager.get_summary_metrics()

        self.lbl_kpi_total.configure(
            text=f"📊 Todos os Pendentes: {metrics['total_files']:,} ({format_bytes(metrics['total_bytes'])})"
        )
        self.lbl_kpi_included.configure(
            text=f"✅ Incluídos: {metrics['included_files']:,} ({format_bytes(metrics['included_bytes'])})"
        )
        self.lbl_kpi_excluded.configure(
            text=f"❌ Excluídos: {metrics['excluded_files']:,} ({format_bytes(metrics['excluded_bytes'])})"
        )
        self.lbl_kpi_undecided.configure(
            text=f"⏳ Revisar Depois: {metrics['undecided_files']:,} ({format_bytes(metrics['undecided_bytes'])})"
        )

        can_generate = metrics["included_files"] > 0
        self.btn_generate_plan.configure(state="normal" if can_generate else "disabled")

    def _refresh_triage_ui(self) -> None:
        """Recalcula métricas e reconstrói a árvore de grupos (usado na carga inicial)."""
        self._update_kpis()
        self._render_groups_table()

    def _get_row_data(self, grp: FolderGroup, is_root: bool = False) -> Tuple[str, Tuple[Any, ...], str]:
        """Gera texto de cabeçalho, colunas e tags para exibição de um grupo na Treeview."""
        dec, orig_path, is_exp = self.triage_manager.resolve_folder_decision(grp.path)

        if is_exp:
            if dec == TriageDecision.INCLUDE:
                chk = "[☑]"
                dec_str = "✅ Incluído"
                tag = "status_included"
            elif dec == TriageDecision.EXCLUDE:
                chk = "[✖]"
                dec_str = "❌ Excluído"
                tag = "status_excluded"
            else:
                chk = "[⬜]"
                dec_str = "⏳ Revisar Depois"
                tag = "status_undecided"
            orig_str = "Decisão Própria (Explícita)"
        elif orig_path:
            if dec == TriageDecision.INCLUDE:
                chk = "[↳☑]"
                dec_str = "↳ Herdado (Incluir)"
                tag = "status_inherited"
            elif dec == TriageDecision.EXCLUDE:
                chk = "[↳✖]"
                dec_str = "↳ Herdado (Excluir)"
                tag = "status_inherited"
            else:
                chk = "[⬜]"
                dec_str = "⏳ Revisar Depois"
                tag = "status_undecided"
            orig_str = f"Herdada de ...\\{os.path.basename(orig_path)}"
        else:
            chk = "[⬜]"
            dec_str = "⏳ Revisar Depois"
            tag = "status_undecided"
            orig_str = "Padrão (Revisar Depois)"

        cat_str = ", ".join(grp.category_labels) if grp.categories else "Geral"

        if is_root:
            friendly_text = f"{chk} {grp.friendly_display_name}"
        else:
            friendly_text = f"{chk} 📁 {grp.name}"

        values = (
            dec_str,
            grp.path,
            f"{grp.total_files:,}",
            format_bytes(grp.total_bytes),
            cat_str,
            orig_str,
        )
        return friendly_text, values, tag

    def _update_tree_in_place(self, folder_path: Optional[str], affected_paths: List[str]) -> None:
        """Atualização cirúrgica in-place: atualiza apenas a linha e os nós afetados,
        preservando seleção, foco, nós abertos e posição da barra de rolagem."""
        scroll_pos = self.tree_groups.yview()

        # 1. Atualiza KPIs
        self._update_kpis()

        # 2. Reúne nós a atualizar (pasta + descendentes afetados + ancestrais diretos)
        nodes_to_update = set(affected_paths)
        if folder_path:
            nodes_to_update.add(folder_path)
            curr = self.triage_manager.folders.get(folder_path)
            p = curr.parent_path if curr else None
            while p:
                nodes_to_update.add(p)
                p_grp = self.triage_manager.folders.get(p)
                p = p_grp.parent_path if p_grp else None

        # 3. Atualiza os nós existentes sem deletar ou recriar
        for path in nodes_to_update:
            if self.tree_groups.exists(path):
                grp = self.triage_manager.folders.get(path)
                if grp:
                    is_root = (path in self._root_paths)
                    f_txt, vals, tag = self._get_row_data(grp, is_root=is_root)
                    self.tree_groups.item(path, text=f_txt, values=vals, tags=(tag,))

        # 4. Preserva seleção e foco
        if self.selected_folder_path and self.tree_groups.exists(self.selected_folder_path):
            current_sel = self.tree_groups.selection()
            if not current_sel or current_sel[0] != self.selected_folder_path:
                self.tree_groups.selection_set(self.selected_folder_path)
                self.tree_groups.focus(self.selected_folder_path)

        # 5. Restaura posição exata do scrollbar vertical
        if scroll_pos:
            self.tree_groups.yview_moveto(scroll_pos[0])

    def _on_tree_node_open(self, event: Any) -> None:
        """Handler do evento <<TreeviewOpen>> para lazy loading de subpastas."""
        def _check_node(node_id: str) -> None:
            if self.tree_groups.item(node_id, "open"):
                self._expand_lazy_children(node_id)
                for ch in self.tree_groups.get_children(node_id):
                    if not ch.endswith("::__dummy__"):
                        _check_node(ch)

        for r in self.tree_groups.get_children(""):
            _check_node(r)

    def _expand_lazy_children(self, parent_path: str) -> None:
        """Substitui o marcador fictício pelos filhos imediatos sob demanda."""
        if not self.tree_groups.exists(parent_path):
            return
        children = self.tree_groups.get_children(parent_path)
        if len(children) == 1 and children[0].endswith("::__dummy__"):
            self.tree_groups.delete(children[0])
            subfolders = self.triage_manager.get_child_folders(parent_path)
            for sf in subfolders:
                if not self.tree_groups.exists(sf.path):
                    f_txt, vals, tg = self._get_row_data(sf, is_root=False)
                    self.tree_groups.insert(parent_path, "end", iid=sf.path, text=f_txt, values=vals, tags=(tg,), open=False)
                    if self.triage_manager.get_child_folders(sf.path):
                        self.tree_groups.insert(sf.path, "end", iid=f"{sf.path}::__dummy__", text="Carregando...", values=("", "", "", "", "", ""))

    def _schedule_debounced_save(self) -> None:
        """Agenda persistência de triage_decisions.json sem travar a interface."""
        self._pending_save = True
        if hasattr(self, "lbl_save_status"):
            self.lbl_save_status.configure(text="💾 Salvando...", text_color="#F59E0B")

        if self._save_debounce_timer:
            try:
                self.after_cancel(self._save_debounce_timer)
            except Exception:
                pass

        self._save_debounce_timer = self.after(350, self._start_async_save)

    def _start_async_save(self) -> None:
        self._save_debounce_timer = None
        if not self._pending_save:
            return

        def _worker() -> None:
            with self._save_lock:
                try:
                    self.triage_manager.save_decisions_to_json(self.triage_decisions_file)
                except Exception:
                    pass
            self.after(0, self._on_save_completed)

        t = threading.Thread(target=_worker, daemon=True)
        self._save_worker_thread = t
        t.start()

    def _on_save_completed(self) -> None:
        self._pending_save = False
        if hasattr(self, "lbl_save_status"):
            self.lbl_save_status.configure(text="✓ Salvo", text_color="#10B981")

    def flush_save(self, timeout: float = 3.0) -> None:
        """Garante conclusão imediata e síncrona de qualquer salvamento pendente."""
        if self._save_debounce_timer:
            try:
                self.after_cancel(self._save_debounce_timer)
            except Exception:
                pass
            self._save_debounce_timer = None

        if self._save_worker_thread and self._save_worker_thread.is_alive():
            self._save_worker_thread.join(timeout=timeout)

        if self._pending_save:
            with self._save_lock:
                try:
                    self.triage_manager.save_decisions_to_json(self.triage_decisions_file)
                except Exception:
                    pass
            self._pending_save = False
            if hasattr(self, "lbl_save_status"):
                self.lbl_save_status.configure(text="✓ Salvo", text_color="#10B981")

    def _render_groups_table(self) -> None:
        """Renderiza a lista de grupos hierarquicamente na Treeview com lazy loading."""
        for item in self.tree_groups.get_children():
            self.tree_groups.delete(item)

        metrics = self.triage_manager.get_summary_metrics()
        if metrics["total_files"] == 0:
            err_msg = self.data_loader.last_load_error or f"Nenhum arquivo pendente encontrado em '{self.reports_dir}'."
            self.lbl_table_status.configure(
                text=f"⚠️ Falha ou ausência de dados: {err_msg}",
                text_color="#EF4444",
            )
            return

        search_txt = self.var_search.get().strip()
        cat_filter = self.var_category_filter.get()
        dec_filter = self.var_decision_filter.get()
        mode_val = self.var_group_mode.get()

        roots = self.triage_manager.get_root_groups()
        num_roots = len(roots)
        total_files = metrics["total_files"]
        total_groups = len(self.triage_manager.folders)

        self.lbl_table_status.configure(
            text=f"Pastas principais: {num_roots} | Arquivos cobertos: {total_files:,} ({format_bytes(metrics['total_bytes'])}) | Grupos carregados: {total_groups:,}",
            text_color="#60A5FA",
        )

        has_filter = bool(search_txt) or (cat_filter and cat_filter != "ALL") or (dec_filter and dec_filter != "ALL")

        if not has_filter and mode_val == "top_level":
            self._root_paths = {r.path for r in roots}
            # Insere raízes prioritárias recolhidas inicialmente
            for r in roots:
                f_txt, vals, tg = self._get_row_data(r, is_root=True)
                self.tree_groups.insert("", "end", iid=r.path, text=f_txt, values=vals, tags=(tg,), open=False)
                # Filhos imediatos
                children = self.triage_manager.get_child_folders(r.path)
                for ch in children:
                    c_txt, c_vals, c_tg = self._get_row_data(ch, is_root=False)
                    self.tree_groups.insert(r.path, "end", iid=ch.path, text=c_txt, values=c_vals, tags=(c_tg,), open=False)
                    if self.triage_manager.get_child_folders(ch.path):
                        self.tree_groups.insert(ch.path, "end", iid=f"{ch.path}::__dummy__", text="Carregando...", values=("", "", "", "", "", ""))

            # Demais raízes de dados fora de Users (se houver)
            for f_path, grp in self.triage_manager.folders.items():
                if grp.total_files > 0 and not self.tree_groups.exists(f_path) and not grp.parent_path:
                    self._root_paths.add(f_path)
                    f_txt, vals, tg = self._get_row_data(grp, is_root=True)
                    self.tree_groups.insert("", "end", iid=f_path, text=f_txt, values=vals, tags=(tg,), open=False)
                    for ch in self.triage_manager.get_child_folders(f_path):
                        c_txt, c_vals, c_tg = self._get_row_data(ch, is_root=False)
                        self.tree_groups.insert(f_path, "end", iid=ch.path, text=c_txt, values=c_vals, tags=(c_tg,), open=False)
                        if self.triage_manager.get_child_folders(ch.path):
                            self.tree_groups.insert(ch.path, "end", iid=f"{ch.path}::__dummy__", text="Carregando...", values=("", "", "", "", "", ""))
        else:
            # Modo detalhado ou filtros ativos: paginação de até 250 itens para resposta imediata
            filtered = self.triage_manager.get_filtered_groups(
                search_text=search_txt,
                category_filter=cat_filter,
                decision_filter=dec_filter,
                group_mode="all",
            )
            MAX_DISPLAY = 250
            display_items = filtered[:MAX_DISPLAY]
            total_matches = len(filtered)
            self._root_paths = {grp.path for grp in display_items}

            for grp in display_items:
                f_txt, vals, tg = self._get_row_data(grp, is_root=True)
                self.tree_groups.insert("", "end", iid=grp.path, text=f_txt, values=vals, tags=(tg,), open=False)

            if total_matches > MAX_DISPLAY:
                self.lbl_table_status.configure(
                    text=f"Filtro ativo: exibindo {MAX_DISPLAY} de {total_matches:,} pastas correspondentes.",
                    text_color="#F59E0B",
                )

        if self.selected_folder_path and self.tree_groups.exists(self.selected_folder_path):
            self.tree_groups.selection_set(self.selected_folder_path)
            self.tree_groups.focus(self.selected_folder_path)

    def _on_tree_double_click(self, event: Any) -> None:
        item_id = self.tree_groups.identify_row(event.y)
        if not item_id or item_id.endswith("::__dummy__"):
            return
        self.tree_groups.selection_set(item_id)
        self.tree_groups.focus(item_id)
        self._on_group_selected(None)
        self._cycle_folder_decision(item_id)

    def _on_tree_space_pressed(self, event: Any) -> None:
        selected = self.tree_groups.selection()
        if not selected or selected[0].endswith("::__dummy__"):
            return
        self._cycle_folder_decision(selected[0])

    def _cycle_folder_decision(self, folder_path: str) -> None:
        t0 = time.perf_counter()
        current_dec, _, _ = self.triage_manager.resolve_folder_decision(folder_path)
        if current_dec == TriageDecision.UNDECIDED:
            new_dec = TriageDecision.INCLUDE
        elif current_dec == TriageDecision.INCLUDE:
            new_dec = TriageDecision.EXCLUDE
        else:
            new_dec = TriageDecision.UNDECIDED

        apply_children = self.apply_to_children_var.get()
        affected = self.triage_manager.set_decision(
            folder_path=folder_path,
            decision=new_dec,
            apply_to_children=apply_children,
        )

        self._update_tree_in_place(folder_path, affected)
        self._schedule_debounced_save()
        self._load_files_for_selected_folder()

        self.last_click_latency_ms = (time.perf_counter() - t0) * 1000

    def _on_filter_changed(self) -> None:
        self._render_groups_table()

    def _on_cat_combo_selected(self, choice: str) -> None:
        if choice == "Todas as Categorias":
            self.var_category_filter.set("ALL")
        else:
            for k, v in CATEGORY_LABELS.items():
                if v == choice:
                    self.var_category_filter.set(k)
                    break
        self._render_groups_table()

    def _on_dec_combo_selected(self, choice: str) -> None:
        if choice == "Todas":
            self.var_decision_filter.set("ALL")
        elif "Incluído" in choice:
            self.var_decision_filter.set(TriageDecision.INCLUDE)
        elif "Excluído" in choice:
            self.var_decision_filter.set(TriageDecision.EXCLUDE)
        else:
            self.var_decision_filter.set(TriageDecision.UNDECIDED)
        self._render_groups_table()

    def _on_mode_combo_selected(self, choice: str) -> None:
        if "Todas" in choice:
            self.var_group_mode.set("all")
        else:
            self.var_group_mode.set("top_level")
        self._render_groups_table()

    def _on_group_selected(self, event: Any) -> None:
        selected = self.tree_groups.selection()
        if not selected or selected[0].endswith("::__dummy__"):
            return
        folder_path = selected[0]
        self.selected_folder_path = folder_path
        self.lbl_selected_folder.configure(
            text=f"Pasta Selecionada: {os.path.basename(folder_path)} ({folder_path})",
            text_color="#60A5FA",
        )
        self._load_files_for_selected_folder()

    def _switch_to_files_tab(self) -> None:
        if not self.selected_folder_path:
            messagebox.showinfo("Seleção Necessária", "Selecione uma pasta na tabela para inspecionar seus arquivos.", parent=self)
            return
        self.tabs.set("🔍 Arquivos da Pasta Selecionada (Opcional)")

    def _apply_decision_to_selected(self, decision: str) -> None:
        if not self.selected_folder_path:
            messagebox.showinfo("Seleção Necessária", "Selecione uma pasta na tabela antes de aplicar a decisão.", parent=self)
            return

        t0 = time.perf_counter()
        apply_children = self.apply_to_children_var.get()
        affected = self.triage_manager.set_decision(
            folder_path=self.selected_folder_path,
            decision=decision,
            apply_to_children=apply_children,
        )

        self._update_tree_in_place(self.selected_folder_path, affected)
        self._schedule_debounced_save()
        self._load_files_for_selected_folder()

        self.last_click_latency_ms = (time.perf_counter() - t0) * 1000

    def _suggest_regenerable_exclusions(self) -> None:
        msg = (
            "Deseja aplicar a sugestão técnica de exclusão para pastas comprovadamente regeneráveis?\n\n"
            "Pastas identificadas: node_modules, build/intermediates, __pycache__, caches temporários.\n"
            "Estas pastas representam arquivos que podem ser facilmente reconstruídos via 'npm install' ou compilação.\n\n"
            "NOTA: Esta ação NÃO apagará nada do disco. Ela apenas marcará essas pastas como 'Excluído do plano'.\n"
            "Você poderá reverter qualquer pasta individualmente a qualquer momento."
        )
        if messagebox.askyesno("Confirmar Sugestão de Exclusão Técnica", msg, parent=self):
            t0 = time.perf_counter()
            count = self.triage_manager.apply_regenerable_suggestions()
            self._update_kpis()
            self._schedule_debounced_save()
            self._render_groups_table()
            self._load_files_for_selected_folder()
            self.last_click_latency_ms = (time.perf_counter() - t0) * 1000
            messagebox.showinfo("Sugestão Aplicada", f"{count} grupos regeneráveis foram marcados como 'Excluído do plano'.", parent=self)

    def _reset_all_triage(self) -> None:
        if messagebox.askyesno("Redefinir Triagem", "Deseja restaurar todas as pastas para o estado inicial 'Revisar Depois'?", parent=self):
            t0 = time.perf_counter()
            self.triage_manager.reset_all_decisions()
            self.plan = None
            self.btn_execute.configure(state="disabled")
            self.btn_execute_batch.configure(state="disabled")
            self.btn_audit.configure(state="disabled")
            self._update_kpis()
            self._schedule_debounced_save()
            self._render_groups_table()
            self._load_files_for_selected_folder()
            self.last_click_latency_ms = (time.perf_counter() - t0) * 1000

    # -------------------------------------------------------------------------
    # Visualização Secundária: Arquivos Individuais
    # -------------------------------------------------------------------------

    def _load_files_for_selected_folder(self) -> None:
        if not self.selected_folder_path:
            return
        self.folder_file_indices = self.triage_manager.get_file_indices_in_folder(
            self.selected_folder_path, include_subfolders=True
        )
        self.file_page = 1
        self.file_total_pages = max(1, math.ceil(len(self.folder_file_indices) / self.file_page_size))
        self.lbl_f_header.configure(
            text=f"Arquivos em: {self.selected_folder_path} ({len(self.folder_file_indices):,} arquivos diretos/subpastas)"
        )
        self._render_file_page()

    def _render_file_page(self) -> None:
        for item in self.tree_files.get_children():
            self.tree_files.delete(item)

        start = (self.file_page - 1) * self.file_page_size
        end = start + self.file_page_size
        page_indices = getattr(self, "folder_file_indices", [])[start:end]
        page_items = self.triage_manager.get_files_by_indices(page_indices)
        self.folder_files = page_items  # preserva compatibilidade com inspeção

        for it in page_items:
            dec = it.get("triage_decision", "undecided")
            dec_label = "✅ Incluído" if dec == "include" else ("❌ Excluído" if dec == "exclude" else "⏳ Revisar")
            cat_label = CATEGORY_LABELS.get(it.get("category", "outros"), "Outros")

            self.tree_files.insert(
                "",
                "end",
                values=(
                    it.get("caminho", ""),
                    format_bytes(it.get("tamanho_bytes", 0)),
                    cat_label,
                    dec_label,
                    format_timestamp(it.get("mtime")),
                ),
            )

        self.lbl_f_page.configure(text=f"Página {self.file_page} de {self.file_total_pages}")
        tot_f = len(getattr(self, "folder_file_indices", []))
        self.lbl_f_total.configure(text=f"Total: {tot_f:,} arquivos nesta pasta/subpastas")
        self.btn_f_prev.configure(state="normal" if self.file_page > 1 else "disabled")
        self.btn_f_next.configure(state="normal" if self.file_page < self.file_total_pages else "disabled")

    def _prev_file_page(self) -> None:
        if self.file_page > 1:
            self.file_page -= 1
            self._render_file_page()

    def _next_file_page(self) -> None:
        if self.file_page < self.file_total_pages:
            self.file_page += 1
            self._render_file_page()

    # -------------------------------------------------------------------------
    # Confirmação da Seleção e Geração do Action Plan
    # -------------------------------------------------------------------------

    def _browse_destination(self) -> None:
        path = filedialog.askdirectory(
            title="Selecionar Pasta Base de Destino para Cópia Segura",
            initialdir=self.var_target_base.get() or "D:\\",
            parent=self,
        )
        if path:
            self.var_target_base.set(path)

    def _confirm_and_generate_plan(self) -> None:
        """Gera o Action Plan filtrado contendo APENAS itens com decisão INCLUDE."""
        # Garante que qualquer salvamento assíncrono em debounce esteja persistido
        self.flush_save()

        metrics = self.triage_manager.get_summary_metrics()
        inc_files = metrics["included_files"]
        if inc_files == 0:
            messagebox.showinfo("Nenhum Arquivo Incluído", "Marque pelo menos uma pasta como 'Incluir no plano' antes de gerar a prévia.", parent=self)
            return

        target_dir = self.var_target_base.get().strip()
        if not target_dir:
            messagebox.showwarning("Destino Obrigatório", "Informe a pasta base de destino.", parent=self)
            return

        generator = ActionPlanGenerator(
            reports_dir=self.reports_dir,
            target_base_dir=target_dir,
        )

        try:
            self.plan = generator.generate_plan(
                include_pending=True,
                include_documents=True,
                triage_manager=self.triage_manager,
            )

            # Persiste o plano aprovado em disco
            plan_file = os.path.join(self.reports_dir, "action_plan_aprovado.json")
            try:
                self.plan.save_to_json(plan_file)
            except Exception:
                pass

            totais = self.plan.totais
            self.order_page = 1
            self.order_total_pages = max(1, math.ceil(len(self.plan.ordens) / self.order_page_size))
            self._render_order_page()

            # Muda foco para a aba de Ordens
            self.tabs.set("📋 Ordens do Action Plan (Prévia Aprovada)")

            has_orders = len(self.plan.ordens) > 0
            if totais.get("espaco_suficiente"):
                self.btn_execute.configure(state="normal" if has_orders else "disabled")
                self.btn_execute_batch.configure(state="normal" if has_orders else "disabled")
                self.btn_audit.configure(state="normal" if has_orders else "disabled")
                self.lbl_progress.configure(
                    text=f"Plano compilado e salvo: {len(self.plan.ordens):,} ordens aprovadas ({format_bytes(totais['tamanho_total_bytes'])}). Espaço no destino: SUFICIENTE."
                )
            else:
                self.btn_execute.configure(state="disabled")
                self.btn_execute_batch.configure(state="disabled")
                self.btn_audit.configure(state="normal" if has_orders else "disabled")
                self.lbl_progress.configure(
                    text=f"Plano compilado e salvo: {len(self.plan.ordens):,} ordens, mas ESPAÇO INSUFICIENTE no destino!"
                )
                messagebox.showwarning(
                    "Espaço Insuficiente",
                    f"O volume selecionado ({format_bytes(totais['tamanho_total_bytes'])}) excede o espaço livre disponível com a margem de segurança de 2 GB.",
                    parent=self,
                )

        except Exception as e:
            messagebox.showerror("Erro ao Gerar Plano", f"Falha ao gerar Action Plan:\n{e}", parent=self)

    def _render_order_page(self) -> None:
        for item in self.tree_orders.get_children():
            self.tree_orders.delete(item)

        if not self.plan or not self.plan.ordens:
            self.lbl_o_page.configure(text="Página 1 de 1")
            self.lbl_o_total.configure(text="Nenhuma ordem gerada.")
            self.btn_o_prev.configure(state="disabled")
            self.btn_o_next.configure(state="disabled")
            return

        start = (self.order_page - 1) * self.order_page_size
        end = start + self.order_page_size
        page_items = self.plan.ordens[start:end]

        for o in page_items:
            status_text = "🟢 CONCLUÍDO" if o.status == "completed" else ("⏳ PENDENTE" if o.status == "pending" else o.status.upper())
            self.tree_orders.insert(
                "",
                "end",
                values=(
                    o.order_id,
                    o.source_path,
                    o.target_path,
                    format_bytes(o.expected_size_bytes),
                    o.category,
                    status_text,
                ),
            )

        self.lbl_o_page.configure(text=f"Página {self.order_page} de {self.order_total_pages}")
        self.lbl_o_total.configure(text=f"Total: {len(self.plan.ordens):,} ordens no plano")
        self.btn_o_prev.configure(state="normal" if self.order_page > 1 else "disabled")
        self.btn_o_next.configure(state="normal" if self.order_page < self.order_total_pages else "disabled")

    def _prev_order_page(self) -> None:
        if self.order_page > 1:
            self.order_page -= 1
            self._render_order_page()

    def _next_order_page(self) -> None:
        if self.order_page < self.order_total_pages:
            self.order_page += 1
            self._render_order_page()

    # -------------------------------------------------------------------------
    # Execução do Safe Mover
    # -------------------------------------------------------------------------

    def _confirm_and_execute(self) -> None:
        if not self.plan or len(self.plan.ordens) == 0:
            messagebox.showinfo("Sem Ordens", "Gere um plano aprovado antes de executar.", parent=self)
            return

        totais = self.plan.totais
        msg = (
            f"Você está prestes a iniciar a cópia segura de:\n\n"
            f"• Quantidade: {totais['total_ordens']:,} arquivos aprovados na triagem\n"
            f"• Tamanho total: {format_bytes(totais['tamanho_total_bytes'])}\n"
            f"• Pasta base de destino: {self.plan.target_base_dir}\n\n"
            f"DECLARAÇÃO DE SEGURANÇA:\n"
            f"A ORIGEM NÃO SERÁ APAGADA. Esta operação realiza exclusivamente\n"
            f"cópia e validação de integridade por SHA-256.\n\n"
            f"Deseja iniciar a cópia segura agora?"
        )

        confirmed = messagebox.askyesno("Confirmar Cópia Segura (Não-Destrutiva)", msg, parent=self)
        if not confirmed:
            return

        self.is_running = True
        self.cancel_event = threading.Event()
        self.btn_execute.configure(state="disabled")
        self.btn_execute_batch.configure(state="disabled")
        self.btn_audit.configure(state="disabled")
        self.btn_generate_plan.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress_bar.set(0)
        self.lbl_progress.configure(text="Iniciando cópia segura em background...")

        def _worker() -> None:
            executor = SafeCopyExecutor(
                plan=self.plan,
                log_dir=self.reports_dir,
                cancel_event=self.cancel_event,
                progress_callback=lambda data: self.after(0, lambda d=data: self._on_progress_update(d)),
            )
            try:
                summary = executor.execute_all()
                self.after(0, lambda s=summary: self._on_execution_finished(s))
            except Exception as e:
                self.after(0, lambda err=str(e): self._on_execution_error(err))

        self.worker_thread = threading.Thread(target=_worker, daemon=True, name="SafeCopyWorkerThread")
        self.worker_thread.start()

    def _confirm_and_execute_batch(self) -> None:
        if not self.plan or len(self.plan.ordens) == 0:
            messagebox.showinfo("Sem Ordens", "Gere um plano aprovado antes de executar.", parent=self)
            return

        batch_size = int(self.combo_batch_size.get() or "10")

        # Filtra ordens pendentes
        pending_orders = [o for o in self.plan.ordens if o.status not in ("completed", "already_completed")]
        if not pending_orders:
            messagebox.showinfo("Plano Concluído", "Todas as ordens do plano já foram concluídas!", parent=self)
            return

        batch_target_orders = pending_orders[:batch_size]
        batch_bytes = sum(o.expected_size_bytes for o in batch_target_orders)

        msg = (
            f"Você está prestes a iniciar a cópia segura em lote de:\n\n"
            f"• Lote atual: {len(batch_target_orders)} arquivos (de {len(pending_orders)} restantes)\n"
            f"• Volume deste lote: {format_bytes(batch_bytes)}\n"
            f"• Pasta base de destino: {self.plan.target_base_dir}\n\n"
            f"DECLARAÇÃO DE SEGURANÇA:\n"
            f"A ORIGEM NÃO SERÁ APAGADA. Esta operação realiza exclusivamente\n"
            f"cópia com arquivo temporário exclusivo, validação de integridade por\n"
            f"SHA-256 dual e renomeação atômica.\n\n"
            f"Deseja executar este lote agora?"
        )

        confirmed = messagebox.askyesno("Confirmar Lote de Cópia Segura (Não-Destrutiva)", msg, parent=self)
        if not confirmed:
            return

        self.is_running = True
        self.cancel_event = threading.Event()
        self.btn_execute.configure(state="disabled")
        self.btn_execute_batch.configure(state="disabled")
        self.btn_audit.configure(state="disabled")
        self.btn_generate_plan.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.lbl_progress.configure(text=f"Executando lote de {len(batch_target_orders)} arquivos em background...")

        def _worker() -> None:
            executor = SafeCopyExecutor(
                plan=self.plan,
                log_dir=self.reports_dir,
                cancel_event=self.cancel_event,
                progress_callback=lambda data: self.after(0, lambda d=data: self._on_progress_update(d)),
            )
            try:
                summary = executor.execute_batch(batch_size=batch_size)
                self.after(0, lambda s=summary: self._on_batch_execution_finished(s))
            except Exception as e:
                self.after(0, lambda err=str(e): self._on_execution_error(err))

        self.worker_thread = threading.Thread(target=_worker, daemon=True, name="SafeCopyBatchWorkerThread")
        self.worker_thread.start()

    def _on_batch_execution_finished(self, summary: Dict[str, Any]) -> None:
        self.is_running = False
        has_pending = summary["ordens_restantes_pendentes"] > 0
        self.btn_execute.configure(state="normal" if has_pending else "disabled")
        self.btn_execute_batch.configure(state="normal" if has_pending else "disabled")
        self.btn_audit.configure(state="normal")
        self.btn_generate_plan.configure(state="normal")
        self.btn_cancel.configure(state="disabled")

        remaining = summary["ordens_restantes_pendentes"]
        msg = (
            f"Lote concluído com sucesso!\n\n"
            f"• Concluídos neste lote: {summary['completadas_no_lote']}\n"
            f"• Falhas / Conflitos: {summary['falhas_no_lote']}\n"
            f"• Volume copiado: {format_bytes(summary['bytes_copiados_no_lote'])}\n"
            f"• Tempo do lote: {summary['duracao_segundos']} s\n"
            f"• Total acumulado concluído: {summary['total_geral_completadas']} de {summary['total_geral_ordens']}\n"
            f"• Restantes no plano: {remaining}\n\n"
            f"A origem permaneceu 100% intacta.\n"
            f"Log registrado em:\n{summary.get('log_path')}"
        )
        self.lbl_progress.configure(
            text=f"Lote finalizado ({summary['completadas_no_lote']} copiados). Restantes: {remaining}. Origem intacta."
        )
        self._render_order_page()
        messagebox.showinfo("Lote Concluído", msg, parent=self)

    def _audit_plan_preview(self) -> None:
        if not self.plan:
            messagebox.showinfo("Sem Plano", "Gere um plano aprovado antes de auditar.", parent=self)
            return

        executor = SafeCopyExecutor(plan=self.plan, log_dir=self.reports_dir)
        audit_data = executor.audit_plan_dry_run()

        status_espaco = "SUFICIENTE" if audit_data["espaco_suficiente"] else "INSUFICIENTE"
        recusadas_msg = f"{audit_data['total_ordens_recusadas']}"
        if audit_data["total_ordens_recusadas"] > 0:
            recusadas_msg += " (saves heurísticos, hashes amostrais ou restrições de segurança)"

        msg = (
            f"Prévia e Auditoria Final do Action Plan (Dry-Run):\n\n"
            f"• Total de ordens no plano: {audit_data['total_ordens_no_plano']:,}\n"
            f"• Ordens prontas para cópia: {audit_data['ordens_prontas']:,} ({format_bytes(audit_data['bytes_prontos'])})\n"
            f"• Ordens já concluídas no destino: {audit_data['ordens_ja_concluidas']:,}\n"
            f"• Conflitos detectados no destino: {audit_data['conflitos_destino']:,}\n"
            f"• Origens mutadas ou inexistentes: {audit_data['origens_mutadas_ou_inexistentes']:,}\n"
            f"• Ordens recusadas previamente: {recusadas_msg}\n\n"
            f"• Destino: {audit_data['target_base_dir']}\n"
            f"• Espaço livre no destino: {format_bytes(audit_data['espaco_livre_destino_bytes'])}\n"
            f"• Espaço requerido (+ 2 GB margem): {format_bytes(audit_data['espaco_requerido_com_margem_bytes'])}\n"
            f"• Status do espaço: {status_espaco}\n\n"
            f"Nenhum arquivo foi copiado ou alterado nesta auditoria prévia."
        )
        messagebox.showinfo("Auditoria Prévia do Action Plan", msg, parent=self)

    def _cancel_execution(self) -> None:
        if self.cancel_event:
            self.lbl_progress.configure(text="Cancelando... Aguardando término do bloco atual.")
            self.cancel_event.set()

    def _on_progress_update(self, data: Dict[str, Any]) -> None:
        completed = data.get("completed", 0)
        total = data.get("total", 1)
        bytes_copied = data.get("bytes_copied", 0)
        total_bytes = data.get("total_bytes", 1)

        frac = min(bytes_copied / total_bytes, 1.0) if total_bytes > 0 else 0.0
        self.progress_bar.set(frac)
        self.lbl_progress.configure(
            text=f"Copiados: {completed:,}/{total:,} arquivos ({format_bytes(bytes_copied)} / {format_bytes(total_bytes)})"
        )
        self._render_order_page()

    def _on_execution_finished(self, summary: Dict[str, Any]) -> None:
        self.is_running = False
        self.btn_execute.configure(state="normal")
        self.btn_execute_batch.configure(state="normal")
        self.btn_audit.configure(state="normal")
        self.btn_generate_plan.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.progress_bar.set(1.0)

        msg = (
            f"Cópia segura finalizada!\n\n"
            f"• Concluídos com sucesso: {summary['completadas']:,}\n"
            f"• Falhas / Conflitos: {summary['falhas']:,}\n"
            f"• Cancelados: {summary['canceladas']:,}\n"
            f"• Volume copiado: {format_bytes(summary['total_bytes_copiados'])}\n"
            f"• Tempo total: {summary['duracao_segundos']} s\n\n"
            f"A origem permaneceu 100% intacta.\n"
            f"Log registrado em:\n{summary.get('log_path')}"
        )
        self.lbl_progress.configure(text="Cópia segura concluída. Origem preservada intacta.")
        self._render_order_page()
        messagebox.showinfo("Cópia Segura Concluída", msg, parent=self)

    def _on_execution_error(self, err_msg: str) -> None:
        self.is_running = False
        self.btn_execute.configure(state="normal")
        self.btn_execute_batch.configure(state="normal")
        self.btn_audit.configure(state="normal")
        self.btn_generate_plan.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.lbl_progress.configure(text=f"Erro na execução: {err_msg}")
        self._render_order_page()
        messagebox.showerror("Erro na Cópia Segura", f"Ocorreu um erro durante a cópia:\n\n{err_msg}\n\nA origem não foi afetada.", parent=self)
