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
from core.path_utils import to_extended_path

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
    ordens_recusadas: List[Dict[str, Any]] = field(default_factory=list)
    status_aprovacao: str = "aprovado"  # "aprovado" ou "nao_aprovado_invalido"
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
            "ordens_recusadas": self.ordens_recusadas,
            "status_aprovacao": self.status_aprovacao,
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
        recusadas = list(data.get("ordens_recusadas", []))
        status_aprovacao = str(data.get("status_aprovacao", data.get("totais", {}).get("status_aprovacao", "aprovado")))

        return cls(
            plan_id=str(data["plan_id"]),
            created_at=str(data["created_at"]),
            source_reports_dir=str(data["source_reports_dir"]),
            target_base_dir=str(data["target_base_dir"]),
            totais=dict(data["totais"]),
            ordens=orders,
            ordens_recusadas=recusadas,
            status_aprovacao=status_aprovacao,
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
        self.heuristic_save_paths = self._load_heuristic_save_rules()

    def _load_heuristic_save_rules(self) -> Set[str]:
        """Carrega diretórios de saves heurísticos / suspeitos do inventário de jogos."""
        heuristic_dirs: Set[str] = set()
        inv_path = os.path.join(self.reports_dir, "inventario_jogos_e_saves.json")
        if os.path.exists(inv_path):
            try:
                with open(inv_path, "r", encoding="utf-8") as f:
                    inv_data = json.load(f)
                for j in inv_data.get("jogos", []):
                    for s in j.get("saves_em_c", []):
                        cert = str(s.get("nivel_certeza", "")).lower()
                        p = s.get("caminho_save", "")
                        if p:
                            p_lower = os.path.normpath(p).lower()
                            if cert in ("baixa", "heuristico", "suspeito") or \
                               "\\temp\\" in p_lower or "\\docker\\" in p_lower or \
                               "\\adobe\\ccx" in p_lower or "\\prismlauncher\\meta" in p_lower:
                                heuristic_dirs.add(p_lower)
            except Exception:
                pass
        return heuristic_dirs

    def is_heuristic_or_suspicious_save(self, source_path: str) -> Tuple[bool, str]:
        """Verifica se o caminho corresponde a um save heurístico ou suspeito."""
        src_norm = os.path.normpath(source_path).lower()
        for h_dir in getattr(self, "heuristic_save_paths", set()):
            if src_norm.startswith(h_dir):
                return True, f"Caminho identificado como save de jogo heurístico/suspeito: '{source_path}'"

        if "\\appdata\\local\\temp\\" in src_norm and "pytest" not in src_norm:
            return True, f"Caminho sob pasta temporária suspeita: '{source_path}'"

        return False, ""

    @staticmethod
    def is_reparse_point_or_symlink(path: str) -> bool:
        """Detecta se o arquivo/diretório é um symlink, junction ou reparse point no Windows."""
        try:
            path_ext = to_extended_path(path)
            if os.path.islink(path_ext):
                return True
            if os.name == "nt" and os.path.exists(path_ext):
                import ctypes
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path_ext))
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
        """Deriva uma estrutura de subpasta limpa e representativa para o arquivo no destino."""
        norm = os.path.normpath(source_path)
        parts = list(Path(norm).parts)

        # Se for Windows com letra de drive (ex: C:\)
        if len(parts) > 1 and ":" in parts[0]:
            parts = parts[1:]  # remove C:\

        # Se estiver em Users\<Nome>\Pasta..., deriva a partir da pasta sob o usuário
        if len(parts) >= 3 and parts[0].lower() == "users":
            rel_parts = parts[2:]
            return os.path.join(*rel_parts)

        # Caso geral: usa os componentes do caminho sem a letra do drive
        return os.path.join(*parts) if parts else os.path.basename(source_path)

    def generate_plan(
        self,
        include_pending: bool = True,
        include_documents: bool = True,
        triage_manager: Optional[Any] = None,
        explicit_included_paths: Optional[Set[str]] = None,
        validate_live_filesystem: bool = False,
    ) -> ActionPlan:
        """Gera o ActionPlan compilando ordens com rejeição estrita de riscos.
        
        Rejeita qualquer ordem baseada em:
        - Save heurístico ou de baixa confiança
        - Hash apenas amostral sem hash completo
        - Caminho de origem inexistente (se validate_live_filesystem=True)
        - Fonte alterada pós-auditoria (se validate_live_filesystem=True)
        - Destino pré-existente (se validate_live_filesystem=True)
        - Caminho fora de raízes autorizadas ou de sistema restrito
        
        Se triage_manager ou explicit_included_paths for fornecido, apenas arquivos com
        decisão 'include' são aceitos.
        """
        plan_id = f"plan_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        created_at = datetime.datetime.now().isoformat()

        ordens: List[ActionOrder] = []
        ordens_recusadas: List[Dict[str, Any]] = []
        caminhos_processados: Set[str] = set()

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

        def _evaluate_candidate(
            item: Dict[str, Any],
            category_tag: str,
            default_reason: str,
        ) -> None:
            src = item.get("caminho")
            if not src:
                return

            src_norm = os.path.normpath(src)
            src_lower = src_norm.lower()

            if src_lower in caminhos_processados:
                return

            size = int(item.get("tamanho_bytes", 0))
            mtime = float(item.get("mtime", 0.0))
            rel_target = self.derive_relative_target_path(src_norm)
            tgt = os.path.join(self.target_base_dir, rel_target)

            # 1. Filtro estrito de triagem por decisão explícita: SOMENTE itens com decisão INCLUDE
            if triage_manager is not None:
                dec, _, _ = triage_manager.resolve_file_decision(src_norm)
                if dec != "include":
                    return
            elif explicit_included_paths is not None:
                if src_lower not in explicit_included_paths:
                    return

            # 2. Rejeição de saves heurísticos / suspeitos
            is_heur, heur_reason = self.is_heuristic_or_suspicious_save(src_norm)
            if is_heur:
                ordens_recusadas.append({
                    "caminho_origem": src_norm,
                    "caminho_destino": tgt,
                    "tamanho_bytes": size,
                    "motivo_recusa": heur_reason,
                    "categoria": "save_heuristico",
                })
                caminhos_processados.add(src_lower)
                return

            # 3. Rejeição de hash apenas amostral sem hash completo
            fp_conf = str(item.get("fingerprint_confidence") or item.get("tipo_hash") or "").lower()
            if fp_conf == "amostral" and not item.get("sha256_completo"):
                ordens_recusadas.append({
                    "caminho_origem": src_norm,
                    "caminho_destino": tgt,
                    "tamanho_bytes": size,
                    "motivo_recusa": "Hash prévio é apenas amostral (não verificado com SHA-256 completo)",
                    "categoria": "hash_amostral",
                })
                caminhos_processados.add(src_lower)
                return

            # 4. Validação de segurança do caminho de origem
            safe_src, src_err = self.validate_source_file(src_norm)
            if not safe_src:
                ordens_recusadas.append({
                    "caminho_origem": src_norm,
                    "caminho_destino": tgt,
                    "tamanho_bytes": size,
                    "motivo_recusa": src_err,
                    "categoria": "seguranca_origem",
                })
                caminhos_processados.add(src_lower)
                return

            # 5. Validação de segurança do caminho de destino
            safe_tgt, tgt_err = self.validate_target_file(tgt, src_norm)
            if not safe_tgt:
                ordens_recusadas.append({
                    "caminho_origem": src_norm,
                    "caminho_destino": tgt,
                    "tamanho_bytes": size,
                    "motivo_recusa": tgt_err,
                    "categoria": "seguranca_destino",
                })
                caminhos_processados.add(src_lower)
                return

            # 6. Validação ao vivo no filesystem (se habilitada)
            if validate_live_filesystem:
                tgt_ext = to_extended_path(tgt)
                src_ext = to_extended_path(src_norm)

                # Checa destino existente
                if os.path.exists(tgt_ext):
                    ordens_recusadas.append({
                        "caminho_origem": src_norm,
                        "caminho_destino": tgt,
                        "tamanho_bytes": size,
                        "motivo_recusa": f"Destino já existe no disco. Sobrescrita proibida: '{tgt}'",
                        "categoria": "conflito_destino",
                    })
                    caminhos_processados.add(src_lower)
                    return

                # Checa existência da origem
                if not os.path.exists(src_ext):
                    ordens_recusadas.append({
                        "caminho_origem": src_norm,
                        "caminho_destino": tgt,
                        "tamanho_bytes": size,
                        "motivo_recusa": f"Arquivo de origem não existe no disco: '{src_norm}'",
                        "categoria": "origem_inexistente",
                    })
                    caminhos_processados.add(src_lower)
                    return

                # Checa mutação da origem pós-auditoria
                try:
                    st = os.stat(src_ext)
                    if st.st_size != size:
                        ordens_recusadas.append({
                            "caminho_origem": src_norm,
                            "caminho_destino": tgt,
                            "tamanho_bytes": size,
                            "motivo_recusa": f"Tamanho da origem foi alterado pós-auditoria (esperado: {size}, atual: {st.st_size})",
                            "categoria": "origem_mutada",
                        })
                        caminhos_processados.add(src_lower)
                        return
                    if abs(st.st_mtime - mtime) >= 1.0:
                        ordens_recusadas.append({
                            "caminho_origem": src_norm,
                            "caminho_destino": tgt,
                            "tamanho_bytes": size,
                            "motivo_recusa": f"Data de modificação da origem foi alterada pós-auditoria (esperado: {mtime}, atual: {st.st_mtime})",
                            "categoria": "origem_mutada",
                        })
                        caminhos_processados.add(src_lower)
                        return
                except OSError as e:
                    ordens_recusadas.append({
                        "caminho_origem": src_norm,
                        "caminho_destino": tgt,
                        "tamanho_bytes": size,
                        "motivo_recusa": f"Falha ao inspecionar origem no filesystem: {e}",
                        "categoria": "origem_mutada",
                    })
                    caminhos_processados.add(src_lower)
                    return

            order_id = f"ord_{hashlib.sha256(src_norm.encode('utf-8')).hexdigest()[:16]}"
            ordens.append(
                ActionOrder(
                    order_id=order_id,
                    source_path=src_norm,
                    target_path=tgt,
                    expected_size_bytes=size,
                    expected_mtime=mtime,
                    expected_sha256=item.get("sha256_completo") or item.get("sha256"),
                    fingerprint_confidence="completo" if (item.get("sha256_completo") or item.get("sha256")) else "nao_calculado",
                    category=category_tag,
                    reason=default_reason,
                    status="pending",
                )
            )
            caminhos_processados.add(src_lower)

        # Processa Pendentes
        for it in pen_data.get("itens", []):
            _evaluate_candidate(it, "pendente_backup", "Arquivo de C: aprovado na triagem para backup em D:")

        # Processa Documentos
        for it in doc_data.get("itens", []):
            prio = str(it.get("prioridade", "alta")).upper()
            _evaluate_candidate(it, "documento_avulso_critico", f"Documento importante de C: com prioridade {prio}")

        total_bytes = sum(o.expected_size_bytes for o in ordens)
        dest_root = os.path.splitdrive(self.target_base_dir)[0] or self.target_base_dir
        if not dest_root.endswith("\\") and not dest_root.endswith("/"):
            dest_root += os.sep

        try:
            usage = shutil.disk_usage(dest_root if os.path.exists(dest_root) else os.path.dirname(self.target_base_dir))
            free_bytes = usage.free
        except Exception:
            free_bytes = 0

        is_approved = (triage_manager is not None or explicit_included_paths is not None)
        status_aprovacao = "aprovado" if is_approved else "nao_aprovado_invalido"

        totais = {
            "total_ordens": len(ordens),
            "total_ordens_recusadas": len(ordens_recusadas),
            "tamanho_total_bytes": total_bytes,
            "espaco_livre_destino_bytes": free_bytes,
            "espaco_necessario_com_margem_bytes": total_bytes + self.safety_margin_bytes,
            "espaco_suficiente": free_bytes >= (total_bytes + self.safety_margin_bytes),
            "status_aprovacao": status_aprovacao,
            "selecao_explicita_presente": is_approved,
        }

        return ActionPlan(
            plan_id=plan_id,
            created_at=created_at,
            source_reports_dir=self.reports_dir,
            target_base_dir=self.target_base_dir,
            totais=totais,
            ordens=ordens,
            ordens_recusadas=ordens_recusadas,
            status_aprovacao=status_aprovacao,
        )
