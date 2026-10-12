"""Aba de Plano de Cópia Segura (Fase 3: Safe Mover).

Permite gerar prévias do plano sem escrever nada no disco, verificar espaço livre,
visualizar ordens em tabela paginada e disparar cópias seguras não-destrutivas
com confirmação explícita ("A origem não será apagada").
"""

from __future__ import annotations

import datetime
import math
import os
import threading
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from core.action_plan import ActionOrder, ActionPlan, ActionPlanGenerator
from core.safe_mover import SafeCopyExecutor
from exporters.excel_exporter import format_bytes, format_timestamp
from gui.data_loader import AuditDataLoader


class SafeMoverView(ctk.CTkFrame):
    """Componente visual para prévia e execução controlada do Safe Mover."""

    def __init__(
        self,
        master: Any,
        data_loader: AuditDataLoader,
        reports_dir: str = "reports",
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)
        self.data_loader = data_loader
        self.reports_dir = reports_dir

        self.plan: Optional[ActionPlan] = None
        self.is_running: bool = False
        self.cancel_event: Optional[threading.Event] = None
        self.worker_thread: Optional[threading.Thread] = None

        # Paginação da prévia
        self.current_page: int = 1
        self.page_size: int = 100
        self.total_pages: int = 1
        self.filtered_orders: List[ActionOrder] = []

        self._create_widgets()

    def _create_widgets(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # -------------------------------------------------------------
        # 1. Painel de Configurações do Destino e Ações
        # -------------------------------------------------------------
        self.cfg_frame = ctk.CTkFrame(self, fg_color=("gray90", "gray17"), corner_radius=8)
        self.cfg_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        row1 = ctk.CTkFrame(self.cfg_frame, fg_color="transparent")
        row1.pack(fill="x", padx=15, pady=(10, 5))

        ctk.CTkLabel(row1, text="Pasta Base de Destino:").pack(side="left", padx=(0, 5))

        default_dest = "D:\\Backup_StorageAuditor" if os.path.exists("D:\\") else os.path.abspath("backup_destino")
        self.var_target_base = ctk.StringVar(value=default_dest)
        self.entry_dest = ctk.CTkEntry(row1, textvariable=self.var_target_base, width=380)
        self.entry_dest.pack(side="left", padx=5)

        self.btn_browse = ctk.CTkButton(
            row1,
            text="Procurar...",
            width=90,
            command=self._browse_destination,
            fg_color="gray30",
            hover_color="gray40",
        )
        self.btn_browse.pack(side="left", padx=5)

        # Botões de Ação
        self.btn_preview = ctk.CTkButton(
            row1,
            text="🔍 Gerar Prévia do Plano",
            command=self._generate_preview,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            width=180,
        )
        self.btn_preview.pack(side="right", padx=5)

        self.btn_execute = ctk.CTkButton(
            row1,
            text="🚀 Iniciar Cópia Segura",
            command=self._confirm_and_execute,
            fg_color="#059669",
            hover_color="#047857",
            width=180,
            state="disabled",
        )
        self.btn_execute.pack(side="right", padx=5)

        self.btn_cancel = ctk.CTkButton(
            row1,
            text="⏹ Cancelar",
            command=self._cancel_execution,
            fg_color="#DC2626",
            hover_color="#B91C1C",
            width=100,
            state="disabled",
        )
        self.btn_cancel.pack(side="right", padx=5)

        # Linha de Aviso de Segurança e Status
        row2 = ctk.CTkFrame(self.cfg_frame, fg_color="transparent")
        row2.pack(fill="x", padx=15, pady=(0, 10))

        self.lbl_safe_notice = ctk.CTkLabel(
            row2,
            text="🛡️ Modo 100% Não-Destrutivo: A origem NUNCA será apagada ou movida. Saves heurísticos são rejeitados.",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10B981",
        )
        self.lbl_safe_notice.pack(side="left")

        # Barra de Progresso interna
        self.progress_bar = ctk.CTkProgressBar(row2, width=320)
        self.progress_bar.pack(side="right", padx=5)
        self.progress_bar.set(0)

        self.lbl_progress = ctk.CTkLabel(row2, text="Prévia não gerada.", font=ctk.CTkFont(size=11), text_color="gray70")
        self.lbl_progress.pack(side="right", padx=10)

        # -------------------------------------------------------------
        # 2. Cards de Estatísticas e Projeção de Espaço Livre
        # -------------------------------------------------------------
        self.cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.cards_frame.grid(row=1, column=0, sticky="ew", padx=15, pady=5)
        for c in range(4):
            self.cards_frame.grid_columnconfigure(c, weight=1)

        self.card_orders = self._create_card(self.cards_frame, 0, "ORDENS APROVADAS", "0", "Aguardando prévia")
        self.card_volume = self._create_card(self.cards_frame, 1, "VOLUME TOTAL", "0 B", "Aguardando prévia")
        self.card_free = self._create_card(self.cards_frame, 2, "ESPAÇO LIVRE DESTINO", "0 B", "Verificando disco...")
        self.card_projection = self._create_card(self.cards_frame, 3, "PROJEÇÃO DE ESPAÇO", "Indefinida", "Margem mínima: 2 GB")

        # -------------------------------------------------------------
        # 3. Tabela Paginada da Prévia
        # -------------------------------------------------------------
        self.table_frame = ctk.CTkFrame(self, fg_color=("gray95", "gray12"), corner_radius=8)
        self.table_frame.grid(row=2, column=0, sticky="nsew", padx=15, pady=5)
        self.table_frame.grid_rowconfigure(0, weight=1)
        self.table_frame.grid_columnconfigure(0, weight=1)

        cols = ("order_id", "source", "target", "size", "mtime", "category", "status")
        self.tree = ttk.Treeview(self.table_frame, columns=cols, show="headings", selectmode="browse")

        self.tree.heading("order_id", text="ID Ordem")
        self.tree.heading("source", text="Caminho Origem (C:)")
        self.tree.heading("target", text="Destino Projetado")
        self.tree.heading("size", text="Tamanho")
        self.tree.heading("mtime", text="Data Modificação")
        self.tree.heading("category", text="Categoria")
        self.tree.heading("status", text="Status / Validação")

        self.tree.column("order_id", width=110, anchor="center")
        self.tree.column("source", width=360, anchor="w")
        self.tree.column("target", width=360, anchor="w")
        self.tree.column("size", width=100, anchor="e")
        self.tree.column("mtime", width=140, anchor="center")
        self.tree.column("category", width=150, anchor="center")
        self.tree.column("status", width=130, anchor="center")

        scrollbar_y = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
        scrollbar_x = ttk.Scrollbar(self.table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(5, 0), pady=(5, 0))
        scrollbar_y.grid(row=0, column=1, sticky="ns", pady=(5, 0))
        scrollbar_x.grid(row=1, column=0, sticky="ew", padx=(5, 0))

        # -------------------------------------------------------------
        # 4. Rodapé de Paginação
        # -------------------------------------------------------------
        self.page_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.page_frame.grid(row=3, column=0, sticky="ew", padx=15, pady=(5, 10))

        self.btn_prev = ctk.CTkButton(self.page_frame, text="◀ Anterior", width=80, command=self._prev_page)
        self.btn_prev.pack(side="left")

        self.lbl_page = ctk.CTkLabel(self.page_frame, text="Página 1 de 1")
        self.lbl_page.pack(side="left", padx=15)

        self.btn_next = ctk.CTkButton(self.page_frame, text="Próxima ▶", width=80, command=self._next_page)
        self.btn_next.pack(side="left")

        self.lbl_total_items = ctk.CTkLabel(self.page_frame, text="Total: 0 ordens", text_color="gray70")
        self.lbl_total_items.pack(side="right")

    def _create_card(self, parent: Any, col: int, title: str, main_val: str, sub_val: str) -> Dict[str, Any]:
        frame = ctk.CTkFrame(parent, fg_color=("gray90", "gray17"), corner_radius=8)
        frame.grid(row=0, column=col, padx=5, sticky="ew")

        lbl_t = ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=10, weight="bold"), text_color="gray60")
        lbl_t.pack(anchor="w", padx=12, pady=(10, 2))

        lbl_v = ctk.CTkLabel(frame, text=main_val, font=ctk.CTkFont(size=18, weight="bold"))
        lbl_v.pack(anchor="w", padx=12, pady=(0, 2))

        lbl_s = ctk.CTkLabel(frame, text=sub_val, font=ctk.CTkFont(size=11), text_color="gray70")
        lbl_s.pack(anchor="w", padx=12, pady=(0, 10))

        return {"frame": frame, "val": lbl_v, "sub": lbl_s}

    def _browse_destination(self) -> None:
        """Abre diálogo para seleção de pasta base de destino."""
        path = filedialog.askdirectory(
            title="Selecionar Pasta Base de Destino para Cópia Segura",
            initialdir=self.var_target_base.get() or "D:\\",
            parent=self,
        )
        if path:
            self.var_target_base.set(path)

    def _generate_preview(self) -> None:
        """Gera a prévia do Action Plan sem alterar nenhum arquivo."""
        target_dir = self.var_target_base.get().strip()
        if not target_dir:
            messagebox.showwarning("Destino Obrigatório", "Informe a pasta base de destino.", parent=self)
            return

        generator = ActionPlanGenerator(
            reports_dir=self.reports_dir,
            target_base_dir=target_dir,
        )

        try:
            self.plan = generator.generate_plan(include_pending=True, include_documents=True)
            self.filtered_orders = list(self.plan.ordens)
            self.current_page = 1
            self.total_pages = max(1, math.ceil(len(self.filtered_orders) / self.page_size))

            # Atualiza Cards
            totais = self.plan.totais
            self.card_orders["val"].configure(text=f"{totais['total_ordens']:,}")
            self.card_orders["sub"].configure(text="Ordens validadas")

            self.card_volume["val"].configure(text=format_bytes(totais["tamanho_total_bytes"]))
            self.card_volume["sub"].configure(text=f"{totais['tamanho_total_bytes']:,} bytes")

            self.card_free["val"].configure(text=format_bytes(totais["espaco_livre_destino_bytes"]))
            self.card_free["sub"].configure(text="No disco de destino")

            if totais.get("espaco_suficiente"):
                self.card_projection["val"].configure(text="SUFICIENTE", text_color="#059669")
                self.card_projection["sub"].configure(text="Margem de 2 GB garantida")
                self.btn_execute.configure(state="normal" if len(self.filtered_orders) > 0 else "disabled")
            else:
                self.card_projection["val"].configure(text="INSUFICIENTE", text_color="#DC2626")
                self.card_projection["sub"].configure(text="Falta espaço livre no disco!")
                self.btn_execute.configure(state="disabled")

            self.lbl_progress.configure(text=f"Prévia gerada: {len(self.filtered_orders)} ordens prontas para cópia.")
            self._render_current_page()

        except Exception as e:
            messagebox.showerror("Erro na Prévia", f"Falha ao gerar o plano de ação:\n{e}", parent=self)

    def _render_current_page(self) -> None:
        """Renderiza as ordens da página atual na Treeview."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        page_items = self.filtered_orders[start:end]

        for o in page_items:
            status_text = o.status.upper()
            if o.status == "completed":
                status_text = "🟢 CONCLUÍDO"
            elif o.status == "pending":
                status_text = "⏳ PENDENTE"
            elif o.status == "copying":
                status_text = "🔄 COPIANDO"
            elif o.status in ("conflict", "failed"):
                status_text = "❌ FALHA/CONFLITO"
            elif o.status == "cancelled":
                status_text = "⏹ CANCELADO"

            self.tree.insert(
                "",
                "end",
                values=(
                    o.order_id,
                    o.source_path,
                    o.target_path,
                    format_bytes(o.expected_size_bytes),
                    format_timestamp(o.expected_mtime),
                    o.category,
                    status_text,
                ),
            )

        self.lbl_page.configure(text=f"Página {self.current_page} de {self.total_pages}")
        self.lbl_total_items.configure(text=f"Total: {len(self.filtered_orders):,} ordens")
        self.btn_prev.configure(state="normal" if self.current_page > 1 else "disabled")
        self.btn_next.configure(state="normal" if self.current_page < self.total_pages else "disabled")

    def _prev_page(self) -> None:
        if self.current_page > 1:
            self.current_page -= 1
            self._render_current_page()

    def _next_page(self) -> None:
        if self.current_page < self.total_pages:
            self.current_page += 1
            self._render_current_page()

    def _confirm_and_execute(self) -> None:
        """Exibe confirmação explícita e dispara o executor em background."""
        if not self.plan or len(self.plan.ordens) == 0:
            messagebox.showinfo("Sem Ordens", "Gere uma prévia com ordens válidas antes de executar.", parent=self)
            return

        totais = self.plan.totais
        msg = (
            f"Você está prestes a iniciar a cópia segura de:\n\n"
            f"• Quantidade: {totais['total_ordens']:,} arquivos aprovados\n"
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
        self.btn_preview.configure(state="disabled")
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

    def _cancel_execution(self) -> None:
        """Solicita cancelamento cooperativo."""
        if self.cancel_event:
            self.lbl_progress.configure(text="Cancelando... Aguardando término do bloco atual.")
            self.cancel_event.set()

    def _on_progress_update(self, data: Dict[str, Any]) -> None:
        """Callback executado na thread da GUI para atualizar progresso."""
        completed = data.get("completed", 0)
        total = data.get("total", 1)
        bytes_copied = data.get("bytes_copied", 0)
        total_bytes = data.get("total_bytes", 1)

        frac = min(bytes_copied / total_bytes, 1.0) if total_bytes > 0 else 0.0
        self.progress_bar.set(frac)
        self.lbl_progress.configure(
            text=f"Copiados: {completed:,}/{total:,} arquivos ({format_bytes(bytes_copied)} / {format_bytes(total_bytes)})"
        )
        self._render_current_page()

    def _on_execution_finished(self, summary: Dict[str, Any]) -> None:
        """Conclusão do Safe Mover."""
        self.is_running = False
        self.btn_execute.configure(state="normal")
        self.btn_preview.configure(state="normal")
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
        self._render_current_page()
        messagebox.showinfo("Cópia Segura Concluída", msg, parent=self)

    def _on_execution_error(self, err_msg: str) -> None:
        """Erro durante o Safe Mover."""
        self.is_running = False
        self.btn_execute.configure(state="normal")
        self.btn_preview.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.lbl_progress.configure(text=f"Erro na execução: {err_msg}")
        self._render_current_page()
        messagebox.showerror("Erro na Cópia Segura", f"Ocorreu um erro durante a cópia:\n\n{err_msg}\n\nA origem não foi afetada.", parent=self)
