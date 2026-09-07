"""Transactional concept/evidence/lifecycle core behind one deep service seam."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from agent_session_tools.config_loader import get_db_path, load_config
from agent_session_tools.context.public import AgentContext, open_context
from agent_session_tools.context.scope import visibility_sql

from .concept_schema import _ensure_schema
from .winddown import (
    _Concept,
    _Issue,
    _parse_bind_document,
    _parse_winddown,
    _Quote,
)

Standing = Literal["proposed", "accepted", "retired"]
TransitionStanding = Literal["accepted", "retired"]


@dataclass(frozen=True)
class BatchResult:
    """Outcome of an atomic wind-down batch."""

    writes: int
    concept_ids: tuple[str, ...] = ()
    errors: tuple[_Issue, ...] = ()


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of one lifecycle transition."""

    writes: int
    concept_id: str
    standing: str | None = None
    event_id: str | None = None
    errors: tuple[_Issue, ...] = ()


@dataclass(frozen=True)
class BindResult:
    """Outcome of atomically binding one legacy root."""

    writes: int
    legacy_concept_id: str
    concept_id: str | None = None
    assertion_id: str | None = None
    errors: tuple[_Issue, ...] = ()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_payload(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _error(path: str, code: str, message: str) -> _Issue:
    return _Issue(path=path, code=code, message=message)


def _call_text(value: object, path: str, maximum: int) -> _Issue | None:
    if not isinstance(value, str):
        return _error(path, "invalid_type", "Value must be text")
    if not value.strip():
        return _error(path, "blank", "Value must not be blank")
    if len(value.strip()) > maximum:
        return _error(path, "too_long", f"Value must be at most {maximum} code points")
    return None


def _rows(
    conn: sqlite3.Connection, sql: str, params: Sequence[object] = ()
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


class _EvidenceResolver:
    """Resolve exact citations after applying the pinned context visibility policy."""

    def __init__(self, context: AgentContext, session_id: str) -> None:
        self._context = context
        self._session_id = session_id
        self._sources: dict[str, str] | None = None

    def _visible_sources(self) -> dict[str, str]:
        if self._sources is not None:
            return self._sources
        store_clause, store_params = self._context.store._where(self._context.access)
        visibility_clause, visibility_params = visibility_sql(
            self._context.conn,
            "e.session_id",
            policy=self._context.policy,
            scope=self._context.scope,
        )
        identities = [
            row[0]
            for row in self._context.conn.execute(
                """SELECT e.id FROM context_evidence e
                   LEFT JOIN context_session_projects sp ON sp.session_id=e.session_id
                   LEFT JOIN context_projects p ON p.id=sp.project_id
                   WHERE e.session_id=? AND """
                + store_clause
                + " AND "
                + visibility_clause
                + " ORDER BY e.id",
                (self._session_id, *store_params, *visibility_params),
            )
        ]
        sources: dict[str, str] = {}
        for identity in identities:
            source = self._context._source(identity)
            if source is not None:
                sources[identity] = source["body"]
        self._sources = sources
        return sources

    @staticmethod
    def _occurrences(body: str, quote: str) -> list[int]:
        starts: list[int] = []
        offset = body.find(quote)
        while offset >= 0:
            starts.append(offset)
            offset = body.find(quote, offset + 1)
        return starts

    def resolve(
        self, quotes: Sequence[_Quote], *, path: str
    ) -> tuple[tuple[dict[str, object], ...], tuple[_Issue, ...]]:
        sources = self._visible_sources()
        citations: list[dict[str, object]] = []
        issues: list[_Issue] = []
        seen: set[tuple[str, int, int, str]] = set()
        for index, locator in enumerate(quotes):
            quote_path = f"{path}/{index}"
            citation: tuple[str, int, int, str] | None = None
            if locator.evidence_id is not None:
                body = sources.get(locator.evidence_id)
                if body is None:
                    issues.append(
                        _error(
                            quote_path,
                            "evidence_unavailable",
                            "Evidence is unavailable in the requested session and scope",
                        )
                    )
                    continue
                assert locator.start is not None and locator.end is not None
                if locator.end > len(body) or body[locator.start : locator.end] != locator.quote:
                    issues.append(
                        _error(
                            quote_path,
                            "locator_mismatch",
                            "Locator does not bind the literal quote to this evidence version",
                        )
                    )
                    continue
                citation = (
                    locator.evidence_id,
                    locator.start,
                    locator.end,
                    locator.quote,
                )
            else:
                matches = [
                    (identity, start, start + len(locator.quote), locator.quote)
                    for identity, body in sources.items()
                    for start in self._occurrences(body, locator.quote)
                ]
                if not matches:
                    issues.append(
                        _error(
                            quote_path,
                            "quote_not_found",
                            "Literal quote was not found in visible evidence for this session",
                        )
                    )
                    continue
                if len(matches) > 1:
                    issues.append(
                        _error(
                            quote_path,
                            "ambiguous_quote",
                            "Literal quote has multiple visible occurrences; supply a locator",
                        )
                    )
                    continue
                citation = matches[0]
            if citation in seen:
                issues.append(
                    _error(
                        quote_path,
                        "duplicate_citation",
                        "Resolved citations must be unique",
                    )
                )
                continue
            seen.add(citation)
            citations.append(
                {
                    "evidence_id": citation[0],
                    "start": citation[1],
                    "end": citation[2],
                    "quote": citation[3],
                }
            )
        return tuple(citations), tuple(issues)


class _ConceptRepository:
    """Private SQL adapter for immutable roots, events, clocks, and FTS."""

    def __init__(self, conn: sqlite3.Connection, *, now: Callable[[], str] | None = None) -> None:
        self.conn = conn
        self._now = now or globals()["_now"]

    def _checkpoint(self, name: str) -> None:
        """No-op fault boundary monkeypatched by rollback tests."""

    def _allocate(self) -> tuple[str, int, int]:
        maximum = self.conn.execute(
            "SELECT COALESCE(max(logical_time),0) FROM context_concept_events"
        ).fetchone()[0]
        row = self.conn.execute(
            """UPDATE context_concept_clock
               SET origin_seq=origin_seq+1,
                   logical_time=max(logical_time,?)+1
               WHERE id=1 AND origin_instance=(
                 SELECT instance FROM context_access_state WHERE id=1
               )
               RETURNING origin_instance,origin_seq,logical_time""",
            (maximum,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Concept clock is unavailable or changed identity")
        self._checkpoint("after_clock")
        return cast(tuple[str, int, int], tuple(row))

    def append_event(
        self,
        *,
        concept_id: str,
        parent_event_id: str | None,
        standing: Standing,
        actor: str,
        reason: str,
    ) -> str:
        origin_instance, origin_seq, logical_time = self._allocate()
        display_timestamp = self._now()
        payload = {
            "concept_id": concept_id,
            "parent_event_id": parent_event_id,
            "standing": standing,
            "actor": actor,
            "reason": reason,
            "display_timestamp": display_timestamp,
            "origin_instance": origin_instance,
            "origin_seq": origin_seq,
            "logical_time": logical_time,
        }
        identity = _hash_payload(payload)
        self.conn.execute(
            """INSERT INTO context_concept_events(
               id,concept_id,parent_event_id,standing,actor,reason,display_timestamp,
               origin_instance,origin_seq,logical_time) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                identity,
                concept_id,
                parent_event_id,
                standing,
                actor,
                reason,
                display_timestamp,
                origin_instance,
                origin_seq,
                logical_time,
            ),
        )
        self._checkpoint("after_event")
        return identity

    def insert_bound(
        self,
        *,
        assertion_id: str,
        origin: Literal["winddown", "legacy-bind"],
        kind: str,
        title: str,
        statement: str,
        tags: Sequence[str],
        confidence: float,
        source_session_id: str,
        source_uri: str,
        producer: str,
        legacy_file_sha256: str | None = None,
        supersedes_concept_id: str | None = None,
    ) -> str:
        self.conn.execute(
            """INSERT INTO context_concepts(
               id,assertion_id,binding_state,origin,kind,title,statement,canonical_tags,
               confidence,source_session_id,source_uri,producer,created_at,
               legacy_file_sha256,supersedes_concept_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                assertion_id,
                assertion_id,
                "bound",
                origin,
                kind,
                title,
                statement,
                _canonical_json(sorted(tags)),
                confidence,
                source_session_id,
                source_uri,
                producer,
                self._now(),
                legacy_file_sha256,
                supersedes_concept_id,
            ),
        )
        self._checkpoint("after_root")
        return assertion_id

    def seed_legacy(
        self,
        *,
        original_bytes: bytes,
        kind: str,
        title: str,
        statement: str,
        tags: Sequence[str],
        confidence: float,
        source_session_id: str,
        source_uri: str,
        producer: str,
    ) -> str:
        """Private pre-seed seam for A3a tests and the later A3b importer."""
        if not isinstance(original_bytes, bytes):
            raise ValueError("Legacy identity requires original file bytes")
        digest = hashlib.sha256(original_bytes).hexdigest()
        identity = "legacy:" + digest
        self.conn.execute(
            """INSERT INTO context_concepts(
               id,assertion_id,binding_state,origin,kind,title,statement,canonical_tags,
               confidence,source_session_id,source_uri,producer,created_at,
               legacy_file_sha256,supersedes_concept_id)
               VALUES (?,NULL,'legacy-unbound','legacy-okf',?,?,?,?,?,?,?,?,?,?,NULL)""",
            (
                identity,
                kind,
                title,
                statement,
                _canonical_json(sorted(tags)),
                confidence,
                source_session_id,
                source_uri,
                producer,
                self._now(),
                digest,
            ),
        )
        self._checkpoint("after_root")
        self.append_event(
            concept_id=identity,
            parent_event_id=None,
            standing="proposed",
            actor=producer,
            reason="legacy import",
        )
        return identity

    def current_event(self, concept_id: str) -> dict[str, Any]:
        rows = _rows(
            self.conn,
            """SELECT * FROM context_concept_events WHERE concept_id=?
               ORDER BY CASE standing WHEN 'retired' THEN 2
                                      WHEN 'accepted' THEN 1 ELSE 0 END DESC,
                        logical_time DESC,origin_instance DESC,origin_seq DESC,id DESC
               LIMIT 1""",
            (concept_id,),
        )
        if not rows:
            raise RuntimeError("Concept has no lifecycle event")
        return rows[0]

    @staticmethod
    def _session_visible(context: AgentContext, session_id: str) -> bool:
        clause, params = visibility_sql(
            context.conn,
            "s.id",
            policy=context.policy,
            scope=context.scope,
        )
        project_clause = ""
        project_params: tuple[object, ...] = ()
        if context.project is not None:
            project_clause = (
                " AND EXISTS (SELECT 1 FROM context_session_projects sp "
                "WHERE sp.session_id=s.id AND sp.project_id=?)"
            )
            project_params = (context.project,)
        return (
            context.conn.execute(
                "SELECT 1 FROM sessions s WHERE s.id=? AND " + clause + project_clause,
                (session_id, *params, *project_params),
            ).fetchone()
            is not None
        )

    def authorized_root(self, context: AgentContext, concept_id: str) -> dict[str, Any] | None:
        head = self.conn.execute(
            """SELECT assertion_id,binding_state,source_session_id
               FROM context_concepts WHERE id=?""",
            (concept_id,),
        ).fetchone()
        if head is None:
            return None
        assertion_id, binding_state, source_session_id = head
        if binding_state == "bound":
            if assertion_id is None or context._assertion(assertion_id) is None:
                return None
        elif not self._session_visible(context, source_session_id):
            return None
        rows = _rows(self.conn, "SELECT * FROM context_concepts WHERE id=?", (concept_id,))
        return rows[0] if rows else None

    def search_fts(self, query: str) -> list[dict[str, Any]]:
        """Private A3a read-model proof; A4 owns the public recall surface."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("FTS query must be nonempty text")
        rows = _rows(
            self.conn,
            """WITH ranked AS (
                 SELECT e.*,
                   row_number() OVER (
                     PARTITION BY concept_id
                     ORDER BY CASE standing WHEN 'retired' THEN 2
                                            WHEN 'accepted' THEN 1 ELSE 0 END DESC,
                              logical_time DESC,origin_instance DESC,origin_seq DESC,id DESC
                   ) AS position
                 FROM context_concept_events e
               )
               SELECT c.id AS concept_id,c.title,c.statement,c.canonical_tags,c.kind,
                      c.binding_state,r.standing
               FROM context_concept_fts
               JOIN context_concepts c ON c.id=context_concept_fts.concept_id
               JOIN ranked r ON r.concept_id=c.id AND r.position=1
               WHERE context_concept_fts MATCH ? AND r.standing!='retired'
               ORDER BY c.id""",
            (query,),
        )
        return [
            {
                **row,
                "trust_label": (
                    "legacy-unbound"
                    if row["binding_state"] == "legacy-unbound"
                    else "model-proposed"
                ),
            }
            for row in rows
        ]


class ConceptService:
    """Small external seam for transactional concept operations."""

    def __init__(
        self,
        db: Path | None = None,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._db = (db or get_db_path(load_config())).expanduser().resolve()
        self._now = now or globals()["_now"]
        self._prepare_schema()

    def _prepare_schema(self) -> None:
        from agent_session_tools.context.managed_history import require_query_target

        require_query_target(self._db)
        conn = sqlite3.connect(self._db.as_uri() + "?mode=rw", uri=True)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            _ensure_schema(conn)
        finally:
            conn.rollback()
            conn.close()

    @staticmethod
    def _call_issues(
        *,
        session_id: object | None = None,
        concept_id: object | None = None,
        actor: object,
        reason: object | None = None,
    ) -> tuple[_Issue, ...]:
        issues: list[_Issue] = []
        if session_id is not None:
            issue = _call_text(session_id, "/session_id", 128)
            if issue:
                issues.append(issue)
        if concept_id is not None:
            issue = _call_text(concept_id, "/concept_id", 128)
            if issue:
                issues.append(issue)
        issue = _call_text(actor, "/actor", 128)
        if issue:
            issues.append(issue)
        if reason is not None:
            issue = _call_text(reason, "/reason", 2000)
            if issue:
                issues.append(issue)
        return tuple(issues)

    def winddown(
        self,
        session_id: str,
        document: object,
        *,
        actor: str,
        project: str | None = None,
    ) -> BatchResult:
        concepts, parse_issues = _parse_winddown(document)
        issues = (*parse_issues, *self._call_issues(session_id=session_id, actor=actor))
        if issues:
            return BatchResult(writes=0, errors=issues)
        if not concepts:
            return BatchResult(writes=0)
        with open_context(self._db, write=True, project=project) as context:
            repository = _ConceptRepository(context.conn, now=self._now)
            resolver = _EvidenceResolver(context, session_id)
            resolved: list[tuple[_Concept, tuple[dict[str, object], ...]]] = []
            resolution_issues: list[_Issue] = []
            for index, concept in enumerate(concepts):
                citations, citation_issues = resolver.resolve(
                    concept.quotes, path=f"/concepts/{index}/quotes"
                )
                resolved.append((concept, citations))
                resolution_issues.extend(citation_issues)
            if resolution_issues:
                return BatchResult(writes=0, errors=tuple(resolution_issues))
            concept_ids: list[str] = []
            for concept, citations in resolved:
                proposal = context.propose(
                    statement=concept.description,
                    state="unknown",
                    target=None,
                    citations=list(citations),
                    producer=actor,
                )
                assertion_id = cast(str, proposal["assertion_id"])
                repository._checkpoint("after_assertion")
                repository.insert_bound(
                    assertion_id=assertion_id,
                    origin="winddown",
                    kind=concept.kind,
                    title=concept.title,
                    statement=concept.description,
                    tags=concept.tags,
                    confidence=concept.confidence,
                    source_session_id=session_id,
                    source_uri=f"sessionweaver://session/{session_id}",
                    producer=actor,
                )
                repository.append_event(
                    concept_id=assertion_id,
                    parent_event_id=None,
                    standing="proposed",
                    actor=actor,
                    reason="winddown",
                )
                concept_ids.append(assertion_id)
            return BatchResult(writes=len(concept_ids), concept_ids=tuple(concept_ids))

    def transition(
        self,
        concept_id: str,
        standing: TransitionStanding,
        *,
        actor: str,
        reason: str,
        project: str | None = None,
    ) -> TransitionResult:
        issues = list(self._call_issues(concept_id=concept_id, actor=actor, reason=reason))
        if standing not in ("accepted", "retired"):
            issues.append(_error("/standing", "invalid_choice", "Unknown standing"))
        if issues:
            return TransitionResult(writes=0, concept_id=concept_id, errors=tuple(issues))
        with open_context(self._db, write=True, project=project) as context:
            repository = _ConceptRepository(context.conn, now=self._now)
            root = repository.authorized_root(context, concept_id)
            if root is None:
                return TransitionResult(
                    writes=0,
                    concept_id=concept_id,
                    errors=(
                        _error(
                            "/concept_id",
                            "concept_unavailable",
                            "Concept is unavailable under the requested scope",
                        ),
                    ),
                )
            if root["binding_state"] == "legacy-unbound" and standing == "accepted":
                return TransitionResult(
                    writes=0,
                    concept_id=concept_id,
                    errors=(
                        _error(
                            "/standing",
                            "legacy_unbound_requires_bind",
                            "Legacy-unbound concepts require exact evidence binding",
                        ),
                    ),
                )
            current = repository.current_event(concept_id)
            current_standing = current["standing"]
            if current_standing == "retired":
                return TransitionResult(
                    writes=0,
                    concept_id=concept_id,
                    errors=(
                        _error(
                            "/standing",
                            "retired_terminal",
                            "Retired concepts are terminal",
                        ),
                    ),
                )
            allowed = (current_standing == "proposed" and standing in ("accepted", "retired")) or (
                current_standing == "accepted" and standing == "retired"
            )
            if not allowed:
                return TransitionResult(
                    writes=0,
                    concept_id=concept_id,
                    errors=(
                        _error(
                            "/standing",
                            "invalid_transition",
                            f"Cannot transition {current_standing} to {standing}",
                        ),
                    ),
                )
            event_id = repository.append_event(
                concept_id=concept_id,
                parent_event_id=cast(str, current["id"]),
                standing=standing,
                actor=actor,
                reason=reason,
            )
            return TransitionResult(
                writes=1,
                concept_id=concept_id,
                standing=standing,
                event_id=event_id,
            )

    def bind_legacy(
        self,
        concept_id: str,
        document: object,
        *,
        actor: str,
        reason: str,
        project: str | None = None,
    ) -> BindResult:
        quotes, parse_issues = _parse_bind_document(document)
        issues = (
            *parse_issues,
            *self._call_issues(concept_id=concept_id, actor=actor, reason=reason),
        )
        if issues:
            return BindResult(writes=0, legacy_concept_id=concept_id, errors=issues)
        with open_context(self._db, write=True, project=project) as context:
            repository = _ConceptRepository(context.conn, now=self._now)
            root = repository.authorized_root(context, concept_id)
            if root is None:
                return BindResult(
                    writes=0,
                    legacy_concept_id=concept_id,
                    errors=(
                        _error(
                            "/concept_id",
                            "concept_unavailable",
                            "Concept is unavailable under the requested scope",
                        ),
                    ),
                )
            if root["binding_state"] != "legacy-unbound":
                return BindResult(
                    writes=0,
                    legacy_concept_id=concept_id,
                    errors=(
                        _error(
                            "/concept_id",
                            "not_legacy_unbound",
                            "Only legacy-unbound roots can be bound",
                        ),
                    ),
                )
            current = repository.current_event(concept_id)
            if current["standing"] == "retired":
                return BindResult(
                    writes=0,
                    legacy_concept_id=concept_id,
                    errors=(
                        _error(
                            "/concept_id",
                            "legacy_already_retired",
                            "Retired legacy roots cannot be bound",
                        ),
                    ),
                )
            resolver = _EvidenceResolver(context, cast(str, root["source_session_id"]))
            citations, resolution_issues = resolver.resolve(quotes, path="/quotes")
            if resolution_issues:
                return BindResult(
                    writes=0,
                    legacy_concept_id=concept_id,
                    errors=resolution_issues,
                )
            proposal = context.propose(
                statement=cast(str, root["statement"]),
                state="unknown",
                target=None,
                citations=list(citations),
                producer=actor,
            )
            assertion_id = cast(str, proposal["assertion_id"])
            repository._checkpoint("after_assertion")
            repository.insert_bound(
                assertion_id=assertion_id,
                origin="legacy-bind",
                kind=cast(str, root["kind"]),
                title=cast(str, root["title"]),
                statement=cast(str, root["statement"]),
                tags=tuple(json.loads(cast(str, root["canonical_tags"]))),
                confidence=float(root["confidence"]),
                source_session_id=cast(str, root["source_session_id"]),
                source_uri=cast(str, root["source_uri"]),
                producer=actor,
                legacy_file_sha256=cast(str, root["legacy_file_sha256"]),
                supersedes_concept_id=concept_id,
            )
            repository.append_event(
                concept_id=assertion_id,
                parent_event_id=None,
                standing="proposed",
                actor=actor,
                reason=reason,
            )
            repository._checkpoint("after_bound_initial_event")
            repository.append_event(
                concept_id=concept_id,
                parent_event_id=cast(str, current["id"]),
                standing="retired",
                actor=actor,
                reason=f"{reason}; bound_to={assertion_id}",
            )
            repository._checkpoint("after_legacy_retired_event")
            return BindResult(
                writes=4,
                legacy_concept_id=concept_id,
                concept_id=assertion_id,
                assertion_id=assertion_id,
            )
