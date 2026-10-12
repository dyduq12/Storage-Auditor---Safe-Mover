"""Carregador de dados e gerenciador de paginação/filtros para a GUI (Fase 2).

Lê os 5 relatórios JSON gerados pela auditoria e provê fatiamento sob demanda (paginação),
pesquisa por texto e filtragem categórica para manter a interface CustomTkinter ágil e responsiva
mesmo diante de 80.000+ itens.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from exporters.excel_exporter import classify_save_status, format_bytes, format_timestamp


class AuditDataLoader:
    """Gerenciador de dados em memória para a interface gráfica."""

    def __init__(self, reports_dir: str = "reports") -> None:
        self.reports_dir = reports_dir
        self.resumo: Dict[str, Any] = {}
        self.duplicatas: List[Dict[str, Any]] = []
        self.pendentes: List[Dict[str, Any]] = []
        self.documentos: List[Dict[str, Any]] = []
        self.jogos: List[Dict[str, Any]] = []
        self.saves_flat: List[Dict[str, Any]] = []
        self.is_loaded: bool = False
        self.last_load_error: Optional[str] = None

    def load_from_directory(self, reports_dir: Optional[str] = None) -> bool:
        """Carrega e valida os cinco arquivos JSON da pasta especificada."""
        target_dir = reports_dir or self.reports_dir
        self.reports_dir = target_dir
        self.last_load_error = None

        if not os.path.exists(target_dir):
            self.last_load_error = f"Diretório de relatórios não existe: '{target_dir}'"
            self.is_loaded = False
            return False

        def _read_json(fname: str) -> Dict[str, Any]:
            p = os.path.join(target_dir, fname)
            if not os.path.exists(p):
                return {}
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                return {}

        self.resumo = _read_json("resumo_para_ia.json")
        dup_json = _read_json("duplicatas_confirmadas.json")
        pen_json = _read_json("pendentes_de_backup_para_d.json")
        doc_json = _read_json("documentos_avulsos.json")
        inv_json = _read_json("inventario_jogos_e_saves.json")

        self.duplicatas = dup_json.get("itens", []) if isinstance(dup_json, dict) else []
        self.pendentes = pen_json.get("itens", []) if isinstance(pen_json, dict) else []
        self.documentos = doc_json.get("itens", []) if isinstance(doc_json, dict) else []
        self.jogos = inv_json.get("jogos", []) if isinstance(inv_json, dict) else []

        # Pré-processa lista de saves com classificação e contagem de compartilhamento
        save_paths = []
        for j in self.jogos:
            for s in j.get("saves_em_c", []):
                save_paths.append(s.get("caminho_save", ""))
        shared_counts = Counter(save_paths)

        self.saves_flat = []
        for j in self.jogos:
            j_nome = j.get("nome", "Desconhecido")
            j_launcher = j.get("launcher", "Desconhecido")
            for s in j.get("saves_em_c", []):
                s_path = s.get("caminho_save", "")
                status_str = classify_save_status(s_path, j_nome, shared_counts)
                self.saves_flat.append({
                    "jogo_nome": j_nome,
                    "launcher": j_launcher,
                    "caminho_save": s_path,
                    "tamanho_bytes": s.get("tamanho_bytes", 0),
                    "quantidade_arquivos": s.get("quantidade_arquivos", 0),
                    "nivel_certeza": s.get("nivel_certeza", "media"),
                    "status_vinculo": status_str,
                    "is_suspeito": "Revisar" in status_str,
                    "mtime": s.get("mtime"),
                })

        self.is_loaded = bool(self.resumo or self.duplicatas or self.pendentes or self.documentos or self.jogos)
        return self.is_loaded

    # -------------------------------------------------------------------------
    # Métricas de Resumo
    # -------------------------------------------------------------------------

    def get_summary_metrics(self) -> Dict[str, Any]:
        """Retorna dicionário com totais consolidados para exibição na aba Resumo."""
        totais = self.resumo.get("totais", {}) if isinstance(self.resumo, dict) else {}
        return {
            "timestamp": self.resumo.get("timestamp_varredura", ""),
            "schema_version": self.resumo.get("schema_version", "1.1.0"),
            "arquivos_c": totais.get("arquivos_varridos_c", len(self.duplicatas) + len(self.pendentes)),
            "arquivos_d": totais.get("arquivos_varridos_d", 0),
            "bytes_c": totais.get("tamanho_total_c_bytes", 0),
            "bytes_d": totais.get("tamanho_total_d_bytes", 0),
            "total_duplicatas": totais.get("total_duplicatas", len(self.duplicatas)),
            "espaco_recuperavel_bytes": totais.get("espaco_recuperavel_bytes", sum(d.get("espaco_recuperavel", 0) for d in self.duplicatas)),
            "total_pendentes": totais.get("total_pendentes_backup", len(self.pendentes)),
            "tamanho_pendentes_bytes": totais.get("tamanho_pendentes_backup_bytes", sum(p.get("tamanho_bytes", 0) for p in self.pendentes)),
            "total_documentos": totais.get("total_documentos_avulsos", len(self.documentos)),
            "tamanho_documentos_bytes": totais.get("tamanho_documentos_avulsos_bytes", sum(d.get("tamanho_bytes", 0) for d in self.documentos)),
            "total_jogos": totais.get("total_jogos_detectados", len(self.jogos)),
            "tamanho_jogos_bytes": totais.get("tamanho_total_jogos_bytes", sum(j.get("tamanho_instalacao_bytes", 0) for j in self.jogos)),
            "total_saves": totais.get("total_saves_detectados", len(self.saves_flat)),
            "tamanho_saves_bytes": totais.get("tamanho_total_saves_bytes", sum(s.get("tamanho_bytes", 0) for s in self.saves_flat)),
        }

    # -------------------------------------------------------------------------
    # Paginação: Duplicatas
    # -------------------------------------------------------------------------

    def get_duplicates_page(
        self,
        page: int = 1,
        page_size: int = 100,
        search: str = "",
        confidence_filter: str = "todos",
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Filtra e pagina duplicatas confirmadas.
        
        Retorna:
            (itens_da_pagina, total_filtrado, total_paginas)
        """
        filtered = self.duplicatas

        # Filtro de confiança
        conf_lower = confidence_filter.lower().strip()
        if conf_lower in ("amostral", "completo"):
            filtered = [d for d in filtered if d.get("nivel_confianca", "").lower() == conf_lower]

        # Busca textual
        if search:
            s_low = search.lower().strip()
            filtered = [
                d for d in filtered
                if s_low in d.get("caminho_origem", "").lower()
                or s_low in d.get("caminho_destino", "").lower()
            ]

        total_items = len(filtered)
        total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 1
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = filtered[start_idx:end_idx]

        return page_items, total_items, total_pages

    # -------------------------------------------------------------------------
    # Paginação: Pendentes de Backup
    # -------------------------------------------------------------------------

    def get_pending_page(
        self,
        page: int = 1,
        page_size: int = 100,
        search: str = "",
        extension_filter: str = "todas",
        iso_only: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Filtra e pagina arquivos pendentes de backup."""
        filtered = self.pendentes

        if iso_only:
            filtered = [p for p in filtered if p.get("is_iso")]

        ext_filter = extension_filter.lower().strip()
        if ext_filter and ext_filter != "todas":
            if not ext_filter.startswith("."):
                ext_filter = "." + ext_filter
            filtered = [p for p in filtered if p.get("extensao", "").lower() == ext_filter]

        if search:
            s_low = search.lower().strip()
            filtered = [p for p in filtered if s_low in p.get("caminho", "").lower()]

        total_items = len(filtered)
        total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 1
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = filtered[start_idx:end_idx]

        return page_items, total_items, total_pages

    # -------------------------------------------------------------------------
    # Paginação: Documentos Avulsos
    # -------------------------------------------------------------------------

    def get_documents_page(
        self,
        page: int = 1,
        page_size: int = 100,
        search: str = "",
        priority_filter: str = "todas",
        folder_filter: str = "todas",
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Filtra e pagina documentos avulsos prioritários."""
        filtered = self.documentos

        prio_lower = priority_filter.lower().strip()
        if prio_lower in ("alta", "media", "baixa"):
            filtered = [d for d in filtered if d.get("prioridade", "").lower() == prio_lower]

        fold_lower = folder_filter.lower().strip()
        if fold_lower and fold_lower != "todas":
            filtered = [d for d in filtered if d.get("pasta_origem", "").lower() == fold_lower]

        if search:
            s_low = search.lower().strip()
            filtered = [d for d in filtered if s_low in d.get("caminho", "").lower()]

        total_items = len(filtered)
        total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 1
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = filtered[start_idx:end_idx]

        return page_items, total_items, total_pages

    # -------------------------------------------------------------------------
    # Visualização: Jogos e Saves
    # -------------------------------------------------------------------------

    def get_games_list(self, search: str = "") -> List[Dict[str, Any]]:
        """Retorna lista de jogos instalados."""
        if not search:
            return self.jogos
        s_low = search.lower().strip()
        return [
            j for j in self.jogos
            if s_low in j.get("nome", "").lower()
            or s_low in j.get("launcher", "").lower()
            or s_low in j.get("caminho_instalacao", "").lower()
        ]

    def get_saves_list(self, search: str = "", suspect_only: bool = False) -> List[Dict[str, Any]]:
        """Retorna lista de saves com filtros de busca e suspeição."""
        filtered = self.saves_flat
        if suspect_only:
            filtered = [s for s in filtered if s.get("is_suspeito")]
        if search:
            s_low = search.lower().strip()
            filtered = [
                s for s in filtered
                if s_low in s.get("jogo_nome", "").lower()
                or s_low in s.get("caminho_save", "").lower()
                or s_low in s.get("status_vinculo", "").lower()
            ]
        return filtered
