import os
import sys
import pytest

# Configura caminhos das bibliotecas Tcl/Tk para estabilidade do Tkinter no Windows/.venv
base_prefix = getattr(sys, "base_prefix", sys.prefix)
tcl_dir = os.path.join(base_prefix, "tcl", "tcl8.6")
tk_dir = os.path.join(base_prefix, "tcl", "tk8.6")

if os.path.isdir(tcl_dir) and "TCL_LIBRARY" not in os.environ:
    os.environ["TCL_LIBRARY"] = tcl_dir
if os.path.isdir(tk_dir) and "TK_LIBRARY" not in os.environ:
    os.environ["TK_LIBRARY"] = tk_dir
