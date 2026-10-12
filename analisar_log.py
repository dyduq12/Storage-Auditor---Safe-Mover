
import json, sys, collections, os
src = sys.argv[1]
out_dir = os.path.dirname(src)
base = os.path.splitext(os.path.basename(src))[0]
out_fail = os.path.join(out_dir, base + "_SO_FALHAS.jsonl")
out_sum = os.path.join(out_dir, base + "_RESUMO.txt")
KW = ("fail", "falh", "error", "erro", "conflict", "conflito", "recus", "mismatch", "denied", "negad")
cnt = collections.defaultdict(collections.Counter)
total = bad_json = nfail = 0
with open(src, "r", encoding="utf-8", errors="replace") as f, open(out_fail, "w", encoding="utf-8") as fo:
    for line in f:
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            d = json.loads(line)
        except Exception:
            bad_json += 1
            continue
        if not isinstance(d, dict):
            continue
        hit = False
        for k, v in d.items():
            if isinstance(v, str):
                if len(v) <= 60:
                    cnt[k][v] += 1
                elif k.lower() in ("erro", "error", "mensagem", "message", "motivo", "detalhe", "detail"):
                    cnt[k][v[:120]] += 1
                lv = v.lower()
                if any(w in lv for w in KW) and k.lower() not in ("caminho", "origem", "destino", "source", "target", "path"):
                    hit = True
        if hit:
            nfail += 1
            fo.write(line + "\n")
lines = [f"Linhas totais: {total}", f"JSON invalido: {bad_json}", f"Linhas com indicio de falha: {nfail}", ""]
for k, c in cnt.items():
    if 1 < len(c) <= 40:
        lines.append(f"== campo '{k}' ({len(c)} valores) ==")
        for v, n in c.most_common(15):
            lines.append(f"  {n:8d}  {v}")
        lines.append("")
open(out_sum, "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
print("Arquivo so com falhas:", out_fail, os.path.getsize(out_fail), "bytes")
print("Resumo:", out_sum)
