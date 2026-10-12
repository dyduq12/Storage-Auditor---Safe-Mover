"""Aba de Resumo Executivo da auditoria (Fase 2)."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Callable, Dict, Optional

import customtkinter as ctk

from exporters.excel_exporter import format_bytes, format_timestamp
from gui.data_loader import AuditDataLoader


class SummaryView(ctk.CTkFrame):
    """Componente visual para exibição dos cards e métricas consolidadas."""

    def __init__(
        self,
        master: Any,
        data_loader: AuditDataLoader,
        on_export_excel: Optional[Callable[[], None]] = None,
        on_refresh: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)
        self.data_loader = data_loader
        self.on_export_excel = on_export_excel
        self.on_refresh = on_refresh

        self._create_widgets()
        self.refresh_view()

    def _create_widgets(self) -> None:
        self.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # Cabeçalho da Aba
        self.title_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.title_frame.grid(row=0, column=0, columnspan=4, sticky="ew", padx=15, pady=(15, 10))

        self.lbl_title = ctk.CTkLabel(
            self.title_frame,
            text="Visão Executiva do Armazenamento",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.lbl_title.pack(side="left")

        self.lbl_timestamp = ctk.CTkLabel(
            self.title_frame,
            text="Nenhum relatório carregado",
            font=ctk.CTkFont(size=12, slant="italic"),
            text_color="gray70",
        )
        self.lbl_timestamp.pack(side="right")

        # Container para Cards de Métricas
        self.cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.cards_frame.grid(row=1, column=0, columnspan=4, sticky="nsew", padx=15, pady=5)
        self.cards_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # Criação dos Cards
        self.card_c = self._create_card(self.cards_frame, 0, 0, "📁 Disco C: Indexado", "0 arquivos", "0 B", "dodgerblue")
        self.card_d = self._create_card(self.cards_frame, 0, 1, "💾 Disco D: Indexado", "0 arquivos", "0 B", "dodgerblue4")
        self.card_dup = self._create_card(self.cards_frame, 0, 2, "🔄 Duplicatas Confirmadas", "0 itens", "0 B recup.", "seagreen")
        self.card_pen = self._create_card(self.cards_frame, 0, 3, "⏳ Pendentes de Backup", "0 itens", "0 B a copiar", "goldenrod")

        self.card_doc = self._create_card(self.cards_frame, 1, 0, "📄 Documentos Avulsos", "0 itens", "0 B", "mediumpurple")
        self.card_jog = self._create_card(self.cards_frame, 1, 1, "🎮 Jogos Instalados", "0 jogos", "0 B", "deepskyblue3")
        self.card_sav = self._create_card(self.cards_frame, 1, 2, "🕹️ Saves em C: (Heurístico)", "0 vínculos", "0 B", "darkorange")
        self.card_rec = self._create_card(self.cards_frame, 1, 3, "✨ Espaço Liberável", "0 duplicatas", "0 B", "forestgreen")

        # Painel Inferior de Ações e Avisos
        self.actions_frame = ctk.CTkFrame(self, fg_color=("gray90", "gray17"), corner_radius=8)
        self.actions_frame.grid(row=2, column=0, columnspan=4, sticky="ew", padx=15, pady=(15, 15))

        self.btn_export_excel = ctk.CTkButton(
            self.actions_frame,
            text="📊 Exportar Relatório Excel (.xlsx)",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#1F4E78",
            hover_color="#163654",
            command=self._on_click_export,
        )
        self.btn_export_excel.pack(side="left", padx=15, pady=12)

        self.btn_open_folder = ctk.CTkButton(
            self.actions_frame,
            text="📂 Abrir Pasta reports/",
            fg_color="gray30",
            hover_color="gray40",
            command=self._open_reports_folder,
        )
        self.btn_open_folder.pack(side="left", padx=5, pady=12)

        self.btn_refresh = ctk.CTkButton(
            self.actions_frame,
            text="🔄 Recarregar Dados",
            fg_color="gray30",
            hover_color="gray40",
            command=self._on_click_refresh,
        )
        self.btn_refresh.pack(side="left", padx=5, pady=12)

        # Aviso de segurança read-only
        self.lbl_warning = ctk.CTkLabel(
            self.actions_frame,
            text="🛡️ Modo 100% Somente-Leitura: nenhum arquivo será movido ou apagado.",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color=("gray30", "gray70"),
        )
        self.lbl_warning.pack(side="right", padx=15, pady=12)

    def _create_card(
        self,
        parent: Any,
        row: int,
        col: int,
        title: str,
        val1: str,
        val2: str,
        accent_color: str,
    ) -> Dict[str, Any]:
        frame = ctk.CTkFrame(parent, corner_radius=10, border_width=1, border_color=("gray75", "gray25"))
        frame.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

        lbl_t = ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=13, weight="bold"), text_color=accent_color)
        lbl_t.pack(anchor="w", padx=12, pady=(10, 4))

        lbl_v1 = ctk.CTkLabel(frame, text=val1, font=ctk.CTkFont(size=18, weight="bold"))
        lbl_v1.pack(anchor="w", padx=12, pady=0)

        lbl_v2 = ctk.CTkLabel(frame, text=val2, font=ctk.CTkFont(size=12), text_color=("gray40", "gray70"))
        lbl_v2.pack(anchor="w", padx=12, pady=(2, 10))

        return {"frame": frame, "lbl_t": lbl_t, "lbl_v1": lbl_v1, "lbl_v2": lbl_v2}

    def refresh_view(self) -> None:
        """Atualiza os valores dos cards com os dados atuais do data_loader."""
        if not self.data_loader.is_loaded:
            self.lbl_timestamp.configure(text="Nenhum relatório carregado em 'reports/'")
            return

        m = self.data_loader.get_summary_metrics()

        ts = format_timestamp(m.get("timestamp"))
        self.lbl_timestamp.configure(text=f"Auditoria de: {ts} (Schema v{m.get('schema_version', '1.1.0')})")

        # Atualiza cards
        self.card_c["lbl_v1"].configure(text=f"{m['arquivos_c']:,} arquivos".replace(",", "."))
        self.card_c["lbl_v2"].configure(text=format_bytes(m["bytes_c"]))

        self.card_d["lbl_v1"].configure(text=f"{m['arquivos_d']:,} arquivos".replace(",", "."))
        self.card_d["lbl_v2"].configure(text=format_bytes(m["bytes_d"]))

        self.card_dup["lbl_v1"].configure(text=f"{m['total_duplicatas']:,} duplicatas".replace(",", "."))
        self.card_dup["lbl_v2"].configure(text=f"{format_bytes(m['espaco_recuperavel_bytes'])} recuperáveis")

        self.card_pen["lbl_v1"].configure(text=f"{m['total_pendentes']:,} pendentes".replace(",", "."))
        self.card_pen["lbl_v2"].configure(text=f"{format_bytes(m['tamanho_pendentes_bytes'])} a sincronizar")

        self.card_doc["lbl_v1"].configure(text=f"{m['total_documentos']:,} documentos".replace(",", "."))
        self.card_doc["lbl_v2"].configure(text=format_bytes(m["tamanho_documentos_bytes"]))

        self.card_jog["lbl_v1"].configure(text=f"{m['total_jogos']} jogos instalados")
        self.card_jog["lbl_v2"].configure(text=format_bytes(m["tamanho_jogos_bytes"]))

        self.card_sav["lbl_v1"].configure(text=f"{m['total_saves']} vínculos em C:")
        self.card_sav["lbl_v2"].configure(text=f"{format_bytes(m['tamanho_saves_bytes'])} (Revisar)")

        self.card_rec["lbl_v1"].configure(text=f"{format_bytes(m['espaco_recuperavel_bytes'])}")
        self.card_rec["lbl_v2"].configure(text=f"Em {m['total_duplicatas']:,} arquivos de C:".replace(",", "."))

    def _on_click_export(self) -> None:
        if self.on_export_excel:
            self.on_export_excel()

    def _on_click_refresh(self) -> None:
        if self.on_refresh:
            self.on_refresh()

    def _open_reports_folder(self) -> None:
        folder = os.path.abspath(self.data_loader.reports_dir)
        if os.path.exists(folder):
            if os.name == "nt":
                os.startfile(folder)
            else:
                subprocess.run(["explorer", folder], check=False)
