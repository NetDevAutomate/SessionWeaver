"""Pre-registered A6 benchmark contract and CLI tests."""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from session_weaver.bench import (
    EXPECTED_GOLD_COUNTS,
    EXPECTED_GOLD_IDS,
    GOLD_EVIDENCE_PATTERNS,
    GoldQuestion,
    _directional_report,
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
    run_benchmark,
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
BASELINE = ROOT / "docs" / "data" / "bench-baseline-phase-a.json"
POC_ELIGIBLE_IDS = (
    "K01",
    "K02",
    "K03",
    "K04",
    "K05",
    "K06",
    "K08",
    "K09",
    "K10",
    "K12",
    "P01",
    "P02",
    "P04",
    "P05",
    "P06",
    "P07",
    "P10",
    "R01",
    "R02",
    "R03",
    "R04",
    "R05",
)


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


def _metric(n: int, hits: int) -> dict[str, Any]:
    return {
        "n": n,
        "hits": hits,
        "recall_at_5": round(hits / n, 6),
        "recall_ci95": [0.1, 0.9],
        "mrr_at_5": 0.5,
        "mrr_ci95": [0.1, 0.9],
    }


def _metric_report() -> dict[str, Any]:
    return {
        "overall": _metric(25, 15),
        "categories": {
            "K": _metric(11, 9),
            "P": _metric(8, 2),
            "R": _metric(6, 4),
        },
    }


def _sample_output() -> dict[str, Any]:
    rows = [
        {
            "id": question_id,
            "type": question_id[0],
            "eligible": True,
            "visible_gold_sessions": 1,
            "hit": int(index < 15),
            "reciprocal_rank": 1.0 if index < 15 else 0.0,
            "concept_candidates": index,
        }
        for index, question_id in enumerate(EXPECTED_GOLD_IDS)
    ]
    investigation_questions = [
        {
            "id": row["id"],
            "type": row["type"],
            "eligible": row["eligible"],
            "visible_gold_sessions": row["visible_gold_sessions"],
            "hit": row["hit"],
            "previous_hit": row["hit"],
            "concept_candidates": row["concept_candidates"],
        }
        for row in rows
    ]
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
            "visible": {"total": 25, "K": 11, "P": 8, "R": 6},
            "visible_question_ids": list(EXPECTED_GOLD_IDS),
            "gold_sessions": {"visible": 70, "missing": 0, "tombstoned": 0, "other": 0},
            "gold_sessions_by_category": {
                "K": {"visible": 33, "missing": 0, "tombstoned": 0, "other": 0},
                "P": {"visible": 28, "missing": 0, "tombstoned": 0, "other": 0},
                "R": {"visible": 17, "missing": 0, "tombstoned": 0, "other": 0},
            },
        },
        "comparability": "not directly comparable",
        "all_25": _metric_report(),
        "visible_subset": _metric_report(),
        "positive_control": {
            "status": "pass",
            "expected": {"recall_at_5": [0.38, 0.58], "mrr_at_5": [0.28, 0.48]},
            "metrics": _metric_report(),
        },
        "diagnostic_unrestricted": _metric_report(),
        "directional_paraphrase": {
            "label": "directional",
            "corpus_verified": 40,
            "metrics": {
                "overall": _metric(40, 0),
                "categories": {"P": _metric(40, 0)},
            },
        },
        "concept_candidate_coverage": {
            question_id: index for index, question_id in enumerate(EXPECTED_GOLD_IDS)
        },
        "per_question": rows,
        "investigation": {
            "hit_miss_flips": [{"id": "K03", "from": 1, "to": 0}],
            "questions": investigation_questions,
        },
        "verdict": "investigate",
        "exit_code": 3,
        "timings_seconds": {"benchmark": 1.0},
    }


def _synthetic_posture(label: str) -> dict[str, Any]:
    return {
        "label": label,
        "exporter_pin_sha": "5dfe0f9b" if label == "pre-fix/provisional" else "fb606468",
        "exporter_at_or_after_fix": label == "post-fix/eligible",
        "gold_sessions": {"visible": 70, "missing": 0, "tombstoned": 0, "other": 0},
        "gold_sessions_by_category": {
            category: {"visible": count, "missing": 0, "tombstoned": 0, "other": 0}
            for category, count in {"K": 33, "P": 28, "R": 17}.items()
        },
        "visible_questions": {"total": 25, "K": 11, "P": 8, "R": 6},
        "question_visibility": {question_id: True for question_id in EXPECTED_GOLD_IDS},
        "visible_gold_counts": {question_id: 1 for question_id in EXPECTED_GOLD_IDS},
    }


def _run_synthetic_benchmark(
    monkeypatch: pytest.MonkeyPatch,
    *,
    posture_label: str = "post-fix/eligible",
    gate: str = "investigate",
    posture_probe: Callable[[], None] | None = None,
) -> dict[str, Any]:
    def fake_posture(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if posture_probe is not None:
            posture_probe()
        return _synthetic_posture(posture_label)

    rows: list[dict[str, Any]] = []
    category_hits = {"K": 9, "P": 2, "R": 4}
    seen = {"K": 0, "P": 0, "R": 0}
    for index, question_id in enumerate(EXPECTED_GOLD_IDS):
        category = question_id[0]
        hit = 1 if gate == "pass" or seen[category] < category_hits[category] else 0
        seen[category] += 1
        rows.append(
            {
                "id": question_id,
                "type": category,
                "eligible": True,
                "visible_gold_sessions": 1,
                "hit": hit,
                "reciprocal_rank": float(hit),
                "concept_candidates": index,
            }
        )
    control_rows = [
        {
            "id": question_id,
            "type": question_id[0],
            "hit": int(index < 12),
            "reciprocal_rank": 0.8 if index < 12 else 0.0,
        }
        for index, question_id in enumerate(EXPECTED_GOLD_IDS)
    ]
    diagnostic_rows = [
        {
            "id": row["id"],
            "type": row["type"],
            "hit": row["hit"],
            "reciprocal_rank": row["reciprocal_rank"],
        }
        for row in rows
    ]
    directional_metric = metric_summary([(0, 0.0)] * 40)

    monkeypatch.setattr("session_weaver.bench.posture", fake_posture)
    monkeypatch.setattr(
        "session_weaver.bench._concept_coverage",
        lambda *_args, **_kwargs: {
            question_id: index for index, question_id in enumerate(EXPECTED_GOLD_IDS)
        },
    )
    monkeypatch.setattr(
        "session_weaver.bench._score_questions",
        lambda *_args, **_kwargs: (rows, control_rows, diagnostic_rows),
    )
    monkeypatch.setattr(
        "session_weaver.bench._directional_report",
        lambda *_args, **_kwargs: {
            "label": "directional",
            "corpus_verified": 40,
            "metrics": {
                "overall": directional_metric,
                "categories": {"P": directional_metric},
            },
        },
    )
    return run_benchmark(Path("unused.db"), gold_path=GOLD)


def test_gold_pin_is_exactly_25_with_k11_p8_r6_and_frozen_ids() -> None:
    gold = load_gold(GOLD)

    validate_gold(gold)

    assert EXPECTED_GOLD_COUNTS == {"K": 11, "P": 8, "R": 6}
    assert [question.id for question in gold] == list(EXPECTED_GOLD_IDS)
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


def test_comparability_requires_the_exact_frozen_poc_eligible_question_ids() -> None:
    assert tuple(POC_ELIGIBLE_IDS) == (
        "K01",
        "K02",
        "K03",
        "K04",
        "K05",
        "K06",
        "K08",
        "K09",
        "K10",
        "K12",
        "P01",
        "P02",
        "P04",
        "P05",
        "P06",
        "P07",
        "P10",
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
    )
    assert comparability_label(POC_ELIGIBLE_IDS) == "directly comparable"
    different_22 = (*POC_ELIGIBLE_IDS[:-1], "R06")
    assert comparability_label(different_22) == "not directly comparable"
    assert comparability_label(EXPECTED_GOLD_IDS) == "not directly comparable"


def test_provisional_posture_forces_a_passing_gate_to_investigate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _run_synthetic_benchmark(
        monkeypatch,
        posture_label="pre-fix/provisional",
        gate="pass",
    )

    assert report["verdict"] == "investigate"
    assert report["exit_code"] == 3
    assert report["investigation"] is not None


def test_benchmark_orchestration_pins_isolated_unclassified_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ambient = tmp_path / "ambient.json"
    ambient.write_text(
        json.dumps({"memory": {"default_scope": "personal", "projects": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STUDYLOOP_CONFIG", str(ambient))
    monkeypatch.setenv("SESSION_CONTEXT_SCOPE", "personal")
    observed: dict[str, Any] = {}

    def probe() -> None:
        config_path = Path(os.environ["STUDYLOOP_CONFIG"])
        observed["config"] = json.loads(config_path.read_text(encoding="utf-8"))
        observed["override"] = os.environ.get("SESSION_CONTEXT_SCOPE")
        observed["path"] = config_path

    _run_synthetic_benchmark(monkeypatch, posture_probe=probe)

    assert observed["config"] == {"memory": {"default_scope": "unclassified", "projects": {}}}
    assert observed["override"] is None
    assert not observed["path"].exists()
    assert os.environ["STUDYLOOP_CONFIG"] == str(ambient)
    assert os.environ["SESSION_CONTEXT_SCOPE"] == "personal"


@pytest.mark.parametrize(
    ("question_count", "verified", "failures"),
    [
        (40, 39, [{"id": "DP40", "missing": 0, "literal_mismatch": 1}]),
        (39, 39, []),
    ],
)
def test_directional_report_rejects_unverified_rows_and_requires_at_least_40_before_scoring(
    question_count: int,
    verified: int,
    failures: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    questions = load_gold(DIRECTIONAL)[:question_count]
    monkeypatch.setattr("session_weaver.bench.load_gold", lambda *_args: questions)
    monkeypatch.setattr(
        "session_weaver.bench.audit_gold",
        lambda *_args, **_kwargs: {
            "questions": question_count,
            "verified_questions": verified,
            "gold_sessions": question_count,
            "existing_sessions": question_count,
            "literal_verified_sessions": verified,
            "failures": failures,
        },
    )

    def unexpected_recall(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("directional scoring must not start before audit passes")

    monkeypatch.setattr("session_weaver.bench.recall", unexpected_recall)

    with pytest.raises(ValueError, match="directional"):
        _directional_report(Path("unused.db"), k=5)


def test_benchmark_output_validates_against_frozen_json_schema() -> None:
    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(_sample_output())


@pytest.mark.parametrize(
    "case",
    [
        "missing_category_posture",
        "missing_visible_ids",
        "short_per_question",
        "wrong_question_order",
        "incomplete_investigation",
        "missing_candidate_coverage",
    ],
)
def test_schema_rejects_incomplete_or_stale_evidence(case: str) -> None:
    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
    report = copy.deepcopy(_sample_output())
    if case == "missing_category_posture":
        report["eligibility"].pop("gold_sessions_by_category")
    elif case == "missing_visible_ids":
        report["eligibility"].pop("visible_question_ids")
    elif case == "short_per_question":
        report["per_question"].pop()
    elif case == "wrong_question_order":
        report["per_question"][0], report["per_question"][1] = (
            report["per_question"][1],
            report["per_question"][0],
        )
    elif case == "incomplete_investigation":
        report["investigation"]["questions"][0].pop("concept_candidates")
    elif case == "missing_candidate_coverage":
        report["concept_candidate_coverage"].pop("R06")

    assert list(Draft202012Validator(schema).iter_errors(report))


def test_real_generated_report_matches_exact_schema_ids_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _run_synthetic_benchmark(monkeypatch)
    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(report)
    assert report["eligibility"]["visible_question_ids"] == list(EXPECTED_GOLD_IDS)
    assert [row["id"] for row in report["per_question"]] == list(EXPECTED_GOLD_IDS)
    assert [row["id"] for row in report["investigation"]["questions"]] == list(EXPECTED_GOLD_IDS)
    assert list(report["concept_candidate_coverage"]) == list(EXPECTED_GOLD_IDS)


def test_markdown_output_contains_metrics_and_ordered_per_question_investigation() -> None:
    rendered = render_markdown(_sample_output())

    for label in (
        "all-25",
        "visible-subset",
        "positive-control",
        "diagnostic-unrestricted",
        "directional-paraphrase",
    ):
        assert f"| {label} |" in rendered
    assert "## Per-question investigation" in rendered
    assert (
        "| ID | Type | Eligible | Visible gold | Concept candidates | Previous hit | Current hit |"
        in rendered
    )
    row_positions = [rendered.index(f"| {question_id} |") for question_id in EXPECTED_GOLD_IDS]
    assert row_positions == sorted(row_positions)


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
    assert set(evidence["concept_candidate_coverage"]) == set(EXPECTED_GOLD_IDS)


def test_committed_baseline_retains_exact_visible_question_ids() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert baseline["eligibility"]["visible_question_ids"] == list(EXPECTED_GOLD_IDS)


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

    assert main(["bench", "run", "--db", str(live), "--live-ro", "--json"]) == 3
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
