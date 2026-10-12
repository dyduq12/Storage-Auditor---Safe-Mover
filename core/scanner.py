"""Varredura de alta performance do sistema de arquivos Windows (NTFS).

Utiliza os.scandir para evitar chamadas de sistema redundantes, trata caminhos longos (>260 chars),
ignora arquivos em nuvem (offline/recall), rejeita symlinks e junctions para evitar loops,
e oferece suporte a cancelamento limpo e callbacks de progresso.
"""

from __future__ import annotations

import ctypes
import datetime
import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set

from config import (
    CLOUD_OFFLINE_MASK,
    FILE_ATTRIBUTE_REPARSE_POINT,
    IGNORED_EXTENSIONS,
    ISO_EXTENSIONS,
    SYSTEM_DIR_NAMES,
)

logger = logging.getLogger("storage_auditor.scanner")


def normalize_windows_path(path: str) -> str:
    """Normaliza caminhos no Windows para suportar caminhos longos (MAX_PATH > 260) via prefixo \\\\?\\."""
    if sys.platform != "win32":
        return os.path.abspath(path)

    abs_path = os.path.abspath(path)
    if len(abs_path) >= 240 and not abs_path.startswith("\\\\?\\"):
        if abs_path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + abs_path[2:]
        return "\\\\?\\" + abs_path
    return abs_path


def strip_long_path_prefix(path: str) -> str:
    """Remove o prefixo \\\\?\\ ou \\\\?\\UNC\\ para exibição amigável em relatórios."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def get_windows_attributes(path: str) -> int:
    """Retorna atributos de arquivo do Windows via GetFileAttributesW.
    
    Retorna 0 se falhar ou se não estiver no Windows.
    """
    if sys.platform != "win32":
        return 0
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        if attrs == 0xFFFFFFFF:  # INVALID_FILE_ATTRIBUTES
            return 0
        return attrs
    except Exception:
        return 0


def is_cloud_offline_file(path: str, win_attrs: Optional[int] = None) -> bool:
    """Verifica se o arquivo possui atributos de offline/recall (ex: OneDrive somente online).
    
    Atende ao Adendo v1.1 requisito 3.
    """
    attrs = win_attrs if win_attrs is not None else get_windows_attributes(path)
    if attrs == 0:
        return False
    # Atributos: OFFLINE (0x1000), RECALL_ON_DATA_ACCESS (0x400000), RECALL_ON_OPEN (0x40000)
    return bool(attrs & CLOUD_OFFLINE_MASK)


def is_reparse_point(path: str, win_attrs: Optional[int] = None) -> bool:
    """Verifica se o arquivo ou diretório é um reparse point (junction, symlink)."""
    attrs = win_attrs if win_attrs is not None else get_windows_attributes(path)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


@dataclass
class ScannedFile:
    """Representação de um arquivo válido indexado na varredura."""

    path: str
    size: int
    mtime: float
    scan_timestamp: str
    drive: str
    is_iso: bool
    extension: str


@dataclass
class IgnoredItem:
    """Representação de um arquivo ou pasta ignorado com o respectivo motivo."""

    path: str
    reason: str
    details: str = ""


class DirectoryScanner:
    """Motor de varredura NTFS de alta performance."""

    def __init__(
        self,
        ignored_extensions: Optional[Set[str]] = None,
        exclude_paths: Optional[List[str]] = None,
    ) -> None:
        self.ignored_extensions = ignored_extensions or IGNORED_EXTENSIONS
        self.exclude_paths: Set[str] = {
            os.path.normcase(os.path.abspath(p.rstrip("\\/")))
            for p in (exclude_paths or [])
            if p
        }
        self.files_per_root: Dict[str, int] = {}

    def scan_directories(
        self,
        roots: List[str],
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> tuple[List[ScannedFile], List[IgnoredItem], List[str]]:
        """Executa varredura profunda em uma lista de diretórios raízes.

        Retorna:
            (arquivos_validos, itens_ignorados, erros_de_io)
        """
        valid_files: List[ScannedFile] = []
        ignored_items: List[IgnoredItem] = []
        io_errors: List[str] = []
        scanned_count = 0
        self.files_per_root = {}

        scan_timestamp = datetime.datetime.now().isoformat()

        for root in roots:
            if cancel_event and cancel_event.is_set():
                logger.info("Varredura cancelada pelo usuário.")
                break

            abs_root = os.path.abspath(root.rstrip("\"'").strip())
            self.files_per_root[abs_root] = 0

            if not os.path.exists(abs_root):
                msg = f"Raiz inexistente ignorada: '{abs_root}' (original: '{root}')"
                logger.warning(msg)
                io_errors.append(msg)
                continue

            drive_letter = os.path.splitdrive(abs_root)[0].upper() or "C:"
            logger.info("Iniciando varredura da raiz: %s", abs_root)

            # Fila de diretórios para BFS/DFS iterativo evitando estouro de recursão
            dir_queue: List[str] = [abs_root]

            while dir_queue:
                if cancel_event and cancel_event.is_set():
                    break

                current_dir = dir_queue.pop()
                norm_current_dir = normalize_windows_path(current_dir)

                # Verifica se a pasta atual está na lista de exclusão (ex: bibliotecas de jogos no D:)
                current_dir_clean = os.path.normcase(
                    os.path.abspath(strip_long_path_prefix(norm_current_dir).rstrip("\\/"))
                )
                if any(
                    current_dir_clean == exc or current_dir_clean.startswith(exc + os.sep)
                    for exc in self.exclude_paths
                ):
                    ignored_items.append(
                        IgnoredItem(
                            path=strip_long_path_prefix(norm_current_dir),
                            reason="excluded_library_path",
                            details="Diretório pertencente a biblioteca de jogos ou escopo excluído.",
                        )
                    )
                    continue

                try:
                    with os.scandir(norm_current_dir) as it:
                        for entry in it:
                            if cancel_event and cancel_event.is_set():
                                break

                            entry_name_lower = entry.name.lower()
                            entry_path_friendly = strip_long_path_prefix(entry.path)

                            # Estágio 0: Ignorar diretórios especiais de sistema
                            if entry_name_lower in SYSTEM_DIR_NAMES:
                                ignored_items.append(
                                    IgnoredItem(
                                        path=entry_path_friendly,
                                        reason="system_directory",
                                        details="Diretório protegido do Windows.",
                                    )
                                )
                                continue

                            # Checagem de symlinks e junctions (Adendo v1.1 requisito 3)
                            try:
                                is_symlink = entry.is_symlink()
                            except (OSError, PermissionError):
                                is_symlink = False

                            win_attrs = get_windows_attributes(entry.path)
                            is_reparse = is_reparse_point(entry.path, win_attrs)

                            if is_symlink or is_reparse:
                                ignored_items.append(
                                    IgnoredItem(
                                        path=entry_path_friendly,
                                        reason="junction_or_symlink",
                                        details="Reparse point, junction ou symlink evitado.",
                                    )
                                )
                                continue

                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    dir_queue.append(entry.path)
                                    continue

                                if entry.is_file(follow_symlinks=False):
                                    # Checagem de arquivos em nuvem offline (Adendo v1.1 requisito 3)
                                    if is_cloud_offline_file(entry.path, win_attrs):
                                        ignored_items.append(
                                            IgnoredItem(
                                                path=entry_path_friendly,
                                                reason="cloud_offline",
                                                details="Arquivo armazenado na nuvem sem cópia local completa.",
                                            )
                                        )
                                        continue

                                    ext = os.path.splitext(entry_name_lower)[1]

                                    # Estágio 0: Filtro de extensões descartáveis
                                    # Nota: .iso não é descartado
                                    if ext in self.ignored_extensions:
                                        ignored_items.append(
                                            IgnoredItem(
                                                path=entry_path_friendly,
                                                reason="ignored_extension",
                                                details=f"Extensão {ext} desconsiderada para backup.",
                                            )
                                        )
                                        continue

                                    # Obter metadados rápidos diretamente da entrada do diretório
                                    stat_info = entry.stat(follow_symlinks=False)
                                    size = stat_info.st_size
                                    mtime = stat_info.st_mtime
                                    is_iso = ext in ISO_EXTENSIONS

                                    valid_files.append(
                                        ScannedFile(
                                            path=entry_path_friendly,
                                            size=size,
                                            mtime=mtime,
                                            scan_timestamp=scan_timestamp,
                                            drive=drive_letter,
                                            is_iso=is_iso,
                                            extension=ext,
                                        )
                                    )
                                    self.files_per_root[abs_root] = self.files_per_root.get(abs_root, 0) + 1

                                    scanned_count += 1
                                    if progress_callback and (scanned_count % 100 == 0):
                                        progress_callback(scanned_count, entry_path_friendly)

                            except (PermissionError, FileNotFoundError, OSError) as file_err:
                                err_msg = f"Erro de I/O no arquivo {entry_path_friendly}: {file_err}"
                                logger.debug(err_msg)
                                io_errors.append(err_msg)
                                continue

                except (PermissionError, FileNotFoundError, OSError) as dir_err:
                    err_msg = f"Erro de I/O ao listar diretório {strip_long_path_prefix(norm_current_dir)}: {dir_err}"
                    logger.warning(err_msg)
                    io_errors.append(err_msg)
                    continue

        if progress_callback:
            progress_callback(scanned_count, "Varredura concluída.")

        return valid_files, ignored_items, io_errors
