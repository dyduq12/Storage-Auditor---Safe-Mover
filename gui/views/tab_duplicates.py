"""Aba de Duplicatas Confirmadas com paginação e sinalização de confiança (Fase 2)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from exporters.excel_exporter import format_bytes, format_timestamp
from gui.data_loader import AuditDataLoader


class DuplicatesView(ctk.CTkFrame):
    """Componente visual para listagem paginada de duplicatas confirmadas."""

    def __init__(self, master: Any, data_loader: AuditDataLoader, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.data_loader = data_loader
        self.current_page: int = 1
        self.page_size: int = 100
        self.total_pages: int = 1
        self.total_filtered: int = 0

        self._create_widgets()
        self.refresh_view()

    def _create_widgets(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 1. Barra Superior de Filtros
        self.filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.filter_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        # Campo de busca por texto
        self.lbl_search = ctk.CTkLabel(self.filter_frame, text="Buscar:")
        self.lbl_search.pack(side="left", padx=(0, 5))

        self.entry_search = ctk.CTkEntry(
            self.filter_frame,
            placeholder_text="Nome do arquivo ou caminho (C: ou D:)...",
            width=320,
        )
        self.entry_search.pack(side="left", padx=(0, 15))
        self.entry_search.bind("<Return>", lambda e: self._on_filter_changed())

        # Filtro de confiança
        self.lbl_conf = ctk.CTkLabel(self.filter_frame, text="Nível de Confiança:")
        self.lbl_conf.pack(side="left", padx=(0, 5))

        self.combo_conf = ctk.CTkComboBox(
            self.filter_frame,
            values=["Todos", "Completo", "Amostral"],
            command=lambda v: self._on_filter_changed(),
            width=140,
        )
        self.combo_conf.set("Todos")
        self.combo_conf.pack(side="left", padx=(0, 15))

        self.btn_apply = ctk.CTkButton(
            self.filter_frame,
            text="Filtrar",
            width=80,
            command=self._on_filter_changed,
        )
        self.btn_apply.pack(side="left", padx=5)

        self.btn_clear = ctk.CTkButton(
            self.filter_frame,
            text="Limpar",
            width=80,
            fg_color="gray35",
            hover_color="gray45",
            command=self._clear_filters,
        )
        self.btn_clear.pack(side="left", padx=5)

        # 2. Tabela Treeview com Scrollbar
        self.table_container = ctk.CTkFrame(self, corner_radius=6)
        self.table_container.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)
        self.table_container.grid_rowconfigure(0, weight=1)
        self.table_container.grid_columnconfigure(0, weight=1)

        columns = ("origem", "destino", "tamanho", "recuperavel", "confianca", "mtime")
        self.tree = ttk.Treeview(
            self.table_container,
            columns=columns,
            show="headings",
            selectmode="browse",
        )

        self.tree.heading("origem", text="Caminho Origem (C:)", anchor="w")
        self.tree.heading("destino", text="Caminho Destino (D:)", anchor="w")
        self.tree.heading("tamanho", text="Tamanho", anchor="e")
        self.tree.heading("recuperavel", text="Espaço Recuperável", anchor="e")
        self.tree.heading("confianca", text="Confiança", anchor="center")
        self.tree.heading("mtime", text="Modificado em", anchor="center")

        self.tree.column("origem", width=340, minwidth=180, anchor="w")
        self.tree.column("destino", width=340, minwidth=180, anchor="w")
        self.tree.column("tamanho", width=100, minwidth=80, anchor="e")
        self.tree.column("recuperavel", width=110, minwidth=90, anchor="e")
        self.tree.column("confianca", width=110, minwidth=90, anchor="center")
        self.tree.column("mtime", width=140, minwidth=110, anchor="center")

        # Configura tags com cores para confiança
        self.tree.tag_configure("amostral", foreground="#D97706")  # Âmbar/Laranja
        self.tree.tag_configure("completo", foreground="#059669")  # Verde

        self.scrollbar_y = ttk.Scrollbar(self.table_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar_y.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        self.scrollbar_y.grid(row=0, column=1, sticky="ns")

        # 3. Barra Inferior de Paginação
        self.pag_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.pag_frame.grid(row=2, column=0, sticky="ew", padx=15, pady=(5, 10))

        self.btn_prev = ctk.CTkButton(
            self.pag_frame,
            text="◀ Anterior",
            width=90,
            command=self._prev_page,
        )
        self.btn_prev.pack(side="left", padx=(0, 10))

        self.lbl_page_info = ctk.CTkLabel(
            self.pag_frame,
            text="Página 1 de 1 (0 itens)",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.lbl_page_info.pack(side="left", padx=10)

        self.btn_next = ctk.CTkButton(
            self.pag_frame,
            text="Próxima ▶",
            width=90,
            command=self._next_page,
        )
        self.btn_next.pack(side="left", padx=(10, 0))

        self.lbl_hint = ctk.CTkLabel(
            self.pag_frame,
            text="⚠️ Confiança Amostral: arquivos > 100 MB verificados por amostragem de 3 MB. Requer conferência.",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color="#D97706",
        )
        self.lbl_hint.pack(side="right", padx=5)

    def _on_filter_changed(self) -> None:
        self.current_page = 1
        self.refresh_view()

    def _clear_filters(self) -> None:
        self.entry_search.delete(0, "end")
        self.combo_conf.set("Todos")
        self.current_page = 1
        self.refresh_view()

    def _prev_page(self) -> None:
        if self.current_page > 1:
            self.current_page -= 1
            self.refresh_view()

    def _next_page(self) -> None:
        if self.current_page < self.total_pages:
            self.current_page += 1
            self.refresh_view()

    def refresh_view(self) -> None:
        """Carrega a página atual de duplicatas e atualiza o Treeview."""
        # Limpa itens existentes
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.data_loader.is_loaded:
            self.lbl_page_info.configure(text="Nenhum relatório carregado")
            self.btn_prev.configure(state="disabled")
            self.btn_next.configure(state="disabled")
            return

        search_query = self.entry_search.get().strip()
        conf_filter = self.combo_conf.get()

        items, total_filtered, total_pages = self.data_loader.get_duplicates_page(
            page=self.current_page,
            page_size=self.page_size,
            search=search_query,
            confidence_filter=conf_filter,
        )

        self.total_filtered = total_filtered
        self.total_pages = total_pages

        # Insere linhas na página visível
        for d in items:
            t_bytes = d.get("tamanho_bytes", 0)
            rec_bytes = d.get("espaco_recuperavel", 0)
            conf = d.get("nivel_confianca", "completo").lower()
            tag = "amostral" if conf == "amostral" else "completo"
            conf_display = "⚠️ AMOSTRAL" if conf == "amostral" else "✓ COMPLETO"

            self.tree.insert(
                "",
                "end",
                values=(
                    d.get("caminho_origem", ""),
                    d.get("caminho_destino", ""),
                    format_bytes(t_bytes),
                    format_bytes(rec_bytes),
                    conf_display,
                    format_timestamp(d.get("mtime_origem")),
                ),
                tags=(tag,),
            )

        # Atualiza controles de navegação
        self.lbl_page_info.configure(
            text=f"Página {self.current_page} de {self.total_pages} ({self.total_filtered:,} duplicatas)".replace(",", ".")
        )
        self.btn_prev.configure(state="normal" if self.current_page > 1 else "disabled")
        self.btn_next.configure(state="normal" if self.current_page < self.total_pages else "disabled")
