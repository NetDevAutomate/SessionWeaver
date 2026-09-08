"""Pre-registered SessionWeaver recall benchmark and corpus-posture gate.

The benchmark consumes the frozen A4 ``recall()`` contract. Tier-1 ontology data is
never queried here: ontology rebuild is corpus-parity preparation performed before
this command, not a recall input. Live evaluation must use a SQLite Online Backup;
``--live-ro`` exists only for explicit read-only diagnostics of the source database.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any, cast
from urllib.parse import unquote, urlparse

from agent_session_tools.context.public import open_context
from agent_session_tools.context.scope import visibility_sql

from .authorization import authorized_concepts
from .recall import RecallReport, _select_sessions, plan, recall

_SCHEMA = "session-weaver.benchmark.v1"
_FIX_SHA = "7f9a19ec"
_STUDYLOOP = Path("/Users/ataylor/code/personal/tools/studyloop")
_DIRECT_URL_GLOB = (
    ".local/share/uv/tools/agent-session-tools/lib/python*/site-packages/"
    "agent_session_tools-*.dist-info/direct_url.json"
)
EXPECTED_GOLD_COUNTS = {"K": 11, "P": 8, "R": 6}
EXPECTED_GOLD_IDS = (
    *(f"K{i:02d}" for i in (1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12)),
    *(f"P{i:02d}" for i in (1, 2, 3, 4, 5, 6, 7, 10)),
    *(f"R{i:02d}" for i in range(1, 7)),
)
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
CATEGORY_FLOORS = {"K": 0.81, "P": 0.15, "R": 0.57}
CONTROL_BANDS = {"recall_at_5": (0.38, 0.58), "mrr_at_5": (0.28, 0.48)}

# Reconstructed verbatim from code/benchmark-questions.py. The gold file is frozen;
# these literal SQL-LIKE patterns are the independent audit contract for it.
GOLD_EVIDENCE_PATTERNS: dict[str, tuple[str, ...]] = {
    "K01": ("%6ef71b215e700a3104d9ab9a71666f67880947f718099b04bd02b0631d3d3412%",),
    "K02": ("%production-pins%5dfe0f9b%",),
    "K03": ("%Cursor Grok 4.5%",),
    "K04": ("%0011-grok-is-capture-only%",),
    "K05": ("%4884 passed%",),
    "K06": ("%DXP4800GT%",),
    "K08": ("%estimate.json%", "%before the first gateway call%"),
    "K09": ("%check-commit-author%",),
    "K10": ("%test_sync_all_default%",),
    "K11": ("%pdf2obsidian%",),
    "K12": ("%two-Mac%runbook%",),
    "P01": ("%cannot converge%",),
    "P02": ("%grok%", "%hollow%"),
    "P03": ("%Stop hook%", "%async%"),
    "P04": ("%opencode%", "%pi%", "%idempoten%"),
    "P05": ("%resurrect%", "%empty%"),
    "P06": ("%dirty%", "%refuse%build%"),
    "P07": ("%MAX_ACTIVE_TOPICS%",),
    "P10": ("%divergent%", "%second Mac%"),
    "R01": ("%uv tool install%", "%symlink%", "%pin%"),
    "R02": ("%grok%", "%test_release_harnesses%"),
    "R03": ("%FTS%", "%26,314%"),
    "R04": ("%machine_id%", "%seq%", "%tiebreak%"),
    "R05": ("%validation gate%", "%judge%", "%phase 0%"),
    "R06": ("%.secrets.baseline%", "%unstaged%"),
}


@dataclass(frozen=True)
class GoldQuestion:
    """One immutable benchmark question and its corpus-verified session answer set."""

    id: str
    type: str
    question: str
    gold: tuple[str, ...]
    evidence: tuple[str, ...] = ()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_gold_path() -> Path:
    return _repo_root() / "docs" / "data" / "gold.json"


def default_directional_path() -> Path:
    return _repo_root() / "docs" / "data" / "gold-paraphrase-directional.json"


def load_gold(path: Path) -> tuple[GoldQuestion, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("gold data must be a list")
    questions: list[GoldQuestion] = []
    for record in raw:
        if not isinstance(record, dict):
            raise ValueError("gold record must be an object")
        evidence = record.get("evidence", ())
        questions.append(
            GoldQuestion(
                id=str(record["id"]),
                type=str(record["type"]),
                question=str(record["question"]),
                gold=tuple(str(value) for value in record["gold"]),
                evidence=tuple(str(value) for value in evidence),
            )
        )
    return tuple(questions)


def validate_gold(gold: Sequence[GoldQuestion]) -> None:
    ids = tuple(question.id for question in gold)
    counts = Counter(question.type for question in gold)
    if ids != EXPECTED_GOLD_IDS or dict(counts) != EXPECTED_GOLD_COUNTS:
        raise ValueError("gold set must remain exactly 25 questions (K11/P8/R6) with frozen ids")
    if any(not question.question or not question.gold for question in gold):
        raise ValueError("every gold question requires text and at least one gold session")


def wilson(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Return the two-sided Wilson interval, including fractional MRR successes."""
    if n <= 0:
        return (0.0, 0.0)
    proportion = max(0.0, min(float(successes) / n, 1.0))
    denominator = 1.0 + z * z / n
    centre = (proportion + z * z / (2.0 * n)) / denominator
    margin = z * math.sqrt((proportion * (1.0 - proportion) + z * z / (4.0 * n)) / n) / denominator
    return (centre - margin, centre + margin)


def score(golds: frozenset[str], ranked: Sequence[str]) -> tuple[int, float]:
    """Frozen PoC recall@k and reciprocal-rank arithmetic."""
    hit = int(any(session_id in golds for session_id in ranked))
    reciprocal_rank = next(
        (1.0 / (index + 1) for index, session_id in enumerate(ranked) if session_id in golds),
        0.0,
    )
    return hit, reciprocal_rank


def ranked_session_ids(report: RecallReport, *, k: int) -> tuple[str, ...]:
    """Map concept provenance to sessions, append session hits, and deduplicate in order."""
    ranked: list[str] = []
    seen: set[str] = set()
    candidates = (
        *(concept.source_session_id for concept in report.concepts),
        *(session.session_id for session in report.sessions),
    )
    for session_id in candidates:
        if session_id is None or session_id in seen:
            continue
        seen.add(session_id)
        ranked.append(session_id)
        if len(ranked) == k:
            break
    return tuple(ranked)


def _rounded_interval(value: tuple[float, float]) -> list[float]:
    return [round(value[0], 6), round(value[1], 6)]


def metric_summary(scores: Sequence[tuple[int, float]]) -> dict[str, Any]:
    n = len(scores)
    hits = sum(hit for hit, _ in scores)
    reciprocal_sum = sum(reciprocal_rank for _, reciprocal_rank in scores)
    return {
        "n": n,
        "hits": hits,
        "recall_at_5": round(hits / n, 6) if n else 0.0,
        "recall_ci95": _rounded_interval(wilson(hits, n)),
        "mrr_at_5": round(reciprocal_sum / n, 6) if n else 0.0,
        "mrr_ci95": _rounded_interval(wilson(reciprocal_sum, n)),
    }


def _metric_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    categories = sorted({cast(str, row["type"]) for row in rows})
    return {
        "overall": metric_summary(
            [(cast(int, row["hit"]), cast(float, row["reciprocal_rank"])) for row in rows]
        ),
        "categories": {
            category: metric_summary(
                [
                    (cast(int, row["hit"]), cast(float, row["reciprocal_rank"]))
                    for row in rows
                    if row["type"] == category
                ]
            )
            for category in categories
        },
    }


def gate_verdict(overall_recall: float, category_recalls: Mapping[str, float]) -> tuple[str, int]:
    """Apply the immutable Q6 bands and floors."""
    if any(
        category_recalls.get(category, 0.0) < floor for category, floor in CATEGORY_FLOORS.items()
    ):
        return ("fail", 1)
    if overall_recall < 0.54:
        return ("fail", 1)
    if overall_recall < 0.64:
        return ("investigate", 3)
    return ("pass", 0)


def _overlaps(interval: tuple[float, float], band: tuple[float, float]) -> bool:
    return interval[0] <= band[1] and interval[1] >= band[0]


def control_verdict(
    recall_value: float,
    mrr_value: float,
    recall_ci: tuple[float, float],
    mrr_ci: tuple[float, float],
) -> str:
    """Classify the fixed 0.48/0.38 ±0.10 positive-control expectation."""
    values = {"recall_at_5": recall_value, "mrr_at_5": mrr_value}
    intervals = {"recall_at_5": recall_ci, "mrr_at_5": mrr_ci}
    misses = [
        key
        for key, value in values.items()
        if not CONTROL_BANDS[key][0] <= value <= CONTROL_BANDS[key][1]
    ]
    if not misses:
        return "pass"
    if all(_overlaps(intervals[key], CONTROL_BANDS[key]) for key in misses):
        return "investigate"
    return "fail"


def comparability_label(visible_question_ids: Sequence[str]) -> str:
    visible = tuple(visible_question_ids)
    return (
        "directly comparable"
        if len(visible) == len(POC_ELIGIBLE_IDS)
        and frozenset(visible) == frozenset(POC_ELIGIBLE_IDS)
        else "not directly comparable"
    )


@contextmanager
def _isolated_benchmark_scope() -> Iterator[None]:
    """Pin benchmark visibility without inheriting process or user scope state."""
    previous_config = os.environ.get("STUDYLOOP_CONFIG")
    previous_override = os.environ.get("SESSION_CONTEXT_SCOPE")
    with TemporaryDirectory(prefix="session-weaver-bench-") as directory:
        config = Path(directory) / "config.json"
        config.write_text(
            json.dumps({"memory": {"default_scope": "unclassified", "projects": {}}}) + "\n",
            encoding="utf-8",
        )
        os.environ["STUDYLOOP_CONFIG"] = str(config)
        os.environ.pop("SESSION_CONTEXT_SCOPE", None)
        try:
            yield
        finally:
            if previous_config is None:
                os.environ.pop("STUDYLOOP_CONFIG", None)
            else:
                os.environ["STUDYLOOP_CONFIG"] = previous_config
            if previous_override is None:
                os.environ.pop("SESSION_CONTEXT_SCOPE", None)
            else:
                os.environ["SESSION_CONTEXT_SCOPE"] = previous_override


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def exporter_provenance() -> dict[str, Any]:
    """Read the installed uv-tool pin and verify descent from the exporter fix."""
    direct_urls = sorted(Path.home().glob(_DIRECT_URL_GLOB))
    pin_sha = "unknown"
    if direct_urls:
        payload = json.loads(direct_urls[0].read_text(encoding="utf-8"))
        url = str(payload.get("url", ""))
        wheel_path = Path(unquote(urlparse(url).path))
        match = re.search(r"/production-pins/([0-9a-f]{8,40})/", wheel_path.as_posix())
        if match:
            pin_sha = match.group(1)
            provenance_path = wheel_path.parent.parent / "provenance.md"
            if provenance_path.is_file():
                provenance = provenance_path.read_text(encoding="utf-8")
                commit = re.search(r"Commit:\s*([0-9a-f]{40})", provenance)
                if commit:
                    pin_sha = commit.group(1)
    at_or_after = False
    if pin_sha != "unknown" and _STUDYLOOP.is_dir():
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", _FIX_SHA, pin_sha],
            cwd=_STUDYLOOP,
            check=False,
            capture_output=True,
            text=True,
        )
        at_or_after = completed.returncode == 0
    return {
        "exporter_pin_sha": pin_sha,
        "exporter_at_or_after_fix": at_or_after,
        "label": "post-fix/eligible" if at_or_after else "pre-fix/provisional",
    }


def posture(
    db: Path,
    gold: Sequence[GoldQuestion],
    *,
    exporter_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify frozen golds under the active pinned visibility policy before scoring."""
    provenance = dict(exporter_provenance or globals()["exporter_provenance"]())
    gold_ids = tuple(dict.fromkeys(session_id for question in gold for session_id in question.gold))
    classifications: dict[str, str] = {}
    with open_context(db, project=None) as context:
        clause, params = visibility_sql(
            context.conn, "s.id", policy=context.policy, scope=context.scope
        )
        tombstones = _table_exists(context.conn, "context_tombstones")
        for session_id in gold_ids:
            exists = context.conn.execute(
                "SELECT 1 FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if exists is None:
                classifications[session_id] = "missing"
                continue
            if (
                tombstones
                and context.conn.execute(
                    "SELECT 1 FROM context_tombstones WHERE session_id=?", (session_id,)
                ).fetchone()
            ):
                classifications[session_id] = "tombstoned"
                continue
            visible = context.conn.execute(
                f"SELECT 1 FROM sessions s WHERE s.id=? AND {clause}",
                (session_id, *params),
            ).fetchone()
            classifications[session_id] = "visible" if visible else "other"
    visible_gold_counts = {
        question.id: sum(classifications[session_id] == "visible" for session_id in question.gold)
        for question in gold
    }
    question_visibility = {
        question_id: count > 0 for question_id, count in visible_gold_counts.items()
    }
    visible_counts = Counter(question.type for question in gold if question_visibility[question.id])
    categories = ("visible", "missing", "tombstoned", "other")
    by_category: dict[str, dict[str, int]] = {}
    for question_type in ("K", "P", "R"):
        category_ids = tuple(
            dict.fromkeys(
                session_id
                for question in gold
                if question.type == question_type
                for session_id in question.gold
            )
        )
        by_category[question_type] = {
            category: sum(classifications[session_id] == category for session_id in category_ids)
            for category in categories
        }
    return {
        **provenance,
        "gold_sessions": {
            category: sum(value == category for value in classifications.values())
            for category in categories
        },
        "gold_sessions_by_category": by_category,
        "visible_questions": {
            "total": sum(question_visibility.values()),
            **{category: visible_counts.get(category, 0) for category in ("K", "P", "R")},
        },
        "question_visibility": question_visibility,
        "visible_gold_counts": visible_gold_counts,
    }


def _literal_match(conn: sqlite3.Connection, session_id: str, patterns: Sequence[str]) -> bool:
    clauses = " AND ".join("body LIKE ?" for _ in patterns)
    row = conn.execute(
        "WITH corpus AS ("
        " SELECT group_concat(content,char(10)) AS body FROM messages WHERE session_id=?"
        ") SELECT 1 FROM corpus WHERE " + clauses,
        (session_id, *patterns),
    ).fetchone()
    return row is not None


def audit_gold(
    db: Path,
    gold: Sequence[GoldQuestion],
    evidence_patterns: Mapping[str, Sequence[str]] = GOLD_EVIDENCE_PATTERNS,
) -> dict[str, Any]:
    """Verify every frozen gold session and its literal corpus fact read-only."""
    failures: list[dict[str, Any]] = []
    existing = 0
    literal_verified = 0
    verified_questions = 0
    with closing(sqlite3.connect(_read_only_uri(db), uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        for question in gold:
            missing = 0
            mismatch = 0
            patterns = tuple(evidence_patterns[question.id])
            for session_id in question.gold:
                row = conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
                if row is None:
                    missing += 1
                    continue
                existing += 1
                if _literal_match(conn, session_id, patterns):
                    literal_verified += 1
                else:
                    mismatch += 1
            if missing or mismatch:
                failures.append(
                    {"id": question.id, "missing": missing, "literal_mismatch": mismatch}
                )
            else:
                verified_questions += 1
        conn.rollback()
    return {
        "questions": len(gold),
        "verified_questions": verified_questions,
        "gold_sessions": sum(len(question.gold) for question in gold),
        "existing_sessions": existing,
        "literal_verified_sessions": literal_verified,
        "failures": failures,
    }


def _positive_control(db: Path, question: str, *, k: int) -> tuple[str, ...]:
    query_plan = plan(question)
    with open_context(db, project=None) as context:
        sessions, _ = _select_sessions(
            context,
            query_plan.and_query,
            query_plan.or_query,
            k,
            frozenset(),
        )
    return tuple(session.session_id for session in sessions)


def _unrestricted_sessions(conn: sqlite3.Connection, question: str, *, k: int) -> list[str]:
    query_plan = plan(question)
    selected: list[str] = []
    for query in (query_plan.and_query, query_plan.or_query):
        if not query or len(selected) >= k:
            continue
        rows = conn.execute(
            "WITH ranked AS ("
            " SELECT m.session_id,m.id,m.timestamp,bm25(messages_fts) AS rank,"
            " row_number() OVER (PARTITION BY m.session_id ORDER BY bm25(messages_fts),"
            " m.timestamp DESC,m.id) AS position"
            " FROM messages_fts JOIN messages m ON m.rowid=messages_fts.rowid"
            " WHERE messages_fts MATCH ?"
            ") SELECT session_id FROM ranked WHERE position=1"
            " ORDER BY rank,timestamp DESC,session_id,id LIMIT 300",
            (query,),
        ).fetchall()
        for (session_id,) in rows:
            value = cast(str, session_id)
            if value not in selected:
                selected.append(value)
                if len(selected) == k:
                    break
    return selected


def _diagnostic_unrestricted(db: Path, question: str, *, k: int) -> tuple[str, ...]:
    query_plan = plan(question)
    ranked: list[str] = []
    with closing(sqlite3.connect(_read_only_uri(db), uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        if _table_exists(conn, "context_concept_fts"):
            for query in (query_plan.and_query, query_plan.or_query):
                if not query or len(ranked) >= k:
                    continue
                rows = conn.execute(
                    "SELECT c.source_session_id FROM context_concept_fts f"
                    " JOIN context_concepts c ON c.id=f.concept_id"
                    " WHERE context_concept_fts MATCH ?"
                    " ORDER BY bm25(context_concept_fts),c.id LIMIT 200",
                    (query,),
                ).fetchall()
                for (session_id,) in rows:
                    if session_id is not None and session_id not in ranked:
                        ranked.append(cast(str, session_id))
                        if len(ranked) == k:
                            break
        if len(ranked) < k:
            for session_id in _unrestricted_sessions(conn, question, k=k):
                if session_id not in ranked:
                    ranked.append(session_id)
                    if len(ranked) == k:
                        break
    return tuple(ranked)


def _concept_coverage(db: Path, gold: Sequence[GoldQuestion]) -> dict[str, int]:
    with open_context(db, project=None) as context:
        source_counts = Counter(
            cast(str, concept.root["source_session_id"])
            for concept in authorized_concepts(context, project=None)
            if concept.root["source_session_id"] is not None
        )
    return {
        question.id: sum(source_counts[session_id] for session_id in question.gold)
        for question in gold
    }


def _score_questions(
    db: Path,
    questions: Sequence[GoldQuestion],
    *,
    k: int,
    question_visibility: Mapping[str, bool],
    visible_gold_counts: Mapping[str, int],
    concept_coverage: Mapping[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    recall_rows: list[dict[str, Any]] = []
    control_rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    for question in questions:
        golds = frozenset(question.gold)
        report = recall(db, question.question, k=k)
        hit, reciprocal_rank = score(golds, ranked_session_ids(report, k=k))
        common = {"id": question.id, "type": question.type}
        recall_rows.append(
            {
                **common,
                "eligible": question_visibility.get(question.id, False),
                "visible_gold_sessions": visible_gold_counts.get(question.id, 0),
                "hit": hit,
                "reciprocal_rank": reciprocal_rank,
                "concept_candidates": concept_coverage.get(question.id, 0),
            }
        )
        control_hit, control_rr = score(golds, _positive_control(db, question.question, k=k))
        control_rows.append({**common, "hit": control_hit, "reciprocal_rank": control_rr})
        diagnostic_hit, diagnostic_rr = score(
            golds, _diagnostic_unrestricted(db, question.question, k=k)
        )
        diagnostic_rows.append({**common, "hit": diagnostic_hit, "reciprocal_rank": diagnostic_rr})
    return recall_rows, control_rows, diagnostic_rows


def _investigation(rows: Sequence[Mapping[str, Any]], reference_path: Path) -> dict[str, Any]:
    reference = {
        cast(str, row["id"]): cast(Sequence[float], row["T2"])
        for row in json.loads(reference_path.read_text(encoding="utf-8"))
    }
    flips = []
    details = []
    for row in rows:
        question_id = cast(str, row["id"])
        old_hit = int(reference[question_id][0])
        new_hit = cast(int, row["hit"])
        if old_hit != new_hit:
            flips.append({"id": question_id, "from": old_hit, "to": new_hit})
        details.append(
            {
                "id": question_id,
                "type": row["type"],
                "eligible": row["eligible"],
                "visible_gold_sessions": row["visible_gold_sessions"],
                "hit": new_hit,
                "previous_hit": old_hit,
                "concept_candidates": row["concept_candidates"],
            }
        )
    return {"hit_miss_flips": flips, "questions": details}


def _directional_report(db: Path, *, k: int) -> dict[str, Any]:
    questions = load_gold(default_directional_path())
    evidence = {question.id: question.evidence for question in questions}
    audit = audit_gold(db, questions, evidence)
    if len(questions) < 40:
        raise ValueError("directional benchmark requires at least 40 corpus-verified questions")
    if audit["verified_questions"] != len(questions) or audit["failures"]:
        raise ValueError("directional benchmark requires every question to pass corpus audit")
    rows = []
    for question in questions:
        hit, reciprocal_rank = score(
            frozenset(question.gold), ranked_session_ids(recall(db, question.question, k=k), k=k)
        )
        rows.append(
            {
                "id": question.id,
                "type": question.type,
                "hit": hit,
                "reciprocal_rank": reciprocal_rank,
            }
        )
    return {
        "label": "directional",
        "corpus_verified": audit["verified_questions"],
        "metrics": _metric_report(rows),
    }


def _run_benchmark(
    db: Path,
    *,
    gold_path: Path | None = None,
    k: int = 5,
    live_ro: bool = False,
) -> dict[str, Any]:
    """Evaluate frozen recall, controls, diagnostics, eligibility, and directionality."""
    del live_ro  # Path authorization happens in the CLI; all library opens are read-only.
    if k != 5:
        raise ValueError("the pre-registered A6 benchmark must run at k=5")
    started = perf_counter()
    gold = load_gold(gold_path or default_gold_path())
    validate_gold(gold)
    posture_result = posture(db, gold)
    coverage = _concept_coverage(db, gold)
    rows, control_rows, diagnostic_rows = _score_questions(
        db,
        gold,
        k=k,
        question_visibility=cast(Mapping[str, bool], posture_result["question_visibility"]),
        visible_gold_counts=cast(Mapping[str, int], posture_result["visible_gold_counts"]),
        concept_coverage=coverage,
    )
    all_metrics = _metric_report(rows)
    visible_rows = [row for row in rows if row["eligible"]]
    visible_metrics = _metric_report(visible_rows)
    control_metrics = _metric_report(control_rows)
    control_overall = control_metrics["overall"]
    control_status = control_verdict(
        control_overall["recall_at_5"],
        control_overall["mrr_at_5"],
        tuple(control_overall["recall_ci95"]),
        tuple(control_overall["mrr_ci95"]),
    )
    category_recalls = {
        category: all_metrics["categories"][category]["recall_at_5"] for category in ("K", "P", "R")
    }
    verdict, exit_code = gate_verdict(all_metrics["overall"]["recall_at_5"], category_recalls)
    if control_status == "fail":
        verdict, exit_code = "fail", 1
    elif control_status == "investigate" and verdict == "pass":
        verdict, exit_code = "investigate", 3
    if posture_result["label"] == "pre-fix/provisional" and verdict == "pass":
        verdict, exit_code = "investigate", 3
    visible_counts = cast(Mapping[str, int], posture_result["visible_questions"])
    question_visibility = cast(Mapping[str, bool], posture_result["question_visibility"])
    visible_question_ids = [question.id for question in gold if question_visibility[question.id]]
    report: dict[str, Any] = {
        "schema": _SCHEMA,
        "k": k,
        "corpus_posture": {
            key: posture_result[key]
            for key in ("label", "exporter_pin_sha", "exporter_at_or_after_fix")
        },
        "eligibility": {
            "all": {"total": 25, **EXPECTED_GOLD_COUNTS},
            "visible": dict(visible_counts),
            "visible_question_ids": visible_question_ids,
            "gold_sessions": posture_result["gold_sessions"],
            "gold_sessions_by_category": posture_result["gold_sessions_by_category"],
        },
        "comparability": comparability_label(visible_question_ids),
        "all_25": all_metrics,
        "visible_subset": visible_metrics,
        "positive_control": {
            "status": control_status,
            "expected": CONTROL_BANDS,
            "metrics": control_metrics,
        },
        "diagnostic_unrestricted": _metric_report(diagnostic_rows),
        "directional_paraphrase": _directional_report(db, k=k),
        "concept_candidate_coverage": coverage,
        "per_question": rows,
        "investigation": None,
        "verdict": verdict,
        "exit_code": exit_code,
        "timings_seconds": {"benchmark": round(perf_counter() - started, 6)},
    }
    if verdict == "investigate":
        report["investigation"] = _investigation(
            rows, _repo_root() / "docs" / "data" / "bench-results-t2.json"
        )
    return report


def run_benchmark(
    db: Path,
    *,
    gold_path: Path | None = None,
    k: int = 5,
    live_ro: bool = False,
) -> dict[str, Any]:
    """Run the benchmark under an isolated, pinned unclassified visibility policy."""
    with _isolated_benchmark_scope():
        return _run_benchmark(db, gold_path=gold_path, k=k, live_ro=live_ro)


def aggregate_evidence(
    report: Mapping[str, Any], *, live_evaluation: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Retain aggregate/sanitized fields only; omit question prose and session identifiers."""
    keys = (
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
    )
    evidence = {key: report[key] for key in keys}
    if live_evaluation is not None:
        evidence["live_evaluation"] = dict(live_evaluation)
    return evidence


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render every required metric table without question text or session identifiers."""
    lines = [
        "# SessionWeaver A6 recall benchmark",
        "",
        f"- Verdict: **{str(report['verdict']).upper()}**",
        f"- Corpus posture: `{report['corpus_posture']['label']}`",
        f"- Comparability: `{report['comparability']}`",
        f"- Positive control: `{report['positive_control']['status']}`",
        (
            "- Stage closure: `blocked` until a post-fix exporter corpus is used."
            if report["corpus_posture"]["label"] == "pre-fix/provisional"
            else "- Stage closure: corpus posture is eligible for the metric verdict."
        ),
        "",
        "| Report | Category | n | Recall@5 (95% CI) | MRR@5 (95% CI) |",
        "|---|---:|---:|---:|---:|",
    ]
    reports = (
        ("all-25", report["all_25"]),
        ("visible-subset", report["visible_subset"]),
        ("positive-control", report["positive_control"]["metrics"]),
        ("diagnostic-unrestricted", report["diagnostic_unrestricted"]),
        ("directional-paraphrase", report["directional_paraphrase"]["metrics"]),
    )
    for label, value in reports:
        metrics = cast(Mapping[str, Any], value)
        for category, summary in (("overall", metrics["overall"]), *metrics["categories"].items()):
            lines.append(
                f"| {label} | {category} | {summary['n']} | "
                f"{summary['recall_at_5']:.3f} {tuple(summary['recall_ci95'])} | "
                f"{summary['mrr_at_5']:.3f} {tuple(summary['mrr_ci95'])} |"
            )
    investigation = report.get("investigation")
    if isinstance(investigation, Mapping):
        lines.extend(
            [
                "",
                "## Per-question investigation",
                "",
                "| ID | Type | Eligible | Visible gold | Concept candidates | "
                "Previous hit | Current hit |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for question in cast(Sequence[Mapping[str, Any]], investigation["questions"]):
            lines.append(
                f"| {question['id']} | {question['type']} | "
                f"{str(question['eligible']).lower()} | {question['visible_gold_sessions']} | "
                f"{question['concept_candidates']} | {question['previous_hit']} | "
                f"{question['hit']} |"
            )
    lines.extend(
        [
            "",
            "`diagnostic-unrestricted` is diagnostic only and never affects the verdict.",
            (
                "The directional paraphrase set is reported separately and never changes "
                "the frozen gate."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(report: Mapping[str, Any], out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "bench-results.json"
    markdown_path = out / "bench-results.md"
    json_path.write_text(
        json.dumps(dict(report), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
