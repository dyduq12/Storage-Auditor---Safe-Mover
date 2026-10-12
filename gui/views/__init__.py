"""Módulo de componentes visuais e abas da interface gráfica."""

from gui.views.tab_documents import DocumentsView
from gui.views.tab_duplicates import DuplicatesView
from gui.views.tab_games import GamesView
from gui.views.tab_pending import PendingView
from gui.views.tab_safe_mover import SafeMoverView
from gui.views.tab_summary import SummaryView

__all__ = [
    "SummaryView",
    "DuplicatesView",
    "PendingView",
    "DocumentsView",
    "GamesView",
    "SafeMoverView",
]
