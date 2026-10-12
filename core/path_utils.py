import os
import sys

def to_extended_path(path: str) -> str:
    """Converte um caminho absoluto no Windows para formato estendido de caminho longo (\\?\\ ou \\?\\UNC\\).
    
    Regras estritas:
    - Retorna o caminho inalterado se não estiver em ambiente Windows (os.name != 'nt').
    - Retorna o caminho inalterado se for vazio ou relativo (nunca prefixa caminhos relativos).
    - Retorna o caminho inalterado se já começar com \\\\?\\ ou \\\\.\\
    - Para caminhos locais absolutos com letra de drive (ex: C:\\...), prefixa com \\\\?\\
    - Para caminhos de rede UNC (ex: \\\\server\\share\\...), converte para \\\\?\\UNC\\server\\share\\...
    """
    if os.name != "nt" or not path:
        return path

    # Se já possui prefixo de dispositivo / estendido, retorna normalizado
    if path.startswith("\\\\?\\") or path.startswith("\\\\.\\"):
        return path

    # Não prefixa caminhos relativos
    if not os.path.isabs(path):
        return path

    norm = os.path.normpath(path)

    # Caminho UNC de rede: \\server\share\...
    if norm.startswith("\\\\"):
        return "\\\\?\\UNC\\" + norm[2:]

    # Caminho local com letra de unidade: C:\...
    return "\\\\?\\" + norm


def from_extended_path(path: str) -> str:
    """Remove o prefixo estendido (\\\\?\\ ou \\\\?\\UNC\\) para logs e mensagens amigáveis."""
    if not path or not isinstance(path, str):
        return path

    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        return path[4:]
    return path
