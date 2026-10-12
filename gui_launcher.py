"""Ponto de entrada direto para iniciar a Interface Gráfica CustomTkinter (Fase 2)."""

import sys
import os

# Adiciona o diretório raiz ao sys.path para garantir importações relativas e absolutas
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.app import StorageAuditorApp


def main() -> None:
    app = StorageAuditorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
