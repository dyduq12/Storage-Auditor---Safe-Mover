"""Testes automatizados da lógica da interface gráfica e AuditWorker (Fase 2)."""

import os
import queue
import threading
import time
import pytest

from config import AuditorConfig
from gui.worker import AuditWorker
from gui.data_loader import AuditDataLoader


def test_audit_worker_cancellation(tmp_path):
    """Testa cancelamento limpo do AuditWorker sem travar threads."""
    event_q = queue.Queue()
    out_dir = str(tmp_path / "reports_worker")
    config = AuditorConfig(
        c_roots=[str(tmp_path)],
        d_roots=[],
        game_library_paths=[],
        save_candidate_roots=[],
        output_dir=out_dir,
        cache_db_path=str(tmp_path / "cache.db"),
    )

    worker = AuditWorker(config=config, event_queue=event_q)
    assert not worker.is_running

    worker.start()
    assert worker.is_running
    worker.cancel()

    # Aguarda o término da thread
    if worker.thread:
        worker.thread.join(timeout=3.0)

    assert not worker.is_running

    # Drena eventos da fila
    events = []
    while not event_q.empty():
        events.append(event_q.get_nowait())

    event_types = [ev[0] for ev in events]
    assert "started" in event_types
    assert "cancelled" in event_types


def test_gui_app_initialization():
    """Testa se StorageAuditorApp instancia sem disparar auditoria automática."""
    try:
        import tkinter
        tkinter.Tk()
    except Exception as e:
        pytest.skip(f"Ambiente headless sem suporte a display gráfico Tkinter: {e}")

    from gui.app import StorageAuditorApp

    app = StorageAuditorApp()
    # Verifica que worker está ocioso
    assert app.worker is None
    # Verifica que abas foram criadas
    assert hasattr(app, "tabview")
    assert hasattr(app, "view_summary")
    assert hasattr(app, "view_duplicates")
    assert hasattr(app, "view_pending")
    assert hasattr(app, "view_documents")
    assert hasattr(app, "view_games")

    # Destrói a janela para liberar recursos
    app.destroy()
