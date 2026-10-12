"""Aba de Inventário de Jogos e Mapeamento Heurístico de Saves (Fase 2)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from exporters.excel_exporter import format_bytes, format_timestamp
from gui.data_loader import AuditDataLoader


class GamesView(ctk.CTkFrame):
    """Componente visual para exibição de jogos instalados e análise de saves."""

    def __init__(self, master: Any, data_loader: AuditDataLoader, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.data_loader = data_loader

        self._create_widgets()
        self.refresh_view()

    def _create_widgets(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 1. Banner de Aviso e Transparência Heurística
        self.banner_frame = ctk.CTkFrame(self, fg_color=("#FEF3C7", "#451A03"), corner_radius=8, border_width=1, border_color="#D97706")
        self.banner_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 8))

        self.lbl_banner_icon = ctk.CTkLabel(self.banner_frame, text="⚠️", font=ctk.CTkFont(size=22))
        self.lbl_banner_icon.pack(side="left", padx=(12, 8), pady=8)

        self.lbl_banner_text = ctk.CTkLabel(
            self.banner_frame,
            text=(
                "AVISO DE SEGURANÇA — VÍNCULOS HEURÍSTICOS DE SAVES:\n"
                "Os saves listados abaixo são aproximações heurísticas baseadas em nomes de pastas e executáveis.\n"
                "Vínculos compartilhados entre jogos (ex: emulador Steam) ou casamentos por termos genéricos são destacados como SUSPEITOS.\n"
                "NÃO AFIRME que são saves reais sem verificação manual. Nenhuma automação deve mover ou apagar essas pastas nesta fase."
            ),
            font=ctk.CTkFont(size=11),
            justify="left",
            text_color=("#92400E", "#FDE68A"),
        )
        self.lbl_banner_text.pack(side="left", padx=5, pady=8)

        # 2. Barra de Filtros
        self.filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.filter_frame.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 8))

        self.lbl_search = ctk.CTkLabel(self.filter_frame, text="Buscar:")
        self.lbl_search.pack(side="left", padx=(0, 5))

        self.entry_search = ctk.CTkEntry(
            self.filter_frame,
            placeholder_text="Buscar por nome de jogo ou pasta de save...",
            width=320,
        )
        self.entry_search.pack(side="left", padx=(0, 15))
        self.entry_search.bind("<Return>", lambda e: self.refresh_view())

        self.chk_suspect_var = ctk.BooleanVar(value=False)
        self.chk_suspect = ctk.CTkCheckBox(
            self.filter_frame,
            text="Exibir apenas Vínculos Suspeitos / Compartilhados",
            variable=self.chk_suspect_var,
            command=self.refresh_view,
        )
        self.chk_suspect.pack(side="left", padx=(0, 15))

        self.btn_filter = ctk.CTkButton(self.filter_frame, text="Filtrar", width=80, command=self.refresh_view)
        self.btn_filter.pack(side="left", padx=5)

        self.btn_clear = ctk.CTkButton(
            self.filter_frame,
            text="Limpar",
            width=80,
            fg_color="gray35",
            hover_color="gray45",
            command=self._clear_filters,
        )
        self.btn_clear.pack(side="left", padx=5)

        # 3. Tabela Split ou Treeview com Detalhes dos Saves
        self.table_container = ctk.CTkFrame(self, corner_radius=6)
        self.table_container.grid(row=2, column=0, sticky="nsew", padx=15, pady=5)
        self.table_container.grid_rowconfigure(0, weight=1)
        self.table_container.grid_columnconfigure(0, weight=1)

        columns = ("jogo", "launcher", "save_path", "tamanho", "arquivos", "certeza", "status")
        self.tree = ttk.Treeview(
            self.table_container,
            columns=columns,
            show="headings",
            selectmode="browse",
        )

        self.tree.heading("jogo", text="Jogo", anchor="w")
        self.tree.heading("launcher", text="Launcher", anchor="center")
        self.tree.heading("save_path", text="Caminho do Save Candidato em C:", anchor="w")
        self.tree.heading("tamanho", text="Tamanho Save", anchor="e")
        self.tree.heading("arquivos", text="Arquivos", anchor="center")
        self.tree.heading("certeza", text="Nível Certeza", anchor="center")
        self.tree.heading("status", text="Avaliação do Vínculo", anchor="w")

        self.tree.column("jogo", width=220, minwidth=140, anchor="w")
        self.tree.column("launcher", width=110, minwidth=80, anchor="center")
        self.tree.column("save_path", width=380, minwidth=220, anchor="w")
        self.tree.column("tamanho", width=110, minwidth=80, anchor="e")
        self.tree.column("arquivos", width=70, minwidth=60, anchor="center")
        self.tree.column("certeza", width=90, minwidth=70, anchor="center")
        self.tree.column("status", width=320, minwidth=200, anchor="w")

        self.tree.tag_configure("suspeito", foreground="#D97706")  # Laranja/Alerta
        self.tree.tag_configure("legitimo", foreground="#059669")  # Verde

        self.scrollbar_y = ttk.Scrollbar(self.table_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar_y.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        self.scrollbar_y.grid(row=0, column=1, sticky="ns")

        # 4. Barra Inferior com Contagens e Resumo de Jogos
        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_frame.grid(row=3, column=0, sticky="ew", padx=15, pady=(5, 10))

        self.lbl_info = ctk.CTkLabel(
            self.bottom_frame,
            text="0 jogos mapeados | 0 vínculos de saves",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.lbl_info.pack(side="left")

        self.lbl_legend = ctk.CTkLabel(
            self.bottom_frame,
            text="🔴 Vínculo Heurístico — Revisar (Compartilhado/Falso Positivo) | 🟢 Legítimo/Provável",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
        )
        self.lbl_legend.pack(side="right")

    def _clear_filters(self) -> None:
        self.entry_search.delete(0, "end")
        self.chk_suspect_var.set(False)
        self.refresh_view()

    def refresh_view(self) -> None:
        """Recarrega os jogos e saves com os filtros aplicados."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.data_loader.is_loaded:
            self.lbl_info.configure(text="Nenhum inventário de jogos carregado")
            return

        search_query = self.entry_search.get().strip()
        suspect_only = self.chk_suspect_var.get()

        saves_list = self.data_loader.get_saves_list(search=search_query, suspect_only=suspect_only)
        games_list = self.data_loader.get_games_list()

        total_jogos = len(games_list)
        total_saves = len(saves_list)

        for s in saves_list:
            is_susp = s.get("is_suspeito", False)
            tag = "suspeito" if is_susp else "legitimo"
            status_text = s.get("status_vinculo", "")

            self.tree.insert(
                "",
                "end",
                values=(
                    s.get("jogo_nome", ""),
                    s.get("launcher", ""),
                    s.get("caminho_save", ""),
                    format_bytes(s.get("tamanho_bytes", 0)),
                    str(s.get("quantidade_arquivos", 0)),
                    s.get("nivel_certeza", "").upper(),
                    status_text,
                ),
                tags=(tag,),
            )

        self.lbl_info.configure(
            text=f"{total_jogos} jogos cadastrados em bibliotecas | {total_saves} vínculos de saves listados ({len([s for s in saves_list if s.get('is_suspeito')])} sinalizados para revisão)"
        )
