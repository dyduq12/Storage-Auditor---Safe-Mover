"""Mapeamento agnóstico de jogos e heurística reversa de saves.

Detecta títulos via Steam (manifestos VDF e ACF), Epic Games (.item), instalações avulsas
(FitGirl, Hydra, pastas em D:\\Games) e jogos da Microsoft Store / Xbox via WindowsApps.

Cruza os títulos com diretórios de saves no disco C: (Documents, Saved Games, AppData,
Public Documents e ProgramData) com busca de profundidade controlada e nível de certeza.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from config import get_save_candidate_roots
from core.scanner import normalize_windows_path, strip_long_path_prefix
from core.windows_apps import WindowsAppsInspector

logger = logging.getLogger("storage_auditor.game_mapper")


@dataclass
class GameSaveInfo:
    """Informações de um diretório de save ou dados associado a um jogo."""

    caminho_save: str
    tamanho_bytes: int
    quantidade_arquivos: int
    nivel_certeza: str  # "alta", "media", "baixa"
    mtime: float


@dataclass
class GameInventoryItem:
    """Inventário de um jogo detectado no armazenamento secundário."""

    nome: str
    launcher: str  # "Steam", "Epic Games", "FitGirl/Hydra/Avulso", "Microsoft Store/Xbox"
    caminho_instalacao: str
    tamanho_instalacao_bytes: int
    executaveis_principais: List[str] = field(default_factory=list)
    saves_em_c: List[GameSaveInfo] = field(default_factory=list)
    tamanho_total_saves_bytes: int = 0


def parse_vdf_simple(content: str) -> dict:
    """Parser simplificado para arquivos Valve Data Format (VDF / ACF)."""
    # Remove comentários C++ (//)
    cleaned_lines = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("//"):
            continue
        cleaned_lines.append(line)
    
    tokens = re.findall(r'"([^"]*)"|(\{|\})', "\n".join(cleaned_lines))
    
    root: dict = {}
    stack: list[dict] = [root]
    current_key = None

    for tok in tokens:
        val = tok[0] if tok[0] else tok[1]
        if val == "{":
            new_dict: dict = {}
            if current_key is not None:
                stack[-1][current_key] = new_dict
                stack.append(new_dict)
                current_key = None
            else:
                stack.append(new_dict)
        elif val == "}":
            if len(stack) > 1:
                stack.pop()
        else:
            if current_key is None:
                current_key = val
            else:
                stack[-1][current_key] = val
                current_key = None

    return root


def sanitize_game_name(name: str) -> str:
    """Remove caracteres especiais e termos comuns para comparação flexível."""
    cleaned = re.sub(r"[^\w\s]", " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def calculate_folder_size_and_mtime(
    folder_path: str, max_depth: int = 5, max_files: int = 250000
) -> tuple[int, int, float]:
    """Calcula tamanho total em bytes, contagem de arquivos e mtime mais recente de uma pasta de forma rápida."""
    total_size = 0
    file_count = 0
    latest_mtime = 0.0

    try:
        norm_path = normalize_windows_path(folder_path)
        queue: list[tuple[str, int]] = [(norm_path, 0)]
        while queue and file_count < max_files:
            curr_dir, depth = queue.pop()
            try:
                with os.scandir(curr_dir) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if depth < max_depth:
                                    queue.append((entry.path, depth + 1))
                            elif entry.is_file(follow_symlinks=False):
                                st = entry.stat(follow_symlinks=False)
                                total_size += st.st_size
                                file_count += 1
                                if st.st_mtime > latest_mtime:
                                    latest_mtime = st.st_mtime
                        except (PermissionError, FileNotFoundError, OSError):
                            continue
            except (PermissionError, FileNotFoundError, OSError):
                continue
    except Exception:
        pass

    return total_size, file_count, latest_mtime


class GameMapper:
    """Mapeador de jogos em disco e rastreador de dependências e saves."""

    def __init__(
        self,
        save_roots: Optional[List[str]] = None,
        max_save_depth: int = 3,
        windows_apps_inspector: Optional[WindowsAppsInspector] = None,
    ) -> None:
        self.save_roots = save_roots or get_save_candidate_roots()
        self.max_save_depth = max_save_depth
        self.windows_apps_inspector = windows_apps_inspector or WindowsAppsInspector()

    def discover_steam_games(self, library_folders: List[str]) -> List[GameInventoryItem]:
        """Descobre jogos do Steam analisando manifests ACF e libraryfolders.vdf / libraryfolder.vdf."""
        games: List[GameInventoryItem] = []
        steamapps_dirs: Set[str] = set()

        for base in library_folders:
            norm_base = os.path.abspath(base)
            # Verifica se o base já é ou contém steamapps
            if os.path.basename(norm_base).lower() == "steamapps":
                steamapps_dirs.add(norm_base)
            else:
                cand = os.path.join(norm_base, "steamapps")
                if os.path.exists(cand):
                    steamapps_dirs.add(cand)

            # Verifica se há libraryfolders.vdf ou libraryfolder.vdf (singular)
            for vdf_name in ["libraryfolders.vdf", "libraryfolder.vdf"]:
                vdf_path = os.path.join(norm_base, "steamapps", vdf_name)
                if not os.path.exists(vdf_path):
                    vdf_path = os.path.join(norm_base, vdf_name)

                if os.path.exists(vdf_path):
                    try:
                        with open(vdf_path, "r", encoding="utf-8", errors="ignore") as f:
                            data = parse_vdf_simple(f.read())
                        libs = data.get("libraryfolders", {})
                        if not libs and "libraryfolder" in data:
                            libs = {"0": data.get("libraryfolder", {})}
                        for lib_data in libs.values():
                            if isinstance(lib_data, dict) and "path" in lib_data:
                                lib_p = os.path.join(lib_data["path"], "steamapps")
                                if os.path.exists(lib_p):
                                    steamapps_dirs.add(os.path.abspath(lib_p))
                    except Exception as exc:
                        logger.warning("Falha ao ler %s em %s: %s", vdf_name, vdf_path, exc)

        for s_dir in steamapps_dirs:
            common_dir = os.path.join(s_dir, "common")
            try:
                for entry in os.scandir(s_dir):
                    if entry.is_file() and entry.name.startswith("appmanifest_") and entry.name.endswith(".acf"):
                        try:
                            with open(entry.path, "r", encoding="utf-8", errors="ignore") as acf_f:
                                acf_data = parse_vdf_simple(acf_f.read())
                            app_state = acf_data.get("AppState", {})
                            name = app_state.get("name")
                            install_dir_name = app_state.get("installdir")
                            size_on_disk = int(app_state.get("SizeOnDisk", 0))

                            if name and install_dir_name:
                                full_install_path = os.path.join(common_dir, install_dir_name)
                                if os.path.exists(full_install_path):
                                    # Se não houver tamanho no manifest ACF, calcula em disco
                                    if size_on_disk <= 0:
                                        size_on_disk, _, _ = calculate_folder_size_and_mtime(full_install_path)

                                    games.append(
                                        GameInventoryItem(
                                            nome=name,
                                            launcher="Steam",
                                            caminho_instalacao=full_install_path,
                                            tamanho_instalacao_bytes=size_on_disk,
                                        )
                                    )
                        except Exception as acf_err:
                            logger.debug("Falha ao analisar %s: %s", entry.path, acf_err)
            except Exception as scan_err:
                logger.warning("Falha ao varrer pasta steamapps %s: %s", s_dir, scan_err)

        return games

    def discover_epic_games(self) -> List[GameInventoryItem]:
        """Descobre jogos instalados via Epic Games Launcher através dos manifestos .item."""
        games: List[GameInventoryItem] = []
        programdata = os.environ.get("ProgramData", "C:\\ProgramData")
        manifest_dir = os.path.join(programdata, "Epic", "EpicGamesLauncher", "Data", "Manifests")

        if not os.path.exists(manifest_dir):
            return games

        try:
            for entry in os.scandir(manifest_dir):
                if entry.is_file() and entry.name.endswith(".item"):
                    try:
                        with open(entry.path, "r", encoding="utf-8", errors="ignore") as f:
                            item_data = json.load(f)
                        name = item_data.get("DisplayName")
                        install_loc = item_data.get("InstallLocation")
                        size = int(item_data.get("InstallationSize", 0))

                        if name and install_loc and os.path.exists(install_loc):
                            if size <= 0:
                                size, _, _ = calculate_folder_size_and_mtime(install_loc)
                            games.append(
                                GameInventoryItem(
                                    nome=name,
                                    launcher="Epic Games",
                                    caminho_instalacao=install_loc,
                                    tamanho_instalacao_bytes=size,
                                )
                            )
                    except Exception as item_err:
                        logger.debug("Falha ao ler manifesto Epic %s: %s", entry.path, item_err)
        except Exception as exc:
            logger.warning("Falha ao varrer manifestos Epic Games: %s", exc)

        return games

    def discover_standalone_games(self, game_roots: List[str]) -> List[GameInventoryItem]:
        """Descobre jogos em pastas como D:\\Games, FitGirl, Hydra e pastas portáteis."""
        games: List[GameInventoryItem] = []
        ignored_names = {
            "steamapps",
            "common",
            "downloading",
            "workshop",
            "shadercache",
            "temp",
            "$recycle.bin",
            "system volume information",
        }

        for root in game_roots:
            if not os.path.exists(root):
                continue

            is_xbox_root = "xboxgames" in root.lower() or os.path.exists(os.path.join(root, ".GamingRoot"))

            try:
                for entry in os.scandir(root):
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() in ignored_names:
                            continue

                        dir_path = entry.path
                        executables: List[str] = []

                        # Busca executáveis no diretório raiz e até 2 níveis de subpastas
                        try:
                            # 1. Raiz do jogo
                            for sub in os.scandir(dir_path):
                                if sub.is_file() and sub.name.lower().endswith(".exe"):
                                    s_name = sub.name.lower()
                                    if not any(ign in s_name for ign in ["unins", "crash", "reporter", "unity"]):
                                        executables.append(sub.name)
                                elif sub.is_dir(follow_symlinks=False) and len(executables) == 0:
                                    # 2. Subpasta direta (ex: Binaries/Win64)
                                    try:
                                        for sub2 in os.scandir(sub.path):
                                            if sub2.is_file() and sub2.name.lower().endswith(".exe"):
                                                s_name2 = sub2.name.lower()
                                                if not any(ign in s_name2 for ign in ["unins", "crash", "reporter"]):
                                                    executables.append(sub2.name)
                                            elif sub2.is_dir(follow_symlinks=False) and len(executables) == 0:
                                                # 3. Terceiro nível
                                                try:
                                                    for sub3 in os.scandir(sub2.path):
                                                        if sub3.is_file() and sub3.name.lower().endswith(".exe"):
                                                            s_name3 = sub3.name.lower()
                                                            if not any(ign in s_name3 for ign in ["unins", "crash"]):
                                                                executables.append(sub3.name)
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                        size, _, _ = calculate_folder_size_and_mtime(dir_path)
                        # Considera se tiver tamanho significativo (> 10 MB)
                        if size > 10 * 1024 * 1024:
                            launcher_name = "XboxGames" if is_xbox_root else "FitGirl/Hydra/Avulso"
                            games.append(
                                GameInventoryItem(
                                    nome=entry.name,
                                    launcher=launcher_name,
                                    caminho_instalacao=dir_path,
                                    tamanho_instalacao_bytes=size,
                                    executaveis_principais=executables,
                                )
                            )
            except Exception as exc:
                logger.warning("Falha ao varrer pasta de jogos avulsos %s: %s", root, exc)

        return games

    def discover_windows_store_games(self, target_drive_letter: str = "D:") -> List[GameInventoryItem]:
        """Descobre jogos da Microsoft Store / Xbox instalados no disco alvo."""
        games: List[GameInventoryItem] = []
        apps = self.windows_apps_inspector.list_installed_apps(target_drive_letter=target_drive_letter)
        for app in apps:
            # Filtra componentes essenciais do sistema
            if app.name.startswith("Microsoft.") or app.name.startswith("Windows."):
                continue
            games.append(
                GameInventoryItem(
                    nome=app.name,
                    launcher="Microsoft Store/Xbox",
                    caminho_instalacao=app.install_location,
                    tamanho_instalacao_bytes=app.size_bytes,
                )
            )
        return games

    def find_game_saves(self, game_name: str, executables: List[str]) -> List[GameSaveInfo]:
        """Busca heurística reversa de diretórios de saves no disco C: com profundidade limitada.
        
        Atende ao Adendo v1.1 requisito 5.
        """
        saves_found: List[GameSaveInfo] = []
        sanitized_title = sanitize_game_name(game_name)
        title_tokens = set(sanitized_title.split())

        # Tokens de executáveis
        exe_tokens = {
            sanitize_game_name(os.path.splitext(e)[0])
            for e in executables
        }

        for save_root in self.save_roots:
            if not os.path.exists(save_root):
                continue

            # Busca em árvore com profundidade máxima restrita
            queue: List[tuple[str, int]] = [(os.path.abspath(save_root), 0)]

            while queue:
                current_dir, depth = queue.pop(0)
                if depth >= self.max_save_depth:
                    continue

                try:
                    for entry in os.scandir(current_dir):
                        if entry.is_dir(follow_symlinks=False):
                            entry_name_clean = sanitize_game_name(entry.name)
                            entry_tokens = set(entry_name_clean.split())

                            # Classificação de Certeza (Adendo v1.1 requisito 5)
                            certainty: Optional[str] = None
                            is_primary_save_dir = (
                                "saved games" in current_dir.lower()
                                or "my games" in current_dir.lower()
                                or "documents" in current_dir.lower()
                            )

                            # 1. Correspondência direta por título (exato ou sub-título com >3 caracteres)
                            if (
                                entry_name_clean == sanitized_title
                                or (len(sanitized_title) > 3 and sanitized_title in entry_name_clean)
                                or (len(entry_name_clean) > 3 and entry_name_clean in sanitized_title)
                            ):
                                certainty = "alta" if is_primary_save_dir else "media"
                            # 2. Correspondência com executável principal do jogo
                            elif any(et == entry_name_clean for et in exe_tokens if len(et) > 3):
                                certainty = "alta" if is_primary_save_dir else "media"
                            # 3. Conjunto de tokens como subconjunto (ex: "The Witcher 3" dentro de "The Witcher 3: Wild Hunt")
                            elif (
                                (len(title_tokens) > 1 and title_tokens.issubset(entry_tokens))
                                or (len(entry_tokens) > 1 and entry_tokens.issubset(title_tokens))
                            ):
                                certainty = "alta" if is_primary_save_dir else "media"
                            # 4. Interseção parcial de tokens relevantes
                            elif len(title_tokens) >= 2 and len(title_tokens.intersection(entry_tokens)) >= 2:
                                certainty = "media" if is_primary_save_dir else "baixa"

                            if certainty:
                                size, count, mtime = calculate_folder_size_and_mtime(entry.path)
                                if count > 0:
                                    saves_found.append(
                                        GameSaveInfo(
                                            caminho_save=entry.path,
                                            tamanho_bytes=size,
                                            quantidade_arquivos=count,
                                            nivel_certeza=certainty,
                                            mtime=mtime,
                                        )
                                    )
                                    # Não precisa aprofundar dentro de uma pasta de save já detectada
                                    continue

                            queue.append((entry.path, depth + 1))
                except Exception:
                    continue

        return saves_found

    def build_full_inventory(
        self,
        steam_libraries: List[str],
        standalone_roots: List[str],
        include_ms_store: bool = True,
    ) -> List[GameInventoryItem]:
        """Gera inventário completo de jogos e cruza com saves em C:."""
        all_games: List[GameInventoryItem] = []

        # 1. Steam
        all_games.extend(self.discover_steam_games(steam_libraries))

        # 2. Epic Games
        all_games.extend(self.discover_epic_games())

        # 3. Avulsos / FitGirl / Hydra
        all_games.extend(self.discover_standalone_games(standalone_roots))

        # 4. Microsoft Store / Xbox
        if include_ms_store:
            all_games.extend(self.discover_windows_store_games(target_drive_letter="D:"))

        # 5. Cruzar saves para cada jogo
        for game in all_games:
            saves = self.find_game_saves(game.nome, game.executaveis_principais)
            game.saves_em_c = saves
            game.tamanho_total_saves_bytes = sum(s.tamanho_bytes for s in saves)

        return all_games
