"""Ponto de entrada de linha de comando do Storage Auditor (Motor 1 - Auditor & Reporter).

Execução 100% somente leitura (Read-Only). Mapeia metadados, calcula impressões digitais,
cruza dados entre C: e D:, mapeia bibliotecas de jogos e saves associados, e gera 5 relatórios JSON.

Adendo v1.1:
- Argumentos CLI para raízes de C:, D:, pasta de saída, limite de hash e modo de teste.
- Execução por comando único.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
import threading
from typing import Callable, List, Optional

from config import (
    DEFAULT_FULL_HASH_THRESHOLD_BYTES,
    SCHEMA_VERSION,
    AuditorConfig,
    get_default_c_roots,
    get_default_d_roots,
    get_default_game_library_paths,
    get_save_candidate_roots,
    parse_cli_path_list,
)
from core.cache import HashCache
from core.comparator import RelationalComparator
from core.fingerprint import FastFingerprintEngine
from core.game_mapper import GameMapper
from core.scanner import DirectoryScanner
from exporters.json_exporter import JsonReportExporter


def setup_logger(verbose: bool = False) -> logging.Logger:
    """Configura o sistema de log com saída formatada no terminal."""
    log_level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger("storage_auditor")
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    return root_logger


def parse_arguments() -> argparse.Namespace:
    """Configura e processa os argumentos de linha de comando (Adendo v1.1 requisito 9)."""
    parser = argparse.ArgumentParser(
        prog="storage_auditor",
        description="Storage Auditor & Safe Mover - Motor 1 (Auditoria e Desduplicação 100% Read-Only)",
    )
    parser.add_argument(
        "--c-roots",
        nargs="+",
        default=None,
        help="Diretórios raízes do disco C: para indexar (ex: C:\\Users\\User\\Documents ou 'Documents;Downloads')",
    )
    parser.add_argument(
        "--d-roots",
        nargs="+",
        default=None,
        help="Diretórios raízes do disco D: para indexar (ex: D:\\ ou D:\\Backup)",
    )
    parser.add_argument(
        "--game-roots",
        nargs="+",
        default=None,
        help="Diretórios de bibliotecas de jogos em D: (ex: 'D:\\Games;D:\\SteamLibrary;D:\\XboxGames')",
    )
    parser.add_argument(
        "--save-roots",
        nargs="+",
        default=None,
        help="Diretórios candidatos para busca de saves em C: (ex: 'Documents;Saved Games;C:\\Users\\Public\\Documents')",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Diretório onde os relatórios JSON serão gerados (padrão: reports)",
    )
    parser.add_argument(
        "--full-hash-limit-mb",
        type=int,
        default=100,
        help="Limite de tamanho em MB para hash SHA-256 completo (padrão: 100 MB)",
    )
    parser.add_argument(
        "--cache-db",
        default=None,
        help="Caminho para o banco SQLite de índice incremental (padrão: <output-dir>/storage_auditor_cache.db)",
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Executa em modo de teste rápido com escopo limitado",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Inicia a interface gráfica desktop (CustomTkinter) do Storage Auditor",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Ativa logs detalhados de depuração",
    )
    return parser.parse_args()


def run_audit(
    config: AuditorConfig,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
) -> int:
    """Executa o pipeline completo de auditoria do Motor 1."""
    logger = logging.getLogger("storage_auditor")
    timestamp = datetime.datetime.now().isoformat()

    logger.info("================================================================")
    logger.info("   STORAGE AUDITOR & SAFE MOVER — FASE 1 (v%s)", config.schema_version)
    logger.info("   Motor 1: Auditor & Reporter (100%% Somente Leitura)")
    logger.info("================================================================")
    logger.info("Raízes C: %s", config.c_roots)
    logger.info("Raízes D: %s", config.d_roots)
    logger.info("Bibliotecas de Jogos D: %s", config.game_library_paths)
    logger.info("Raízes Candidatas de Saves C: %s", config.save_candidate_roots)
    logger.info("Limite de Hash Completo: %s MB", config.full_hash_threshold_bytes // (1024 * 1024))
    logger.info("Pasta de Saída: %s", os.path.abspath(config.output_dir))
    logger.info("Cache SQLite: %s", os.path.abspath(config.cache_db_path))

    if cancel_event is None:
        cancel_event = threading.Event()
    has_inconsistency = False

    if progress_callback:
        progress_callback("init", "Inicializando cache e escopos de varredura...", 0, 100)

    # 1. Inicializar Cache SQLite incremental (Adendo v1.1 requisito 8)
    with HashCache(config.cache_db_path) as cache:
        fp_engine = FastFingerprintEngine(
            cache=cache,
            full_hash_threshold_bytes=config.full_hash_threshold_bytes,
            sample_chunk_size=config.sample_chunk_size,
        )

        # 2. Varredura do Disco C:
        logger.info("\n[1/4] Varrendo diretórios do disco C: ...")
        if progress_callback:
            progress_callback("scan_c", "Iniciando varredura no disco C:...", 1, 100)

        def _prog_c(count: int, p: str) -> None:
            logger.debug("C: [%d arquivos] %s", count, p)
            if progress_callback:
                progress_callback("scan_c", f"Varrendo C: [{count} arquivos] {os.path.basename(p)}", count, 0)

        scanner_c = DirectoryScanner()
        files_c, ignored_c, errors_c = scanner_c.scan_directories(
            config.c_roots,
            progress_callback=_prog_c,
            cancel_event=cancel_event,
        )
        logger.info("C: %d arquivos indexados (%d itens ignorados, %d erros de I/O)", len(files_c), len(ignored_c), len(errors_c))

        if cancel_event.is_set():
            logger.warning("Auditoria cancelada durante varredura de C:.")
            return 1

        # Detalha contagem por raiz de C:
        for root in config.c_roots:
            norm_r = os.path.abspath(root)
            cnt = scanner_c.files_per_root.get(norm_r, 0)
            logger.info("  -> Raiz C: [%s]: %d arquivos", norm_r, cnt)

        # 3. Varredura do Disco D: (excluindo pastas de jogos - Adendo v1.1 requisito 2)
        logger.info("\n[2/4] Varrendo diretórios do disco D: (excluindo bibliotecas de jogos) ...")
        if progress_callback:
            progress_callback("scan_d", "Iniciando varredura no disco D:...", 25, 100)

        def _prog_d(count: int, p: str) -> None:
            logger.debug("D: [%d arquivos] %s", count, p)
            if progress_callback:
                progress_callback("scan_d", f"Varrendo D: [{count} arquivos] {os.path.basename(p)}", count, 0)

        scanner_d = DirectoryScanner(exclude_paths=config.game_library_paths)
        files_d, ignored_d, errors_d = scanner_d.scan_directories(
            config.d_roots,
            progress_callback=_prog_d,
            cancel_event=cancel_event,
        )
        logger.info("D: %d arquivos indexados (%d itens ignorados, %d erros de I/O)", len(files_d), len(ignored_d), len(errors_d))

        if cancel_event.is_set():
            logger.warning("Auditoria cancelada durante varredura de D:.")
            return 1

        # Detalha contagem por raiz de D:
        for root in config.d_roots:
            norm_r = os.path.abspath(root)
            cnt = scanner_d.files_per_root.get(norm_r, 0)
            logger.info("  -> Raiz D: [%s]: %d arquivos", norm_r, cnt)

        # Validação de consistência: Se alguma raiz existente produziu 0 arquivos enquanto outras produziram
        total_files_all = len(files_c) + len(files_d)
        for r, c in scanner_c.files_per_root.items():
            if c == 0 and total_files_all > 0:
                logger.warning(
                    "[AVISO CRÍTICO] A raiz de C: '%s' existe no disco mas produziu ZERO itens indexados!", r
                )
                has_inconsistency = True

        for r, c in scanner_d.files_per_root.items():
            if c == 0 and total_files_all > 0:
                logger.warning(
                    "[AVISO CRÍTICO] A raiz de D: '%s' existe no disco mas produziu ZERO itens indexados!", r
                )
                has_inconsistency = True

        # Se C: foi solicitado mas produziu 0 enquanto D: produziu arquivos
        if config.c_roots and len(files_c) == 0 and len(files_d) > 0:
            logger.error(
                "[FALHA DE AUDITORIA] Inconsistência fatal: Nenhuma raiz de C: produziu arquivos indexados, enquanto D: possui %d arquivos.",
                len(files_d),
            )
            has_inconsistency = True

        # 4. Mapeamento de Jogos e Saves em C: (Adendo v1.1 requisitos 2 e 5)
        logger.info("\n[3/4] Mapeando bibliotecas de jogos e rastreando saves em C: ...")
        if progress_callback:
            progress_callback("games", "Mapeando jogos e saves...", 50, 100)

        game_mapper = GameMapper(
            save_roots=config.save_candidate_roots,
            max_save_depth=config.max_save_scan_depth,
        )
        games = game_mapper.build_full_inventory(
            steam_libraries=config.game_library_paths,
            standalone_roots=config.game_library_paths,
            include_ms_store=not config.test_mode,
        )
        total_saves_count = sum(len(g.saves_em_c) for g in games)
        logger.info("Detectados %d jogos e %d vínculos de saves/dados em C:", len(games), total_saves_count)

        if cancel_event.is_set():
            logger.warning("Auditoria cancelada após mapeamento de jogos.")
            return 1

        # 5. Cruzamento Relacional C: vs D:
        logger.info("\n[4/4] Executando cruzamento relacional e detecção de duplicatas...")
        if progress_callback:
            progress_callback("compare", "Iniciando cruzamento relacional C vs D...", 70, 100)

        def _comp_prog(msg: str, cur: int, tot: int) -> None:
            logger.debug("%s: %d/%d", msg, cur, tot)
            if progress_callback:
                progress_callback("compare", f"{msg} ({cur}/{tot})", cur, tot)

        comparator = RelationalComparator(fingerprint_engine=fp_engine)
        comp_result = comparator.compare(
            files_c=files_c,
            files_d=files_d,
            progress_callback=_comp_prog,
            cancel_event=cancel_event,
        )

        if cancel_event.is_set():
            logger.warning("Auditoria cancelada durante cruzamento relacional.")
            return 1

        # 6. Exportação dos Relatórios JSON (Adendo v1.1 requisito 7)
        logger.info("\nExportando relatórios JSON para '%s' ...", config.output_dir)
        if progress_callback:
            progress_callback("export", "Exportando relatórios JSON...", 95, 100)

        exporter = JsonReportExporter(output_dir=config.output_dir, schema_version=config.schema_version)
        report_files = exporter.export_all(
            files_c=files_c,
            files_d=files_d,
            comp_result=comp_result,
            games=games,
            timestamp=timestamp,
        )

    # 7. Resumo Executivo no Terminal
    mb_recuperavel = comp_result.espaco_total_recuperavel_bytes / (1024 * 1024)
    mb_pendente = comp_result.tamanho_total_pendente_bytes / (1024 * 1024)

    logger.info("================================================================")
    logger.info("                  RESUMO DA AUDITORIA (FASE 1)                  ")
    logger.info("================================================================")
    logger.info("Arquivos indexados em C:             %d", len(files_c))
    logger.info("Arquivos indexados em D:             %d", len(files_d))
    logger.info("Duplicatas confirmadas (C vs D):      %d (%.2f MB recuperáveis)", len(comp_result.duplicatas), mb_recuperavel)
    logger.info("Pendentes de backup para D:          %d (%.2f MB)", len(comp_result.pendentes_backup), mb_pendente)
    logger.info("Documentos avulsos identificados:    %d", len(comp_result.documentos_avulsos))
    logger.info("Jogos mapeados no inventário:        %d", len(games))
    logger.info("Diretórios de saves localizados:     %d", total_saves_count)
    logger.info("----------------------------------------------------------------")
    logger.info("Arquivos gerados com sucesso:")
    for key, path in report_files.items():
        logger.info("  - [%s]: %s", key, os.path.abspath(path))
    logger.info("================================================================")

    if has_inconsistency:
        logger.error(
            "FALHA: A auditoria detectou raízes vazias ou inconsistências entre C: e D:. "
            "Verifique os alertas emitidos acima."
        )
        return 2

    logger.info("Varredura 100%% concluída com sucesso e sem modificações em disco.")
    if progress_callback:
        progress_callback("done", "Auditoria concluída com sucesso!", 100, 100)
    return 0


def main() -> None:
    """Função principal executável via CLI ou GUI."""
    args = parse_arguments()

    if args.gui:
        from gui.app import StorageAuditorApp
        app = StorageAuditorApp()
        app.mainloop()
        return

    logger = setup_logger(verbose=args.verbose)

    # Processa e valida listas de caminhos recebidos
    logger.info("--- VALIDAÇÃO DE RAÍZES E ESCOPOS CLI ---")

    # C: Roots
    if args.c_roots:
        c_roots, c_invalid = parse_cli_path_list(args.c_roots, is_c_roots=True)
    else:
        c_roots = get_default_c_roots()
        c_invalid = []

    # D: Roots
    if args.d_roots:
        d_roots, d_invalid = parse_cli_path_list(args.d_roots)
    else:
        d_roots = get_default_d_roots()
        d_invalid = []

    # Game Roots
    if args.game_roots:
        game_roots, g_invalid = parse_cli_path_list(args.game_roots)
    else:
        game_roots = get_default_game_library_paths()
        g_invalid = []

    # Save Roots
    if args.save_roots:
        save_roots, s_invalid = parse_cli_path_list(args.save_roots, is_c_roots=True)
    else:
        save_roots = get_save_candidate_roots()
        s_invalid = []

    # Logs explícitos de validação de raízes
    logger.info("Raízes C: recebidas: %s", args.c_roots)
    logger.info("Raízes C: validadas (%d): %s", len(c_roots), c_roots)
    for inv_p, reason in c_invalid:
        logger.warning("  [IGNORADO C:] '%s' -> Motivo: %s", inv_p, reason)

    logger.info("Raízes D: recebidas: %s", args.d_roots)
    logger.info("Raízes D: validadas (%d): %s", len(d_roots), d_roots)
    for inv_p, reason in d_invalid:
        logger.warning("  [IGNORADO D:] '%s' -> Motivo: %s", inv_p, reason)

    logger.info("Raízes de Jogos recebidas: %s", args.game_roots)
    logger.info("Raízes de Jogos validadas (%d): %s", len(game_roots), game_roots)
    for inv_p, reason in g_invalid:
        logger.warning("  [IGNORADO JOGO:] '%s' -> Motivo: %s", inv_p, reason)

    logger.info("Raízes de Saves recebidas: %s", args.save_roots)
    logger.info("Raízes de Saves validadas (%d): %s", len(save_roots), save_roots)
    for inv_p, reason in s_invalid:
        logger.warning("  [IGNORADO SAVE:] '%s' -> Motivo: %s", inv_p, reason)

    output_dir = args.output_dir
    cache_db = args.cache_db or os.path.join(output_dir, "storage_auditor_cache.db")
    full_hash_threshold = args.full_hash_limit_mb * 1024 * 1024

    config = AuditorConfig(
        c_roots=c_roots,
        d_roots=d_roots,
        game_library_paths=game_roots,
        save_candidate_roots=save_roots,
        output_dir=output_dir,
        cache_db_path=cache_db,
        full_hash_threshold_bytes=full_hash_threshold,
        test_mode=args.test_mode,
        verbose=args.verbose,
        schema_version=SCHEMA_VERSION,
    )

    sys.exit(run_audit(config))


if __name__ == "__main__":
    main()
