"""Concept-first, AND->OR recall: concepts, then deduplicated raw-text sessions.

Ported verbatim from the storage-decision PoC (see ``code/bench-t2.py``'s
``terms()``/``okf()`` and ``code/bench-day1.py``'s ``planned_fts()``): tokenize
the question, drop stop words and short tokens, quote every remaining term for
FTS5, try the AND-joined query first, and fall back to the OR-joined query
only when AND does not fill the requested result count.

Design authority: ``reviews/2026-09-07-phase2-design-council/ARBITRATION.md``
Q3 (a new surface reusing F1's storage/scope/tombstone machinery -- no new
store, no embeddings) and the settled rulings in
``reviews/2026-09-07-sessionweaver-phase2/EXECUTION-ERRATA.md`` #2 (legacy
roots keep their label and session-level provenance), #5 (deterministic
concept ordering) and #6 (concepts first, then deduplicated sessions; the
tier-1 ontology is never a third result class).

Every concept returned here passes through ``authorization.authorized_concepts``
-- the exact seam ``projection.py`` uses -- so recall and projection can never
disagree about which concept roots are visible. Sessions are selected
independently through ``messages_fts`` + ``visibility_sql`` and excluded when
already cited by a returned concept.

This module never imports ``agent_session_tools.semantic_search`` and never
reads ``message_embeddings``: recall ships with **no embeddings** (Q3), and
the ontology tables are not consulted (errata #6).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from agent_session_tools.context.public import AgentContext, open_context
from agent_session_tools.context.scope import visibility_sql

from .authorization import AuthorizedConcept, authorized_concepts

# Pinned verbatim from code/bench-t2.py / code/bench-day1.py -- do not add or
# remove a single word without updating test_recall.py's pinning test. Kept as
# the PoC's own string+split() form (not a list literal) so a diff against the
# PoC source stays a literal comparison.
STOP = frozenset(
    "a an the is are was were be been being do does did to of in on for with"  # noqa: SIM905
    " and or not what which who why how when where whose that this these those"
    " it its during every any can cant can't could should would will shall"
    " about into from as at by we our your my i you they them he she his her".split()
)

_TERM = re.compile(r"[a-zA-Z0-9_./-]+")
_CONCEPT_FTS_LIMIT = 200
_SESSION_FTS_LIMIT = 300
_PROVENANCE_BOUND = "machine-confirmed citation"
_PROVENANCE_LEGACY = "legacy-unbound (session-level provenance)"


def _terms(question: str) -> tuple[str, ...]:
    return tuple(
        token for token in _TERM.findall(question.lower()) if token not in STOP and len(token) > 2
    )


def _quote_term(term: str) -> str:
    """Wrap one extracted token as an FTS5 double-quoted phrase.

    ``_terms`` only ever emits ``[a-zA-Z0-9_./-]+`` tokens, so there is never a
    literal double quote to escape here. The quoting exists so FTS5 never
    reads a bare token (for example a leading ``-``) as a query operator --
    not to sanitise characters the tokenizer already excludes.
    """
    return f'"{term}"'


@dataclass(frozen=True)
class QueryPlan:
    """The pure AND->OR plan for one question.

    ``fallback_used`` defaults to false here; ``recall()`` (the executor)
    returns a copy with it set once it observes whether the OR-only query
    actually contributed a result.
    """

    terms: tuple[str, ...]
    and_query: str
    or_query: str
    fallback_used: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "terms": list(self.terms),
            "and_query": self.and_query,
            "or_query": self.or_query,
            "fallback_used": self.fallback_used,
        }


def plan(question: str) -> QueryPlan:
    """Tokenize, drop stop words/short tokens, and build the AND/OR FTS queries."""
    terms = _terms(question)
    quoted = tuple(_quote_term(term) for term in terms)
    return QueryPlan(
        terms=terms,
        and_query=" AND ".join(quoted),
        or_query=" OR ".join(quoted),
    )


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    start: int
    end: int

    def to_dict(self) -> dict[str, object]:
        return {"evidence_id": self.evidence_id, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class ConceptHit:
    concept_id: str
    kind: str
    title: str
    statement: str
    standing: str
    binding_state: str
    confidence: float
    source_session_id: str | None
    provenance_label: str
    citations: tuple[Citation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "concept_id": self.concept_id,
            "kind": self.kind,
            "title": self.title,
            "statement": self.statement,
            "standing": self.standing,
            "binding_state": self.binding_state,
            "confidence": self.confidence,
            "source_session_id": self.source_session_id,
            "provenance_label": self.provenance_label,
            "citations": [citation.to_dict() for citation in self.citations],
        }


@dataclass(frozen=True)
class SessionHit:
    session_id: str
    source: str
    project_path: str | None
    updated_at: str | None
    preview: str

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "source": self.source,
            "project_path": self.project_path,
            "updated_at": self.updated_at,
            "preview": self.preview,
        }


@dataclass(frozen=True)
class RecallReport:
    concepts: tuple[ConceptHit, ...]
    sessions: tuple[SessionHit, ...]
    plan: QueryPlan
    k: int
    project: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "concepts": [concept.to_dict() for concept in self.concepts],
            "sessions": [session.to_dict() for session in self.sessions],
            "plan": self.plan.to_dict(),
            "k": self.k,
            "project": self.project,
        }


def _concept_hit(authorized: AuthorizedConcept) -> ConceptHit:
    root = authorized.root
    bound = cast(str, root["binding_state"]) == "bound"
    citations = tuple(
        Citation(
            evidence_id=cast(str, citation["evidence_id"]),
            start=cast(int, citation["start_offset"]),
            end=cast(int, citation["end_offset"]),
        )
        for citation in authorized.citations
    )
    return ConceptHit(
        concept_id=authorized.concept_id,
        kind=cast(str, root["kind"]),
        title=cast(str, root["title"]),
        statement=cast(str, root["statement"]),
        standing=authorized.standing,
        binding_state=cast(str, root["binding_state"]),
        confidence=float(cast(float, root["confidence"])),
        source_session_id=cast(str | None, root["source_session_id"]),
        provenance_label=_PROVENANCE_BOUND if bound else _PROVENANCE_LEGACY,
        citations=citations,
    )


def _fts_ranked_concept_ids(conn: sqlite3.Connection, query: str) -> list[str]:
    if not query:
        return []
    rows = conn.execute(
        "SELECT concept_id FROM context_concept_fts"
        " WHERE context_concept_fts MATCH ?"
        " ORDER BY bm25(context_concept_fts), concept_id"
        " LIMIT ?",
        (query, _CONCEPT_FTS_LIMIT),
    ).fetchall()
    return [cast(str, row[0]) for row in rows]


def _select_concepts(
    conn: sqlite3.Connection,
    authorized_by_id: dict[str, AuthorizedConcept],
    and_query: str,
    or_query: str,
    k: int,
) -> tuple[tuple[ConceptHit, ...], bool]:
    selected: list[ConceptHit] = []
    seen: set[str] = set()
    fallback_used = False
    if not authorized_by_id:
        return (), fallback_used
    for query, is_fallback in ((and_query, False), (or_query, True)):
        if not query or len(selected) >= k:
            continue
        for concept_id in _fts_ranked_concept_ids(conn, query):
            if concept_id in seen:
                continue
            seen.add(concept_id)
            authorized = authorized_by_id.get(concept_id)
            if authorized is None:
                continue
            selected.append(_concept_hit(authorized))
            if is_fallback:
                fallback_used = True
            if len(selected) == k:
                break
    return tuple(selected), fallback_used


def _project_clause(context: AgentContext) -> tuple[str, tuple[object, ...]]:
    if context.project is None:
        return "", ()
    return (
        " AND EXISTS (SELECT 1 FROM context_session_projects sp"
        " WHERE sp.session_id=s.id AND sp.project_id=?)",
        (context.project,),
    )


def _select_sessions(
    context: AgentContext,
    and_query: str,
    or_query: str,
    k: int,
    exclude_session_ids: frozenset[str],
) -> tuple[tuple[SessionHit, ...], bool]:
    visibility_clause, visibility_params = visibility_sql(
        context.conn, "s.id", policy=context.policy, scope=context.scope
    )
    project_clause, project_params = _project_clause(context)
    sql = (
        "SELECT m.session_id, s.source, s.project_path, s.updated_at,"
        " substr(m.content,1,300)"
        " FROM messages_fts"
        " JOIN messages m ON m.rowid=messages_fts.rowid"
        " JOIN sessions s ON s.id=m.session_id"
        f" WHERE messages_fts MATCH ? AND {visibility_clause}{project_clause}"
        " ORDER BY bm25(messages_fts), m.timestamp DESC"
        " LIMIT ?"
    )
    selected: list[SessionHit] = []
    seen: set[str] = set(exclude_session_ids)
    fallback_used = False
    for query, is_fallback in ((and_query, False), (or_query, True)):
        if not query or len(selected) >= k:
            continue
        try:
            rows = context.conn.execute(
                sql,
                (query, *visibility_params, *project_params, _SESSION_FTS_LIMIT),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for session_id, source, project_path, updated_at, preview in rows:
            if session_id in seen:
                continue
            seen.add(session_id)
            selected.append(
                SessionHit(
                    session_id=cast(str, session_id),
                    source=cast(str, source),
                    project_path=cast(str | None, project_path),
                    updated_at=cast(str | None, updated_at),
                    preview=cast(str, preview),
                )
            )
            if is_fallback:
                fallback_used = True
            if len(selected) == k:
                break
    return tuple(selected), fallback_used


def recall(db: Path, question: str, *, k: int = 5, project: str | None = None) -> RecallReport:
    """Concepts first (via the shared authorization seam), then deduplicated sessions.

    Opens ``db`` read-only through ``open_context`` (never ``write=True``).
    Never touches ``message_embeddings`` or imports ``semantic_search``; the
    tier-1 ontology is not consulted (errata #6).
    """
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 50:
        raise ValueError("k must be an integer between 1 and 50")
    query_plan = plan(question)
    with open_context(db, project=project) as context:
        authorized_by_id = {
            authorized.concept_id: authorized
            for authorized in authorized_concepts(context, project=context.project)
        }
        concepts, concept_fallback = _select_concepts(
            context.conn, authorized_by_id, query_plan.and_query, query_plan.or_query, k
        )
        exclude_session_ids = frozenset(
            concept.source_session_id for concept in concepts if concept.source_session_id
        )
        sessions, session_fallback = _select_sessions(
            context, query_plan.and_query, query_plan.or_query, k, exclude_session_ids
        )
    return RecallReport(
        concepts=concepts,
        sessions=sessions,
        plan=replace(query_plan, fallback_used=concept_fallback or session_fallback),
        k=k,
        project=project,
    )
