"""Módulo de Triagem e Classificação de Candidatos a Backup (Fase 3: Triage Engine).

Agrupa itens pendentes de backup por pastas e categorias, permitindo ao usuário
tomar decisões em nível de pasta/grupo (Incluir, Excluir, Revisar Depois) com:
- Suporte a herança pai/filho configurável (aplicar a descendentes).
- Precedência de decisão específica: a decisão explícita da subpasta prevalece sobre a do pai.
- Categorização inteligente (Regenerável/Cache, Git, Jogos/GTA, Obsidian, Projetos, Fotos, Compactados, Docs, Outros).
- Inicialização 100% segura: todos os grupos iniciam como 'Revisar depois' (nenhuma inclusão ou exclusão pré-selecionada).
- Cálculo reativo imediato de totais de arquivos e bytes por status de decisão.
"""

from __future__ import annotations

import json
import os
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class TriageDecision:
    UNDECIDED: str = "undecided"  # Revisar depois / Não selecionado
    INCLUDE: str = "include"      # Incluir no plano de backup
    EXCLUDE: str = "exclude"      # Excluir do plano de backup


DECISION_TO_METRIC_KEY: Dict[str, str] = {
    TriageDecision.INCLUDE: "included",
    TriageDecision.EXCLUDE: "excluded",
    TriageDecision.UNDECIDED: "undecided",
}


class TriageCategory:
    REGENERAVEL: str = "regeneravel"          # node_modules, __pycache__, build, etc.
    GIT_REPO: str = "git_repo"                # .git
    GAMES_LAUNCHERS: str = "games_launchers"  # Rockstar, GTA, My Games, launchers
    NOTES_KNOWLEDGE: str = "notes_knowledge"  # Obsidian, Bad notes, markdown vaults
    PROJETOS_DEV: str = "projetos_dev"        # Código fonte (.py, .ts, .js, .rs, etc.)
    FOTOS_IMAGENS: str = "fotos_imagens"      # .jpg, .png, .webp, .raw, .heic
    COMPACTADOS: str = "compactados"          # .zip, .rar, .7z, .tar, .gz
    DOCUMENTOS: str = "documentos"            # .pdf, .docx, .xlsx, .txt, .csv
    OUTROS: str = "outros"                    # Demais arquivos


CATEGORY_LABELS: Dict[str, str] = {
    TriageCategory.REGENERAVEL: "Cache / Regenerável",
    TriageCategory.GIT_REPO: "Repositório Git",
    TriageCategory.GAMES_LAUNCHERS: "Jogos / Launchers",
    TriageCategory.NOTES_KNOWLEDGE: "Anotações / Obsidian",
    TriageCategory.PROJETOS_DEV: "Projetos / Código",
    TriageCategory.FOTOS_IMAGENS: "Fotos / Imagens",
    TriageCategory.COMPACTADOS: "Compactados / Arquivos",
    TriageCategory.DOCUMENTOS: "Documentos",
    TriageCategory.OUTROS: "Outros",
}

# Conjuntos de extensões e padrões por categoria
REGENERABLE_FOLDER_KEYWORDS = {
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".gradle",
    "intermediates",
    "stripped_native_libs",
    ".cache",
}

GIT_FOLDER_KEYWORDS = {".git"}

GAMES_FOLDER_KEYWORDS = {
    "rockstar games",
    "rockstar games launcher",
    "gta",
    "grand theft auto",
    "my games",
    "saved games",
    "ubisoft",
    "steamapps",
    "steam",
    "fitgirl",
    "hydra",
    "launchbox",
}

NOTES_KEYWORDS = {
    ".obsidian",
    "bad notes",
    "notes",
    "vault",
    "anotacoes",
}

PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".avif", ".heic", ".heif",
    ".raw", ".cr2", ".nef", ".arw", ".dng", ".bmp", ".tiff", ".tif", ".psd"
}

ARCHIVE_EXTENSIONS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".img"
}

DOC_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".txt", ".rtf", ".csv"
}

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".rs", ".go", ".c", ".cpp",
    ".h", ".java", ".kt", ".gradle", ".sh", ".ps1", ".bat"
}

REGENERABLE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".map", ".class", ".o", ".obj"
}


def classify_file_category(file_path: str) -> str:
    """Classifica a categoria de um arquivo baseando-se em seu caminho e extensão."""
    p_lower = file_path.lower().replace("/", "\\")
    parts = p_lower.split("\\")
    ext = os.path.splitext(p_lower)[1]

    # 1. Caches e pastas regeneráveis
    for part in parts:
        if part in REGENERABLE_FOLDER_KEYWORDS:
            return TriageCategory.REGENERAVEL
        if part == "build" and ("android" in p_lower or "app" in p_lower or "node_modules" in p_lower):
            return TriageCategory.REGENERAVEL

    if ext in REGENERABLE_EXTENSIONS:
        return TriageCategory.REGENERAVEL

    # 2. Repositório Git (não é considerado lixo, mas categoria isolada)
    for part in parts:
        if part in GIT_FOLDER_KEYWORDS:
            return TriageCategory.GIT_REPO

    # 3. Jogos e Launchers
    for part in parts:
        for kw in GAMES_FOLDER_KEYWORDS:
            if kw in part:
                return TriageCategory.GAMES_LAUNCHERS

    # 4. Anotações e Obsidian
    for part in parts:
        for kw in NOTES_KEYWORDS:
            if kw in part:
                return TriageCategory.NOTES_KNOWLEDGE

    # 5. Fotos e Imagens
    if ext in PHOTO_EXTENSIONS:
        return TriageCategory.FOTOS_IMAGENS

    # 6. Compactados
    if ext in ARCHIVE_EXTENSIONS:
        return TriageCategory.COMPACTADOS

    # 7. Documentos
    if ext in DOC_EXTENSIONS:
        return TriageCategory.DOCUMENTOS

    # 8. Código fonte
    if ext in CODE_EXTENSIONS or ext == ".md":
        return TriageCategory.PROJETOS_DEV

    return TriageCategory.OUTROS


@dataclass
class FolderGroup:
    """Representa uma pasta agrupada com métricas consolidadas e estado de decisão."""

    path: str
    name: str
    parent_path: Optional[str]
    direct_files: int = 0
    direct_bytes: int = 0
    total_files: int = 0
    total_bytes: int = 0
    categories: Set[str] = field(default_factory=set)
    extensions: Counter = field(default_factory=Counter)
    explicit_decision: Optional[str] = None  # None significa sem decisão própria explícita
    apply_to_children: bool = True
    suggested_action: Optional[str] = None   # ex: "exclude" para regeneráveis

    @property
    def category_labels(self) -> List[str]:
        return [CATEGORY_LABELS.get(c, c) for c in sorted(self.categories)]

    @property
    def is_mostly_regenerable(self) -> bool:
        return TriageCategory.REGENERAVEL in self.categories and len(self.categories) == 1

    @property
    def friendly_display_name(self) -> str:
        """Retorna nome amigável com ícone contextual."""
        name_lower = self.name.lower()
        if name_lower == "desktop":
            return "🖥️ Área de Trabalho (Desktop)"
        elif name_lower == "downloads":
            return "📥 Downloads"
        elif name_lower == "documents":
            return "📄 Documentos (Documents)"
        elif name_lower == "pictures":
            return "🖼️ Imagens / Fotos (Pictures)"
        elif name_lower == "music":
            return "🎵 Músicas (Music)"
        elif name_lower == "videos":
            return "🎬 Vídeos (Videos)"

        if TriageCategory.GAMES_LAUNCHERS in self.categories:
            icon = "🎮"
        elif TriageCategory.NOTES_KNOWLEDGE in self.categories:
            icon = "📝"
        elif TriageCategory.PROJETOS_DEV in self.categories:
            icon = "💻"
        elif TriageCategory.FOTOS_IMAGENS in self.categories:
            icon = "🖼️"
        elif TriageCategory.REGENERAVEL in self.categories:
            icon = "⚙️"
        else:
            icon = "📁"
        return f"{icon} {self.name}"


PRIORITY_USER_FOLDERS = ["desktop", "downloads", "documents", "pictures", "music", "videos"]

FRIENDLY_FOLDER_NAMES = {
    "desktop": "Área de Trabalho (Desktop)",
    "downloads": "Downloads",
    "documents": "Documentos (Documents)",
    "pictures": "Imagens / Fotos (Pictures)",
    "music": "Músicas (Music)",
    "videos": "Vídeos (Videos)",
}


class TriageManager:
    """Gerenciador de triagem hierárquica e cálculo reativo de decisões."""

    def __init__(self, pending_items: Optional[List[Dict[str, Any]]] = None) -> None:
        self.raw_items: List[Dict[str, Any]] = list(pending_items or [])
        self.item_categories: List[str] = []
        self.file_to_item_idx: Dict[str, int] = {}

        # Mapeamento de pastas
        self.folders: Dict[str, FolderGroup] = {}
        self.child_folders: Dict[str, List[str]] = {}
        self.files_by_folder: Dict[str, List[int]] = {}

        # Decisões explícitas do usuário: path -> (decision, apply_to_children)
        self.explicit_decisions: Dict[str, Tuple[str, bool]] = {}

        # Cache de resolução para performance: path -> (resolved_decision, origin_path, is_explicit)
        self._cache_resolved: Dict[str, Tuple[str, str, bool]] = {}

        # Estado efetivo e métricas pré-computadas para atualização reativa em O(delta)
        self._folder_effective: Dict[str, str] = {}
        self._metrics: Dict[str, int] = {}

        self._build_hierarchy()

    def get_root_groups(self) -> List[FolderGroup]:
        """Retorna as raízes principais reais da auditoria numa ordem previsível e amigável:
        Desktop, Downloads, Documents, Pictures, Music, Videos; seguidas por outras raízes."""
        roots: List[FolderGroup] = []
        for path, grp in self.folders.items():
            if grp.total_files == 0:
                continue
            parts = Path(path).parts
            # Raízes sob C:\Users\<user>\<pasta>
            if len(parts) == 4 and parts[1].lower() == "users":
                roots.append(grp)
            # Raízes de dados em outros drives ou fora de Users (ex: D:\Games, C:\Projetos)
            elif len(parts) == 2 and parts[1].lower() not in ("users", "windows", "program files", "program files (x86)"):
                roots.append(grp)
            # Scans em pastas com arquivos diretos na raiz absoluta
            elif len(parts) == 1 and grp.direct_files > 0:
                if grp not in roots:
                    roots.append(grp)


        priority_map = {name: i + 1 for i, name in enumerate(PRIORITY_USER_FOLDERS)}

        def sort_key(g: FolderGroup):
            p_name = Path(g.path).parts[-1].lower()
            prio = priority_map.get(p_name, 100)
            return (prio, -g.total_bytes, g.name)

        roots.sort(key=sort_key)
        return roots

    def get_child_folders(self, parent_path: str) -> List[FolderGroup]:
        """Retorna as subpastas filhas imediatas de um caminho pai, ordenadas por tamanho desc."""
        child_paths = self.child_folders.get(parent_path, [])
        children = [self.folders[cp] for cp in child_paths if cp in self.folders and self.folders[cp].total_files > 0]
        children.sort(key=lambda g: (-g.total_bytes, g.name))
        return children


    def _build_hierarchy(self) -> None:
        """Constrói a hierarquia de pastas e métricas a partir dos itens pendentes."""
        self.folders.clear()
        self.child_folders.clear()
        self.files_by_folder.clear()
        self.item_categories = []
        self.file_to_item_idx.clear()

        # 1. Classifica cada item e agrupa por diretório imediato
        for idx, item in enumerate(self.raw_items):
            file_path = item.get("caminho", "")
            size = int(item.get("tamanho_bytes", 0))
            cat = classify_file_category(file_path)
            self.item_categories.append(cat)
            self.file_to_item_idx[file_path.lower()] = idx

            folder_path = os.path.dirname(os.path.abspath(file_path)) if file_path else ""
            if not folder_path:
                continue

            norm_folder = os.path.normpath(folder_path)
            if norm_folder not in self.files_by_folder:
                self.files_by_folder[norm_folder] = []
            self.files_by_folder[norm_folder].append(idx)

        # 2. Descobre todos os nós de diretório e seus pais até a raiz
        all_dirs: Set[str] = set()
        for f in self.files_by_folder.keys():
            curr = f
            while curr:
                all_dirs.add(curr)
                parent = os.path.dirname(curr)
                if parent == curr:
                    break
                curr = parent

        # 3. Inicializa cada FolderGroup
        for d in all_dirs:
            parent = os.path.dirname(d)
            parent_path = parent if parent and parent != d else None
            name = os.path.basename(d) or d

            self.folders[d] = FolderGroup(
                path=d,
                name=name,
                parent_path=parent_path,
                direct_files=0,
                direct_bytes=0,
                total_files=0,
                total_bytes=0,
                categories=set(),
                extensions=Counter(),
                explicit_decision=None,
                apply_to_children=True,
            )
            if parent_path:
                if parent_path not in self.child_folders:
                    self.child_folders[parent_path] = []
                self.child_folders[parent_path].append(d)

        # 4. Atribui arquivos diretos e categorias aos nós
        for folder_path, item_indices in self.files_by_folder.items():
            grp = self.folders.get(folder_path)
            if not grp:
                continue
            grp.direct_files = len(item_indices)
            for idx in item_indices:
                it = self.raw_items[idx]
                sz = int(it.get("tamanho_bytes", 0))
                ext = os.path.splitext(it.get("caminho", ""))[1].lower()
                cat = self.item_categories[idx]
                grp.direct_bytes += sz
                grp.categories.add(cat)
                grp.extensions[ext] += 1

        # 5. Propaga totais acumulados de baixo para cima (bottom-up)
        # Ordena diretórios por profundidade decrescente
        sorted_dirs = sorted(all_dirs, key=lambda p: len(Path(p).parts), reverse=True)
        for d in sorted_dirs:
            grp = self.folders[d]
            grp.total_files += grp.direct_files
            grp.total_bytes += grp.direct_bytes
            if grp.parent_path and grp.parent_path in self.folders:
                parent_grp = self.folders[grp.parent_path]
                parent_grp.total_files += grp.total_files
                parent_grp.total_bytes += grp.total_bytes
                parent_grp.categories.update(grp.categories)
                for ext, cnt in grp.extensions.items():
                    parent_grp.extensions[ext] += cnt

        # 6. Identifica sugestão para regeneráveis (apenas sugestão, decisão começa como UNDECIDED!)
        for d, grp in self.folders.items():
            parts_lower = [p.lower() for p in Path(d).parts]
            is_regen = any(p in REGENERABLE_FOLDER_KEYWORDS for p in parts_lower)
            if is_regen or (TriageCategory.REGENERAVEL in grp.categories and len(grp.categories) == 1):
                grp.suggested_action = TriageDecision.EXCLUDE

        # 7. Inicializa estado de decisões efetivas e agregados de KPI em O(1)
        self._folder_effective = {d: TriageDecision.UNDECIDED for d in self.folders}
        tot_files = len(self.raw_items)
        tot_bytes = sum(g.direct_bytes for g in self.folders.values())
        self._metrics = {
            "total_files": tot_files,
            "total_bytes": tot_bytes,
            "included_files": 0,
            "included_bytes": 0,
            "excluded_files": 0,
            "excluded_bytes": 0,
            "undecided_files": tot_files,
            "undecided_bytes": tot_bytes,
        }

    def set_decision(
        self,
        folder_path: str,
        decision: str,
        apply_to_children: bool = True,
    ) -> List[str]:
        """Define a decisão explícita para uma pasta e propaga via delta para métricas.
        
        Retorna a lista de caminhos de diretórios cujas decisões visuais ou efetivas foram afetadas.
        Se decision for UNDECIDED, remove a decisão explícita (volta a herdar ou padrão).
        """
        norm_path = os.path.normpath(os.path.abspath(folder_path))

        if decision == TriageDecision.UNDECIDED or decision is None:
            if norm_path in self.explicit_decisions:
                del self.explicit_decisions[norm_path]
            if norm_path in self.folders:
                self.folders[norm_path].explicit_decision = None
        else:
            self.explicit_decisions[norm_path] = (decision, apply_to_children)
            if norm_path in self.folders:
                self.folders[norm_path].explicit_decision = decision
                self.folders[norm_path].apply_to_children = apply_to_children

        # Invalida cache de resolução
        self._cache_resolved.clear()

        # Determina o impacto na subárvore e atualiza métricas por delta
        affected: List[str] = []
        if norm_path in self.folders:
            # Descobre se há decisão herdada de algum ancestral para norm_path
            parent_inherited: Optional[str] = None
            curr_p = self.folders[norm_path].parent_path
            while curr_p:
                if curr_p in self.explicit_decisions:
                    p_dec, p_apply = self.explicit_decisions[curr_p]
                    if p_apply:
                        parent_inherited = p_dec
                        break
                parent_grp = self.folders.get(curr_p)
                curr_p = parent_grp.parent_path if parent_grp else None

            # Percorre a subárvore top-down (BFS) a partir de norm_path
            queue = deque([(norm_path, parent_inherited)])
            while queue:
                curr, inherited = queue.popleft()
                if curr in self.explicit_decisions:
                    exp_dec, exp_apply = self.explicit_decisions[curr]
                    eff_dec = exp_dec
                    child_inherited = exp_dec if exp_apply else inherited
                else:
                    eff_dec = inherited if inherited else TriageDecision.UNDECIDED
                    child_inherited = inherited

                old_eff = self._folder_effective.get(curr, TriageDecision.UNDECIDED)
                if old_eff != eff_dec:
                    self._folder_effective[curr] = eff_dec
                    grp = self.folders.get(curr)
                    if grp:
                        df = grp.direct_files
                        db = grp.direct_bytes
                        if df > 0 or db > 0:
                            old_k = DECISION_TO_METRIC_KEY.get(old_eff, "undecided")
                            new_k = DECISION_TO_METRIC_KEY.get(eff_dec, "undecided")
                            self._metrics[f"{old_k}_files"] -= df
                            self._metrics[f"{old_k}_bytes"] -= db
                            self._metrics[f"{new_k}_files"] += df
                            self._metrics[f"{new_k}_bytes"] += db
                    affected.append(curr)
                elif curr == norm_path:
                    # Mesmo se a decisão efetiva não mudou, o status explícito/origem mudou
                    affected.append(curr)

                for ch in self.child_folders.get(curr, []):
                    queue.append((ch, child_inherited))

        return affected

    def save_decisions_to_json(self, filepath: str) -> None:
        """Salva as decisões explícitas de triagem em arquivo JSON."""
        data = {
            "version": "1.0.0",
            "explicit_decisions": {
                path: {"decision": dec, "apply_to_children": apply_children}
                for path, (dec, apply_children) in self.explicit_decisions.items()
            }
        }
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_decisions_from_json(self, filepath: str) -> bool:
        """Carrega decisões explícitas de triagem salvas em arquivo JSON."""
        if not os.path.exists(filepath):
            return False
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            decisions = data.get("explicit_decisions", {})
            for path, dec_info in decisions.items():
                dec = dec_info.get("decision")
                apply_children = dec_info.get("apply_to_children", True)
                if dec in (TriageDecision.INCLUDE, TriageDecision.EXCLUDE):
                    self.set_decision(path, dec, apply_to_children=apply_children)
            return True
        except Exception:
            return False

    def resolve_folder_decision(self, folder_path: str) -> Tuple[str, str, bool]:
        """Resolve a decisão efetiva para uma pasta.
        
        Retorna: (decisão_efetiva, caminho_origem_da_decisão, is_explicit)
        Regra de precedência:
        1. Se a pasta tem decisão explícita própria -> USA ELA.
        2. Senão, sobe para o pai mais próximo que tenha decisão explícita com apply_to_children=True.
        3. Se nenhum tiver -> UNDECIDED.
        """
        norm_path = os.path.normpath(folder_path)
        if norm_path in self._cache_resolved:
            return self._cache_resolved[norm_path]

        curr: Optional[str] = norm_path
        first: bool = True
        while curr:
            if curr in self.explicit_decisions:
                dec, apply_children = self.explicit_decisions[curr]
                if first or apply_children:
                    res = (dec, curr, first)
                    self._cache_resolved[norm_path] = res
                    return res

            parent = os.path.dirname(curr)
            if not parent or parent == curr:
                break
            curr = parent
            first = False

        res = (TriageDecision.UNDECIDED, "", False)
        self._cache_resolved[norm_path] = res
        return res

    def resolve_file_decision(self, file_path: str) -> Tuple[str, str, bool]:
        """Resolve a decisão para um arquivo baseado no diretório mais próximo."""
        folder = os.path.dirname(os.path.abspath(file_path)) if file_path else ""
        return self.resolve_folder_decision(folder)

    def get_summary_metrics(self) -> Dict[str, Any]:
        """Calcula e retorna as métricas consolidadas em tempo real sobre todos os itens pendentes em O(1)."""
        return dict(self._metrics)

    def get_filtered_groups(
        self,
        search_text: str = "",
        category_filter: str = "ALL",
        decision_filter: str = "ALL",
        group_mode: str = "top_level",
        min_depth: int = 1,
        max_depth: Optional[int] = None,
    ) -> List[FolderGroup]:
        """Retorna a lista de grupos de pastas filtrada para exibição na UI.
        
        group_mode:
        - 'top_level': retorna as raízes principais reais da auditoria em ordem amigável
          (Desktop, Downloads, Documents, Pictures, Music, Videos; outras raízes).
        - 'all': exibe todas as pastas da árvore hierárquica.
        Se search_text for informado, busca em todos os nós da árvore.
        """
        results: List[FolderGroup] = []
        s_lower = search_text.strip().lower()

        # Se for modo raízes/top_level e não houver filtros ativos (busca, categoria ou decisão)
        if group_mode == "top_level" and not s_lower and (not category_filter or category_filter == "ALL") and (not decision_filter or decision_filter == "ALL"):
            return self.get_root_groups()

        for path, grp in self.folders.items():
            if grp.total_files == 0:
                continue

            # 1. Filtro de texto (nome ou caminho)
            if s_lower:
                if s_lower not in grp.path.lower() and s_lower not in grp.name.lower():
                    continue

            # 2. Filtro de categoria
            if category_filter and category_filter != "ALL":
                if category_filter not in grp.categories:
                    continue

            # 3. Filtro de decisão resolvida
            dec, _, _ = self.resolve_folder_decision(grp.path)
            if decision_filter and decision_filter != "ALL":
                if dec != decision_filter:
                    continue

            results.append(grp)

        # Ordena: pastas com mais arquivos primeiro
        results.sort(key=lambda g: g.total_files, reverse=True)
        return results


    def get_file_indices_in_folder(
        self,
        folder_path: str,
        include_subfolders: bool = False,
    ) -> List[int]:
        """Retorna os índices brutos dos arquivos contidos na pasta (em O(subpastas))."""
        norm_target = os.path.normpath(folder_path)
        if not include_subfolders:
            return list(self.files_by_folder.get(norm_target, []))

        stack = [norm_target]
        subdirs = []
        while stack:
            curr = stack.pop()
            subdirs.append(curr)
            stack.extend(self.child_folders.get(curr, []))

        indices: List[int] = []
        for d in subdirs:
            if d in self.files_by_folder:
                indices.extend(self.files_by_folder[d])
        return indices

    def get_files_by_indices(self, indices: List[int]) -> List[Dict[str, Any]]:
        """Resolve e retorna os dados de arquivos apenas para os índices solicitados (paginação)."""
        items: List[Dict[str, Any]] = []
        for idx in indices:
            it = dict(self.raw_items[idx])
            dec, orig, is_exp = self.resolve_file_decision(it.get("caminho", ""))
            it["triage_decision"] = dec
            it["triage_origin"] = orig
            it["triage_is_explicit"] = is_exp
            it["category"] = self.item_categories[idx]
            items.append(it)
        return items

    def get_files_in_folder(
        self,
        folder_path: str,
        include_subfolders: bool = False,
    ) -> List[Dict[str, Any]]:
        """Retorna os arquivos contidos na pasta especificada."""
        indices = self.get_file_indices_in_folder(folder_path, include_subfolders=include_subfolders)
        return self.get_files_by_indices(indices)

    def get_included_items(self) -> List[Dict[str, Any]]:
        """Retorna exatamente a lista de itens cuja decisão resolvida é INCLUDE."""
        included = []
        for folder_path, indices in self.files_by_folder.items():
            if self._folder_effective.get(folder_path) == TriageDecision.INCLUDE:
                for idx in indices:
                    it = dict(self.raw_items[idx])
                    it["category"] = self.item_categories[idx]
                    included.append(it)
        return included

    def apply_regenerable_suggestions(self) -> int:
        """Aplica a sugestão técnica de EXCLUDE para todas as pastas comprovadamente regeneráveis.
        
        Importante: Só é executado mediante invocação explícita do usuário (botão).
        Retorna a quantidade de pastas alteradas.
        """
        count = 0
        for path, grp in self.folders.items():
            if grp.suggested_action == TriageDecision.EXCLUDE:
                self.set_decision(path, TriageDecision.EXCLUDE, apply_to_children=True)
                count += 1
        return count

    def reset_all_decisions(self) -> None:
        """Redefine todas as decisões explícitas de volta para UNDECIDED."""
        self.explicit_decisions.clear()
        for grp in self.folders.values():
            grp.explicit_decision = None
            grp.apply_to_children = True
        self._cache_resolved.clear()
        for d in self.folders:
            self._folder_effective[d] = TriageDecision.UNDECIDED
        tot_files = len(self.raw_items)
        tot_bytes = sum(g.direct_bytes for g in self.folders.values())
        self._metrics = {
            "total_files": tot_files,
            "total_bytes": tot_bytes,
            "included_files": 0,
            "included_bytes": 0,
            "excluded_files": 0,
            "excluded_bytes": 0,
            "undecided_files": tot_files,
            "undecided_bytes": tot_bytes,
        }
