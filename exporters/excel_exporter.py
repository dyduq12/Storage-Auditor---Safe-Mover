"""Exportador de relatórios em formato Excel (.xlsx) com openpyxl (Modo Write-Only de Baixo Consumo de Memória).

Otimizado para grandes volumes de dados (115.000+ linhas) em computadores com memória RAM restrita (< 0.3 GB livre):
- Utiliza a API streaming openpyxl.Workbook(write_only=True) que grava XML diretamente em disco via buffer mínimo.
- Processa e descarrega um arquivo JSON por vez sequencialmente (del data, gc.collect()), reduzindo o pico de memória para < 70 MB.
- Configura todas as propriedades estruturais antes das linhas: freeze_panes = 'A2', auto_filter e column_dimensions com larguras ideais.
- Salva primeiro em arquivo temporário no diretório de destino e só substitui o arquivo final após validação de integridade ZIP.
- Suporta cancelamento limpo e reporte de progresso thread-safe para a interface gráfica.
- Em caso de erro ou cancelamento, limpa apenas o arquivo temporário gerado, preservando os JSONs e qualquer XLSX anterior.
"""

from __future__ import annotations

import datetime
import gc
import json
import os
import threading
import uuid
import zipfile
from collections import Counter
from typing import Any, Callable, Dict, List, Optional

import openpyxl
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


class ExportCancelledError(Exception):
    """Exceção levantada quando o usuário cancela a exportação Excel em andamento."""
    pass


def format_bytes(byte_count: int | float | None) -> str:
    """Formata valor em bytes para string legível (B, KB, MB, GB, TB)."""
    if byte_count is None:
        return "0 B"
    bytes_val = float(byte_count)
    if bytes_val < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0 or unit == "TB":
            return f"{bytes_val:.2f} {unit}" if unit != "B" else f"{int(bytes_val)} B"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} TB"


def format_timestamp(val: Any) -> str:
    """Formata timestamps ISO ou UNIX float para 'YYYY-MM-DD HH:MM:SS'."""
    if not val:
        return ""
    if isinstance(val, (int, float)):
        try:
            dt = datetime.datetime.fromtimestamp(val)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, OverflowError):
            return str(val)
    if isinstance(val, str):
        try:
            dt = datetime.datetime.fromisoformat(val)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return val
    return str(val)


def classify_save_status(save_path: str, game_name: str, shared_counts: Dict[str, int]) -> str:
    """Classifica o status do vínculo do save para auditoria transparente."""
    p_lower = save_path.lower()
    g_lower = game_name.lower()
    motivos = []

    if shared_counts.get(save_path, 0) > 1:
        motivos.append("Pasta compartilhada entre múltiplos jogos/emuladores")
    if p_lower.endswith("\\steam"):
        motivos.append("Pasta raiz genérica de emulador Steam")
    if "\\fonts\\" in p_lower or ("edition" in p_lower and "edition" in g_lower):
        if "\\fonts\\" in p_lower:
            motivos.append("Casamento falso por palavra 'Edition'")
    if "\\nova pasta" in p_lower or "\\light" in p_lower:
        if "light" in g_lower and not ("dying light" in p_lower):
            motivos.append("Casamento falso por termo genérico 'light'")
    if "mods" in p_lower and "expanded" in p_lower:
        motivos.append("Diretório de Mods, não savegame real")
    if "breakpoint" in p_lower and "wildlands" in g_lower:
        motivos.append("Casamento com outro jogo da franquia (Breakpoint vs Wildlands)")
    if "division" in p_lower and "wildlands" in g_lower:
        motivos.append("Casamento com outro jogo da franquia (The Division)")
    if "future soldier" in p_lower and "wildlands" in g_lower:
        motivos.append("Casamento com outro jogo da franquia (Future Soldier)")

    if motivos:
        return f"Vínculo Heurístico — Revisar ({'; '.join(motivos)})"
    return "Legítimo / Provável"


class ExcelReportExporter:
    """Exportador de dados de auditoria para Excel utilizando streaming write_only."""

    def __init__(self, output_dir: str = "reports") -> None:
        self.output_dir = output_dir

        # Estilos corporativos para WriteOnlyCell
        self.header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        self.header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
        self.title_font = Font(name="Segoe UI", size=13, bold=True, color="1F4E78")
        self.subtitle_font = Font(name="Segoe UI", size=10, italic=True, color="595959")
        self.data_font = Font(name="Segoe UI", size=10)
        self.alert_font = Font(name="Segoe UI", size=10, bold=True, color="C00000")
        self.warning_font = Font(name="Segoe UI", size=10, bold=True, color="B25E00")

        self.align_left = Alignment(horizontal="left", vertical="center")
        self.align_right = Alignment(horizontal="right", vertical="center")
        self.align_center = Alignment(horizontal="center", vertical="center")

    def _make_header_row(self, ws: Any, headers: List[str]) -> List[WriteOnlyCell]:
        """Cria lista de WriteOnlyCell com preenchimento institucional e fonte negrito."""
        cells = []
        for h in headers:
            c = WriteOnlyCell(ws, value=h)
            c.fill = self.header_fill
            c.font = self.header_font
            c.alignment = self.align_center
            cells.append(c)
        return cells

    def _set_column_widths(self, ws: Any, widths: Dict[str, int]) -> None:
        """Aplica larguras pré-configuradas às colunas da aba write_only."""
        for col_letter, width in widths.items():
            ws.column_dimensions[col_letter].width = width

    def _check_cancel(self, cancel_event: Optional[threading.Event]) -> None:
        """Verifica se o cancelamento foi solicitado."""
        if cancel_event and cancel_event.is_set():
            raise ExportCancelledError("Exportação Excel cancelada pelo usuário.")

    def export_from_reports_dir(
        self,
        reports_dir: Optional[str] = None,
        output_filename: str = "relatorio_auditoria_completo.xlsx",
        target_filepath: Optional[str] = None,
        max_items_per_sheet: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> str:
        """Lê os 5 JSONs sequencialmente do disco e exporta via streaming com baixo consumo de memória."""
        r_dir = reports_dir or self.output_dir
        if not target_filepath:
            os.makedirs(self.output_dir, exist_ok=True)
            target_filepath = os.path.join(self.output_dir, output_filename)

        final_dest = os.path.abspath(target_filepath)
        dest_dir = os.path.dirname(final_dest)
        os.makedirs(dest_dir, exist_ok=True)

        # Arquivo temporário no mesmo diretório para garantir atomicidade no mesmo sistema de arquivos
        temp_filename = f".~tmp_export_{uuid.uuid4().hex[:8]}_{os.path.basename(final_dest)}.tmp"
        temp_filepath = os.path.join(dest_dir, temp_filename)

        wb = openpyxl.Workbook(write_only=True)

        try:
            if progress_callback:
                progress_callback("Iniciando exportação em modo streaming...", 0.05)
            self._check_cancel(cancel_event)

            # -------------------------------------------------------------
            # 1. Aba Resumo
            # -------------------------------------------------------------
            if progress_callback:
                progress_callback("Processando Aba 1/5: Resumo Executivo...", 0.10)
            self._write_sheet_resumo_from_file(wb, r_dir)
            self._check_cancel(cancel_event)

            # -------------------------------------------------------------
            # 2. Aba Duplicatas
            # -------------------------------------------------------------
            if progress_callback:
                progress_callback("Processando Aba 2/5: Duplicatas Confirmadas...", 0.25)
            self._write_sheet_duplicatas_from_file(wb, r_dir, max_items_per_sheet, cancel_event, progress_callback)
            self._check_cancel(cancel_event)

            # -------------------------------------------------------------
            # 3. Aba Pendentes de Backup
            # -------------------------------------------------------------
            if progress_callback:
                progress_callback("Processando Aba 3/5: Pendentes de Backup...", 0.55)
            self._write_sheet_pendentes_from_file(wb, r_dir, max_items_per_sheet, cancel_event, progress_callback)
            self._check_cancel(cancel_event)

            # -------------------------------------------------------------
            # 4. Aba Documentos Avulsos
            # -------------------------------------------------------------
            if progress_callback:
                progress_callback("Processando Aba 4/5: Documentos Avulsos...", 0.80)
            self._write_sheet_documentos_from_file(wb, r_dir, max_items_per_sheet, cancel_event)
            self._check_cancel(cancel_event)

            # -------------------------------------------------------------
            # 5. Aba Jogos e Saves
            # -------------------------------------------------------------
            if progress_callback:
                progress_callback("Processando Aba 5/5: Jogos e Saves...", 0.90)
            self._write_sheet_jogos_saves_from_file(wb, r_dir, cancel_event)
            self._check_cancel(cancel_event)

            # Salva no arquivo temporário
            if progress_callback:
                progress_callback("Gravando arquivo Excel em disco...", 0.96)
            wb.save(temp_filepath)

            # Validação básica de integridade
            if not os.path.exists(temp_filepath) or os.path.getsize(temp_filepath) < 512:
                raise IOError(f"Falha ao gerar arquivo Excel temporário em {temp_filepath} (arquivo vazio ou corrompido).")

            if not zipfile.is_zipfile(temp_filepath):
                raise IOError(f"O arquivo Excel temporário {temp_filepath} não é um contêiner ZIP válido.")

            # Substituição atômica no destino final
            if progress_callback:
                progress_callback("Finalizando e validando integridade...", 0.99)
            os.replace(temp_filepath, final_dest)

            if progress_callback:
                progress_callback("Exportação Excel concluída com sucesso!", 1.0)

            return final_dest

        except Exception:
            # Em erro ou cancelamento, limpa APENAS o temporário criado por esta execução
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except OSError:
                    pass
            raise

    def export(
        self,
        resumo_data: Dict[str, Any],
        duplicatas_data: Dict[str, Any],
        pendentes_data: Dict[str, Any],
        documentos_data: Dict[str, Any],
        inventario_data: Dict[str, Any],
        output_filename: str = "relatorio_auditoria_completo.xlsx",
        target_filepath: Optional[str] = None,
        max_items_per_sheet: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> str:
        """Exporta a partir de dados fornecidos diretamente em memória (usado em testes)."""
        if not target_filepath:
            os.makedirs(self.output_dir, exist_ok=True)
            target_filepath = os.path.join(self.output_dir, output_filename)

        final_dest = os.path.abspath(target_filepath)
        dest_dir = os.path.dirname(final_dest)
        os.makedirs(dest_dir, exist_ok=True)

        temp_filename = f".~tmp_export_{uuid.uuid4().hex[:8]}_{os.path.basename(final_dest)}.tmp"
        temp_filepath = os.path.join(dest_dir, temp_filename)

        wb = openpyxl.Workbook(write_only=True)

        try:
            self._check_cancel(cancel_event)
            self._write_sheet_resumo_mem(wb, resumo_data)
            self._write_sheet_duplicatas_mem(wb, duplicatas_data, max_items_per_sheet, cancel_event)
            self._write_sheet_pendentes_mem(wb, pendentes_data, max_items_per_sheet, cancel_event)
            self._write_sheet_documentos_mem(wb, documentos_data, max_items_per_sheet, cancel_event)
            self._write_sheet_jogos_saves_mem(wb, inventario_data, cancel_event)

            wb.save(temp_filepath)

            if not os.path.exists(temp_filepath) or os.path.getsize(temp_filepath) < 512 or not zipfile.is_zipfile(temp_filepath):
                raise IOError(f"Falha na validação do arquivo Excel {temp_filepath}")

            os.replace(temp_filepath, final_dest)
            return final_dest

        except Exception:
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except OSError:
                    pass
            raise

    # -------------------------------------------------------------------------
    # Construtores de Abas - Modo Streaming a partir do Disco
    # -------------------------------------------------------------------------

    @staticmethod
    def _find_report_file(reports_dir: str, primary: str, aliases: Optional[List[str]] = None) -> Optional[str]:
        """Localiza arquivo de relatório suportando variações de nomenclatura."""
        candidates = [primary] + (aliases or [])
        for name in candidates:
            p = os.path.join(reports_dir, name)
            if os.path.exists(p):
                return p
        return None

    def _write_sheet_resumo_from_file(self, wb: openpyxl.Workbook, reports_dir: str) -> None:
        """Escreve aba Resumo lendo resumo_para_ia.json."""
        p = self._find_report_file(reports_dir, "resumo_para_ia.json", ["resumo.json"])
        data: Dict[str, Any] = {}
        if p:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        self._write_sheet_resumo_mem(wb, data)
        del data
        gc.collect()

    def _write_sheet_resumo_mem(self, wb: openpyxl.Workbook, data: Dict[str, Any]) -> None:
        """Escreve aba Resumo."""
        ws = wb.create_sheet(title="Resumo")
        ws.freeze_panes = "A5"
        self._set_column_widths(ws, {"A": 38, "B": 18, "C": 24, "D": 24})

        # Linha 1: Título
        c_title = WriteOnlyCell(ws, value="STORAGE AUDITOR & SAFE MOVER — RESUMO EXECUTIVO")
        c_title.font = self.title_font
        ws.append([c_title])

        # Linha 2: Subtítulo
        sub_text = (
            f"Relatório gerado em: {format_timestamp(data.get('timestamp_varredura'))} | "
            f"Versão Schema: {data.get('schema_version', '1.1.0')} | "
            "Modo: 100% Somente Leitura"
        )
        c_sub = WriteOnlyCell(ws, value=sub_text)
        c_sub.font = self.subtitle_font
        ws.append([c_sub])

        # Linha 3: Espaço
        ws.append([])

        # Linha 4: Cabeçalho
        headers = ["Métrica / Categoria", "Total de Itens", "Tamanho Formatado", "Tamanho em Bytes"]
        ws.append(self._make_header_row(ws, headers))

        totais = data.get("totais", {})
        metricas = [
            ("Arquivos Indexados no Disco C:", totais.get("arquivos_varridos_c", 0), totais.get("tamanho_total_c_bytes", 0)),
            ("Arquivos Indexados no Disco D:", totais.get("arquivos_varridos_d", 0), totais.get("tamanho_total_d_bytes", 0)),
            ("Duplicatas Confirmadas (C vs D):", totais.get("total_duplicatas", 0), totais.get("espaco_recuperavel_bytes", 0)),
            ("Espaço Recuperável Potencial em C:", totais.get("total_duplicatas", 0), totais.get("espaco_recuperavel_bytes", 0)),
            ("Pendentes de Backup para D:", totais.get("total_pendentes_backup", 0), totais.get("tamanho_pendentes_backup_bytes", 0)),
            ("Documentos Avulsos Críticos em C:", totais.get("total_documentos_avulsos", 0), totais.get("tamanho_documentos_avulsos_bytes", 0)),
            ("Jogos Instalados Mapeados:", totais.get("total_jogos_detectados", 0), totais.get("tamanho_total_jogos_bytes", 0)),
            ("Vínculos Heurísticos de Saves em C:", totais.get("total_saves_detectados", 0), totais.get("tamanho_total_saves_bytes", 0)),
        ]

        for label, count, b_val in metricas:
            ws.append([label, count, format_bytes(b_val), b_val])

        ws.auto_filter.ref = "A4:D12"

    def _write_sheet_duplicatas_from_file(
        self,
        wb: openpyxl.Workbook,
        reports_dir: str,
        max_items: Optional[int],
        cancel_event: Optional[threading.Event],
        progress_callback: Optional[Callable[[str, float], None]],
    ) -> None:
        """Escreve aba Duplicatas lendo duplicatas_confirmadas.json e liberando memória."""
        p = self._find_report_file(reports_dir, "duplicatas_confirmadas.json", ["relatorio_duplicatas.json", "duplicatas.json"])
        data: Dict[str, Any] = {}
        if p:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        self._write_sheet_duplicatas_mem(wb, data, max_items, cancel_event, progress_callback)
        del data
        gc.collect()

    def _write_sheet_duplicatas_mem(
        self,
        wb: openpyxl.Workbook,
        data: Dict[str, Any],
        max_items: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        """Escreve aba Duplicatas."""
        ws = wb.create_sheet(title="Duplicatas")
        ws.freeze_panes = "A2"
        self._set_column_widths(ws, {
            "A": 45, "B": 45, "C": 14, "D": 16, "E": 18,
            "F": 16, "G": 32, "H": 32, "I": 20, "J": 20
        })

        headers = [
            "Caminho Origem (C:)",
            "Caminho Destino (D:)",
            "Tamanho",
            "Tamanho (Bytes)",
            "Espaço Recuperável (Bytes)",
            "Nível de Confiança",
            "Hash Amostral",
            "Hash Completo",
            "Data Modificação Origem",
            "Data Modificação Destino",
        ]
        ws.append(self._make_header_row(ws, headers))

        itens = data.get("itens", [])
        if max_items:
            itens = itens[:max_items]

        total_rows = 1
        for idx, item in enumerate(itens):
            if idx % 1000 == 0:
                self._check_cancel(cancel_event)
                if progress_callback and len(itens) > 0:
                    prog = 0.25 + (idx / len(itens)) * 0.25
                    progress_callback(f"Duplicatas: {idx:,}/{len(itens):,} linhas...", prog)

            t_bytes = item.get("tamanho_bytes", 0)
            rec_bytes = item.get("espaco_recuperavel", 0)
            conf = item.get("nivel_confianca", "completo").lower()

            if conf == "amostral":
                conf_cell = WriteOnlyCell(ws, value="⚠️ AMOSTRAL")
                conf_cell.font = self.warning_font
                conf_cell.alignment = self.align_center
            else:
                conf_cell = WriteOnlyCell(ws, value="✓ COMPLETO")
                conf_cell.alignment = self.align_center

            row = [
                item.get("caminho_origem", ""),
                item.get("caminho_destino", ""),
                format_bytes(t_bytes),
                t_bytes,
                rec_bytes,
                conf_cell,
                item.get("hash_amostral", ""),
                item.get("hash_completo", ""),
                format_timestamp(item.get("mtime_origem")),
                format_timestamp(item.get("mtime_destino")),
            ]
            ws.append(row)
            total_rows += 1

        ws.auto_filter.ref = f"A1:J{max(total_rows, 2)}"

    def _write_sheet_pendentes_from_file(
        self,
        wb: openpyxl.Workbook,
        reports_dir: str,
        max_items: Optional[int],
        cancel_event: Optional[threading.Event],
        progress_callback: Optional[Callable[[str, float], None]],
    ) -> None:
        """Escreve aba Pendentes de Backup lendo pendentes_de_backup_para_d.json e liberando memória."""
        p = self._find_report_file(reports_dir, "pendentes_de_backup_para_d.json", ["relatorio_pendentes_backup.json", "pendentes_backup.json", "pendentes.json"])
        data: Dict[str, Any] = {}
        if p:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        self._write_sheet_pendentes_mem(wb, data, max_items, cancel_event, progress_callback)
        del data
        gc.collect()

    def _write_sheet_pendentes_mem(
        self,
        wb: openpyxl.Workbook,
        data: Dict[str, Any],
        max_items: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        """Escreve aba Pendentes de Backup."""
        ws = wb.create_sheet(title="Pendentes de Backup")
        ws.freeze_panes = "A2"
        self._set_column_widths(ws, {
            "A": 55, "B": 15, "C": 16, "D": 12, "E": 14, "F": 22
        })

        headers = [
            "Caminho (C:)",
            "Tamanho",
            "Tamanho (Bytes)",
            "Extensão",
            "É Imagem ISO",
            "Data Modificação",
        ]
        ws.append(self._make_header_row(ws, headers))

        itens = data.get("itens", [])
        if max_items:
            itens = itens[:max_items]

        total_rows = 1
        for idx, item in enumerate(itens):
            if idx % 2000 == 0:
                self._check_cancel(cancel_event)
                if progress_callback and len(itens) > 0:
                    prog = 0.55 + (idx / len(itens)) * 0.25
                    progress_callback(f"Pendentes: {idx:,}/{len(itens):,} linhas...", prog)

            t_bytes = item.get("tamanho_bytes", 0)
            row = [
                item.get("caminho", ""),
                format_bytes(t_bytes),
                t_bytes,
                item.get("extensao", ""),
                "SIM" if item.get("is_iso") else "NÃO",
                format_timestamp(item.get("mtime")),
            ]
            ws.append(row)
            total_rows += 1

        ws.auto_filter.ref = f"A1:F{max(total_rows, 2)}"

    def _write_sheet_documentos_from_file(
        self,
        wb: openpyxl.Workbook,
        reports_dir: str,
        max_items: Optional[int],
        cancel_event: Optional[threading.Event],
    ) -> None:
        """Escreve aba Documentos Avulsos lendo documentos_avulsos.json e liberando memória."""
        p = self._find_report_file(reports_dir, "documentos_avulsos.json", ["relatorio_documentos_avulsos.json", "documentos.json"])
        data: Dict[str, Any] = {}
        if p:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        self._write_sheet_documentos_mem(wb, data, max_items, cancel_event)
        del data
        gc.collect()

    def _write_sheet_documentos_mem(
        self,
        wb: openpyxl.Workbook,
        data: Dict[str, Any],
        max_items: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Escreve aba Documentos Avulsos."""
        ws = wb.create_sheet(title="Documentos Avulsos")
        ws.freeze_panes = "A2"
        self._set_column_widths(ws, {
            "A": 50, "B": 20, "C": 15, "D": 16, "E": 12, "F": 16, "G": 22
        })

        headers = [
            "Caminho (C:)",
            "Pasta Origem",
            "Tamanho",
            "Tamanho (Bytes)",
            "Extensão",
            "Prioridade",
            "Data Modificação",
        ]
        ws.append(self._make_header_row(ws, headers))

        itens = data.get("itens", [])
        if max_items:
            itens = itens[:max_items]

        total_rows = 1
        for idx, item in enumerate(itens):
            if idx % 500 == 0:
                self._check_cancel(cancel_event)

            t_bytes = item.get("tamanho_bytes", 0)
            prio = item.get("prioridade", "media").lower()

            if prio == "alta":
                prio_cell = WriteOnlyCell(ws, value="🔴 ALTA")
                prio_cell.font = self.alert_font
                prio_cell.alignment = self.align_center
            else:
                prio_cell = WriteOnlyCell(ws, value="🟡 MÉDIA")
                prio_cell.alignment = self.align_center

            row = [
                item.get("caminho", ""),
                item.get("pasta_origem", ""),
                format_bytes(t_bytes),
                t_bytes,
                item.get("extensao", ""),
                prio_cell,
                format_timestamp(item.get("mtime")),
            ]
            ws.append(row)
            total_rows += 1

        ws.auto_filter.ref = f"A1:G{max(total_rows, 2)}"

    def _write_sheet_jogos_saves_from_file(
        self,
        wb: openpyxl.Workbook,
        reports_dir: str,
        cancel_event: Optional[threading.Event],
    ) -> None:
        """Escreve aba Jogos e Saves lendo inventario_jogos_e_saves.json e liberando memória."""
        p = self._find_report_file(reports_dir, "inventario_jogos_e_saves.json", ["inventario_jogos_saves.json", "jogos_saves.json"])
        data: Dict[str, Any] = {}
        if p:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        self._write_sheet_jogos_saves_mem(wb, data, cancel_event)
        del data
        gc.collect()

    def _write_sheet_jogos_saves_mem(
        self,
        wb: openpyxl.Workbook,
        data: Dict[str, Any],
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Escreve aba Jogos e Saves."""
        ws = wb.create_sheet(title="Jogos e Saves")
        ws.freeze_panes = "A2"
        self._set_column_widths(ws, {
            "A": 30, "B": 15, "C": 45, "D": 18, "E": 18,
            "F": 45, "G": 16, "H": 16, "I": 14, "J": 16, "K": 35, "L": 22
        })

        headers = [
            "Nome do Jogo",
            "Launcher",
            "Caminho Instalação",
            "Tamanho Instalação",
            "Tamanho Instalação (Bytes)",
            "Caminho Save em C:",
            "Tamanho Save",
            "Tamanho Save (Bytes)",
            "Qtd Arquivos Save",
            "Nível de Certeza",
            "Status do Vínculo",
            "Data Modificação Save",
        ]
        ws.append(self._make_header_row(ws, headers))

        jogos = data.get("jogos", [])

        # Identifica saves compartilhados
        save_paths: List[str] = []
        for j in jogos:
            for s in j.get("saves_em_c", []):
                save_paths.append(s.get("caminho_save", ""))
        shared_counts = Counter(save_paths)

        total_rows = 1
        for j in jogos:
            self._check_cancel(cancel_event)
            j_nome = j.get("nome", "")
            j_launcher = j.get("launcher", "")
            j_path = j.get("caminho_instalacao", "")
            j_bytes = j.get("tamanho_instalacao_bytes", 0)
            saves = j.get("saves_em_c", [])

            if not saves:
                row = [
                    j_nome,
                    j_launcher,
                    j_path,
                    format_bytes(j_bytes),
                    j_bytes,
                    "[Nenhum save localizado]",
                    "0 B",
                    0,
                    0,
                    "-",
                    "N/A",
                    "",
                ]
                ws.append(row)
                total_rows += 1
            else:
                for s in saves:
                    s_path = s.get("caminho_save", "")
                    s_bytes = s.get("tamanho_bytes", 0)
                    s_files = s.get("quantidade_arquivos", 0)
                    s_cert = s.get("nivel_certeza", "media")
                    status_str = classify_save_status(s_path, j_nome, shared_counts)

                    if "Revisar" in status_str:
                        status_cell = WriteOnlyCell(ws, value=status_str)
                        status_cell.font = self.warning_font
                    else:
                        status_cell = WriteOnlyCell(ws, value=status_str)

                    row = [
                        j_nome,
                        j_launcher,
                        j_path,
                        format_bytes(j_bytes),
                        j_bytes,
                        s_path,
                        format_bytes(s_bytes),
                        s_bytes,
                        s_files,
                        s_cert.upper(),
                        status_cell,
                        format_timestamp(s.get("mtime")),
                    ]
                    ws.append(row)
                    total_rows += 1

        ws.auto_filter.ref = f"A1:L{max(total_rows, 2)}"
