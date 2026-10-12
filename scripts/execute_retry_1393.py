"""Script de execução controlada da retomada das 1.393 ordens elegíveis.

Autorização expressa concedida pelo usuário:
- Plano: plan_20261012_160800_323192f6 (inalterado)
- Ordens: estritamente as 1.393 elegíveis
- Destinos de 82.788 ordens: intocados
- 31 ordens bloqueadas: estritamente não executadas
- Origens: estritamente somente-leitura (100% intactas)
- Tolerância a falhas: abortar após 5 falhas consecutivas
- Log: arquivo novo append-only JSONL com identificador RETRY
- Relatório consolidado pós-execução (JSON)
"""

import datetime
import json
import os
import sys
import time

# Adiciona diretório raiz ao sys.path se necessário
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.action_plan import ActionPlan, ActionOrder
from core.safe_mover import SafeCopyExecutor
from core.path_utils import to_extended_path


def main():
    print("=" * 80)
    print("INICIANDO RETOMADA CONTROLADA (1.393 ORDENS ELEGÍVEIS)")
    print("=" * 80)

    plan_path = os.path.join(BASE_DIR, "reports", "action_plan_aprovado.json")
    audit_path = r"C:\Users\Eduardo\.gemini\antigravity-ide\brain\280c164d-7e42-486a-ab8b-f1f3d0ccaf0b\scratch\audit_reconciliation_exact.json"
    reports_dir = os.path.join(BASE_DIR, "reports")

    # 1. Carrega plano aprovado existente
    print("\n1. Carregando plano existente...")
    with open(plan_path, "r", encoding="utf-8") as f:
        full_plan_dict = json.load(f)

    assert full_plan_dict["plan_id"] == "plan_20261012_160800_323192f6"
    assert full_plan_dict["target_base_dir"] == r"D:\Backup_StorageAuditor"

    # 2. Carrega lista das 1.393 ordens elegíveis
    print("2. Carregando lista estrita de ordens elegíveis...")
    with open(audit_path, "r", encoding="utf-8") as f:
        audit_data = json.load(f)

    eligible_list = audit_data["eligible_orders_list"]
    eligible_ids = set(o["order_id"] for o in eligible_list)
    assert len(eligible_ids) == 1393, f"Esperado 1393 ordens elegíveis, obtido {len(eligible_ids)}"

    # 3. Cria subplano exclusivo contendo APENAS as 1.393 ordens
    orders_map = {o["order_id"]: ActionOrder.from_dict(o) for o in full_plan_dict["ordens"]}
    selected_orders = [orders_map[oid] for oid in eligible_ids]

    total_bytes_to_copy = sum(o.expected_size_bytes for o in selected_orders)
    print(f"  Total de ordens a executar: {len(selected_orders)}")
    print(f"  Total de bytes a copiar:    {total_bytes_to_copy:,} bytes ({total_bytes_to_copy / (1024*1024):.2f} MB)")

    # Cria objeto ActionPlan contendo exatamente essas ordens
    retry_plan = ActionPlan(
        plan_id=full_plan_dict["plan_id"],
        created_at=full_plan_dict["created_at"],
        source_reports_dir=reports_dir,
        target_base_dir=full_plan_dict["target_base_dir"],
        totais={
            "total_ordens": len(selected_orders),
            "tamanho_total_bytes": total_bytes_to_copy,
            "espaco_necessario_com_margem_bytes": total_bytes_to_copy,
            "status_aprovacao": "aprovado",
            "selecao_explicita_presente": True,
        },
        ordens=selected_orders,
        status_aprovacao="aprovado",
    )

    # 4. Configuração do Executor com log RETRY e limite de 5 falhas consecutivas
    ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    retry_log_filename = f"execution_log_{retry_plan.plan_id}_RETRY_{ts_str}.jsonl"

    print(f"\n3. Configurando SafeCopyExecutor:")
    print(f"  Log append-only:           {retry_log_filename}")
    print(f"  Tolerância a falhas:       Abortar após 5 falhas consecutivas")
    print(f"  Proteção das origens:      Somente leitura (intactas)")
    print(f"  Proteção do destino:       Proibição absoluta de sobrescrita")

    progress_step = [0]
    t_start_run = time.time()

    def on_progress(data):
        if not data:
            return
        status = data.get("status")
        if status == "step":
            progress_step[0] += 1
            if progress_step[0] % 100 == 0 or progress_step[0] == len(selected_orders):
                elapsed = time.time() - t_start_run
                rate = progress_step[0] / elapsed if elapsed > 0 else 0
                print(f"  Progresso: {progress_step[0]}/{len(selected_orders)} ({progress_step[0]/len(selected_orders)*100:.1f}%) | {rate:.1f} ordens/s")

    executor = SafeCopyExecutor(
        plan=retry_plan,
        log_dir=reports_dir,
        custom_log_filename=retry_log_filename,
        max_consecutive_failures=5,
        progress_callback=on_progress,
    )

    # 5. Execução real controlada
    print("\n4. Executando ordens elegíveis...")
    exec_exception = None
    try:
        executor.execute_all()
    except Exception as e:
        exec_exception = e
        print(f"\n[ALERTA] Execução interrompida: {e}")

    # 6. Consolidação dos resultados por ordem a partir do log gerado
    print("\n5. Consolidando relatório de execução a partir do log oficial...")
    log_file_full_path = executor.log_file_path

    order_results = {}
    with open(log_file_full_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            ev = data.get("event")
            oid = data.get("order_id")
            if not oid:
                continue
            # Resultados terminais
            if ev in ("COMPLETED", "ALREADY_COMPLETED", "FAILED", "MKDIR_FAILED", "PREFLIGHT_FAILED", "CANCELLED"):
                order_results[oid] = data

    status_counts = {}
    completed_orders = []
    failed_orders = []
    conflict_orders = []
    total_bytes_copied = 0

    for o in selected_orders:
        oid = o.order_id
        res = order_results.get(oid)
        if res:
            ev = res.get("event")
            status_counts[ev] = status_counts.get(ev, 0) + 1
            if ev in ("COMPLETED", "ALREADY_COMPLETED"):
                completed_orders.append({
                    "order_id": oid,
                    "status": "completed",
                    "bytes_copied": res.get("bytes_copied", o.expected_size_bytes),
                    "sha256": res.get("sha256"),
                    "duration_ms": res.get("duration_ms", 0.0),
                    "source_path": o.source_path,
                    "target_path": o.target_path,
                })
                total_bytes_copied += res.get("bytes_copied", o.expected_size_bytes)
            else:
                failed_orders.append({
                    "order_id": oid,
                    "status": ev.lower(),
                    "error": res.get("error"),
                    "source_path": o.source_path,
                    "target_path": o.target_path,
                })
                if "Destino já existe" in str(res.get("error", "")):
                    conflict_orders.append(oid)
        else:
            status_counts["NOT_EXECUTED"] = status_counts.get("NOT_EXECUTED", 0) + 1
            failed_orders.append({
                "order_id": oid,
                "status": "not_executed",
                "error": "Não alcançado devido a interrupção prévia",
                "source_path": o.source_path,
                "target_path": o.target_path,
            })

    total_duration = time.time() - t_start_run

    summary_report = {
        "plan_id": retry_plan.plan_id,
        "execution_type": "CONTROLLED_RETRY_1393",
        "timestamp_inicio": datetime.datetime.fromtimestamp(t_start_run).isoformat(),
        "timestamp_fim": datetime.datetime.now().isoformat(),
        "duracao_segundos": round(total_duration, 2),
        "total_ordens_submetidas": len(selected_orders),
        "status_counts": status_counts,
        "completadas": len(completed_orders),
        "falhas": len(failed_orders),
        "conflitos": len(conflict_orders),
        "total_bytes_copiados": total_bytes_copied,
        "log_path": log_file_full_path,
        "abort_exception": str(exec_exception) if exec_exception else None,
        "uncompleted_orders": failed_orders,
        "completed_sample": completed_orders[:10],
    }

    summary_file_path = os.path.join(
        reports_dir,
        f"execution_summary_{retry_plan.plan_id}_RETRY.json"
    )
    with open(summary_file_path, "w", encoding="utf-8") as f:
        json.dump(summary_report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print("RELATÓRIO CONSOLIDADO DA RETOMADA")
    print("=" * 80)
    print(f"Total de ordens submetidas: {len(selected_orders)}")
    print(f"Concluídas com sucesso:    {len(completed_orders)}")
    print(f"Falhas:                    {len(failed_orders)}")
    print(f"Conflitos:                 {len(conflict_orders)}")
    print(f"Bytes copiados:            {total_bytes_copied:,} bytes ({total_bytes_copied / (1024*1024):.2f} MB)")
    print(f"Duração total:             {total_duration:.2f} segundos")
    print(f"Arquivo de Log RETRY:      {log_file_full_path}")
    print(f"Resumo Estruturado (JSON): {summary_file_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
