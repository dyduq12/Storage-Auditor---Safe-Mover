"""Criação e execução controlada do plano de backup independente para código-fonte.

Autorização expressa concedida pelo usuário:
- 9 arquivos de código-fonte do projeto
- Destino isolado: D:\\Backup_StorageAuditor\\Workspace_SourceCode\\
- Preservação da estrutura relativa
- Origens estritamente somente-leitura
- Validação prévia de hash SHA-256, tamanho e mtime
- Destino não pode existir previamente
- Cópia para temporário com renomeação atômica pós-validação de hash
- Plano e logs novos, isolados e autônomos
"""

import datetime
import hashlib
import json
import os
import sys
import time
import uuid

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.action_plan import ActionPlan, ActionOrder
from core.safe_mover import SafeCopyExecutor
from core.path_utils import to_extended_path


def compute_sha256_file(path_str: str) -> str:
    h = hashlib.sha256()
    with open(to_extended_path(path_str), "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 80)
    print("PLANO INDEPENDENTE DE BACKUP: CÓDIGO-FONTE DO PROJETO (9 ARQUIVOS)")
    print("=" * 80)

    reports_dir = os.path.join(BASE_DIR, "reports")
    target_base = r"D:\Backup_StorageAuditor\Workspace_SourceCode"

    relative_files = [
        r"core\action_plan.py",
        r"core\safe_mover.py",
        r"core\triage.py",
        r"core\path_utils.py",
        r"gui\app.py",
        r"gui\data_loader.py",
        r"gui\views\tab_safe_mover.py",
        r"tests\test_triage.py",
        r"tests\test_long_paths_and_safe_mover.py",
    ]

    print("\n1. Inspecionando estado atual das fontes e calculando hashes...")
    orders = []
    total_bytes = 0

    for rel in relative_files:
        src = os.path.join(BASE_DIR, rel)
        tgt = os.path.join(target_base, rel)

        src_ext = to_extended_path(src)
        if not os.path.exists(src_ext):
            raise FileNotFoundError(f"Arquivo de origem obrigatório não encontrado: '{src}'")

        st = os.stat(src_ext)
        size = st.st_size
        mtime = st.st_mtime
        sha = compute_sha256_file(src)
        total_bytes += size

        oid = f"ord_src_{uuid.uuid4().hex[:12]}"
        order = ActionOrder(
            order_id=oid,
            source_path=src,
            target_path=tgt,
            expected_size_bytes=size,
            expected_mtime=mtime,
            expected_sha256=sha,
            category="codigo_fonte_projeto",
            reason="Preservação de código-fonte atual do projeto",
            status="pending",
        )
        orders.append(order)
        print(f"  + {rel:42} | {size:6} B | SHA: {sha[:16]}... | MTime: {datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')}")

    # 2. Gera novo Action Plan autônomo
    ts_now = datetime.datetime.now()
    plan_id = f"plan_sourcecode_{ts_now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    plan_filename = f"action_plan_sourcecode_{ts_now.strftime('%Y%m%d_%H%M%S')}.json"
    plan_filepath = os.path.join(reports_dir, plan_filename)

    plan = ActionPlan(
        plan_id=plan_id,
        created_at=ts_now.isoformat(),
        source_reports_dir=reports_dir,
        target_base_dir=target_base,
        totais={
            "total_ordens": len(orders),
            "tamanho_total_bytes": total_bytes,
            "espaco_necessario_com_margem_bytes": total_bytes,
            "status_aprovacao": "aprovado",
            "selecao_explicita_presente": True,
            "descricao": "Plano independente de preservação do código-fonte atual",
        },
        ordens=orders,
        status_aprovacao="aprovado",
    )

    plan.save_to_json(plan_filepath)
    print(f"\n2. Plano autônomo gerado e salvo:")
    print(f"  Plan ID:           {plan_id}")
    print(f"  Arquivo do Plano:  {plan_filepath}")
    print(f"  Total de Ordens:   {len(orders)}")
    print(f"  Total de Bytes:    {total_bytes:,} B ({total_bytes / 1024:.2f} KB)")
    print(f"  Destino Base:      {target_base}")

    # 3. Pré-validação estrita de não-existência do destino
    print("\n3. Validando ausência dos destinos antes de iniciar gravação...")
    for o in orders:
        tgt_ext = to_extended_path(o.target_path)
        if os.path.exists(tgt_ext):
            raise RuntimeError(f"Destino já existe. Sobrescrita terminantemente proibida: '{o.target_path}'")
    print("  Todos os 9 caminhos de destino estão comprovadamente ausentes (sem conflito).")

    # 4. Execução do plano via SafeCopyExecutor com log exclusivo
    log_filename = f"execution_log_{plan_id}.jsonl"
    print(f"\n4. Executando cópia segura:")
    print(f"  Log append-only:   {log_filename}")

    t_start = time.time()
    executor = SafeCopyExecutor(
        plan=plan,
        log_dir=reports_dir,
        custom_log_filename=log_filename,
        max_consecutive_failures=1,
    )

    executor.execute_all()
    duration = time.time() - t_start

    # 5. Consolidação e verificação dos resultados a partir do log oficial
    print("\n5. Consolidando resultados do log oficial...")
    order_results = {}
    with open(executor.log_file_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            ev = d.get("event")
            oid = d.get("order_id")
            if oid and ev in ("COMPLETED", "ALREADY_COMPLETED", "FAILED", "PREFLIGHT_FAILED", "CANCELLED"):
                order_results[oid] = d

    completed_orders = []
    failed_orders = []

    for o in orders:
        res = order_results.get(o.order_id)
        if res and res.get("event") in ("COMPLETED", "ALREADY_COMPLETED"):
            # Confirmação pós-cópia: checa se hash no destino é exatamente igual
            dst_sha = compute_sha256_file(o.target_path)
            assert dst_sha.lower() == o.expected_sha256.lower(), f"Hash mismatch pós-cópia em {o.target_path}"
            completed_orders.append({
                "order_id": o.order_id,
                "source_path": o.source_path,
                "target_path": o.target_path,
                "bytes_copied": o.expected_size_bytes,
                "sha256_src": o.expected_sha256,
                "sha256_dst": dst_sha,
                "duration_ms": res.get("duration_ms", 0.0),
                "status": "completed",
            })
        else:
            failed_orders.append({
                "order_id": o.order_id,
                "source_path": o.source_path,
                "target_path": o.target_path,
                "status": "failed",
                "error": res.get("error") if res else "Ordem não encontrada no log",
            })

    summary_data = {
        "plan_id": plan_id,
        "execution_type": "SOURCE_CODE_BACKUP_9_FILES",
        "timestamp_inicio": ts_now.isoformat(),
        "timestamp_fim": datetime.datetime.now().isoformat(),
        "duracao_segundos": round(duration, 3),
        "total_ordens": len(orders),
        "completadas": len(completed_orders),
        "falhas": len(failed_orders),
        "total_bytes_copiados": sum(c["bytes_copied"] for c in completed_orders),
        "log_path": executor.log_file_path,
        "plan_path": plan_filepath,
        "uncompleted_orders": failed_orders,
        "completed_orders": completed_orders,
    }

    summary_file = os.path.join(reports_dir, f"execution_summary_{plan_id}.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print("RELATÓRIO CONSOLIDADO: BACKUP DE CÓDIGO-FONTE CONCLUÍDO")
    print("=" * 80)
    print(f"Total de ordens executadas: {len(orders)}")
    print(f"Concluídas com sucesso:     {len(completed_orders)}")
    print(f"Falhas:                     {len(failed_orders)}")
    print(f"Bytes copiados:             {summary_data['total_bytes_copiados']:,} bytes ({summary_data['total_bytes_copiados'] / 1024:.2f} KB)")
    print(f"Duração total:              {duration:.3f} segundos")
    print(f"Arquivo do Plano:           {plan_filepath}")
    print(f"Arquivo de Log (JSONL):     {executor.log_file_path}")
    print(f"Resumo Estruturado (JSON):  {summary_file}")
    print("=" * 80)


if __name__ == "__main__":
    main()
