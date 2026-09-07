"""Concept-first AND->OR recall: planner, authorization, ordering, CLI, contract."""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol

import pytest
import yaml
from agent_session_tools.context.provenance import Origin
from agent_session_tools.context.scope import ScopePolicy, apply_policy
from agent_session_tools.context.store import ContextStore, NativeSource

import session_weaver.recall as recall_module
from session_weaver.cli import main
from session_weaver.concepts import ConceptService, _ConceptRepository
from session_weaver.recall import STOP, QueryPlan, plan, recall

_NOW = "2026-09-08T12:00:00+00:00"
_CONTRACT_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "recall-contract.json"


class ProductionStore(Protocol):
    conn: sqlite3.Connection
    db_path: Path
    config_path: Path


def _service(store: ProductionStore) -> ConceptService:
    return ConceptService(store.db_path, now=lambda: _NOW)


def _capture(
    store: ProductionStore,
    body: str,
    *,
    session_id: str = "fixture-session-1",
    key: str | None = None,
) -> str:
    native_key = key or hashlib.sha256(body.encode()).hexdigest()[:16]
    return ContextStore(store.conn).capture(
        NativeSource(
            session_id=session_id,
            native_key=native_key,
            harness="fixture",
            native_kind="message:user",
            native_locator=f"fixture://{session_id}/{native_key}",
            parser_version="recall-test-v1",
            machine_id="fixture-machine",
            body=body,
            origin=Origin.CONVERSATION,
            recorded_at=_NOW,
        )
    )


def _concept(
    quote: str,
    *,
    title: str = "Recall test concept",
    description: str = "Recall test statement.",
    kind: str = "Finding",
    tags: list[str] | None = None,
    confidence: float = 0.9,
) -> dict[str, Any]:
    return {
        "type": kind,
        "title": title,
        "description": description,
        "tags": tags or ["recall", "session-weaver"],
        "confidence": confidence,
        "quotes": [{"quote": quote}],
    }


def _document(*concepts: dict[str, Any]) -> dict[str, Any]:
    return {"concepts": list(concepts)}


def _message(
    store: ProductionStore,
    *,
    message_id: str,
    session_id: str,
    content: str,
    timestamp: str = _NOW,
) -> None:
    store.conn.execute(
        "INSERT INTO messages(id,session_id,role,content,timestamp) VALUES (?,?,?,?,?)",
        (message_id, session_id, "user", content, timestamp),
    )
    store.conn.commit()


def _add_session(store: ProductionStore, session_id: str, *, project_path: str) -> None:
    store.conn.execute(
        "INSERT INTO sessions(id,source,project_path,updated_at) VALUES (?,?,?,?)",
        (session_id, "fixture", project_path, _NOW),
    )
    store.conn.commit()


# --- 1. Planner ---------------------------------------------------------------

_PINNED_STOP_WORDS = (
    "a an the is are was were be been being do does did to of in on for with"
    " and or not what which who why how when where whose that this these those"
    " it its during every any can cant can't could should would will shall"
    " about into from as at by we our your my i you they them he she his her"
)


def test_stop_set_is_pinned_verbatim_from_the_poc() -> None:
    assert frozenset(_PINNED_STOP_WORDS.split()) == STOP
    assert len(_PINNED_STOP_WORDS.split()) == len(STOP)


def test_terms_lowercases_and_drops_stopwords_and_short_tokens() -> None:
    result = plan("The WIDGET explode? at 2x-speed a.b/c an OK go").terms
    assert "widget" in result
    assert "explode" in result
    assert all(token not in STOP for token in result)
    assert all(len(token) > 2 for token in result)
    # "at"/"a"/"an" are stop words; "ok"/"go" are <=2 chars and dropped.
    assert "ok" not in result
    assert "go" not in result


def test_plan_quotes_every_term_and_joins_and_or_queries() -> None:
    result = plan("widget explode")
    assert result.terms == ("widget", "explode")
    assert result.and_query == '"widget" AND "explode"'
    assert result.or_query == '"widget" OR "explode"'
    assert result.fallback_used is False


def test_plan_of_only_stopwords_and_short_tokens_is_empty() -> None:
    result = plan("is a to of it an OK")
    assert result == QueryPlan(terms=(), and_query="", or_query="")


@pytest.mark.parametrize(
    "question",
    [
        'widget "explode"',
        "widget*",
        "widget-explode",
        "widget NOT explode",
        "widget:explode",
        '"""',
        "***",
        "---",
        "NOT NOT NOT",
        "",
        "   ",
        'widget"; DROP TABLE context_concept_fts; --',
        "colon:colon:colon",
        "-leading-hyphen-term",
    ],
)
def test_plan_never_raises_operationalerror_against_a_real_fts5_table(question: str) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE context_concept_fts USING fts5("
            "title, statement, tags, kind, concept_id UNINDEXED)"
        )
        result = plan(question)
        for query in (result.and_query, result.or_query):
            if not query:
                continue
            conn.execute(
                "SELECT concept_id FROM context_concept_fts WHERE context_concept_fts MATCH ?",
                (query,),
            ).fetchall()
    finally:
        conn.close()


# --- 2. Scope, tombstone, retired, legacy-unbound -----------------------------


def test_recall_scope_restricts_concepts_to_work_personal_unclassified(
    production_store: ProductionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(production_store)
    _capture(production_store, "work scope concept evidence", key="work-concept-evidence")
    work_concept = service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "work scope concept evidence",
                title="Work scope concept",
                description="scopeconceptterm work statement",
            )
        ),
        actor="model",
    ).concept_ids[0]
    _capture(
        production_store,
        "personal scope concept evidence",
        key="personal-concept-evidence",
        session_id="fixture-session-2",
    )
    personal_concept = service.winddown(
        "fixture-session-2",
        _document(
            _concept(
                "personal scope concept evidence",
                title="Personal scope concept",
                description="scopeconceptterm personal statement",
            )
        ),
        actor="model",
    ).concept_ids[0]

    config = yaml.safe_load(production_store.config_path.read_text(encoding="utf-8"))
    config["memory"]["projects"] = {
        "work-project": {"scope": "work", "roots": []},
        "personal-project": {"scope": "personal", "roots": []},
    }
    production_store.config_path.write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    apply_policy(
        production_store.conn,
        ScopePolicy.from_config(config),
        actor="recall-scope-test",
        dry_run=False,
    )
    production_store.conn.executemany(
        "INSERT INTO context_session_projects VALUES (?,?,?)",
        (
            ("fixture-session-1", "work-project", "explicit"),
            ("fixture-session-2", "personal-project", "explicit"),
        ),
    )
    production_store.conn.commit()

    monkeypatch.setenv("SESSION_CONTEXT_SCOPE", "work")
    work_report = recall(production_store.db_path, "scopeconceptterm")
    assert {hit.concept_id for hit in work_report.concepts} == {work_concept}

    monkeypatch.setenv("SESSION_CONTEXT_SCOPE", "personal")
    personal_report = recall(production_store.db_path, "scopeconceptterm")
    assert {hit.concept_id for hit in personal_report.concepts} == {personal_concept}

    monkeypatch.setenv("SESSION_CONTEXT_SCOPE", "unclassified")
    unclassified_report = recall(production_store.db_path, "scopeconceptterm")
    assert unclassified_report.concepts == ()


def test_recall_scope_restricts_sessions_to_work_personal_unclassified(
    production_store: ProductionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _message(
        production_store,
        message_id="work-session-msg",
        session_id="fixture-session-1",
        content="scopesessionterm raw work message",
    )
    _message(
        production_store,
        message_id="personal-session-msg",
        session_id="fixture-session-2",
        content="scopesessionterm raw personal message",
    )

    config = yaml.safe_load(production_store.config_path.read_text(encoding="utf-8"))
    config["memory"]["projects"] = {
        "work-project": {"scope": "work", "roots": []},
        "personal-project": {"scope": "personal", "roots": []},
    }
    production_store.config_path.write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    apply_policy(
        production_store.conn,
        ScopePolicy.from_config(config),
        actor="recall-scope-session-test",
        dry_run=False,
    )
    production_store.conn.executemany(
        "INSERT INTO context_session_projects VALUES (?,?,?)",
        (
            ("fixture-session-1", "work-project", "explicit"),
            ("fixture-session-2", "personal-project", "explicit"),
        ),
    )
    production_store.conn.commit()

    monkeypatch.setenv("SESSION_CONTEXT_SCOPE", "work")
    work_report = recall(production_store.db_path, "scopesessionterm")
    assert {hit.session_id for hit in work_report.sessions} == {"fixture-session-1"}

    monkeypatch.setenv("SESSION_CONTEXT_SCOPE", "personal")
    personal_report = recall(production_store.db_path, "scopesessionterm")
    assert {hit.session_id for hit in personal_report.sessions} == {"fixture-session-2"}

    monkeypatch.setenv("SESSION_CONTEXT_SCOPE", "unclassified")
    unclassified_report = recall(production_store.db_path, "scopesessionterm")
    assert unclassified_report.sessions == ()


def test_recall_excludes_a_tombstoned_sessions_concepts_and_messages(
    production_store: ProductionStore,
) -> None:
    service = _service(production_store)
    _capture(production_store, "tombstone concept evidence", key="tombstone-evidence")
    service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "tombstone concept evidence",
                title="Tombstoned concept",
                description="tombstonedterm statement",
            )
        ),
        actor="model",
    )
    _message(
        production_store,
        message_id="tombstone-msg",
        session_id="fixture-session-1",
        content="tombstonedterm raw message",
    )
    production_store.conn.execute(
        "INSERT INTO context_tombstones VALUES (?,?,?)",
        ("fixture-session-1", "deletion-1", _NOW),
    )
    production_store.conn.commit()

    report = recall(production_store.db_path, "tombstonedterm")

    assert report.concepts == ()
    assert report.sessions == ()


def test_recall_excludes_a_retired_concept(production_store: ProductionStore) -> None:
    service = _service(production_store)
    _capture(production_store, "retired concept evidence", key="retired-evidence")
    concept_id = service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "retired concept evidence",
                title="Retired concept",
                description="retiredconceptterm statement",
            )
        ),
        actor="model",
    ).concept_ids[0]
    assert service.transition(concept_id, "retired", actor="owner", reason="obsolete").writes == 1

    report = recall(production_store.db_path, "retiredconceptterm")

    assert report.concepts == ()


def test_recall_returns_legacy_unbound_concept_with_label_and_empty_citations(
    production_store: ProductionStore,
) -> None:
    _service(production_store)  # installs the concept sidecar schema
    identity = _ConceptRepository(production_store.conn, now=lambda: _NOW).seed_legacy(
        original_bytes=b"legacy-recall-fixture",
        kind="Finding",
        title="Legacy recall concept",
        statement="legacyrecallterm legacy statement",
        tags=("legacy", "recall"),
        confidence=0.8,
        source_session_id="fixture-session-1",
        source_uri="sessionweaver://session/fixture-session-1",
        producer="fixture-writer/0.1",
    )
    production_store.conn.commit()

    report = recall(production_store.db_path, "legacyrecallterm")

    assert len(report.concepts) == 1
    hit = report.concepts[0]
    assert hit.concept_id == identity
    assert hit.binding_state == "legacy-unbound"
    assert hit.provenance_label == "legacy-unbound (session-level provenance)"
    assert hit.citations == ()


# --- 3. Ordering ---------------------------------------------------------------


def test_recall_breaks_bm25_ties_by_ascending_full_concept_id(
    production_store: ProductionStore,
) -> None:
    service = _service(production_store)
    ids: list[str] = []
    for suffix in ("one", "two"):
        _capture(production_store, f"tie evidence body {suffix}", key=f"tie-evidence-{suffix}")
        ids.append(
            service.winddown(
                "fixture-session-1",
                _document(
                    _concept(
                        f"tie evidence body {suffix}",
                        title="Tie concept",
                        description="tieterm identical statement",
                    )
                ),
                actor="model",
            ).concept_ids[0]
        )

    report = recall(production_store.db_path, "tieterm", k=2)

    assert [hit.concept_id for hit in report.concepts] == sorted(ids)


def test_recall_fallback_appends_or_only_results_after_and_results(
    production_store: ProductionStore,
) -> None:
    service = _service(production_store)
    _capture(production_store, "both terms evidence", key="both-terms-evidence")
    both_id = service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "both terms evidence",
                title="Both terms concept",
                description="alpha bravo statement",
            )
        ),
        actor="model",
    ).concept_ids[0]
    _capture(production_store, "only term evidence", key="only-term-evidence")
    only_id = service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "only term evidence",
                title="Only alpha concept",
                description="alpha only statement",
            )
        ),
        actor="model",
    ).concept_ids[0]

    report = recall(production_store.db_path, "alpha bravo", k=2)

    assert [hit.concept_id for hit in report.concepts] == [both_id, only_id]
    assert report.plan.fallback_used is True


def test_recall_and_query_reaching_k_alone_never_marks_fallback_used(
    production_store: ProductionStore,
) -> None:
    service = _service(production_store)
    for suffix in ("x", "y"):
        _capture(production_store, f"alpha bravo evidence {suffix}", key=f"ab-evidence-{suffix}")
        service.winddown(
            "fixture-session-1",
            _document(
                _concept(
                    f"alpha bravo evidence {suffix}",
                    title=f"Alpha bravo {suffix}",
                    description="alpha bravo statement",
                )
            ),
            actor="model",
        )

    report = recall(production_store.db_path, "alpha bravo", k=2)

    assert len(report.concepts) == 2
    assert report.plan.fallback_used is False


# --- 4. Dedup ------------------------------------------------------------------


def test_recall_dedupes_sessions_already_cited_by_returned_concepts_and_fills_k(
    production_store: ProductionStore,
) -> None:
    service = _service(production_store)
    _capture(production_store, "dedup cited evidence", key="dedup-evidence")
    concept_id = service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "dedup cited evidence",
                title="Dedup concept",
                description="dedupterm statement",
            )
        ),
        actor="model",
    ).concept_ids[0]
    _add_session(production_store, "fixture-session-3", project_path="/proj/dedup-3")
    _message(
        production_store,
        message_id="dedup-msg-1",
        session_id="fixture-session-1",
        content="dedupterm raw message one",
    )
    _message(
        production_store,
        message_id="dedup-msg-2",
        session_id="fixture-session-2",
        content="dedupterm raw message two",
    )
    _message(
        production_store,
        message_id="dedup-msg-3",
        session_id="fixture-session-3",
        content="dedupterm raw message three",
    )

    report = recall(production_store.db_path, "dedupterm", k=2)

    assert [hit.concept_id for hit in report.concepts] == [concept_id]
    session_ids = {hit.session_id for hit in report.sessions}
    assert "fixture-session-1" not in session_ids
    assert session_ids == {"fixture-session-2", "fixture-session-3"}
    assert len(report.sessions) == 2


# --- 4b. Explicit project filter and defensive guards -------------------------


def test_recall_with_explicit_project_filters_sessions_to_that_project(
    production_store: ProductionStore,
) -> None:
    _message(
        production_store,
        message_id="project-filter-msg-1",
        session_id="fixture-session-1",
        content="projectfilterterm session one",
    )
    _message(
        production_store,
        message_id="project-filter-msg-2",
        session_id="fixture-session-2",
        content="projectfilterterm session two",
    )
    config = yaml.safe_load(production_store.config_path.read_text(encoding="utf-8"))
    config["memory"]["projects"] = {
        "alpha": {"scope": "unclassified", "roots": []},
        "beta": {"scope": "unclassified", "roots": []},
    }
    production_store.config_path.write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    apply_policy(
        production_store.conn,
        ScopePolicy.from_config(config),
        actor="recall-project-test",
        dry_run=False,
    )
    production_store.conn.executemany(
        "INSERT INTO context_session_projects VALUES (?,?,?)",
        (
            ("fixture-session-1", "alpha", "explicit"),
            ("fixture-session-2", "beta", "explicit"),
        ),
    )
    production_store.conn.commit()

    report = recall(production_store.db_path, "projectfilterterm", project="alpha")

    assert {hit.session_id for hit in report.sessions} == {"fixture-session-1"}
    assert report.project == "alpha"


def test_recall_rejects_k_outside_one_to_fifty_at_the_library_level(
    production_store: ProductionStore,
) -> None:
    with pytest.raises(ValueError, match="k must be"):
        recall(production_store.db_path, "question", k=0)
    with pytest.raises(ValueError, match="k must be"):
        recall(production_store.db_path, "question", k=51)
    with pytest.raises(ValueError, match="k must be"):
        recall(production_store.db_path, "question", k=True)


def test_authorized_concepts_rejects_a_project_mismatched_with_the_open_context(
    production_store: ProductionStore,
) -> None:
    from agent_session_tools.context.public import open_context

    from session_weaver.authorization import authorized_concepts

    with (
        open_context(production_store.db_path, project=None) as context,
        pytest.raises(ValueError, match="project must match"),
    ):
        authorized_concepts(context, project="mismatched-project")


def test_fts_ranked_concept_ids_propagates_operationalerror() -> None:
    from session_weaver.recall import _fts_ranked_concept_ids

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE context_concept_fts USING fts5("
            "title, statement, tags, kind, concept_id UNINDEXED)"
        )
        with pytest.raises(sqlite3.OperationalError, match="unterminated string"):
            _fts_ranked_concept_ids(conn, '"unterminated')
        assert _fts_ranked_concept_ids(conn, "") == []
    finally:
        conn.close()


def test_recall_session_fallback_appends_or_only_session_after_and_results(
    production_store: ProductionStore,
) -> None:
    _add_session(production_store, "fixture-session-3", project_path="/proj/session-fallback-3")
    _message(
        production_store,
        message_id="session-fallback-both",
        session_id="fixture-session-1",
        content="sessionfallbackalpha sessionfallbackbravo both terms",
    )
    _message(
        production_store,
        message_id="session-fallback-only",
        session_id="fixture-session-3",
        content="sessionfallbackalpha only term",
    )

    report = recall(production_store.db_path, "sessionfallbackalpha sessionfallbackbravo", k=2)

    assert [hit.session_id for hit in report.sessions] == [
        "fixture-session-1",
        "fixture-session-3",
    ]
    assert report.plan.fallback_used is True


# --- 5. Zero-embedding guard (R11) --------------------------------------------


def _assert_no_embedding_statements(executed: list[str]) -> None:
    forbidden = ("message_embeddings", "semantic_search", "embedding")
    for statement in executed:
        lowered = statement.lower()
        assert not any(term in lowered for term in forbidden), statement


def test_zero_embedding_guard_catches_a_violation_as_a_positive_control() -> None:
    """Proves the assertion helper actually fails on a forbidden statement."""
    with pytest.raises(AssertionError):
        _assert_no_embedding_statements(["SELECT * FROM message_embeddings"])
    with pytest.raises(AssertionError):
        _assert_no_embedding_statements(["SELECT semantic_search()"])


def test_recall_never_executes_a_statement_touching_embeddings(
    production_store: ProductionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(production_store)
    _capture(production_store, "trace guard evidence", key="trace-guard-evidence")
    service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "trace guard evidence",
                title="Trace guard concept",
                description="traceguardterm statement",
            )
        ),
        actor="model",
    )
    executed: list[str] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(executed.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)

    recall(production_store.db_path, "traceguardterm")

    assert executed
    _assert_no_embedding_statements(executed)


def test_recall_module_never_imports_semantic_search() -> None:
    source = Path(recall_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all("semantic_search" not in alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or "semantic_search" not in node.module
            assert all("semantic_search" not in alias.name for alias in node.names)


# --- 7. Contract ---------------------------------------------------------------


def _validate(schema: dict[str, Any], instance: Any, defs: dict[str, Any] | None = None) -> None:
    resolved_defs: dict[str, Any] = schema.get("$defs", defs if defs is not None else {})
    if "$ref" in schema:
        target = schema["$ref"]
        assert target.startswith("#/$defs/"), f"unsupported $ref {target}"
        _validate(resolved_defs[target.removeprefix("#/$defs/")], instance, resolved_defs)
        return
    python_types: dict[str, Any] = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    expected_types = schema.get("type")
    if expected_types is not None:
        allowed = expected_types if isinstance(expected_types, list) else [expected_types]
        assert any(
            isinstance(instance, python_types[name])
            and not (name == "integer" and isinstance(instance, bool))
            for name in allowed
        ), f"{instance!r} does not match type(s) {allowed}"
    if "enum" in schema:
        assert instance in schema["enum"], f"{instance!r} not in {schema['enum']}"
    if isinstance(instance, dict):
        for key in schema.get("required", ()):
            assert key in instance, f"missing required key {key!r}"
        if schema.get("additionalProperties") is False:
            allowed_keys = set(schema.get("properties", {}))
            assert set(instance) <= allowed_keys, f"unexpected keys {set(instance) - allowed_keys}"
        for key, subschema in schema.get("properties", {}).items():
            if key in instance:
                _validate(subschema, instance[key], resolved_defs)
    if isinstance(instance, list):
        item_schema = schema.get("items")
        if item_schema is not None:
            for item in instance:
                _validate(item_schema, item, resolved_defs)
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema:
            assert instance >= schema["minimum"]
        if "maximum" in schema:
            assert instance <= schema["maximum"]
    if isinstance(instance, str) and "minLength" in schema:
        assert len(instance) >= schema["minLength"]


def test_recall_report_validates_against_the_committed_contract(
    production_store: ProductionStore,
) -> None:
    service = _service(production_store)
    _capture(production_store, "contract evidence body", key="contract-evidence")
    service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "contract evidence body",
                title="Contract concept",
                description="contractterm statement",
            )
        ),
        actor="model",
    )
    _message(
        production_store,
        message_id="contract-msg",
        session_id="fixture-session-2",
        content="contractterm raw message",
    )

    report = recall(production_store.db_path, "contractterm")
    schema = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))

    _validate(schema, report.to_dict())


def test_a_corrupted_report_fails_the_committed_contract() -> None:
    schema = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    corrupted = {
        "concepts": [{"concept_id": "x"}],  # missing every other required key
        "sessions": [],
        "plan": {"terms": [], "and_query": "", "or_query": "", "fallback_used": False},
        "k": 5,
        "project": None,
    }

    with pytest.raises(AssertionError):
        _validate(schema, corrupted)


# --- 8. CLI ---------------------------------------------------------------------


def _assert_closed(conn: sqlite3.Connection) -> None:
    try:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            conn.execute("SELECT 1")
    finally:
        conn.close()


def test_recall_cli_json_output_matches_the_contract(
    production_store: ProductionStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _service(production_store)
    _capture(production_store, "cli contract evidence", key="cli-contract-evidence")
    service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "cli contract evidence",
                title="CLI contract concept",
                description="clicontractterm statement",
            )
        ),
        actor="model",
    )

    assert main(["recall", "clicontractterm", "--db", str(production_store.db_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "recall"
    schema = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    _validate(schema, {key: value for key, value in payload.items() if key != "command"})


def test_recall_cli_default_text_output_lists_concepts_then_sessions(
    production_store: ProductionStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _service(production_store)
    _capture(production_store, "cli text evidence", key="cli-text-evidence")
    service.winddown(
        "fixture-session-1",
        _document(
            _concept(
                "cli text evidence",
                title="CLI text concept",
                description="clitextterm statement",
            )
        ),
        actor="model",
    )

    assert main(["recall", "clitextterm", "--db", str(production_store.db_path)]) == 0
    output = capsys.readouterr().out

    concepts_at = output.index("concepts (")
    sessions_at = output.index("sessions (")
    assert concepts_at < sessions_at
    assert "CLI text concept" in output


def test_recall_cli_succeeds_with_zero_results(
    production_store: ProductionStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "recall",
                "zzznonsenseabcdef",
                "--db",
                str(production_store.db_path),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["concepts"] == []
    assert payload["sessions"] == []


def test_recall_cli_rejects_explicit_empty_values_before_any_opener(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _unexpected_recall(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("empty recall input must be rejected before any opener")

    monkeypatch.setattr("session_weaver.cli.recall", _unexpected_recall)

    with pytest.raises(SystemExit) as raised:
        main(["recall", ""])
    assert raised.value.code == 2

    with pytest.raises(SystemExit) as raised:
        main(["recall", "question", "--db", ""])
    assert raised.value.code == 2

    with pytest.raises(SystemExit) as raised:
        main(["recall", "question", "--project", ""])
    assert raised.value.code == 2


@pytest.mark.parametrize("value", ["0", "51", "-1", "", "not-a-number"])
def test_recall_cli_rejects_k_outside_one_to_fifty(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["recall", "question", "--k", value])

    assert raised.value.code == 2
    assert "--k" in capsys.readouterr().err


def test_recall_cli_maps_a_missing_database_to_exit_one_with_content_free_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "does-not-exist.db"

    assert main(["recall", "question", "--db", str(missing)]) == 1
    payload = json.loads(capsys.readouterr().err)

    assert payload["command"] == "recall"
    assert str(missing) not in json.dumps(payload)


def test_recall_cli_opens_the_database_read_only(
    production_store: ProductionStore,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    connect_calls: list[tuple[Any, dict[str, Any]]] = []
    probe_results: list[bool] = []

    def tracked_connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        connect_calls.append((database, kwargs))
        conn = real_connect(database, *args, **kwargs)
        if "mode=ro" in str(database):
            try:
                conn.execute("CREATE TABLE recall_cli_write_probe(x)")
            except sqlite3.OperationalError:
                probe_results.append(True)
            else:
                probe_results.append(False)
                conn.rollback()
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    assert main(["recall", "reach context evidence", "--db", str(production_store.db_path)]) == 0

    assert connect_calls
    assert any("mode=ro" in str(database) for database, _ in connect_calls)
    assert probe_results
    assert all(probe_results)


def test_recall_cli_closes_every_owned_connection_on_success_and_failure(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def tracked_connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(database, *args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    assert main(["recall", "reach context evidence", "--db", str(production_store.db_path)]) == 0
    capsys.readouterr()
    invalid_db = tmp_path / "invalid.db"
    invalid_db.write_bytes(b"not sqlite")
    assert main(["recall", "reach context evidence", "--db", str(invalid_db)]) == 1
    capsys.readouterr()

    assert opened
    for conn in opened:
        _assert_closed(conn)
