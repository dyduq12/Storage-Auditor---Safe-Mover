"""Módulo do Action Plan da Fase 3: Safe Mover (Cópia Segura Não-Destrutiva).

Define o schema estrito e versionado do plano de ação, modelos de dados para
ordens individuais e o gerador de plano com validação rigorosa de segurança:
- Rejeição de caminhos relativos e path traversal (..)
- Rejeição de pastas de sistema e arquivos especiais
- Rejeição de symlinks, junctions e reparse points
- Rejeição de destinos aninhados na origem
- Rejeição total de saves heurísticos / suspeitos
- Validação estrita de schema
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config import FILE_ATTRIBUTE_REPARSE_POINT, SYSTEM_DIR_NAMES

ACTION_PLAN_SCHEMA_VERSION: str = "1.0.0"
DEFAULT_SAFETY_MARGIN_BYTES: int = 2 * 1024 * 1024 * 1024  # 2.0 GB de margem livre


class ActionPlanValidationError(Exception):
    """Exceção levantada quando um plano ou ordem viola regras de segurança ou integridade."""
    pass


@dataclass
class ActionOrder:
    """Representa uma ordem individual de cópia segura no Action Plan."""

    order_id: str
    source_path: str
    target_path: str
    expected_size_bytes: int
    expected_mtime: float
    expected_sha256: Optional[str] = None
    fingerprint_confidence: str = "nao_calculado"  # "completo", "amostral", "nao_calculado"
    category: str = "pendente_backup"  # "pendente_backup", "documento_avulso_critico"
    reason: str = ""
    status: str = "pending"  # "pending", "validated", "copying", "completed", "failed", "conflict", "skipped", "cancelled"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionOrder:
        required_fields = ["order_id", "source_path", "target_path", "expected_size_bytes", "expected_mtime"]
        for rf in required_fields:
            if rf not in data:
                raise ActionPlanValidationError(f"Campo obrigatório ausente na ActionOrder: '{rf}'")
        return cls(
            order_id=str(data["order_id"]),
            source_path=str(data["source_path"]),
            target_path=str(data["target_path"]),
            expected_size_bytes=int(data["expected_size_bytes"]),
            expected_mtime=float(data["expected_mtime"]),
            expected_sha256=data.get("expected_sha256"),
            fingerprint_confidence=str(data.get("fingerprint_confidence", "nao_calculado")),
            category=str(data.get("category", "pendente_backup")),
            reason=str(data.get("reason", "")),
            status=str(data.get("status", "pending")),
            error=data.get("error"),
        )


@dataclass
class ActionPlan:
    """Plano de Ação completo e versionado para execução controlada pelo Safe Mover."""

    plan_id: str
    created_at: str
    source_reports_dir: str
    target_base_dir: str
    totais: Dict[str, Any]
    ordens: List[ActionOrder] = field(default_factory=list)
    schema_version: str = ACTION_PLAN_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "source_reports_dir": self.source_reports_dir,
            "target_base_dir": self.target_base_dir,
            "totais": self.totais,
            "ordens": [o.to_dict() for o in self.ordens],
        }

    def save_to_json(self, filepath: str) -> None:
        """Salva o plano de ação em arquivo JSON formatado."""
        dest_dir = os.path.dirname(os.path.abspath(filepath))
        os.makedirs(dest_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionPlan:
        version = data.get("schema_version")
        if version != ACTION_PLAN_SCHEMA_VERSION:
            raise ActionPlanValidationError(
                f"Versão de schema incompatível: esperada '{ACTION_PLAN_SCHEMA_VERSION}', obtida '{version}'"
            )
        for req in ["plan_id", "created_at", "source_reports_dir", "target_base_dir", "totais", "ordens"]:
            if req not in data:
                raise ActionPlanValidationError(f"Campo obrigatório ausente no ActionPlan: '{req}'")

        orders = [ActionOrder.from_dict(o) for o in data["ordens"]]
        return cls(
            schema_version=version,
            plan_id=str(data["plan_id"]),
            created_at=str(data["created_at"]),
            source_reports_dir=str(data["source_reports_dir"]),
            target_base_dir=str(data["target_base_dir"]),
            totais=dict(data["totais"]),
            ordens=orders,
        )

    @classmethod
    def load_from_json(cls, filepath: str) -> ActionPlan:
        """Carrega e valida um ActionPlan a partir de um arquivo JSON."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Arquivo de plano não encontrado: '{filepath}'")
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


class ActionPlanGenerator:
    """Gerador e validador de Action Plan a partir dos relatórios auditados."""

    def __init__(
        self,
        reports_dir: str = "reports",
        target_base_dir: str = "D:\\Backup_StorageAuditor",
        safety_margin_bytes: int = DEFAULT_SAFETY_MARGIN_BYTES,
        authorized_source_roots: Optional[List[str]] = None,
        authorized_target_roots: Optional[List[str]] = None,
    ) -> None:
        self.reports_dir = os.path.abspath(reports_dir)
        self.target_base_dir = os.path.abspath(target_base_dir)
        self.safety_margin_bytes = safety_margin_bytes
        self.authorized_source_roots = [os.path.abspath(r) for r in (authorized_source_roots or [])]
        self.authorized_target_roots = [os.path.abspath(r) for r in (authorized_target_roots or [])]

    @staticmethod
    def is_reparse_point_or_symlink(path: str) -> bool:
        """Detecta se o arquivo/diretório é um symlink, junction ou reparse point no Windows."""
        try:
            if os.path.islink(path):
                return True
            if os.name == "nt" and os.path.exists(path):
                import ctypes
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
                if attrs != -1 and (attrs & FILE_ATTRIBUTE_REPARSE_POINT):
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def validate_path_safety(path: str) -> Tuple[bool, str]:
        """Aplica verificações de segurança em um caminho (sem traversal, absoluto, sem pastas de sistema)."""
        if not path or not isinstance(path, str):
            return False, "Caminho vazio ou inválido."

        # Rejeita caminhos relativos
        if not os.path.isabs(path):
            return False, f"Caminho não é absoluto: '{path}'"

        # Normaliza e checa path traversal
        norm = os.path.normpath(path)
        parts = Path(norm).parts
        if ".." in parts or "." in parts:
            return False, f"Tentativa de path traversal detectada em: '{path}'"

        # Rejeita pastas de sistema conhecidas
        for part in parts:
            clean_part = part.lower().strip("\\/ ")
            if clean_part in SYSTEM_DIR_NAMES:
                return False, f"Caminho cruza pasta de sistema restrita ('{part}'): '{path}'"

        return True, ""

    def validate_source_file(self, source_path: str) -> Tuple[bool, str]:
        """Valida se o arquivo de origem é elegível com segurança."""
        safe, err = self.validate_path_safety(source_path)
        if not safe:
            return False, err

        # Checa raízes autorizadas de origem, se configuradas
        if self.authorized_source_roots:
            norm_src = os.path.normpath(source_path).lower()
            allowed = any(norm_src.startswith(r.lower()) for r in self.authorized_source_roots)
            if not allowed:
                return False, f"Origem fora das raízes autorizadas: '{source_path}'"

        # Checa symlink / reparse point
        if self.is_reparse_point_or_symlink(source_path):
            return False, f"Arquivo de origem é symlink ou junction: '{source_path}'"

        return True, ""

    def validate_target_file(self, target_path: str, source_path: str) -> Tuple[bool, str]:
        """Valida segurança do caminho de destino e ausência de aninhamento com a origem."""
        safe, err = self.validate_path_safety(target_path)
        if not safe:
            return False, err

        norm_target = os.path.normpath(target_path).lower()
        norm_source = os.path.normpath(source_path).lower()

        # O destino não pode ser idêntico à origem
        if norm_target == norm_source:
            return False, "Caminho de destino é idêntico ao caminho de origem."

        # O destino não pode estar contido na pasta de origem
        norm_source_dir = os.path.dirname(norm_source)
        if norm_target.startswith(norm_source_dir + os.sep) or norm_target == norm_source_dir:
            return False, "Caminho de destino está aninhado dentro do diretório de origem."

        # O destino deve estar contido na raiz base de destino
        norm_target_base = os.path.normpath(self.target_base_dir).lower()
        if not norm_target.startswith(norm_target_base):
            return False, f"Destino fora da pasta base de destino configurada: '{target_path}'"

        # Checa raízes autorizadas de destino, se configuradas
        if self.authorized_target_roots:
            allowed = any(norm_target.startswith(r.lower()) for r in self.authorized_target_roots)
            if not allowed:
                return False, f"Destino fora das raízes autorizadas de destino: '{target_path}'"

        return True, ""

    @staticmethod
    def derive_relative_target_path(source_path: str) -> str:
        """Deriva uma estrutura de subpasta limpa e representativa para o arquivo no destino.
        
        Exemplo:
        C:\\Users\\Eduardo\\Documents\\relatorio.pdf -> Documents\\relatorio.pdf
        C:\\Users\\Eduardo\\Downloads\\Setup\\app.exe -> Downloads\\Setup\\app.exe
        C:\\Dados\\fotos\\img.png -> Dados\\fotos\\img.png
        """
        norm = os.path.normpath(source_path)
        parts = list(Path(norm).parts)

        # Se for Windows com letra de drive (ex: C:\)
        if len(parts) > 1 and ":" in parts[0]:
            parts = parts[1:]  # remove C:\

        # Se estiver em Users\<Nome>\Pasta..., deriva a partir da pasta sob o usuário
        if len(parts) >= 3 and parts[0].lower() == "users":
            # [Users, Eduardo, Documents, ...] -> [Documents, ...]
            rel_parts = parts[2:]
            return os.path.join(*rel_parts)

        # Caso geral: usa os componentes do caminho sem a letra do drive
        return os.path.join(*parts) if parts else os.path.basename(source_path)

    def generate_plan(
        self,
        include_pending: bool = True,
        include_documents: bool = True,
    ) -> ActionPlan:
        """Gera o ActionPlan compilando ordens a partir dos relatórios existentes."""
        plan_id = f"plan_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        created_at = datetime.datetime.now().isoformat()

        ordens: List[ActionOrder] = []
        caminhos_processados: Set[str] = set()

        # ---------------------------------------------------------------------
        # 1. Carrega Relatórios JSON
        # ---------------------------------------------------------------------
        def _read_report(primary: str, aliases: List[str]) -> Dict[str, Any]:
            for name in [primary] + aliases:
                p = os.path.join(self.reports_dir, name)
                if os.path.exists(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            return json.load(f)
                    except Exception:
                        return {}
            return {}

        pen_data = _read_report("pendentes_de_backup_para_d.json", ["relatorio_pendentes_backup.json"]) if include_pending else {}
        doc_data = _read_report("documentos_avulsos.json", ["relatorio_documentos_avulsos.json"]) if include_documents else {}

        # ---------------------------------------------------------------------
        # 2. Processa Pendentes de Backup
        # ---------------------------------------------------------------------
        for item in pen_data.get("itens", []):
            src = item.get("caminho")
            if not src or src.lower() in caminhos_processados:
                continue

            # Valida segurança da origem
            safe_src, src_err = self.validate_source_file(src)
            if not safe_src:
                continue

            rel_target = self.derive_relative_target_path(src)
            tgt = os.path.join(self.target_base_dir, rel_target)

            # Valida segurança do destino
            safe_tgt, tgt_err = self.validate_target_file(tgt, src)
            if not safe_tgt:
                continue

            size = int(item.get("tamanho_bytes", 0))
            mtime = float(item.get("mtime", 0.0))
            order_id = f"ord_{hashlib.sha256(src.encode('utf-8')).hexdigest()[:16]}"

            ordens.append(
                ActionOrder(
                    order_id=order_id,
                    source_path=src,
                    target_path=tgt,
                    expected_size_bytes=size,
                    expected_mtime=mtime,
                    expected_sha256=None,
                    fingerprint_confidence="nao_calculado",
                    category="pendente_backup",
                    reason="Arquivo de C: pendente de backup em D:",
                    status="pending",
                )
            )
            caminhos_processados.add(src.lower())

        # ---------------------------------------------------------------------
        # 3. Processa Documentos Avulsos Críticos
        # ---------------------------------------------------------------------
        for item in doc_data.get("itens", []):
            src = item.get("caminho")
            if not src or src.lower() in caminhos_processados:
                continue

            safe_src, src_err = self.validate_source_file(src)
            if not safe_src:
                continue

            rel_target = self.derive_relative_target_path(src)
            tgt = os.path.join(self.target_base_dir, rel_target)

            safe_tgt, tgt_err = self.validate_target_file(tgt, src)
            if not safe_tgt:
                continue

            size = int(item.get("tamanho_bytes", 0))
            mtime = float(item.get("mtime", 0.0))
            prio = str(item.get("prioridade", "alta")).lower()
            order_id = f"ord_{hashlib.sha256(src.encode('utf-8')).hexdigest()[:16]}"

            ordens.append(
                ActionOrder(
                    order_id=order_id,
                    source_path=src,
                    target_path=tgt,
                    expected_size_bytes=size,
                    expected_mtime=mtime,
                    expected_sha256=None,
                    fingerprint_confidence="nao_calculado",
                    category="documento_avulso_critico",
                    reason=f"Documento importante de C: com prioridade {prio.upper()}",
                    status="pending",
                )
            )
            caminhos_processados.add(src.lower())

        # ---------------------------------------------------------------------
        # 4. Cálculo de Espaço Livre e Projeção
        # ---------------------------------------------------------------------
        total_bytes = sum(o.expected_size_bytes for o in ordens)
        dest_root = os.path.splitdrive(self.target_base_dir)[0] or self.target_base_dir
        if not dest_root.endswith("\\") and not dest_root.endswith("/"):
            dest_root += os.sep

        try:
            usage = shutil.disk_usage(dest_root if os.path.exists(dest_root) else os.path.dirname(self.target_base_dir))
            free_bytes = usage.free
        except Exception:
            free_bytes = 0

        totais = {
            "total_ordens": len(ordens),
            "tamanho_total_bytes": total_bytes,
            "espaco_livre_destino_bytes": free_bytes,
            "espaco_necessario_com_margem_bytes": total_bytes + self.safety_margin_bytes,
            "espaco_suficiente": free_bytes >= (total_bytes + self.safety_margin_bytes),
        }

        return ActionPlan(
            plan_id=plan_id,
            created_at=created_at,
            source_reports_dir=self.reports_dir,
            target_base_dir=self.target_base_dir,
            totais=totais,
            ordens=ordens,
        )
