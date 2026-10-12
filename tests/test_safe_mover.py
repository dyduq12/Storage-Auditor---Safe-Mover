"""Testes automatizados do Safe Mover (Fase 3: Cópia Segura Não-Destrutiva).

Garante que todos os cenários utilizem estritamente pastas temporárias sintéticas,
validem o hash SHA-256 e NUNCA removam ou alterem arquivos de origem.
"""

import hashlib
import json
import os
import threading
import time
import pytest

from core.action_plan import ActionOrder, ActionPlan
from core.safe_mover import (
    CopyCancelledError,
    HashMismatchError,
    InsufficientSpaceError,
    SafeCopyExecutor,
)


def _compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def test_safe_copy_full_cycle_success_preserves_source(tmp_path):
    """Testa cópia completa de arquivos com validação SHA-256 e preservação estrita da origem."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    log_dir = tmp_path / "logs"
    src_dir.mkdir()
    tgt_dir.mkdir()
    log_dir.mkdir()

    # Cria arquivo sintético na origem
    src_file = src_dir / "documento_importante.pdf"
    content = b"CONTEUDO_ORIGINAL_NAO_DEVE_SER_MODIFICADO" * 500
    src_file.write_bytes(content)

    src_stat = os.stat(str(src_file))
    src_hash = _compute_sha256(str(src_file))
    src_mtime = src_stat.st_mtime
    src_size = src_stat.st_size

    tgt_file = tgt_dir / "documento_importante.pdf"

    order = ActionOrder(
        order_id="ord_test_01",
        source_path=str(src_file),
        target_path=str(tgt_file),
        expected_size_bytes=src_size,
        expected_mtime=src_mtime,
        expected_sha256=src_hash,
        fingerprint_confidence="completo",
        category="documento_avulso_critico",
        reason="Teste de cópia bem-sucedida",
    )

    plan = ActionPlan(
        plan_id="plan_test_success",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": src_size, "espaco_necessario_com_margem_bytes": 0},
        ordens=[order],
    )

    executor = SafeCopyExecutor(plan=plan, log_dir=str(log_dir))
    summary = executor.execute_all()

    assert summary["completadas"] == 1
    assert summary["falhas"] == 0
    assert order.status == "completed"

    # 1. Valida integridade do arquivo copiado no destino
    assert tgt_file.exists()
    assert _compute_sha256(str(tgt_file)) == src_hash

    # 2. ASSERÇÃO CRÍTICA: Origem deve permanecer 100% idêntica e intacta
    assert src_file.exists()
    assert src_file.read_bytes() == content
    assert os.stat(str(src_file)).st_mtime == src_mtime
    assert os.stat(str(src_file)).st_size == src_size

    # 3. Nenhum temporário órfão deve restar
    temp_files = [f for f in os.listdir(str(tgt_dir)) if f.startswith(".~tmp_")]
    assert len(temp_files) == 0


def test_safe_copy_aborts_on_existing_target(tmp_path):
    """Testa que destino pré-existente aborta com conflito e NUNCA é sobrescrito."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    src_dir.mkdir()
    tgt_dir.mkdir()

    src_file = src_dir / "arquivo.txt"
    src_file.write_bytes(b"DADOS_ORIGEM")

    tgt_file = tgt_dir / "arquivo.txt"
    existing_dest_content = b"DADOS_PRE_EXISTENTES_NO_DESTINO"
    tgt_file.write_bytes(existing_dest_content)

    order = ActionOrder(
        order_id="ord_conflict",
        source_path=str(src_file),
        target_path=str(tgt_file),
        expected_size_bytes=len(b"DADOS_ORIGEM"),
        expected_mtime=os.stat(str(src_file)).st_mtime,
    )

    plan = ActionPlan(
        plan_id="plan_conflict",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": 100, "espaco_necessario_com_margem_bytes": 0},
        ordens=[order],
    )

    executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path))
    summary = executor.execute_all()

    assert summary["completadas"] == 0
    assert summary["falhas"] == 1
    assert order.status in ("conflict", "failed")
    assert "Destino já existe" in str(order.error)

    # Verifica que o destino NÃO foi sobrescrito
    assert tgt_file.read_bytes() == existing_dest_content

    # Verifica que a origem está intacta
    assert src_file.read_bytes() == b"DADOS_ORIGEM"


def test_safe_copy_aborts_on_mutated_source(tmp_path):
    """Testa aborto quando a origem foi alterada após o plano ser criado."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    src_dir.mkdir()
    tgt_dir.mkdir()

    src_file = src_dir / "mutante.txt"
    src_file.write_bytes(b"VERSAO_ORIGINAL")
    st = os.stat(str(src_file))

    order = ActionOrder(
        order_id="ord_mutated",
        source_path=str(src_file),
        target_path=str(tgt_dir / "mutante.txt"),
        expected_size_bytes=st.st_size,
        expected_mtime=st.st_mtime,
    )

    # Altera o arquivo de origem (mutação pós-auditoria)
    time.sleep(0.01)
    src_file.write_bytes(b"CONTEUDO_MODIFICADO_DEPOIS_DO_PLANO")

    plan = ActionPlan(
        plan_id="plan_mutated",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": 100, "espaco_necessario_com_margem_bytes": 0},
        ordens=[order],
    )

    executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path))
    summary = executor.execute_all()

    assert summary["completadas"] == 0
    assert summary["falhas"] == 1
    assert order.status in ("mutated", "failed")
    assert not (tgt_dir / "mutante.txt").exists()


def test_safe_copy_aborts_and_cleans_on_hash_mismatch(tmp_path, monkeypatch):
    """Testa aborto e limpeza do temporário quando há divergência de SHA-256."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    src_dir.mkdir()
    tgt_dir.mkdir()

    src_file = src_dir / "corrompido.txt"
    src_file.write_bytes(b"CONTEUDO_BOM")

    st = os.stat(str(src_file))

    order = ActionOrder(
        order_id="ord_corrupt",
        source_path=str(src_file),
        target_path=str(tgt_dir / "corrompido.txt"),
        expected_size_bytes=st.st_size,
        expected_mtime=st.st_mtime,
        expected_sha256="HASH_ERRADO_ESPERADO_INCORRETO",  # força falha de validação
    )

    plan = ActionPlan(
        plan_id="plan_corrupt",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": 100, "espaco_necessario_com_margem_bytes": 0},
        ordens=[order],
    )

    executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path))
    summary = executor.execute_all()

    assert summary["completadas"] == 0
    assert summary["falhas"] == 1
    assert order.status == "failed"

    # Destino final nunca foi criado
    assert not (tgt_dir / "corrompido.txt").exists()

    # Nenhum arquivo temporário residual
    temp_files = [f for f in os.listdir(str(tgt_dir)) if f.startswith(".~tmp_")]
    assert len(temp_files) == 0

    # Origem intocada
    assert src_file.read_bytes() == b"CONTEUDO_BOM"


def test_safe_copy_cancellation_cleans_temp_only(tmp_path):
    """Testa cancelamento durante cópia: limpa SOMENTE temporário e preserva origem intacta."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    src_dir.mkdir()
    tgt_dir.mkdir()

    # Arquivo grande para permitir interceptação de cancelamento
    src_file = src_dir / "grande.bin"
    src_file.write_bytes(b"A" * (2 * 1024 * 1024))  # 2 MB

    st = os.stat(str(src_file))
    tgt_file = tgt_dir / "grande.bin"

    order = ActionOrder(
        order_id="ord_cancel",
        source_path=str(src_file),
        target_path=str(tgt_file),
        expected_size_bytes=st.st_size,
        expected_mtime=st.st_mtime,
    )

    plan = ActionPlan(
        plan_id="plan_cancel",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": st.st_size, "espaco_necessario_com_margem_bytes": 0},
        ordens=[order],
    )

    cancel_event = threading.Event()
    cancel_event.set()  # Cancela antes ou no primeiro chunk

    executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path), cancel_event=cancel_event)
    summary = executor.execute_all()

    assert summary["canceladas"] >= 1
    assert not tgt_file.exists()

    # Nenhum temporário órfão
    temp_files = [f for f in os.listdir(str(tgt_dir)) if f.startswith(".~tmp_")]
    assert len(temp_files) == 0

    # Origem intacta
    assert src_file.exists()
    assert src_file.stat().st_size == 2 * 1024 * 1024


def test_safe_copy_insufficient_space_detection(tmp_path, monkeypatch):
    """Testa detecção e bloqueio de execução por espaço livre insuficiente no destino."""
    import shutil

    # Simula disk_usage retornando pouco espaço livre
    class MockUsage:
        free = 100
        total = 1000
        used = 900

    monkeypatch.setattr(shutil, "disk_usage", lambda path: MockUsage())

    order = ActionOrder(
        order_id="ord_space",
        source_path="C:\\dummy.txt",
        target_path="D:\\dummy.txt",
        expected_size_bytes=5000,
        expected_mtime=1600000000.0,
    )

    plan = ActionPlan(
        plan_id="plan_space",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(tmp_path),
        target_base_dir=str(tmp_path),
        totais={"espaco_necessario_com_margem_bytes": 100000},
        ordens=[order],
    )

    executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path))
    with pytest.raises(InsufficientSpaceError):
        executor.execute_all()


def test_safe_copy_permission_error_resilience(tmp_path, monkeypatch):
    """Testa se erro de permissão de escrita limpa o temporário e falha sem travar."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    src_dir.mkdir()
    tgt_dir.mkdir()

    src_file = src_dir / "arquivo_perm.txt"
    src_file.write_bytes(b"DADOS_ORIGINAIS_PRESERVADOS")

    order = ActionOrder(
        order_id="ord_perm",
        source_path=str(src_file),
        target_path=str(tgt_dir / "arquivo_perm.txt"),
        expected_size_bytes=len(b"DADOS_ORIGINAIS_PRESERVADOS"),
        expected_mtime=os.stat(str(src_file)).st_mtime,
    )

    plan = ActionPlan(
        plan_id="plan_perm",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": 100, "espaco_necessario_com_margem_bytes": 0},
        ordens=[order],
    )

    # Simula erro de permissão na abertura do arquivo temporário
    orig_open = open
    def mock_open(file, mode="r", *args, **kwargs):
        if "wb" in mode and ".~tmp_safecopy_" in str(file):
            raise PermissionError("Acesso negado simulado para teste de permissão.")
        return orig_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)

    executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path))
    summary = executor.execute_all()

    assert summary["completadas"] == 0
    assert summary["falhas"] == 1
    assert order.status == "failed"
    assert "Acesso negado" in str(order.error)

    # Origem intocada
    assert src_file.read_bytes() == b"DADOS_ORIGINAIS_PRESERVADOS"


def test_source_never_deleted_or_modified_in_any_scenario(tmp_path):
    """Garante que em nenhum cenário (sucesso, conflito, erro) a origem seja apagada ou modificada."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    src_dir.mkdir()
    tgt_dir.mkdir()

    # Cria 3 arquivos de teste
    f1 = src_dir / "f1.txt"
    f2 = src_dir / "f2.txt"
    f3 = src_dir / "f3.txt"
    c1, c2, c3 = b"CONTEUDO_1", b"CONTEUDO_2", b"CONTEUDO_3"
    f1.write_bytes(c1)
    f2.write_bytes(c2)
    f3.write_bytes(c3)

    # f2 terá conflito no destino pré-existente
    (tgt_dir / "f2.txt").write_bytes(b"JA_EXISTE_NO_DESTINO")

    orders = [
        ActionOrder("ord_1", str(f1), str(tgt_dir / "f1.txt"), len(c1), os.stat(str(f1)).st_mtime),
        ActionOrder("ord_2", str(f2), str(tgt_dir / "f2.txt"), len(c2), os.stat(str(f2)).st_mtime),
        ActionOrder("ord_3", str(f3), str(tgt_dir / "f3.txt"), len(c3), os.stat(str(f3)).st_mtime, expected_sha256="ERRO_HASH"),
    ]

    plan = ActionPlan(
        plan_id="plan_audit_protection",
        created_at="2026-10-12T00:00:00",
        source_reports_dir=str(src_dir),
        target_base_dir=str(tgt_dir),
        totais={"tamanho_total_bytes": 100, "espaco_necessario_com_margem_bytes": 0},
        ordens=orders,
    )

    executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path))
    summary = executor.execute_all()

    # ord_1 teve sucesso
    assert orders[0].status == "completed"
    # ord_2 teve conflito
    assert orders[1].status in ("conflict", "failed")
    # ord_3 teve falha de hash
    assert orders[2].status == "failed"

    # Confirma que TODOS os arquivos de origem continuam intactos com tamanho e bytes exatos
    assert f1.exists() and f1.read_bytes() == c1
    assert f2.exists() and f2.read_bytes() == c2
    assert f3.exists() and f3.read_bytes() == c3
