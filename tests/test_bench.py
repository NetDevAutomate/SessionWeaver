"""Pre-registered A6 benchmark contract and CLI tests."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from session_weaver.bench import (
    EXPECTED_GOLD_COUNTS,
    GOLD_EVIDENCE_PATTERNS,
    GoldQuestion,
    aggregate_evidence,
    audit_gold,
    comparability_label,
    control_verdict,
    gate_verdict,
    load_gold,
    metric_summary,
    posture,
    ranked_session_ids,
    render_markdown,
    score,
    validate_gold,
    wilson,
)
from session_weaver.cli import main
from session_weaver.recall import ConceptHit, QueryPlan, RecallReport, SessionHit

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "docs" / "data" / "gold.json"
DIRECTIONAL = ROOT / "docs" / "data" / "gold-paraphrase-directional.json"
CONTRACT = ROOT / "docs" / "data" / "bench-contract.json"


def _report() -> RecallReport:
    return RecallReport(
        concepts=(
            ConceptHit(
                concept_id="legacy:one",
                kind="Finding",
                title="one",
                statement="one",
                standing="proposed",
                binding_state="legacy_unbound",
                confidence=0.5,
                source_session_id="session-a",
                provenance_label="legacy-unbound (session-level provenance)",
                citations=(),
            ),
            ConceptHit(
                concept_id="legacy:two",
                kind="Finding",
                title="two",
                statement="two",
                standing="proposed",
                binding_state="legacy_unbound",
                confidence=0.5,
                source_session_id="session-b",
                provenance_label="legacy-unbound (session-level provenance)",
                citations=(),
            ),
        ),
        sessions=(
            SessionHit("session-b", "kiro_cli", None, None, "duplicate"),
            SessionHit("session-c", "codex", None, None, "third"),
            SessionHit("session-d", "codex", None, None, "fourth"),
        ),
        plan=QueryPlan(("term",), '"term"', '"term"'),
        k=5,
        project=None,
    )


def _sample_output() -> dict[str, Any]:
    metric = {
        "n": 25,
        "hits": 16,
        "recall_at_5": 0.64,
        "recall_ci95": [0.4515, 0.7970],
        "mrr_at_5": 0.5,
        "mrr_ci95": [0.3194, 0.6806],
    }
    category = {key: dict(metric) for key in ("K", "P", "R")}
    return {
        "schema": "session-weaver.benchmark.v1",
        "k": 5,
        "corpus_posture": {
            "label": "post-fix/eligible",
            "exporter_pin_sha": "fb60646847181891fdd48fbaf1ca90ec11275c03",
            "exporter_at_or_after_fix": True,
        },
        "eligibility": {
            "all": {"total": 25, "K": 11, "P": 8, "R": 6},
            "visible": {"total": 22, "K": 10, "P": 7, "R": 5},
            "gold_sessions": {"visible": 40, "missing": 1, "tombstoned": 0, "other": 2},
        },
        "comparability": "directly comparable",
        "all_25": {"overall": dict(metric), "categories": category},
        "visible_subset": {"overall": dict(metric), "categories": category},
        "positive_control": {
            "status": "pass",
            "expected": {"recall_at_5": [0.38, 0.58], "mrr_at_5": [0.28, 0.48]},
            "metrics": {"overall": dict(metric), "categories": category},
        },
        "diagnostic_unrestricted": {"overall": dict(metric), "categories": category},
        "directional_paraphrase": {
            "label": "directional",
            "corpus_verified": 40,
            "metrics": {"overall": dict(metric), "categories": {"P": dict(metric)}},
        },
        "concept_candidate_coverage": {"K01": 1},
        "per_question": [
            {
                "id": "K01",
                "type": "K",
                "eligible": True,
                "visible_gold_sessions": 1,
                "hit": 1,
                "reciprocal_rank": 1.0,
                "concept_candidates": 1,
            }
        ],
        "investigation": None,
        "verdict": "pass",
        "exit_code": 0,
        "timings_seconds": {"benchmark": 1.0},
    }


def test_gold_pin_is_exactly_25_with_k11_p8_r6_and_frozen_ids() -> None:
    gold = load_gold(GOLD)

    validate_gold(gold)

    assert EXPECTED_GOLD_COUNTS == {"K": 11, "P": 8, "R": 6}
    assert [question.id for question in gold] == [
        *(f"K{i:02d}" for i in (1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12)),
        *(f"P{i:02d}" for i in (1, 2, 3, 4, 5, 6, 7, 10)),
        *(f"R{i:02d}" for i in range(1, 7)),
    ]
    assert set(GOLD_EVIDENCE_PATTERNS) == {question.id for question in gold}


def test_directional_paraphrase_set_is_separate_corpus_verified_and_at_least_40() -> None:
    records = json.loads(DIRECTIONAL.read_text(encoding="utf-8"))

    assert len(records) >= 40
    assert {record["type"] for record in records} == {"P"}
    assert all(record["id"].startswith("DP") for record in records)
    assert all(record["question"] and record["gold"] and record["evidence"] for record in records)
    assert len({record["question"] for record in records}) == len(records)


def test_wilson_matches_pre_registered_known_value() -> None:
    low, high = wilson(16, 25)

    assert low == pytest.approx(0.4515, abs=0.01)
    assert high == pytest.approx(0.7970, abs=0.01)
    assert wilson(0, 0) == (0.0, 0.0)


def test_ranked_session_ids_maps_concepts_first_then_sessions_and_deduplicates() -> None:
    assert ranked_session_ids(_report(), k=5) == (
        "session-a",
        "session-b",
        "session-c",
        "session-d",
    )
    assert ranked_session_ids(_report(), k=2) == ("session-a", "session-b")


@pytest.mark.parametrize(
    ("ranked", "expected"),
    [
        (("wrong", "gold", "other"), (1, 0.5)),
        (("wrong", "other"), (0, 0.0)),
        (("gold", "gold", "other"), (1, 1.0)),
    ],
)
def test_score_matches_frozen_poc_arithmetic(
    ranked: tuple[str, ...], expected: tuple[int, float]
) -> None:
    assert score(frozenset({"gold"}), ranked) == expected


def test_metric_summary_reports_recall_mrr_and_wilson_intervals() -> None:
    summary = metric_summary([(1, 1.0), (0, 0.0), (1, 0.5), (0, 0.0)])

    assert summary["n"] == 4
    assert summary["hits"] == 2
    assert summary["recall_at_5"] == 0.5
    assert summary["mrr_at_5"] == 0.375
    assert summary["recall_ci95"] == pytest.approx((0.1500, 0.8500), abs=0.0001)
    assert summary["mrr_ci95"] == pytest.approx((0.0919, 0.7806), abs=0.0001)


@pytest.mark.parametrize(
    ("overall", "categories", "expected"),
    [
        (0.539, {"K": 1.0, "P": 1.0, "R": 1.0}, ("fail", 1)),
        (0.54, {"K": 0.81, "P": 0.15, "R": 0.57}, ("investigate", 3)),
        (0.639, {"K": 1.0, "P": 1.0, "R": 1.0}, ("investigate", 3)),
        (0.64, {"K": 0.81, "P": 0.15, "R": 0.57}, ("pass", 0)),
        (0.90, {"K": 0.80, "P": 1.0, "R": 1.0}, ("fail", 1)),
        (0.90, {"K": 1.0, "P": 0.14, "R": 1.0}, ("fail", 1)),
        (0.90, {"K": 1.0, "P": 1.0, "R": 0.56}, ("fail", 1)),
    ],
)
def test_gate_verdict_uses_fixed_bands_and_floors_without_reinterpretation(
    overall: float, categories: dict[str, float], expected: tuple[str, int]
) -> None:
    assert gate_verdict(overall, categories) == expected


def test_positive_control_passes_inside_band_investigates_ci_overlap_and_fails_far_miss() -> None:
    assert control_verdict(0.48, 0.38, (0.30, 0.66), (0.20, 0.56)) == "pass"
    assert control_verdict(0.60, 0.38, (0.42, 0.70), (0.20, 0.56)) == "investigate"
    assert control_verdict(0.75, 0.70, (0.65, 0.82), (0.60, 0.78)) == "fail"


def test_comparability_is_direct_only_for_22_of_25_visible_questions() -> None:
    assert comparability_label(22) == "directly comparable"
    assert comparability_label(21) == "not directly comparable"
    assert comparability_label(23) == "not directly comparable"


def test_benchmark_output_validates_against_frozen_json_schema() -> None:
    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(_sample_output())


def test_markdown_output_contains_every_required_metric_table() -> None:
    rendered = render_markdown(_sample_output())

    for label in (
        "all-25",
        "visible-subset",
        "positive-control",
        "diagnostic-unrestricted",
        "directional-paraphrase",
    ):
        assert f"| {label} |" in rendered


def test_aggregate_evidence_excludes_question_text_session_ids_paths_and_prose() -> None:
    evidence = aggregate_evidence(_sample_output())
    serialized = json.dumps(evidence, sort_keys=True)

    assert set(evidence) == {
        "schema",
        "k",
        "corpus_posture",
        "eligibility",
        "comparability",
        "all_25",
        "visible_subset",
        "positive_control",
        "diagnostic_unrestricted",
        "directional_paraphrase",
        "concept_candidate_coverage",
        "verdict",
        "exit_code",
        "timings_seconds",
    }
    assert re.search(r"(?:[0-9a-f]{8}-){4}[0-9a-f]{12}", serialized) is None
    assert "/Users/" not in serialized
    assert "What " not in serialized
    assert "session-a" not in serialized
    assert set(evidence["concept_candidate_coverage"]) == {"K01"}


def test_audit_gold_reports_existing_and_literal_pattern_mismatches(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE sessions(id TEXT PRIMARY KEY);"
        "CREATE TABLE messages(id TEXT PRIMARY KEY,session_id TEXT,content TEXT);"
    )
    conn.execute("INSERT INTO sessions VALUES ('gold-ok')")
    conn.execute("INSERT INTO sessions VALUES ('gold-wrong')")
    conn.execute("INSERT INTO messages VALUES ('m1','gold-ok','literal alpha and beta fact')")
    conn.execute("INSERT INTO messages VALUES ('m2','gold-wrong','unrelated body')")
    conn.commit()
    conn.close()
    gold = [
        GoldQuestion(id="P01", type="P", question="paraphrase", gold=("gold-ok",)),
        GoldQuestion(id="P02", type="P", question="paraphrase", gold=("gold-wrong",)),
        GoldQuestion(id="P03", type="P", question="paraphrase", gold=("missing",)),
    ]

    result = audit_gold(
        db,
        gold,
        {"P01": ("%alpha%", "%beta%"), "P02": ("%alpha%",), "P03": ("%alpha%",)},
    )

    assert result == {
        "questions": 3,
        "verified_questions": 1,
        "gold_sessions": 3,
        "existing_sessions": 2,
        "literal_verified_sessions": 1,
        "failures": [
            {"id": "P02", "missing": 0, "literal_mismatch": 1},
            {"id": "P03", "missing": 1, "literal_mismatch": 0},
        ],
    }


def test_posture_classifies_visible_missing_tombstoned_and_other_with_category_counts(
    production_store: Any,
) -> None:
    conn = production_store.conn
    conn.execute(
        "INSERT INTO context_tombstones(session_id,deletion_id,deleted_at) "
        "VALUES ('fixture-session-2','delete-test','2026-09-07T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO context_retirements(kind,object_id,retired_at) "
        "VALUES ('session','fixture-session-1','2026-09-07T00:00:00Z')"
    )
    conn.commit()
    gold = [
        GoldQuestion(
            id="K01",
            type="K",
            question="q",
            gold=("fixture-session-1", "fixture-session-2", "missing"),
        )
    ]

    result = posture(
        production_store.db_path,
        gold,
        exporter_provenance={
            "exporter_pin_sha": "fb60646847181891fdd48fbaf1ca90ec11275c03",
            "exporter_at_or_after_fix": True,
            "label": "post-fix/eligible",
        },
    )

    assert result["gold_sessions"] == {
        "visible": 0,
        "missing": 1,
        "tombstoned": 1,
        "other": 1,
    }
    assert result["gold_sessions_by_category"] == {
        "K": {"visible": 0, "missing": 1, "tombstoned": 1, "other": 1},
        "P": {"visible": 0, "missing": 0, "tombstoned": 0, "other": 0},
        "R": {"visible": 0, "missing": 0, "tombstoned": 0, "other": 0},
    }
    assert result["visible_questions"] == {"total": 0, "K": 0, "P": 0, "R": 0}
    assert result["question_visibility"] == {"K01": False}
    assert result["visible_gold_counts"] == {"K01": 0}


def test_cli_refuses_live_database_without_live_ro_before_benchmark_opens(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = Path.home() / ".config" / "studyloop" / "sessions.db"

    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("live database must be refused before benchmark execution")

    monkeypatch.setattr("session_weaver.cli.run_benchmark", unexpected)

    assert main(["bench", "run", "--db", str(live), "--json"]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"] == "live database requires --live-ro"


def test_cli_live_ro_passes_read_only_mode_to_benchmark(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = Path.home() / ".config" / "studyloop" / "sessions.db"
    seen: dict[str, Any] = {}

    def fake_run(db: Path, **kwargs: Any) -> dict[str, Any]:
        seen.update({"db": db, **kwargs})
        return _sample_output()

    monkeypatch.setattr("session_weaver.cli.run_benchmark", fake_run)

    assert main(["bench", "run", "--db", str(live), "--live-ro", "--json"]) == 0
    assert seen["db"] == live
    assert seen["live_ro"] is True
    assert json.loads(capsys.readouterr().out)["schema"] == "session-weaver.benchmark.v1"


def test_audit_gold_cli_returns_nonzero_for_any_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "fixture.db"
    db.touch()
    monkeypatch.setattr(
        "session_weaver.cli.audit_gold",
        lambda *_args, **_kwargs: {
            "questions": 25,
            "verified_questions": 24,
            "gold_sessions": 40,
            "existing_sessions": 40,
            "literal_verified_sessions": 39,
            "failures": [{"id": "P01", "missing": 0, "literal_mismatch": 1}],
        },
    )

    assert main(["bench", "audit-gold", "--db", str(db)]) == 1
    assert json.loads(capsys.readouterr().out)["verified_questions"] == 24
