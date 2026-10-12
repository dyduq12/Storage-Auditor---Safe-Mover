"""Testes automatizados do Action Plan (Fase 3: Safe Mover)."""

import json
import os
import pytest

from core.action_plan import (
    ActionOrder,
    ActionPlan,
    ActionPlanGenerator,
    ActionPlanValidationError,
    ACTION_PLAN_SCHEMA_VERSION,
)


def test_action_plan_schema_serialization_and_validation(tmp_path):
    """Valida serialização, desserialização e integridade do schema versionado."""
    order = ActionOrder(
        order_id="ord_1",
        source_path="C:\\Users\\User\\Documents\\doc.pdf",
        target_path="D:\\Backup\\Documents\\doc.pdf",
        expected_size_bytes=1024,
        expected_mtime=1600000000.0,
        expected_sha256="abc123hash",
        fingerprint_confidence="completo",
        category="documento_avulso_critico",
        reason="Teste de schema",
    )

    plan = ActionPlan(
        plan_id="plan_test_001",
        created_at="2026-10-12T00:00:00",
        source_reports_dir="reports",
        target_base_dir="D:\\Backup",
        totais={"total_ordens": 1, "tamanho_total_bytes": 1024},
        ordens=[order],
    )

    plan_dict = plan.to_dict()
    assert plan_dict["schema_version"] == ACTION_PLAN_SCHEMA_VERSION
    assert len(plan_dict["ordens"]) == 1

    # Salva e recarrega de JSON
    json_path = tmp_path / "plan.json"
    plan.save_to_json(str(json_path))
    assert json_path.exists()

    loaded = ActionPlan.load_from_json(str(json_path))
    assert loaded.plan_id == "plan_test_001"
    assert len(loaded.ordens) == 1
    assert loaded.ordens[0].order_id == "ord_1"
    assert loaded.ordens[0].expected_size_bytes == 1024


def test_action_plan_rejects_malformed_schema():
    """Valida rejeição de schema com versão incompatível ou campos faltantes."""
    # Versão errada
    bad_version = {
        "schema_version": "99.9.9",
        "plan_id": "p1",
        "created_at": "2026-10-12",
        "source_reports_dir": "reports",
        "target_base_dir": "D:\\",
        "totais": {},
        "ordens": [],
    }
    with pytest.raises(ActionPlanValidationError):
        ActionPlan.from_dict(bad_version)

    # Campo obrigatório ausente
    missing_field = {
        "schema_version": ACTION_PLAN_SCHEMA_VERSION,
        "plan_id": "p1",
        # falta target_base_dir
        "created_at": "2026-10-12",
        "source_reports_dir": "reports",
        "totais": {},
        "ordens": [],
    }
    with pytest.raises(ActionPlanValidationError):
        ActionPlan.from_dict(missing_field)


def test_plan_rejects_heuristic_saves(tmp_path):
    """Garante que o gerador de plano NÃO inclua saves heurísticos/suspeitos."""
    reports_dir = tmp_path / "reports_saves"
    reports_dir.mkdir()

    # Cria inventário com saves suspeitos
    inv_data = {
        "jogos": [
            {
                "nome": "OPERATOR",
                "saves_em_c": [
                    {
                        "caminho_save": "C:\\Users\\Public\\Documents\\Steam",
                        "tamanho_bytes": 10000,
                        "nivel_certeza": "baixa",
                        "mtime": 1600000000.0,
                    }
                ],
            }
        ]
    }
    with open(reports_dir / "inventario_jogos_e_saves.json", "w", encoding="utf-8") as f:
        json.dump(inv_data, f)

    # Documento válido
    doc_data = {
        "itens": [
            {
                "caminho": "C:\\Users\\User\\Documents\\valido.txt",
                "tamanho_bytes": 100,
                "mtime": 1600000000.0,
            }
        ]
    }
    with open(reports_dir / "documentos_avulsos.json", "w", encoding="utf-8") as f:
        json.dump(doc_data, f)

    gen = ActionPlanGenerator(
        reports_dir=str(reports_dir),
        target_base_dir="D:\\Backup",
    )
    plan = gen.generate_plan(include_pending=False, include_documents=True)

    # Deve conter apenas o documento válido, nenhum save
    assert len(plan.ordens) == 1
    assert "valido.txt" in plan.ordens[0].source_path
    for o in plan.ordens:
        assert "Steam" not in o.source_path
        assert o.category != "save_jogo"


def test_plan_rejects_system_and_traversal_paths():
    """Valida rejeição de caminhos com traversal (..) e pastas de sistema."""
    # Traversal
    ok, err = ActionPlanGenerator.validate_path_safety("C:\\Users\\User\\..\\Windows\\cmd.exe")
    assert not ok
    assert "traversal" in err.lower() or "sistema" in err.lower()

    # Relativo
    ok, err = ActionPlanGenerator.validate_path_safety("relativo\\pasta\\arquivo.txt")
    assert not ok
    assert "não é absoluto" in err.lower()

    # Pasta de sistema Windows
    ok, err = ActionPlanGenerator.validate_path_safety("C:\\Windows\\System32\\calc.exe")
    assert not ok
    assert "sistema" in err.lower()

    # Lixeira
    ok, err = ActionPlanGenerator.validate_path_safety("C:\\$Recycle.Bin\\arquivo.txt")
    assert not ok
    assert "sistema" in err.lower()


def test_plan_rejects_target_inside_source():
    """Valida rejeição de destino aninhado dentro da pasta de origem."""
    gen = ActionPlanGenerator(target_base_dir="C:\\Users\\User\\Documents\\Backup")
    src = "C:\\Users\\User\\Documents\\arquivo.txt"
    tgt = "C:\\Users\\User\\Documents\\Backup\\arquivo.txt"

    ok, err = gen.validate_target_file(tgt, src)
    assert not ok
    assert "aninhado" in err.lower()


def test_plan_rejects_target_outside_authorized_roots():
    """Valida rejeição de destino fora das raízes autorizadas."""
    gen = ActionPlanGenerator(
        target_base_dir="D:\\Backup",
        authorized_target_roots=["D:\\Backup"],
    )
    src = "C:\\Users\\User\\Documents\\arquivo.txt"
    tgt_bad = "E:\\OutroDrive\\arquivo.txt"

    ok, err = gen.validate_target_file(tgt_bad, src)
    assert not ok
    assert "fora da pasta base" in err.lower() or "autorizadas" in err.lower()
