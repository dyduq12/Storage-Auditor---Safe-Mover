"""Aba de Arquivos Pendentes de Backup com paginação (Fase 2)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from exporters.excel_exporter import format_bytes, format_timestamp
from gui.data_loader import AuditDataLoader


class PendingView(ctk.CTkFrame):
    """Componente visual para listagem paginada de pendentes de backup para D:."""

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

        # 1. Barra de Filtros
        self.filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.filter_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        self.lbl_search = ctk.CTkLabel(self.filter_frame, text="Buscar:")
        self.lbl_search.pack(side="left", padx=(0, 5))

        self.entry_search = ctk.CTkEntry(
            self.filter_frame,
            placeholder_text="Nome ou extensão do arquivo pendente em C:...",
            width=320,
        )
        self.entry_search.pack(side="left", padx=(0, 15))
        self.entry_search.bind("<Return>", lambda e: self._on_filter_changed())

        self.lbl_ext = ctk.CTkLabel(self.filter_frame, text="Extensão:")
        self.lbl_ext.pack(side="left", padx=(0, 5))

        self.combo_ext = ctk.CTkComboBox(
            self.filter_frame,
            values=["Todas", ".iso", ".png", ".jpg", ".psd", ".dng", ".mp4", ".zip", ".rar", ".pdf", ".docx"],
            command=lambda v: self._on_filter_changed(),
            width=120,
        )
        self.combo_ext.set("Todas")
        self.combo_ext.pack(side="left", padx=(0, 15))

        self.chk_iso_var = ctk.BooleanVar(value=False)
        self.chk_iso = ctk.CTkCheckBox(
            self.filter_frame,
            text="Apenas Imagens ISO",
            variable=self.chk_iso_var,
            command=self._on_filter_changed,
        )
        self.chk_iso.pack(side="left", padx=(0, 15))

        self.btn_apply = ctk.CTkButton(self.filter_frame, text="Filtrar", width=80, command=self._on_filter_changed)
        self.btn_apply.pack(side="left", padx=5)

        self.btn_clear = ctk.CTkButton(self.filter_frame, text="Limpar", width=80, fg_color="gray35", hover_color="gray45", command=self._clear_filters)
        self.btn_clear.pack(side="left", padx=5)

        # 2. Tabela Treeview com Scrollbar
        self.table_container = ctk.CTkFrame(self, corner_radius=6)
        self.table_container.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)
        self.table_container.grid_rowconfigure(0, weight=1)
        self.table_container.grid_columnconfigure(0, weight=1)

        columns = ("caminho", "tamanho", "extensao", "is_iso", "mtime")
        self.tree = ttk.Treeview(
            self.table_container,
            columns=columns,
            show="headings",
            selectmode="browse",
        )

        self.tree.heading("caminho", text="Caminho do Arquivo em C: (Pendente de Backup)", anchor="w")
        self.tree.heading("tamanho", text="Tamanho", anchor="e")
        self.tree.heading("extensao", text="Extensão", anchor="center")
        self.tree.heading("is_iso", text="Imagem ISO", anchor="center")
        self.tree.heading("mtime", text="Modificado em", anchor="center")

        self.tree.column("caminho", width=550, minwidth=250, anchor="w")
        self.tree.column("tamanho", width=120, minwidth=90, anchor="e")
        self.tree.column("extensao", width=90, minwidth=70, anchor="center")
        self.tree.column("is_iso", width=90, minwidth=70, anchor="center")
        self.tree.column("mtime", width=150, minwidth=120, anchor="center")

        self.tree.tag_configure("iso", foreground="#2563EB")  # Azul para ISO

        self.scrollbar_y = ttk.Scrollbar(self.table_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar_y.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        self.scrollbar_y.grid(row=0, column=1, sticky="ns")

        # 3. Barra Inferior de Paginação
        self.pag_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.pag_frame.grid(row=2, column=0, sticky="ew", padx=15, pady=(5, 10))

        self.btn_prev = ctk.CTkButton(self.pag_frame, text="◀ Anterior", width=90, command=self._prev_page)
        self.btn_prev.pack(side="left", padx=(0, 10))

        self.lbl_page_info = ctk.CTkLabel(
            self.pag_frame,
            text="Página 1 de 1 (0 itens)",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.lbl_page_info.pack(side="left", padx=10)

        self.btn_next = ctk.CTkButton(self.pag_frame, text="Próxima ▶", width=90, command=self._next_page)
        self.btn_next.pack(side="left", padx=(10, 0))

        self.lbl_hint = ctk.CTkLabel(
            self.pag_frame,
            text="Arquivos existentes em C: sem cópia idêntica detectada no disco D:.",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color="gray70",
        )
        self.lbl_hint.pack(side="right", padx=5)

    def _on_filter_changed(self) -> None:
        self.current_page = 1
        self.refresh_view()

    def _clear_filters(self) -> None:
        self.entry_search.delete(0, "end")
        self.combo_ext.set("Todas")
        self.chk_iso_var.set(False)
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
        """Carrega a página atual de pendentes e atualiza a visualização."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.data_loader.is_loaded:
            self.lbl_page_info.configure(text="Nenhum relatório carregado")
            self.btn_prev.configure(state="disabled")
            self.btn_next.configure(state="disabled")
            return

        search_query = self.entry_search.get().strip()
        ext_filter = self.combo_ext.get()
        iso_only = self.chk_iso_var.get()

        items, total_filtered, total_pages = self.data_loader.get_pending_page(
            page=self.current_page,
            page_size=self.page_size,
            search=search_query,
            extension_filter=ext_filter,
            iso_only=iso_only,
        )

        self.total_filtered = total_filtered
        self.total_pages = total_pages

        for p in items:
            t_bytes = p.get("tamanho_bytes", 0)
            is_iso = bool(p.get("is_iso"))
            tag = "iso" if is_iso else ""

            self.tree.insert(
                "",
                "end",
                values=(
                    p.get("caminho", ""),
                    format_bytes(t_bytes),
                    p.get("extensao", ""),
                    "SIM" if is_iso else "NÃO",
                    format_timestamp(p.get("mtime")),
                ),
                tags=(tag,) if tag else (),
            )

        self.lbl_page_info.configure(
            text=f"Página {self.current_page} de {self.total_pages} ({self.total_filtered:,} arquivos pendentes)".replace(",", ".")
        )
        self.btn_prev.configure(state="normal" if self.current_page > 1 else "disabled")
        self.btn_next.configure(state="normal" if self.current_page < self.total_pages else "disabled")
