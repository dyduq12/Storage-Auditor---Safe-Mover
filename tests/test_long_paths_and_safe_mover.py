import hashlib
import os
import sys
import uuid
import pytest
from pathlib import Path

from core.action_plan import ActionOrder, ActionPlan
from core.path_utils import to_extended_path, from_extended_path
from core.safe_mover import SafeCopyExecutor, HashMismatchError, TargetConflictError


def _compute_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(to_extended_path(path), "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestPathUtils:
    """Testes unitários para a camada de caminhos longos (path_utils)."""

    def test_relative_paths_untouched(self):
        assert to_extended_path("relative/path/file.txt") == "relative/path/file.txt"
        assert to_extended_path("file.txt") == "file.txt"
        assert to_extended_path("") == ""

    def test_absolute_drive_path(self):
        if os.name == "nt":
            path = r"C:\Users\Eduardo\Documents\file.txt"
            ext = to_extended_path(path)
            assert ext.startswith(r"\\?\C:")
            assert ext == r"\\?\C:\Users\Eduardo\Documents\file.txt"
            # Idempotente
            assert to_extended_path(ext) == ext
            # Conversão reversa
            assert from_extended_path(ext) == path

    def test_unc_path(self):
        if os.name == "nt":
            unc = r"\\server\share\folder\file.txt"
            ext = to_extended_path(unc)
            assert ext.startswith(r"\\?\UNC\server\share")
            assert from_extended_path(ext) == unc


class TestLongPathsSafeMoverSynthetic:
    """Testes sintéticos isolados em tmp_path com caminhos > 260 caracteres."""

    def test_copy_long_path_over_260_chars_and_verify_source_intact(self, tmp_path):
        """Testa cópia segura com caminhos de origem e destino superiores a 260 caracteres."""
        # Cria uma árvore de pastas profunda com mais de 280 caracteres
        sub_dirs = ["deep_level_" + ("x" * 25) for _ in range(8)]
        src_dir = tmp_path / "source_tree" / Path(*sub_dirs)
        os.makedirs(to_extended_path(str(src_dir)), exist_ok=True)

        src_file = src_dir / ("sample_test_source_" + ("s" * 20) + ".dat")
        src_str = str(src_file)

        # Garante que o caminho da origem tem > 260 caracteres
        assert len(src_str) >= 260, f"Caminho de origem muito curto: {len(src_str)}"

        content = b"Conteudo confidencial e integro para teste de caminhos longos " * 100
        with open(to_extended_path(src_str), "wb") as f:
            f.write(content)

        st_initial = os.stat(to_extended_path(src_str))
        src_hash_initial = _compute_hash(src_str)
        src_size_initial = st_initial.st_size
        src_mtime_initial = st_initial.st_mtime

        # Cria árvore de destino também com > 280 caracteres
        dst_sub_dirs = ["backup_dest_" + ("y" * 25) for _ in range(8)]
        dst_dir = tmp_path / "target_tree" / Path(*dst_sub_dirs)
        dst_file = dst_dir / ("target_backup_file_" + ("t" * 20) + ".dat")
        dst_str = str(dst_file)

        assert len(dst_str) >= 260, f"Caminho de destino muito curto: {len(dst_str)}"

        plan = ActionPlan(
            plan_id="plan_synthetic_test_long_path",
            created_at="2026-10-12T17:00:00",
            source_reports_dir=str(tmp_path),
            target_base_dir=str(tmp_path / "target_tree"),
            totais={"total_ordens": 1, "tamanho_total_bytes": src_size_initial, "espaco_suficiente": True},
            ordens=[],
            status_aprovacao="aprovado",
        )

        order = ActionOrder(
            order_id="ord_synthetic_001",
            source_path=src_str,
            target_path=dst_str,
            expected_size_bytes=src_size_initial,
            expected_mtime=src_mtime_initial,
            expected_sha256=src_hash_initial,
        )
        plan.ordens.append(order)

        executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path / "logs"))

        # Executa cópia
        executor.copy_single_order(order)

        assert order.status == "completed"
        assert order.error is None
        assert os.path.exists(to_extended_path(dst_str))
        assert _compute_hash(dst_str) == src_hash_initial

        # VERIFICAÇÃO CRÍTICA: A ORIGEM PERMANECEU 100% INTACTA
        st_after = os.stat(to_extended_path(src_str))
        src_hash_after = _compute_hash(src_str)

        assert st_after.st_size == src_size_initial, "O tamanho da origem foi alterado!"
        assert abs(st_after.st_mtime - src_mtime_initial) < 0.001, "O mtime da origem foi alterado!"
        assert src_hash_after == src_hash_initial, "O hash SHA-256 da origem foi alterado!"

        # TESTE DE RETOMADA (RESUME): Não deve duplicar cópia nem alterar nada
        completed_count_before = executor.completed_orders
        executor.copy_single_order(order)
        assert executor.completed_orders == completed_count_before + 1
        assert _compute_hash(dst_str) == src_hash_initial

    def test_conflict_on_preexisting_different_target(self, tmp_path):
        """Testa que destino pré-existente diferente causa erro de conflito sem sobrescrita."""
        src_file = tmp_path / "origem.txt"
        src_str = str(src_file)
        with open(to_extended_path(src_str), "wb") as f:
            f.write(b"origem dados")

        st_src = os.stat(to_extended_path(src_str))
        src_hash = _compute_hash(src_str)

        dst_file = tmp_path / "destino.txt"
        dst_str = str(dst_file)
        with open(to_extended_path(dst_str), "wb") as f:
            f.write(b"outro conteudo diferente")

        plan = ActionPlan(
            plan_id="plan_conflict_test",
            created_at="2026-10-12T17:00:00",
            source_reports_dir=str(tmp_path),
            target_base_dir=str(tmp_path),
            totais={"total_ordens": 1, "tamanho_total_bytes": st_src.st_size, "espaco_suficiente": True},
            ordens=[],
            status_aprovacao="aprovado",
        )

        order = ActionOrder(
            order_id="ord_conflict_001",
            source_path=src_str,
            target_path=dst_str,
            expected_size_bytes=st_src.st_size,
            expected_mtime=st_src.st_mtime,
            expected_sha256=src_hash,
        )
        plan.ordens.append(order)

        executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path / "logs"))
        executor.copy_single_order(order)

        assert order.status == "conflict"
        # Destino NÃO pode ter sido sobrescrito
        with open(to_extended_path(dst_str), "rb") as f:
            assert f.read() == b"outro conteudo diferente"

        # Origem permanece intacta
        assert _compute_hash(src_str) == src_hash

    def test_cleanup_temp_file_on_hash_mismatch(self, tmp_path):
        """Testa que se o hash divergir, o temporário é removido e destino não é criado."""
        src_file = tmp_path / "origem_mismatch.txt"
        src_str = str(src_file)
        with open(to_extended_path(src_str), "wb") as f:
            f.write(b"conteudo original")

        st_src = os.stat(to_extended_path(src_str))
        src_hash = _compute_hash(src_str)

        dst_dir = tmp_path / "target_mismatch"
        dst_str = str(dst_dir / "arquivo_final.txt")

        plan = ActionPlan(
            plan_id="plan_mismatch_test",
            created_at="2026-10-12T17:00:00",
            source_reports_dir=str(tmp_path),
            target_base_dir=str(tmp_path),
            totais={"total_ordens": 1, "tamanho_total_bytes": st_src.st_size, "espaco_suficiente": True},
            ordens=[],
            status_aprovacao="aprovado",
        )

        # Fornece um hash esperado intencionalmente falso para simular divergência
        order = ActionOrder(
            order_id="ord_mismatch_001",
            source_path=src_str,
            target_path=dst_str,
            expected_size_bytes=st_src.st_size,
            expected_mtime=st_src.st_mtime,
            expected_sha256="0000000000000000000000000000000000000000000000000000000000000000",
        )
        plan.ordens.append(order)

        executor = SafeCopyExecutor(plan=plan, log_dir=str(tmp_path / "logs"))
        executor.copy_single_order(order)

        assert order.status == "failed"
        assert "diverge do hash prévio" in str(order.error)
        assert not os.path.exists(to_extended_path(dst_str))

        # Confirma que nenhum arquivo temporário ficou órfão no diretório
        if os.path.exists(to_extended_path(str(dst_dir))):
            assert len(os.listdir(to_extended_path(str(dst_dir)))) == 0

        # Origem permanece intacta
        assert _compute_hash(src_str) == src_hash
