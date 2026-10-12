"""Wrapper seguro para listar aplicativos da Microsoft Store e Xbox Games.

Utiliza chamadas assíncronas do PowerShell (Get-AppxPackage e Get-AppxVolume) para contornar
bloqueios do TrustedInstaller na pasta WindowsApps sem exigir elevação de privilégios perigosa.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("storage_auditor.windows_apps")

CREATE_NO_WINDOW: int = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class WindowsAppInfo:
    """Metadados de um aplicativo instalado da Microsoft Store / Xbox."""

    name: str
    package_full_name: str
    install_location: str
    drive: str
    size_bytes: int = 0


class WindowsAppsInspector:
    """Coletor de aplicativos e jogos da Microsoft Store / Xbox via PowerShell."""

    def __init__(self, timeout_seconds: int = 20) -> None:
        self.timeout = timeout_seconds

    def list_installed_apps(self, target_drive_letter: Optional[str] = None) -> List[WindowsAppInfo]:
        """Obtém lista de pacotes AppX instalados no Windows."""
        if sys.platform != "win32":
            logger.debug("Plataforma não-Windows detectada. Pulando consulta AppX.")
            return []

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Get-AppxPackage | Select-Object Name, PackageFullName, InstallLocation | ConvertTo-Json -Compress",
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="replace",
            )
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
            logger.warning("Não foi possível executar o PowerShell para coletar AppX: %s", exc)
            return []

        if res.returncode != 0 or not res.stdout.strip():
            logger.debug("PowerShell retornou código %s ou saída vazia.", res.returncode)
            return []

        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            logger.warning("Falha ao decodificar JSON retornado pelo PowerShell: %s", exc)
            return []

        # PowerShell pode retornar um único dicionário se houver apenas 1 resultado
        if isinstance(data, dict):
            items = [data]
        elif isinstance(data, list):
            items = data
        else:
            return []

        apps: List[WindowsAppInfo] = []
        for item in items:
            name = item.get("Name") or ""
            pkg = item.get("PackageFullName") or ""
            loc = item.get("InstallLocation") or ""

            if not loc:
                continue

            drive = os.path.splitdrive(loc)[0].upper() or "C:"

            # Filtra por unidade se solicitado (ex: apenas jogos instalados em D:)
            if target_drive_letter:
                target_clean = target_drive_letter.rstrip(":").upper() + ":"
                if drive != target_clean:
                    continue

            # Calcula tamanho se a pasta for legível
            size_bytes = 0
            try:
                if os.path.exists(loc):
                    for entry in os.scandir(loc):
                        if entry.is_file(follow_symlinks=False):
                            size_bytes += entry.stat(follow_symlinks=False).st_size
            except Exception:
                pass

            apps.append(
                WindowsAppInfo(
                    name=name,
                    package_full_name=pkg,
                    install_location=loc,
                    drive=drive,
                    size_bytes=size_bytes,
                )
            )

        return apps
