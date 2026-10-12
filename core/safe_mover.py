"""Módulo Safe Mover (Cópia Segura Não-Destrutiva) da Fase 3.

Executor controlado exclusivamente por Action Plan:
- 100% NÃO-DESTRUTIVO: a origem NUNCA é removida, renomeada ou truncada.
- Pre-flight imediato por ordem: revalida existência, tamanho e mtime da origem.
- Detecção e bloqueio de conflito: nunca sobrescreve destino pré-existente.
- Cópia para arquivo temporário exclusivo (.~tmp_safecopy_*) com SHA-256 dual simultâneo.
- Validação estrita de integridade (tamanho + hash de origem vs destino).
- Renomeação atômica do temporário para o destino final (sem sobrescrever).
- Limpeza segura: em falha, cancelamento ou divergência, remove SOMENTE o temporário.
- Auditoria append-only por evento (.jsonl) e relatório consolidado (.json).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.action_plan import ActionOrder, ActionPlan


class SafeMoverError(Exception):
    """Exceção base de erro no Safe Mover."""
    pass


class CopyCancelledError(SafeMoverError):
    """Exceção lançada quando a cópia é cancelada cooperativamente pelo usuário."""
    pass


class SourceMutatedError(SafeMoverError):
    """Exceção lançada quando o arquivo de origem sofreu alteração pós-auditoria."""
    pass


class TargetConflictError(SafeMoverError):
    """Exceção lançada quando o arquivo de destino já existe (nunca sobrescreve)."""
    pass


class HashMismatchError(SafeMoverError):
    """Exceção lançada quando o hash SHA-256 do destino diverge do hash lido da origem."""
    pass


class InsufficientSpaceError(SafeMoverError):
    """Exceção lançada quando o espaço livre no destino é insuficiente."""
    pass


BUFFER_SIZE: int = 1024 * 1024  # 1 MB por bloco de I/O


class SafeCopyExecutor:
    """Executor de cópias seguras não-destrutivas orientado a Action Plan."""

    def __init__(
        self,
        plan: ActionPlan,
        log_dir: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.plan = plan
        self.log_dir = os.path.abspath(log_dir or plan.source_reports_dir)
        self.cancel_event = cancel_event
        self.progress_callback = progress_callback

        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file_path = os.path.join(
            self.log_dir,
            f"execution_log_{plan.plan_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        )

        self.total_orders = len(plan.ordens)
        self.completed_orders = 0
        self.failed_orders = 0
        self.skipped_orders = 0
        self.cancelled_orders = 0
        self.total_bytes_copied = 0

    def _log_event(
        self,
        event_type: str,
        order: Optional[ActionOrder] = None,
        bytes_copied: int = 0,
        sha256: Optional[str] = None,
        duration_ms: float = 0.0,
        error: Optional[str] = None,
    ) -> None:
        """Registra evento estruturado no log append-only (.jsonl)."""
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "event": event_type,
            "order_id": order.order_id if order else None,
            "source_path": order.source_path if order else None,
            "target_path": order.target_path if order else None,
            "bytes_copied": bytes_copied,
            "sha256": sha256,
            "duration_ms": round(duration_ms, 2),
            "error": error,
        }
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _report_progress(self, current_order: Optional[ActionOrder], stage: str) -> None:
        """Dispara callback de progresso thread-safe se registrado."""
        if self.progress_callback:
            data = {
                "stage": stage,
                "current_order_id": current_order.order_id if current_order else None,
                "completed": self.completed_orders,
                "failed": self.failed_orders,
                "skipped": self.skipped_orders,
                "total": self.total_orders,
                "bytes_copied": self.total_bytes_copied,
                "total_bytes": self.plan.totais.get("tamanho_total_bytes", 0),
            }
            self.progress_callback(data)

    def preflight_order(self, order: ActionOrder) -> Tuple[bool, str]:
        """Revalidação estrita prévia da ordem imediatamente antes da cópia."""
        src = order.source_path
        tgt = order.target_path

        # 1. Origem deve existir e ser um arquivo regular
        if not os.path.exists(src):
            return False, f"Origem não existe: '{src}'"

        if not os.path.isfile(src) or os.path.islink(src):
            return False, f"Origem não é um arquivo regular: '{src}'"

        # 2. Revalida tamanho e mtime com a auditoria
        try:
            st = os.stat(src)
            current_size = st.st_size
            current_mtime = st.st_mtime
        except OSError as e:
            return False, f"Falha ao obter metadados da origem: {e}"

        if current_size != order.expected_size_bytes:
            return False, f"Tamanho da origem foi alterado (esperado: {order.expected_size_bytes}, atual: {current_size})"

        if abs(current_mtime - order.expected_mtime) >= 1.0:
            return False, f"Data de modificação da origem foi alterada (esperado: {order.expected_mtime}, atual: {current_mtime})"

        # 3. Destino NUNCA pode existir previamente
        if os.path.exists(tgt):
            return False, f"Destino já existe. Sobrescrita terminantemente proibida: '{tgt}'"

        return True, ""

    def copy_single_order(self, order: ActionOrder) -> None:
        """Executa a cópia segura de uma ordem com temporário, hashing dual e renomeação atômica."""
        t_start = time.time()
        order.status = "copying"
        self._log_event("ORDER_START", order=order)

        # 1. Pre-flight imediato
        ok, err_msg = self.preflight_order(order)
        if not ok:
            order.status = "failed"
            order.error = err_msg
            if "Destino já existe" in err_msg:
                order.status = "conflict"
            elif "alterado" in err_msg or "alterada" in err_msg:
                order.status = "mutated"
            self.failed_orders += 1
            self._log_event("PREFLIGHT_FAILED", order=order, error=err_msg)
            return

        # 2. Prepara diretório de destino e nome temporário exclusivo
        dest_dir = os.path.dirname(order.target_path)
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            order.status = "failed"
            order.error = f"Falha ao criar diretório de destino: {e}"
            self.failed_orders += 1
            self._log_event("MKDIR_FAILED", order=order, error=str(e))
            return

        temp_filename = f".~tmp_safecopy_{order.order_id}_{uuid.uuid4().hex[:8]}.tmp"
        temp_path = os.path.join(dest_dir, temp_filename)

        hasher_src = hashlib.sha256()
        hasher_dst = hashlib.sha256()
        bytes_this_order = 0

        try:
            # 3. Cópia em blocos com hashing dual simultâneo
            with open(order.source_path, "rb") as f_src:
                with open(temp_path, "wb") as f_dst:
                    while True:
                        if self.cancel_event and self.cancel_event.is_set():
                            raise CopyCancelledError("Cópia cancelada cooperativamente pelo usuário.")

                        chunk = f_src.read(BUFFER_SIZE)
                        if not chunk:
                            break

                        hasher_src.update(chunk)
                        f_dst.write(chunk)
                        hasher_dst.update(chunk)
                        bytes_this_order += len(chunk)

                    f_dst.flush()
                    os.fsync(f_dst.fileno())

            # 4. Verificação de integridade pós-cópia
            if self.cancel_event and self.cancel_event.is_set():
                raise CopyCancelledError("Cópia cancelada após gravação de blocos.")

            temp_size = os.path.getsize(temp_path)
            if temp_size != order.expected_size_bytes:
                raise IOError(f"Tamanho gravado divergente: esperado {order.expected_size_bytes}, obtido {temp_size}")

            src_hash = hasher_src.hexdigest()
            dst_hash = hasher_dst.hexdigest()

            if src_hash != dst_hash:
                raise HashMismatchError(f"Divergência de hash SHA-256 entre origem ({src_hash}) e destino ({dst_hash})")

            if order.expected_sha256 and src_hash.lower() != order.expected_sha256.lower():
                raise HashMismatchError(f"Hash calculado ({src_hash}) diverge do hash prévio do plano ({order.expected_sha256})")

            # 5. Verificação final de não-concorrência de destino antes da renomeação
            if os.path.exists(order.target_path):
                raise TargetConflictError(f"Destino apareceu durante a cópia: '{order.target_path}'")

            # 6. Renomeação atômica
            # os.rename no Windows falha se o destino existir (proteção nativa)
            os.rename(temp_path, order.target_path)

            duration = (time.time() - t_start) * 1000
            order.status = "completed"
            order.expected_sha256 = src_hash
            order.error = None

            self.completed_orders += 1
            self.total_bytes_copied += bytes_this_order
            self._log_event(
                "COMPLETED",
                order=order,
                bytes_copied=bytes_this_order,
                sha256=src_hash,
                duration_ms=duration,
            )

        except CopyCancelledError:
            # Em cancelamento: limpa SOMENTE o temporário, preserva a origem intacta
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            order.status = "cancelled"
            order.error = "Operação cancelada pelo usuário."
            self.cancelled_orders += 1
            self._log_event("CANCELLED", order=order, error="Cancelado")
            raise

        except Exception as e:
            # Em erro: limpa SOMENTE o temporário, preserva a origem intacta
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            order.status = "failed"
            order.error = str(e)
            self.failed_orders += 1
            self._log_event("FAILED", order=order, error=str(e))

    def execute_all(self) -> Dict[str, Any]:
        """Executa todas as ordens pendentes do plano em sequência estritamente não-destrutiva."""
        t_start_total = time.time()
        self._log_event("PLAN_EXECUTION_START")
        self._report_progress(None, "started")

        # Checagem preliminar de espaço livre
        dest_root = os.path.splitdrive(self.plan.target_base_dir)[0] or self.plan.target_base_dir
        if not dest_root.endswith("\\") and not dest_root.endswith("/"):
            dest_root += os.sep

        try:
            usage = shutil.disk_usage(dest_root if os.path.exists(dest_root) else os.path.dirname(self.plan.target_base_dir))
            free_bytes = usage.free
            total_req = self.plan.totais.get("espaco_necessario_com_margem_bytes", 0)
            if free_bytes < total_req:
                err_space = f"Espaço insuficiente no disco {dest_root}: {free_bytes} bytes livres vs {total_req} bytes requeridos com margem."
                self._log_event("INSUFFICIENT_SPACE", error=err_space)
                raise InsufficientSpaceError(err_space)
        except InsufficientSpaceError:
            raise
        except Exception:
            # Se não for possível aferir disk_usage, prossegue com cautela
            pass

        try:
            for idx, order in enumerate(self.plan.ordens):
                if self.cancel_event and self.cancel_event.is_set():
                    self._report_progress(order, "cancelled")
                    raise CopyCancelledError("Execução do plano cancelada pelo usuário.")

                self._report_progress(order, "running")
                self.copy_single_order(order)
                self._report_progress(order, "step")

        except CopyCancelledError:
            self._log_event("PLAN_EXECUTION_CANCELLED")
            self._report_progress(None, "cancelled")
            for o in self.plan.ordens:
                if o.status in ("pending", "copying"):
                    o.status = "cancelled"
                    o.error = "Operação cancelada pelo usuário."
                    self.cancelled_orders += 1
        except Exception as e:
            self._log_event("PLAN_EXECUTION_ERROR", error=str(e))
            self._report_progress(None, "error")
            raise

        total_duration = time.time() - t_start_total
        self._log_event(
            "PLAN_EXECUTION_FINISHED",
            duration_ms=total_duration * 1000,
            bytes_copied=self.total_bytes_copied,
        )
        self._report_progress(None, "finished")

        # Salva resumo da execução em JSON estruturado
        summary = {
            "plan_id": self.plan.plan_id,
            "timestamp_inicio": datetime.datetime.fromtimestamp(t_start_total).isoformat(),
            "timestamp_fim": datetime.datetime.now().isoformat(),
            "duracao_segundos": round(total_duration, 2),
            "total_ordens": self.total_orders,
            "completadas": self.completed_orders,
            "falhas": self.failed_orders,
            "ignoradas": self.skipped_orders,
            "canceladas": self.cancelled_orders,
            "total_bytes_copiados": self.total_bytes_copied,
            "log_path": self.log_file_path,
        }

        summary_file = os.path.join(
            self.log_dir,
            f"execution_summary_{self.plan.plan_id}.json"
        )
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        return summary
