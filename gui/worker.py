"""Trabalhador em background (AuditWorker) para execução thread-safe na GUI (Fase 2).

Executa a auditoria completa fora da thread principal do Tkinter, comunicando-se
exclusivamente via queue.Queue para prevenir qualquer congelamento da interface.
"""

from __future__ import annotations

import logging
import queue
import threading
import traceback
from typing import Any, Callable, Dict, Optional

from config import AuditorConfig


class AuditWorker:
    """Gerencia a execução da auditoria em thread separada com cancelamento limpo."""

    def __init__(
        self,
        config: AuditorConfig,
        event_queue: queue.Queue,
    ) -> None:
        self.config = config
        self.event_queue = event_queue
        self.cancel_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.is_running: bool = False

    def start(self) -> None:
        """Inicia a execução da auditoria em segundo plano."""
        if self.is_running:
            return
        self.is_running = True
        self.cancel_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="AuditWorkerThread")
        self.thread.start()

    def cancel(self) -> None:
        """Sinaliza cancelamento gracioso da auditoria."""
        if self.is_running:
            self.cancel_event.set()
            self.event_queue.put(("cancelled", {"message": "Cancelamento solicitado pelo usuário."}))

    def _progress_callback(self, stage: str, message: str, current: int, total: int) -> None:
        """Envia atualização de progresso para a fila thread-safe."""
        self.event_queue.put((
            "progress",
            {
                "stage": stage,
                "message": message,
                "current": current,
                "total": total,
            },
        ))

    def _run(self) -> None:
        """Método executado na thread de trabalho."""
        from main import run_audit

        exit_code = 1
        try:
            self.event_queue.put(("started", {"message": "Auditoria iniciada..."}))

            exit_code = run_audit(
                config=self.config,
                cancel_event=self.cancel_event,
                progress_callback=self._progress_callback,
            )

            if self.cancel_event.is_set():
                self.event_queue.put(("cancelled", {"message": "Auditoria interrompida com sucesso."}))
            elif exit_code == 0:
                self.event_queue.put(("finished", {"exit_code": 0, "reports_dir": self.config.output_dir}))
            else:
                self.event_queue.put(("finished", {"exit_code": exit_code, "reports_dir": self.config.output_dir}))

        except Exception as e:
            tb = traceback.format_exc()
            self.event_queue.put(("error", {"message": str(e), "traceback": tb}))
        finally:
            self.is_running = False
