"""Aplicação Desktop CustomTkinter principal do Storage Auditor (Fase 2).

Implementa a janela principal responsiva com:
- Seleção de escopo padrão (Documents, Downloads, Desktop, Videos, Pictures)
- Inclusão e remoção de pastas personalizadas por seletor nativo
- Configuração de raízes de D:, jogos, saves e limite de hash
- Execução não-bloqueante em background com comunicação thread-safe via fila
- Monitoramento de progresso, cancelamento limpo e tratamento de erros
- Abas de Resumo, Duplicatas, Pendentes, Documentos e Jogos/Saves
- Exportação em um clique para planilha Excel (.xlsx) com openpyxl
"""

from __future__ import annotations

import datetime
import logging
import os
import queue
import threading
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from config import (
    DEFAULT_FULL_HASH_THRESHOLD_BYTES,
    SCHEMA_VERSION,
    AuditorConfig,
    get_default_c_roots,
    get_default_d_roots,
    get_default_game_library_paths,
    get_save_candidate_roots,
    parse_cli_path_list,
)
from exporters.excel_exporter import ExcelReportExporter, ExportCancelledError
from gui.data_loader import AuditDataLoader
from gui.views.tab_documents import DocumentsView
from gui.views.tab_duplicates import DuplicatesView
from gui.views.tab_games import GamesView
from gui.views.tab_pending import PendingView
from gui.views.tab_summary import SummaryView
from gui.worker import AuditWorker

# Configuração visual do CustomTkinter
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class StorageAuditorApp(ctk.CTk):
    """Janela principal da interface desktop CustomTkinter."""

    def __init__(self) -> None:
        super().__init__()

        self.title("Storage Auditor & Safe Mover — Auditoria e Desduplicação (100% Read-Only)")
        self.geometry("1280x860")
        self.minsize(1040, 680)

        # Fila thread-safe de eventos de auditoria
        self.event_queue: queue.Queue = queue.Queue()
        self.worker: Optional[AuditWorker] = None

        # Controle de exportação em background
        self.is_exporting: bool = False
        self.export_cancel_event: Optional[threading.Event] = None
        self.export_thread: Optional[threading.Thread] = None

        # Carregador de dados em memória
        self.data_loader = AuditDataLoader(reports_dir="reports")
        self.data_loader.load_from_directory()

        # Variáveis de Configuração
        self._init_config_vars()

        # Constrói interface
        self._create_layout()

        # Inicia loop de verificação da fila thread-safe
        self.after(50, self._check_worker_queue)

        # Intercepta fechamento da janela
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

    def _init_config_vars(self) -> None:
        """Inicializa variáveis reativas de configuração."""
        user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))

        # Pastas padrão de C:
        self.c_folder_vars: Dict[str, ctk.BooleanVar] = {
            "Documents": ctk.BooleanVar(value=True),
            "Downloads": ctk.BooleanVar(value=True),
            "Desktop": ctk.BooleanVar(value=True),
            "Videos": ctk.BooleanVar(value=True),
            "Pictures": ctk.BooleanVar(value=True),
        }
        self.custom_c_folders: List[str] = []

        # Raízes D:
        d_defaults = get_default_d_roots()
        self.var_d_roots = ctk.StringVar(value="; ".join(d_defaults) if d_defaults else "D:\\")

        # Bibliotecas de Jogos
        g_defaults = get_default_game_library_paths()
        self.var_game_roots = ctk.StringVar(value="; ".join(g_defaults) if g_defaults else "D:\\Games; D:\\SteamLibrary; D:\\XboxGames")

        # Diretório de Saída
        self.var_output_dir = ctk.StringVar(value="reports")

        # Limite de Hash Completo em MB
        self.var_hash_limit_mb = ctk.StringVar(value="100")

    def _create_layout(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # -------------------------------------------------------------
        # 1. Cabeçalho Superior
        # -------------------------------------------------------------
        self.header_frame = ctk.CTkFrame(self, fg_color=("gray85", "gray14"), corner_radius=0)
        self.header_frame.grid(row=0, column=0, sticky="ew")

        self.lbl_app_title = ctk.CTkLabel(
            self.header_frame,
            text="STORAGE AUDITOR & SAFE MOVER",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.lbl_app_title.pack(side="left", padx=20, pady=10)

        self.lbl_app_sub = ctk.CTkLabel(
            self.header_frame,
            text="Motor 1: Auditor & Reporter (100% Somente Leitura)",
            font=ctk.CTkFont(size=12, slant="italic"),
            text_color="gray70",
        )
        self.lbl_app_sub.pack(side="left", padx=5, pady=10)

        self.status_pill = ctk.CTkLabel(
            self.header_frame,
            text="🟢 PRONTO",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("gray75", "gray25"),
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.status_pill.pack(side="right", padx=20, pady=10)

        # -------------------------------------------------------------
        # 2. Painel de Configurações e Controles de Auditoria
        # -------------------------------------------------------------
        self.config_panel = ctk.CTkFrame(self, fg_color=("gray90", "gray17"), corner_radius=8)
        self.config_panel.grid(row=1, column=0, sticky="ew", padx=15, pady=(10, 5))

        # Linha 1 de Configuração: Pastas padrão de C: e Raiz D:
        row1 = ctk.CTkFrame(self.config_panel, fg_color="transparent")
        row1.pack(fill="x", padx=15, pady=(10, 5))

        ctk.CTkLabel(row1, text="Pastas C:").pack(side="left", padx=(0, 10))
        for name, var in self.c_folder_vars.items():
            cb = ctk.CTkCheckBox(row1, text=name, variable=var)
            cb.pack(side="left", padx=6)

        btn_add_custom = ctk.CTkButton(
            row1,
            text="+ Pasta C:...",
            width=100,
            fg_color="gray30",
            hover_color="gray40",
            command=self._add_custom_c_folder,
        )
        btn_add_custom.pack(side="left", padx=10)

        # Campo Raízes D:
        ctk.CTkLabel(row1, text="Raiz D:").pack(side="left", padx=(15, 5))
        self.entry_d = ctk.CTkEntry(row1, textvariable=self.var_d_roots, width=140)
        self.entry_d.pack(side="left", padx=5)

        # Linha 2 de Configuração: Jogos, Saída, Limite Hash e Ações
        row2 = ctk.CTkFrame(self.config_panel, fg_color="transparent")
        row2.pack(fill="x", padx=15, pady=(5, 10))

        ctk.CTkLabel(row2, text="Bibliotecas Jogos:").pack(side="left", padx=(0, 5))
        self.entry_games = ctk.CTkEntry(row2, textvariable=self.var_game_roots, width=280)
        self.entry_games.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row2, text="Pasta Saída:").pack(side="left", padx=(0, 5))
        self.entry_out = ctk.CTkEntry(row2, textvariable=self.var_output_dir, width=100)
        self.entry_out.pack(side="left", padx=(0, 5))

        btn_browse_out = ctk.CTkButton(
            row2,
            text="...",
            width=30,
            fg_color="gray30",
            hover_color="gray40",
            command=self._browse_output_dir,
        )
        btn_browse_out.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row2, text="Limite Hash (MB):").pack(side="left", padx=(0, 5))
        self.entry_hash = ctk.CTkEntry(row2, textvariable=self.var_hash_limit_mb, width=60)
        self.entry_hash.pack(side="left", padx=(0, 20))

        # Botão Iniciar Auditoria
        self.btn_start = ctk.CTkButton(
            row2,
            text="▶ Iniciar Auditoria",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#059669",
            hover_color="#047857",
            command=self._start_audit,
            width=150,
        )
        self.btn_start.pack(side="right", padx=(5, 0))

        # Botão Cancelar Auditoria
        self.btn_cancel = ctk.CTkButton(
            row2,
            text="⏹ Cancelar",
            fg_color="#DC2626",
            hover_color="#B91C1C",
            command=self._cancel_audit,
            state="disabled",
            width=100,
        )
        self.btn_cancel.pack(side="right", padx=5)

        # Barra de Progresso e Mensagem
        self.prog_frame = ctk.CTkFrame(self.config_panel, fg_color="transparent")
        self.prog_frame.pack(fill="x", padx=15, pady=(0, 10))

        self.progress_bar = ctk.CTkProgressBar(self.prog_frame)
        self.progress_bar.pack(fill="x", side="top", pady=(0, 4))
        self.progress_bar.set(0)

        self.lbl_progress_status = ctk.CTkLabel(
            self.prog_frame,
            text="Pronto para auditar. Selecione os escopos acima e clique em 'Iniciar Auditoria'.",
            font=ctk.CTkFont(size=11),
            text_color="gray75",
        )
        self.lbl_progress_status.pack(anchor="w")

        # -------------------------------------------------------------
        # 3. Notebook de Abas
        # -------------------------------------------------------------
        self.tabview = ctk.CTkTabview(self, corner_radius=8)
        self.tabview.grid(row=2, column=0, sticky="nsew", padx=15, pady=5)

        tab_resumo = self.tabview.add("Resumo")
        tab_duplicatas = self.tabview.add("Duplicatas")
        tab_pendentes = self.tabview.add("Pendentes de Backup")
        tab_documentos = self.tabview.add("Documentos Avulsos")
        tab_jogos = self.tabview.add("Jogos e Saves")

        # Instancia componentes de cada aba
        self.view_summary = SummaryView(
            tab_resumo,
            data_loader=self.data_loader,
            on_export_excel=self._export_excel,
            on_refresh=self._reload_data,
        )
        self.view_summary.pack(fill="both", expand=True)

        self.view_duplicates = DuplicatesView(tab_duplicatas, data_loader=self.data_loader)
        self.view_duplicates.pack(fill="both", expand=True)

        self.view_pending = PendingView(tab_pendentes, data_loader=self.data_loader)
        self.view_pending.pack(fill="both", expand=True)

        self.view_documents = DocumentsView(tab_documentos, data_loader=self.data_loader)
        self.view_documents.pack(fill="both", expand=True)

        self.view_games = GamesView(tab_jogos, data_loader=self.data_loader)
        self.view_games.pack(fill="both", expand=True)

        # -------------------------------------------------------------
        # 4. Rodapé
        # -------------------------------------------------------------
        self.footer_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.footer_frame.grid(row=3, column=0, sticky="ew", padx=15, pady=(2, 6))

        self.lbl_footer = ctk.CTkLabel(
            self.footer_frame,
            text="🛡️ Auditoria 100% Segura e Somente-Leitura — Nenhum arquivo é modificado, movido ou excluído pelo Motor 1.",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color="gray60",
        )
        self.lbl_footer.pack(side="left")

    # -----------------------------------------------------------------
    # Ações de Configuração e Navegação
    # -----------------------------------------------------------------

    def _add_custom_c_folder(self) -> None:
        """Abre seletor nativo de diretório para adicionar pasta personalizada em C:."""
        folder = filedialog.askdirectory(title="Selecione pasta adicional para auditar em C:")
        if folder:
            norm_f = os.path.abspath(folder)
            if norm_f not in self.custom_c_folders:
                self.custom_c_folders.append(norm_f)
                messagebox.showinfo("Pasta Adicionada", f"Pasta incluída no escopo de C:\n{norm_f}")

    def _browse_output_dir(self) -> None:
        """Abre seletor nativo para selecionar pasta de saída."""
        folder = filedialog.askdirectory(title="Selecione o diretório para salvar os relatórios")
        if folder:
            self.var_output_dir.set(os.path.abspath(folder))

    def _build_auditor_config(self) -> AuditorConfig:
        """Monta objeto AuditorConfig a partir das configurações da interface."""
        user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))

        # Raízes C: selecionadas
        c_roots = []
        for name, var in self.c_folder_vars.items():
            if var.get():
                p = os.path.join(user_profile, name)
                if os.path.exists(p):
                    c_roots.append(os.path.abspath(p))

        for custom in self.custom_c_folders:
            if os.path.exists(custom) and custom not in c_roots:
                c_roots.append(custom)

        # Raízes D:
        d_tokens = [t.strip() for t in self.var_d_roots.get().split(";") if t.strip()]
        d_roots, _ = parse_cli_path_list(d_tokens)

        # Bibliotecas de Jogos
        g_tokens = [t.strip() for t in self.var_game_roots.get().split(";") if t.strip()]
        game_roots, _ = parse_cli_path_list(g_tokens)

        # Raízes de Saves
        save_roots = get_save_candidate_roots()

        # Limite de Hash
        try:
            limit_mb = int(self.var_hash_limit_mb.get().strip())
        except ValueError:
            limit_mb = 100
        full_hash_threshold = limit_mb * 1024 * 1024

        out_dir = self.var_output_dir.get().strip() or "reports"
        cache_db = os.path.join(out_dir, "storage_auditor_cache.db")

        return AuditorConfig(
            c_roots=c_roots,
            d_roots=d_roots,
            game_library_paths=game_roots,
            save_candidate_roots=save_roots,
            output_dir=out_dir,
            cache_db_path=cache_db,
            full_hash_threshold_bytes=full_hash_threshold,
            schema_version=SCHEMA_VERSION,
        )

    # -----------------------------------------------------------------
    # Ciclo de Vida do AuditWorker em Background
    # -----------------------------------------------------------------

    def _start_audit(self) -> None:
        """Dispara a execução da auditoria em thread separada."""
        if self.worker and self.worker.is_running:
            return

        config = self._build_auditor_config()

        if not config.c_roots and not config.d_roots:
            messagebox.showwarning("Escopo Vazio", "Selecione ao menos uma pasta de C: ou D: para auditar.")
            return

        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.status_pill.configure(text="🟡 AUDITANDO...", text_color="#D97706")
        self.progress_bar.set(0)
        self.lbl_progress_status.configure(text="Iniciando motor de auditoria...")

        self.worker = AuditWorker(config=config, event_queue=self.event_queue)
        self.worker.start()

    def _cancel_audit(self) -> None:
        """Solicita cancelamento gracioso da auditoria."""
        if self.worker and self.worker.is_running:
            self.lbl_progress_status.configure(text="Solicitando cancelamento gracioso...")
            self.worker.cancel()

    def _check_worker_queue(self) -> None:
        """Consome eventos da fila de forma segura no thread da GUI."""
        try:
            while True:
                event_type, data = self.event_queue.get_nowait()
                self._handle_worker_event(event_type, data)
        except queue.Empty:
            pass
        finally:
            self.after(50, self._check_worker_queue)

    def _handle_worker_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Processa eventos vindos do AuditWorker."""
        if event_type == "started":
            self.lbl_progress_status.configure(text=data.get("message", "Iniciando..."))

        elif event_type == "progress":
            stage = data.get("stage", "")
            msg = data.get("message", "")
            cur = data.get("current", 0)
            tot = data.get("total", 0)

            self.lbl_progress_status.configure(text=msg)

            if stage == "scan_c":
                self.progress_bar.set(0.15)
            elif stage == "scan_d":
                self.progress_bar.set(0.35)
            elif stage == "games":
                self.progress_bar.set(0.50)
            elif stage == "compare":
                if tot > 0:
                    prog_val = 0.50 + (cur / tot) * 0.40
                    self.progress_bar.set(min(prog_val, 0.90))
            elif stage == "export":
                self.progress_bar.set(0.95)

        elif event_type == "finished":
            exit_code = data.get("exit_code", 0)
            r_dir = data.get("reports_dir", "reports")
            self.progress_bar.set(1.0)
            self.btn_start.configure(state="normal")
            self.btn_cancel.configure(state="disabled")

            if exit_code == 0:
                self.status_pill.configure(text="🟢 CONCLUÍDO", text_color="#059669")
                self.lbl_progress_status.configure(text="Auditoria concluída com sucesso! Relatórios exportados.")
                self.data_loader.load_from_directory(r_dir)
                self._refresh_all_views()
                messagebox.showinfo("Auditoria Concluída", f"Auditoria concluída com sucesso!\nRelatórios atualizados em: {r_dir}")
            else:
                self.status_pill.configure(text="🔴 ALERTA", text_color="#DC2626")
                self.lbl_progress_status.configure(text="Auditoria finalizada com alertas ou inconsistências. Verifique logs.")

        elif event_type == "cancelled":
            self.btn_start.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
            self.status_pill.configure(text="⏹ CANCELADO", text_color="gray70")
            self.lbl_progress_status.configure(text=data.get("message", "Operação cancelada."))
            messagebox.showwarning("Auditoria Interrompida", "A auditoria foi interrompida com segurança.")

        elif event_type == "error":
            self.btn_start.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
            self.status_pill.configure(text="❌ ERRO", text_color="#DC2626")
            err_msg = data.get("message", "Erro desconhecido.")
            self.lbl_progress_status.configure(text=f"Erro: {err_msg}")
            messagebox.showerror("Erro na Auditoria", f"Ocorreu um erro durante a auditoria:\n\n{err_msg}")

    # -----------------------------------------------------------------
    # Atualização de Dados e Exportação Excel
    # -----------------------------------------------------------------

    def _reload_data(self) -> None:
        """Recarrega os JSONs do disco e atualiza todas as abas."""
        r_dir = self.var_output_dir.get().strip() or "reports"
        self.data_loader.load_from_directory(r_dir)
        self._refresh_all_views()

    def _refresh_all_views(self) -> None:
        """Atualiza todas as 5 abas com os dados atuais."""
        self.view_summary.refresh_view()
        self.view_duplicates.refresh_view()
        self.view_pending.refresh_view()
        self.view_documents.refresh_view()
        self.view_games.refresh_view()

    def _export_excel(self) -> None:
        """Dispara a exportação para planilha Excel (.xlsx) em thread separada com streaming de baixo consumo."""
        if self.is_exporting:
            messagebox.showinfo("Exportação em Andamento", "Uma exportação já está sendo executada. Aguarde a finalização.")
            return

        if not self.data_loader.is_loaded:
            messagebox.showwarning("Dados Ausentes", "Nenhum relatório encontrado para exportar. Execute a auditoria primeiro.")
            return

        r_dir = self.var_output_dir.get().strip() or "reports"
        default_name = f"relatorio_auditoria_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        initial_dir = os.path.abspath(r_dir)

        # Diálogo nativo para escolha do arquivo de destino
        file_path = filedialog.asksaveasfilename(
            parent=self,
            title="Salvar Relatório Excel Completo",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".xlsx",
            filetypes=[("Planilha Excel (*.xlsx)", "*.xlsx"), ("Todos os Arquivos (*.*)", "*.*")],
        )
        if not file_path:
            return  # Usuário cancelou diálogo

        # Confirmação explícita se o arquivo de destino já existe
        if os.path.exists(file_path):
            confirmed = messagebox.askyesno(
                "Confirmar Substituição",
                f"O arquivo '{os.path.basename(file_path)}' já existe.\n\nDeseja substituí-lo?",
                icon="warning",
                parent=self,
            )
            if not confirmed:
                return

        # Configura estado da interface
        self.is_exporting = True
        self.export_cancel_event = threading.Event()
        self.status_pill.configure(text="📊 EXPORTANDO EXCEL...", text_color="#2563EB")
        self.progress_bar.set(0.05)
        self.lbl_progress_status.configure(text="Iniciando exportação em streaming de baixo consumo de memória...")

        # Thread dedicada para não travar a interface gráfica
        def _run_worker() -> None:
            exporter = ExcelReportExporter(output_dir=r_dir)
            try:
                def _prog_cb(msg: str, frac: float) -> None:
                    self.after(0, lambda m=msg, f=frac: self._update_export_progress(m, f))

                excel_path = exporter.export_from_reports_dir(
                    reports_dir=r_dir,
                    target_filepath=file_path,
                    cancel_event=self.export_cancel_event,
                    progress_callback=_prog_cb,
                )
                self.after(0, lambda p=excel_path: self._on_export_finished(p))
            except ExportCancelledError:
                self.after(0, self._on_export_cancelled)
            except Exception as e:
                self.after(0, lambda err=str(e): self._on_export_error(err))

        self.export_thread = threading.Thread(target=_run_worker, daemon=True, name="ExcelExportThread")
        self.export_thread.start()

    def _update_export_progress(self, message: str, fraction: float) -> None:
        """Atualiza barra de progresso da exportação na thread da GUI."""
        self.lbl_progress_status.configure(text=message)
        self.progress_bar.set(fraction)

    def _on_export_finished(self, excel_path: str) -> None:
        """Callback executado na thread da GUI após conclusão da exportação."""
        self.is_exporting = False
        self.status_pill.configure(text="🟢 PRONTO", text_color="#059669")
        self.progress_bar.set(1.0)
        self.lbl_progress_status.configure(text="Planilha Excel exportada com sucesso!")
        messagebox.showinfo(
            "Exportação Concluída",
            f"Planilha Excel gerada com sucesso via streaming de baixo consumo!\n\nArquivo:\n{os.path.abspath(excel_path)}",
            parent=self,
        )

    def _on_export_cancelled(self) -> None:
        """Callback executado na thread da GUI após cancelamento da exportação."""
        self.is_exporting = False
        self.status_pill.configure(text="⏹ CANCELADO", text_color="gray70")
        self.lbl_progress_status.configure(text="Exportação Excel cancelada. O arquivo anterior foi preservado.")
        messagebox.showwarning("Exportação Cancelada", "A exportação para Excel foi cancelada.", parent=self)

    def _on_export_error(self, err_message: str) -> None:
        """Callback executado na thread da GUI após erro na exportação."""
        self.is_exporting = False
        self.status_pill.configure(text="❌ ERRO", text_color="#DC2626")
        self.lbl_progress_status.configure(text=f"Erro na exportação Excel: {err_message}")
        messagebox.showerror(
            "Erro na Exportação",
            f"Ocorreu um erro durante a geração da planilha Excel:\n\n{err_message}\n\nNenhum arquivo anterior foi corrompido.",
            parent=self,
        )

    def _on_close_window(self) -> None:
        """Trata o encerramento seguro da janela."""
        if (self.worker and self.worker.is_running) or self.is_exporting:
            if messagebox.askyesno(
                "Processo em Andamento",
                "Uma auditoria ou exportação está em execução. Deseja cancelar e sair?",
                parent=self,
            ):
                if self.worker and self.worker.is_running:
                    self.worker.cancel()
                if self.is_exporting and self.export_cancel_event:
                    self.export_cancel_event.set()
                self.destroy()
        else:
            self.destroy()
