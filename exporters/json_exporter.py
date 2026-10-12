"""Exportador de relatórios estruturados em formato JSON.

Gera os 4 relatórios descritos na especificação arquitetural mais o resumo_para_ia.json:
1. duplicatas_confirmadas.json
2. pendentes_de_backup_para_d.json
3. inventario_jogos_e_saves.json
4. documentos_avulsos.json
5. resumo_para_ia.json (compacto com totais e top N)

Atende rigorosamente ao Adendo v1.1:
- Todas as chaves são ASCII sem acentos (requisito 7).
- Inclui schema_version em todos os arquivos (requisito 7).
- Arquivos .iso com prioridade "baixa" mantidos (requisito 7).
- Inclui mtime, tamanho e timestamp de varredura (requisito 6).
- Inclui nível de confiança em duplicatas (requisito 1).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from config import SCHEMA_VERSION
from core.comparator import ComparisonResult
from core.game_mapper import GameInventoryItem
from core.scanner import ScannedFile

logger = logging.getLogger("storage_auditor.json_exporter")


class JsonReportExporter:
    """Exportador especializado para arquivos JSON segmentados."""

    def __init__(self, output_dir: str = "reports", schema_version: str = SCHEMA_VERSION) -> None:
        self.output_dir = output_dir
        self.schema_version = schema_version
        os.makedirs(self.output_dir, exist_ok=True)

    def _write_json_file(self, filename: str, data: Dict[str, Any]) -> str:
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=True)
        logger.info("Relatório gerado com sucesso: %s", filepath)
        return filepath

    def export_duplicatas(
        self, comp_result: ComparisonResult, timestamp: str
    ) -> str:
        """Exporta duplicatas_confirmadas.json."""
        items = []
        for d in comp_result.duplicatas:
            items.append(
                {
                    "caminho_origem": d.caminho_origem,
                    "caminho_destino": d.caminho_destino,
                    "tamanho_bytes": d.tamanho_bytes,
                    "hash_amostral": d.hash_amostral,
                    "hash_completo": d.hash_completo,
                    "espaco_recuperavel": d.espaco_recuperavel,
                    "nivel_confianca": d.nivel_confianca,
                    "mtime_origem": d.mtime_origem,
                    "mtime_destino": d.mtime_destino,
                }
            )

        payload = {
            "schema_version": self.schema_version,
            "timestamp_varredura": timestamp,
            "total_duplicatas": len(items),
            "espaco_total_recuperavel_bytes": comp_result.espaco_total_recuperavel_bytes,
            "itens": items,
        }
        return self._write_json_file("duplicatas_confirmadas.json", payload)

    def export_pendentes_backup(
        self, comp_result: ComparisonResult, timestamp: str
    ) -> str:
        """Exporta pendentes_de_backup_para_d.json."""
        items = []
        for p in comp_result.pendentes_backup:
            items.append(
                {
                    "caminho": p.caminho,
                    "tamanho_bytes": p.tamanho_bytes,
                    "mtime": p.mtime,
                    "extensao": p.extensao,
                    "is_iso": p.is_iso,
                }
            )

        payload = {
            "schema_version": self.schema_version,
            "timestamp_varredura": timestamp,
            "total_pendentes": len(items),
            "tamanho_total_pendente_bytes": comp_result.tamanho_total_pendente_bytes,
            "itens": items,
        }
        return self._write_json_file("pendentes_de_backup_para_d.json", payload)

    def export_inventario_jogos(
        self, games: List[GameInventoryItem], timestamp: str
    ) -> str:
        """Exporta inventario_jogos_e_saves.json."""
        total_size_games = sum(g.tamanho_instalacao_bytes for g in games)
        total_size_saves = sum(g.tamanho_total_saves_bytes for g in games)

        jogos_list = []
        for g in games:
            saves_list = []
            for s in g.saves_em_c:
                saves_list.append(
                    {
                        "caminho_save": s.caminho_save,
                        "tamanho_bytes": s.tamanho_bytes,
                        "quantidade_arquivos": s.quantidade_arquivos,
                        "nivel_certeza": s.nivel_certeza,
                        "mtime": s.mtime,
                    }
                )

            jogos_list.append(
                {
                    "nome": g.nome,
                    "launcher": g.launcher,
                    "caminho_instalacao": g.caminho_instalacao,
                    "tamanho_instalacao_bytes": g.tamanho_instalacao_bytes,
                    "executaveis_principais": g.executaveis_principais,
                    "tamanho_total_saves_bytes": g.tamanho_total_saves_bytes,
                    "saves_em_c": saves_list,
                }
            )

        payload = {
            "schema_version": self.schema_version,
            "timestamp_varredura": timestamp,
            "total_jogos": len(jogos_list),
            "tamanho_total_jogos_bytes": total_size_games,
            "tamanho_total_saves_bytes": total_size_saves,
            "jogos": jogos_list,
        }
        return self._write_json_file("inventario_jogos_e_saves.json", payload)

    def export_documentos_avulsos(
        self, comp_result: ComparisonResult, timestamp: str
    ) -> str:
        """Exporta documentos_avulsos.json."""
        items = []
        for doc in comp_result.documentos_avulsos:
            items.append(
                {
                    "caminho": doc.caminho,
                    "tamanho_bytes": doc.tamanho_bytes,
                    "mtime": doc.mtime,
                    "extensao": doc.extensao,
                    "prioridade": doc.prioridade,
                    "pasta_origem": doc.pasta_origem,
                }
            )

        total_bytes = sum(doc.tamanho_bytes for doc in comp_result.documentos_avulsos)
        payload = {
            "schema_version": self.schema_version,
            "timestamp_varredura": timestamp,
            "total_documentos": len(items),
            "tamanho_total_bytes": total_bytes,
            "itens": items,
        }
        return self._write_json_file("documentos_avulsos.json", payload)

    def export_resumo_para_ia(
        self,
        files_c: List[ScannedFile],
        files_d: List[ScannedFile],
        comp_result: ComparisonResult,
        games: List[GameInventoryItem],
        timestamp: str,
        top_n: int = 10,
    ) -> str:
        """Exporta resumo_para_ia.json compacto para tomada de decisão assistida por IA."""
        total_c_size = sum(f.size for f in files_c)
        total_d_size = sum(f.size for f in files_d)
        total_games_size = sum(g.tamanho_instalacao_bytes for g in games)
        total_saves_size = sum(g.tamanho_total_saves_bytes for g in games)
        total_saves_count = sum(len(g.saves_em_c) for g in games)
        total_docs_size = sum(d.tamanho_bytes for d in comp_result.documentos_avulsos)

        # Top N Duplicatas mais pesadas
        sorted_dups = sorted(
            comp_result.duplicatas, key=lambda d: d.tamanho_bytes, reverse=True
        )[:top_n]
        top_dups = [
            {
                "caminho_origem": d.caminho_origem,
                "caminho_destino": d.caminho_destino,
                "tamanho_bytes": d.tamanho_bytes,
                "espaco_recuperavel": d.espaco_recuperavel,
                "nivel_confianca": d.nivel_confianca,
            }
            for d in sorted_dups
        ]

        # Top N Pendentes de backup mais pesados
        sorted_pending = sorted(
            comp_result.pendentes_backup, key=lambda p: p.tamanho_bytes, reverse=True
        )[:top_n]
        top_pending = [
            {
                "caminho": p.caminho,
                "tamanho_bytes": p.tamanho_bytes,
                "extensao": p.extensao,
            }
            for p in sorted_pending
        ]

        # Top N Documentos avulsos mais pesados
        sorted_docs = sorted(
            comp_result.documentos_avulsos, key=lambda d: d.tamanho_bytes, reverse=True
        )[:top_n]
        top_docs = [
            {
                "caminho": d.caminho,
                "tamanho_bytes": d.tamanho_bytes,
                "prioridade": d.prioridade,
                "pasta_origem": d.pasta_origem,
            }
            for d in sorted_docs
        ]

        # Top N Jogos mais pesados com seus saves associados
        sorted_games = sorted(
            games, key=lambda g: g.tamanho_instalacao_bytes, reverse=True
        )[:top_n]
        top_games = [
            {
                "nome": g.nome,
                "launcher": g.launcher,
                "caminho_instalacao": g.caminho_instalacao,
                "tamanho_instalacao_bytes": g.tamanho_instalacao_bytes,
                "tamanho_total_saves_bytes": g.tamanho_total_saves_bytes,
                "saves_em_c": [
                    {
                        "caminho_save": s.caminho_save,
                        "tamanho_bytes": s.tamanho_bytes,
                        "nivel_certeza": s.nivel_certeza,
                    }
                    for s in g.saves_em_c
                ],
            }
            for g in sorted_games
        ]

        payload = {
            "schema_version": self.schema_version,
            "timestamp_varredura": timestamp,
            "totais": {
                "arquivos_varridos_c": len(files_c),
                "arquivos_varridos_d": len(files_d),
                "tamanho_total_c_bytes": total_c_size,
                "tamanho_total_d_bytes": total_d_size,
                "total_duplicatas": len(comp_result.duplicatas),
                "espaco_recuperavel_bytes": comp_result.espaco_total_recuperavel_bytes,
                "total_pendentes_backup": len(comp_result.pendentes_backup),
                "tamanho_pendentes_backup_bytes": comp_result.tamanho_total_pendente_bytes,
                "total_documentos_avulsos": len(comp_result.documentos_avulsos),
                "tamanho_documentos_avulsos_bytes": total_docs_size,
                "total_jogos_detectados": len(games),
                "tamanho_total_jogos_bytes": total_games_size,
                "total_saves_detectados": total_saves_count,
                "tamanho_total_saves_bytes": total_saves_size,
            },
            "maiores_duplicatas": top_dups,
            "maiores_pendentes_backup": top_pending,
            "maiores_documentos_avulsos": top_docs,
            "jogos_com_maior_tamanho_e_saves": top_games,
        }
        return self._write_json_file("resumo_para_ia.json", payload)

    def export_all(
        self,
        files_c: List[ScannedFile],
        files_d: List[ScannedFile],
        comp_result: ComparisonResult,
        games: List[GameInventoryItem],
        timestamp: str,
    ) -> Dict[str, str]:
        """Exporta todos os 5 relatórios JSON."""
        return {
            "duplicatas": self.export_duplicatas(comp_result, timestamp),
            "pendentes": self.export_pendentes_backup(comp_result, timestamp),
            "jogos": self.export_inventario_jogos(games, timestamp),
            "documentos": self.export_documentos_avulsos(comp_result, timestamp),
            "resumo_ia": self.export_resumo_para_ia(files_c, files_d, comp_result, games, timestamp),
        }
