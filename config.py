"""Configurações globais e constantes do Storage Auditor & Safe Mover.

Define regras de escopo, extensões ignoradas, atributos de arquivos de nuvem,
limites de buffer para hashing em múltiplos estágios e heurísticas de jogos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set

SCHEMA_VERSION: str = "1.1.0"

# Atributos de arquivos do Windows (NTFS / Win32)
FILE_ATTRIBUTE_READONLY: int = 0x00000001
FILE_ATTRIBUTE_HIDDEN: int = 0x00000002
FILE_ATTRIBUTE_SYSTEM: int = 0x00000004
FILE_ATTRIBUTE_DIRECTORY: int = 0x00000010
FILE_ATTRIBUTE_ARCHIVE: int = 0x00000020
FILE_ATTRIBUTE_DEVICE: int = 0x00000040
FILE_ATTRIBUTE_NORMAL: int = 0x00000080
FILE_ATTRIBUTE_TEMPORARY: int = 0x00000100
FILE_ATTRIBUTE_SPARSE_FILE: int = 0x00000200
FILE_ATTRIBUTE_REPARSE_POINT: int = 0x00000400
FILE_ATTRIBUTE_COMPRESSED: int = 0x00000800
FILE_ATTRIBUTE_OFFLINE: int = 0x00001000
FILE_ATTRIBUTE_NOT_CONTENT_INDEXED: int = 0x00002000
FILE_ATTRIBUTE_ENCRYPTED: int = 0x00004000
FILE_ATTRIBUTE_RECALL_ON_OPEN: int = 0x00040000
FILE_ATTRIBUTE_PINNED: int = 0x00080000
FILE_ATTRIBUTE_UNPINNED: int = 0x00100000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS: int = 0x00400000

# Máscara combinada para arquivos que estão na nuvem (offline / recall)
CLOUD_OFFLINE_MASK: int = (
    FILE_ATTRIBUTE_OFFLINE
    | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    | FILE_ATTRIBUTE_RECALL_ON_OPEN
)

# Parâmetros de Hashing
SAMPLE_CHUNK_SIZE: int = 64 * 1024  # 64 KB
HASH_READ_BLOCK_SIZE: int = 1024 * 1024  # 1 MB
DEFAULT_FULL_HASH_THRESHOLD_BYTES: int = 100 * 1024 * 1024  # 100 MB
EXPANDED_SAMPLE_THRESHOLD_BYTES: int = 2 * 1024 * 1024 * 1024  # 2 GB

# Extensões descartadas no Estágio 0 (não relevantes para backup de usuário)
# NOTA: .iso NÃO é descartado (Adendo v1.1 requisito 7: marcado como baixa prioridade)
IGNORED_EXTENSIONS: Set[str] = {
    ".dll",
    ".tmp",
    ".sys",
    ".log",
    ".cab",
    ".bak",
    ".chk",
    ".dxso",
    ".shader",
    ".manifest",
    ".pdb",
    ".cache",
    ".lock",
    ".dmp",
}

# Extensões de documentos importantes para documentos_avulsos.json
DOCUMENT_EXTENSIONS: Set[str] = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".txt",
    ".csv",
    ".epub",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
}

# Extensões de mídia e imagens ISO
ISO_EXTENSIONS: Set[str] = {
    ".iso",
    ".img",
    ".bin",
    ".cue",
}

# Pastas de sistema que devem ser ignoradas na varredura padrão
SYSTEM_DIR_NAMES: Set[str] = {
    "$recycle.bin",
    "system volume information",
    "recovery",
    "msocache",
    "perflogs",
    "windows",
    "winsxs",
}


def get_default_c_roots() -> List[str]:
    """Retorna raízes padrão recomendadas do usuário no disco C:."""
    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    candidates = [
        os.path.join(user_profile, "Desktop"),
        os.path.join(user_profile, "Documents"),
        os.path.join(user_profile, "Downloads"),
        os.path.join(user_profile, "Videos"),
        os.path.join(user_profile, "Pictures"),
        os.path.join(user_profile, "Saved Games"),
    ]
    return [c for c in candidates if os.path.exists(c)]


def get_default_d_roots() -> List[str]:
    """Retorna raízes padrão do disco D: se existir, ou fallback vazio."""
    if os.path.exists("D:\\"):
        return ["D:\\"]
    return []


def get_default_game_library_paths() -> List[str]:
    """Retorna pastas conhecidas de bibliotecas de jogos no disco D:.
    
    Essas pastas não entram no fingerprint geral de arquivos (Adendo v1.1 item 2);
    são medidas à parte pelo inventário de jogos.
    """
    candidates = [
        "D:\\Games",
        "D:\\SteamLibrary",
        "D:\\Steam",
        "D:\\Epic Games",
        "D:\\XboxGames",
        "D:\\GOG Games",
        "D:\\Riot Games",
        "D:\\Ubisoft",
        "D:\\Battle.net",
        "D:\\EA Games",
        "D:\\Hydra",
    ]
    return [c for c in candidates if os.path.exists(c)]


def get_save_candidate_roots() -> List[str]:
    """Retorna diretórios candidatos a conter saves e dados de configuração de jogos.
    
    Conforme Adendo v1.1 item 5, inclui:
    - %USERPROFILE%\\Documents (e subpastas My Games)
    - %USERPROFILE%\\Saved Games
    - %APPDATA% (Roaming)
    - %LOCALAPPDATA%
    - %USERPROFILE%\\AppData\\LocalLow
    - C:\\Users\\Public\\Documents (emuladores de repack)
    - %ProgramData% (ex: Steam emu)
    """
    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    public_profile = os.environ.get("PUBLIC", "C:\\Users\\Public")
    appdata = os.environ.get("APPDATA", os.path.join(user_profile, "AppData", "Roaming"))
    localappdata = os.environ.get("LOCALAPPDATA", os.path.join(user_profile, "AppData", "Local"))
    programdata = os.environ.get("ProgramData", "C:\\ProgramData")

    candidates = [
        os.path.join(user_profile, "Documents"),
        os.path.join(user_profile, "Saved Games"),
        appdata,
        localappdata,
        os.path.join(user_profile, "AppData", "LocalLow"),
        os.path.join(public_profile, "Documents"),
        os.path.join(programdata, "Steam"),
        os.path.join(programdata, "Epic"),
    ]
    return [c for c in candidates if os.path.exists(c)]


KNOWN_USER_FOLDERS: Dict[str, str] = {
    "documents": "Documents",
    "documentos": "Documents",
    "downloads": "Downloads",
    "desktop": "Desktop",
    "area de trabalho": "Desktop",
    "videos": "Videos",
    "pictures": "Pictures",
    "imagens": "Pictures",
    "fotos": "Pictures",
    "saved games": "Saved Games",
    "savedgames": "Saved Games",
    "jogos salvos": "Saved Games",
    "music": "Music",
    "musicas": "Music",
}


def parse_cli_path_list(
    raw_inputs: Optional[List[str]],
    is_c_roots: bool = False,
) -> tuple[List[str], List[tuple[str, str]]]:
    """Processa tokens brutos de caminho vindos do argparse / CLI.
    
    Suporta:
    - Separação por ponto-e-vírgula (;), vírgula (,) e espaços múltiplos
    - Aspas duplas e simples residuais
    - Barras invertidas no final com aspas (ex: D:\\" que no Windows gera D:\\")
    - Variáveis de ambiente (%USERPROFILE%, $env:USERPROFILE, etc.)
    - Nomes amigáveis de pastas do usuário no C: (Documents, Downloads, etc.)
    
    Retorna:
        (caminhos_validos_existentes, lista_de_tuplas_invalidas_com_motivo)
    """
    import re
    if not raw_inputs:
        return [], []

    raw_tokens: List[str] = []
    for item in raw_inputs:
        if not item:
            continue
        parts = re.split(r"[;,]", item)
        for p in parts:
            cleaned = p.strip().strip("\"'").strip()
            if cleaned:
                raw_tokens.append(cleaned)

    valid_paths: List[str] = []
    invalid_paths: List[tuple[str, str]] = []
    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))

    for token in raw_tokens:
        # 1. Expandir variáveis de ambiente
        expanded = os.path.expandvars(os.path.expanduser(token))
        expanded = expanded.rstrip("\"'").strip()

        if not expanded:
            continue

        # 2. Verificar se é alias de pasta padrão do usuário
        token_lower = expanded.lower()
        if token_lower in KNOWN_USER_FOLDERS:
            folder_name = KNOWN_USER_FOLDERS[token_lower]
            candidate = os.path.join(user_profile, folder_name)
            if os.path.exists(candidate):
                norm_p = os.path.abspath(candidate)
                if norm_p not in valid_paths:
                    valid_paths.append(norm_p)
                continue

        # 3. Verificar caminho como caminho absoluto ou relativo ao CWD
        abs_candidate = os.path.abspath(expanded)
        if os.path.exists(abs_candidate):
            if abs_candidate not in valid_paths:
                valid_paths.append(abs_candidate)
            continue

        # 4. Se for raiz de C: e não achou, tentar em %USERPROFILE%/<expanded>
        if is_c_roots:
            user_cand = os.path.join(user_profile, expanded)
            if os.path.exists(user_cand):
                norm_user = os.path.abspath(user_cand)
                if norm_user not in valid_paths:
                    valid_paths.append(norm_user)
                continue

        invalid_paths.append((token, f"Caminho não existe no sistema de arquivos: '{abs_candidate}'"))

    return valid_paths, invalid_paths


@dataclass
class AuditorConfig:
    """Configuração operacional do Auditor (Motor 1)."""

    c_roots: List[str] = field(default_factory=get_default_c_roots)
    d_roots: List[str] = field(default_factory=get_default_d_roots)
    game_library_paths: List[str] = field(default_factory=get_default_game_library_paths)
    save_candidate_roots: List[str] = field(default_factory=get_save_candidate_roots)
    output_dir: str = "reports"
    cache_db_path: str = os.path.join("reports", "storage_auditor_cache.db")
    full_hash_threshold_bytes: int = DEFAULT_FULL_HASH_THRESHOLD_BYTES
    sample_chunk_size: int = SAMPLE_CHUNK_SIZE
    max_save_scan_depth: int = 3
    test_mode: bool = False
    verbose: bool = False
    schema_version: str = SCHEMA_VERSION
