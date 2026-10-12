"""Testes automatizados da Fase 4: Cópia Segura em Lotes Controlados e Auditoria Dry-Run.

Garante que todos os testes executem estritamente em pastas temporárias sintéticas,
validem o particionamento em lotes, a retomada transparente (resume), e PROVEM por
snapshots matemáticos de SHA-256 e atributos que a origem permanece 100% idêntica e intacta.
"""

import hashlib
import json
import os
import shutil
import threading
import time
from typing import Dict, Tuple
import pytest

from core.action_plan import ActionOrder, ActionPlan
from core.safe_mover import (
    CopyCancelledError,
    HashMismatchError,
    InsufficientSpaceError,
    SafeCopyExecutor,
)


def _file_snapshot(directory: str) -> Dict[str, Tuple[int, float, str]]:
    """Gera um snapshot matemático estrito de todos os arquivos de um diretório:

    Mapeia caminho_relativo -> (tamanho_bytes, mtime, sha256_hex)
    """
    snapshot = {}
    for root, _, files in os.walk(directory):
        for f in files:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, directory)
            st = os.stat(full_path)
            h = hashlib.sha256()
            with open(full_path, "rb") as fp:
                while chunk := fp.read(65536):
                    h.update(chunk)
            snapshot[rel_path] = (st.st_size, st.st_mtime, h.hexdigest())
    return snapshot


def test_batch_execution_controlled_chunks_and_resume(tmp_path):
    """Testa execução controlada em lotes pequenos com retomada (resume) e prova de origem intacta."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    log_dir = tmp_path / "logs"
    src_dir.mkdir()
    tgt_dir.mkdir()
    log_dir.mkdir()

    # Cria 7 arquivos sintéticos na origem
    orders = []
    total_bytes = 0
    for i in range(1, 8):
        f = src_dir / f"file_{i:02d}.dat"
        content = f"DADOS_ARQUIVO_{i}_SEGURO_E_INTACTO\n".encode("utf-8") * (100 * i)
        f.write_bytes(content)
        st = os.stat(str(f))
        h = hashlib.sha256(content).hexdigest()
        total_bytes += len(content)

        order = ActionOrder(
            order_id=f"ord_{i:02d}",
            source_path=str(f),
            target_path=str(tgt_dir / f"file_{i:02d}.dat"),
            expected_size_bytes=st.st_size,
            expected_mtime=st.st_mtime,
            expected_sha256=h,
            fingerprint_confidence="completo",
            category="pendente_backup",
        )
        orders.append(order)

    # 1. Snapshot da origem antes de qualquer operação
    snapshot_before = _file_snapshot(str(src_dir))
    assert len(snapshot_before) == 7

    plan = ActionPlan(
        plan_id="plan_batches_test",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": total_bytes, "espaco_necessario_com_margem_bytes": 0},
        ordens=orders,
    )

    executor = SafeCopyExecutor(plan=plan, log_dir=str(log_dir))

    # 2. Executa Lote 1 (tamanho = 3)
    b1 = executor.execute_batch(batch_size=3)
    assert b1["ordens_processadas_no_lote"] == 3
    assert b1["completadas_no_lote"] == 3
    assert b1["ordens_restantes_pendentes"] == 4
    assert not b1["plano_totalmente_concluido"]

    # Verifica que exatamente 3 arquivos existem no destino
    copied_dst_1 = [f for f in os.listdir(str(tgt_dir)) if not f.startswith(".~tmp_")]
    assert len(copied_dst_1) == 3

    # Prova de integridade da origem após Lote 1
    assert _file_snapshot(str(src_dir)) == snapshot_before

    # 3. Executa Lote 2 (tamanho = 3)
    b2 = executor.execute_batch(batch_size=3)
    assert b2["ordens_processadas_no_lote"] == 3
    assert b2["completadas_no_lote"] == 3
    assert b2["ordens_restantes_pendentes"] == 1

    # Verifica 6 arquivos no destino
    copied_dst_2 = [f for f in os.listdir(str(tgt_dir)) if not f.startswith(".~tmp_")]
    assert len(copied_dst_2) == 6

    # Prova de integridade da origem após Lote 2
    assert _file_snapshot(str(src_dir)) == snapshot_before

    # 4. Executa Lote 3 (tamanho = 3, mas resta apenas 1)
    b3 = executor.execute_batch(batch_size=3)
    assert b3["ordens_processadas_no_lote"] == 1
    assert b3["completadas_no_lote"] == 1
    assert b3["ordens_restantes_pendentes"] == 0
    assert b3["plano_totalmente_concluido"]

    # Todos os 7 arquivos copiados no destino com hashes corretos
    for i in range(1, 8):
        dst_file = tgt_dir / f"file_{i:02d}.dat"
        assert dst_file.exists()
        assert hashlib.sha256(dst_file.read_bytes()).hexdigest() == orders[i - 1].expected_sha256

    # 5. Testa RETOMADA (RESUME): novo executor sobre o mesmo plano concluído
    # Deve identificar que todos os 7 arquivos já estão completos e não duplicar/falhar
    executor_resume = SafeCopyExecutor(plan=plan, log_dir=str(log_dir))
    b_resume = executor_resume.execute_batch(batch_size=5)
    assert b_resume["completadas_no_lote"] == 0
    assert b_resume["total_geral_completadas"] == 7
    assert b_resume["ordens_restantes_pendentes"] == 0

    # Nenhum arquivo temporário residual
    temp_files = [f for f in os.listdir(str(tgt_dir)) if f.startswith(".~tmp_")]
    assert len(temp_files) == 0

    # PROVA FINAL: Snapshot da origem permanece 100% idêntico
    snapshot_after = _file_snapshot(str(src_dir))
    assert snapshot_after == snapshot_before


def test_audit_plan_dry_run_comprehensive(tmp_path):
    """Testa a auditoria prévia do Action Plan sem copiar nenhum arquivo."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    log_dir = tmp_path / "logs"
    src_dir.mkdir()
    tgt_dir.mkdir()
    log_dir.mkdir()

    # 1. Arquivo normal pronto
    f1 = src_dir / "pronto.txt"
    f1.write_bytes(b"CONTEUDO_PRONTO")
    st1 = os.stat(str(f1))

    # 2. Arquivo que terá conflito no destino (destino existe com conteúdo diferente)
    f2 = src_dir / "conflito.txt"
    f2.write_bytes(b"ORIGEM_CONFLITO")
    st2 = os.stat(str(f2))
    (tgt_dir / "conflito.txt").write_bytes(b"DESTINO_DIFERENTE_JA_EXISTENTE")

    # 3. Arquivo já concluído (destino existe idêntico em tamanho e hash)
    f3 = src_dir / "ja_feito.txt"
    f3.write_bytes(b"JA_CONCLUIDO_100_PORCENTO")
    st3 = os.stat(str(f3))
    h3 = hashlib.sha256(b"JA_CONCLUIDO_100_PORCENTO").hexdigest()
    (tgt_dir / "ja_feito.txt").write_bytes(b"JA_CONCLUIDO_100_PORCENTO")

    # 4. Arquivo com mutação na origem
    f4 = src_dir / "mutado.txt"
    f4.write_bytes(b"CONTEUDO_INICIAL")
    st4 = os.stat(str(f4))
    f4.write_bytes(b"CONTEUDO_MODIFICADO_SUBITAMENTE")

    # 5. Arquivo inexistente na origem
    f5 = src_dir / "inexistente.txt"

    orders = [
        ActionOrder("ord_1", str(f1), str(tgt_dir / "pronto.txt"), st1.st_size, st1.st_mtime),
        ActionOrder("ord_2", str(f2), str(tgt_dir / "conflito.txt"), st2.st_size, st2.st_mtime),
        ActionOrder("ord_3", str(f3), str(tgt_dir / "ja_feito.txt"), st3.st_size, st3.st_mtime, expected_sha256=h3),
        ActionOrder("ord_4", str(f4), str(tgt_dir / "mutado.txt"), st4.st_size, st4.st_mtime),
        ActionOrder("ord_5", str(f5), str(tgt_dir / "inexistente.txt"), 100, 1600000000.0),
    ]

    recusadas_fake = [
        {"caminho_origem": "C:\\fake\\heur.sav", "motivo_recusa": "Save heurístico", "categoria": "save_heuristico"}
    ]

    plan = ActionPlan(
        plan_id="plan_dry_run_test",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": 1000, "espaco_necessario_com_margem_bytes": 0},
        ordens=orders,
        ordens_recusadas=recusadas_fake,
    )

    snapshot_src_before = _file_snapshot(str(src_dir))

    executor = SafeCopyExecutor(plan=plan, log_dir=str(log_dir))
    audit = executor.audit_plan_dry_run()

    # Validações estritas dos números da auditoria dry-run
    assert audit["total_ordens_no_plano"] == 5
    assert audit["ordens_prontas"] == 1  # apenas ord_1
    assert audit["conflitos_destino"] == 1  # ord_2
    assert audit["ordens_ja_concluidas"] == 1  # ord_3
    assert audit["origens_mutadas_ou_inexistentes"] == 2  # ord_4 e ord_5
    assert audit["total_ordens_recusadas"] == 1

    # NENHUM arquivo foi criado no destino (pronto.txt NÃO deve existir ainda)
    assert not (tgt_dir / "pronto.txt").exists()

    # Prova de integridade absoluta da origem
    assert _file_snapshot(str(src_dir)) == snapshot_src_before


def test_batch_cancellation_cleans_temp_and_preserves_source(tmp_path):
    """Testa cancelamento no meio de um lote: limpa SOMENTE temporário e preserva origem intacta."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    log_dir = tmp_path / "logs"
    src_dir.mkdir()
    tgt_dir.mkdir()
    log_dir.mkdir()

    f1 = src_dir / "f1.bin"
    f2 = src_dir / "f2.bin"
    c1 = b"X" * (1024 * 1024)
    c2 = b"Y" * (1024 * 1024)
    f1.write_bytes(c1)
    f2.write_bytes(c2)

    orders = [
        ActionOrder("ord_1", str(f1), str(tgt_dir / "f1.bin"), len(c1), os.stat(str(f1)).st_mtime),
        ActionOrder("ord_2", str(f2), str(tgt_dir / "f2.bin"), len(c2), os.stat(str(f2)).st_mtime),
    ]

    plan = ActionPlan(
        plan_id="plan_batch_cancel",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": len(c1) + len(c2), "espaco_necessario_com_margem_bytes": 0},
        ordens=orders,
    )

    cancel_event = threading.Event()
    cancel_event.set()  # Cancela imediatamente

    snapshot_before = _file_snapshot(str(src_dir))

    executor = SafeCopyExecutor(plan=plan, log_dir=str(log_dir), cancel_event=cancel_event)
    with pytest.raises(CopyCancelledError):
        executor.execute_batch(batch_size=2)

    # Nenhum temporário residual
    temp_files = [f for f in os.listdir(str(tgt_dir)) if f.startswith(".~tmp_")]
    assert len(temp_files) == 0

    # Origem intocada
    assert _file_snapshot(str(src_dir)) == snapshot_before


def test_batch_insufficient_space_blocks_batch(tmp_path, monkeypatch):
    """Testa se falta de espaço para o lote bloqueia antes de gravar dados."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    src_dir.mkdir()
    tgt_dir.mkdir()

    f = src_dir / "arquivo.bin"
    f.write_bytes(b"A" * 5000)

    class MockUsage:
        free = 100  # Menos que os 5000 bytes requeridos
        total = 10000
        used = 9900

    monkeypatch.setattr(shutil, "disk_usage", lambda path: MockUsage())

    order = ActionOrder("ord_sp", str(f), str(tgt_dir / "arquivo.bin"), 5000, os.stat(str(f)).st_mtime)
    plan = ActionPlan(
        plan_id="plan_sp",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": 5000, "espaco_necessario_com_margem_bytes": 0},
        ordens=[order],
    )

    snapshot_before = _file_snapshot(str(src_dir))

    executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path))
    with pytest.raises(InsufficientSpaceError):
        executor.execute_batch(batch_size=1)

    assert not (tgt_dir / "arquivo.bin").exists()
    assert _file_snapshot(str(src_dir)) == snapshot_before
