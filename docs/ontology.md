# Build and inspect the ontology

[Overview](../README.md) · [OKF knowledge](knowledge.md) · [Architecture](architecture/README.md)

The ontology connects sessions through the things they reference. For example, two sessions
may mention the same file even when their conversation titles differ. The maintained builder
extracts these relationships deterministically from canonical session messages without a
model call. Its current role is **diagnostics**, not recall or verified execution evidence.

## Vocabulary and instances

The T-Box defines classes and relationships; the A-Box contains their concrete instances.
Both live as derived tables in the selected SQLite session database.

| Class | Example |
| --- | --- |
| Project | A filesystem project root. |
| Harness | The coding agent that produced the session. |
| Session | A recorded conversation. |
| SubagentSession | A child session, also a Session. |
| Artifact | A referenced file path. |
| Command | A command class identified by its executable. |
| TestRun | A captured test summary. |

| Relationship | Direction | Evidence limit |
| --- | --- | --- |
| `ranIn` | Session → Project | Recorded project context. |
| `conductedBy` | Session → Harness | Recorded source harness. |
| `childOf` | SubagentSession → Session | Parent metadata and an available parent. |
| `touched` | Session → Artifact | A path mention does not prove an edit. |
| `executed` | Session → Command | Parsed command text does not prove execution. |
| `produced` | Session → TestRun | A parsed summary is not independent test verification. |

## Build with the maintained command

After `session-export` has populated a database, choose its path explicitly:

```bash
session-weaver ontology rebuild --db /absolute/path/to/sessions.db
session-weaver ontology status --db /absolute/path/to/sessions.db
session-weaver ontology rebuild --incremental --db /absolute/path/to/sessions.db
```

`rebuild` writes derived tables in that selected database. It builds staging tables, validates
references and types, and swaps the result within a transaction. An incremental rebuild reuses
unchanged extraction rows when the previous state is valid; it may fall back to a full rebuild
and reports the mode and reason. Both modes return JSON with counts, candidate sessions,
extraction version and logical hash.

`status` is read-only. Check `healthy`, coverage, freshness, source-count agreement, schema,
foreign keys, domain/range and logical-hash agreement. A missing or unhealthy ontology is a
failure, not a request to silently repair it. Run an explicit rebuild when appropriate.
Omitting `--db` selects the configured store, normally `~/.config/studyloop/sessions.db`.

For a rehearsal, use a SQLite Online Backup of the source and pass the backup path explicitly.
Do not copy just the main file of a WAL-mode database. The maintained
[`ontology_live.py`](../src/session_weaver/ontology_live.py) harness and
[documented opt-in test](../README.md#development) exercise this backup-only acceptance path.
Do not use the frozen `code/build-ontology.py` as an installation tutorial.

## Inspect a relationship

Open your chosen rehearsal database read-only:

```bash
sqlite3 -readonly /absolute/path/to/ontology-rehearsal.db
```

Find source sessions mentioning a file, then use `.quit` to leave SQLite:

```sql
SELECT r.subject AS session, a.label AS artifact
FROM ontology_relation AS r
JOIN ontology_individual AS a ON a.id = r.object
WHERE r.predicate = 'touched'
  AND a.label LIKE '%pipeline.py%';
```

Direct SQL is an operator diagnostic over the selected database, not a scope-filtered recall
interface. Check the original conversation before drawing conclusions from a returned edge.

## What the graph cannot establish

- The current extraction version retains the PoC's regular expressions, including macOS-style
  `/Users/...` artifact detection. Other path forms and equivalent aliases can be missed.
- The graph does not automatically populate concepts, explain a decision, or validate learning.
  Use the [OKF/concept workflow](knowledge.md) for source-linked explanations.
- Recall does not consume these tables. A larger graph is not evidence of better retrieval.
- Normal/global delta sync excludes ontology tables. The pinned first-time whole-database
  seed may still carry them; destination-local seed sanitization remains future work.

The maintained implementation is [`ontology.py`](../src/session_weaver/ontology.py), with
[CLI coverage](../tests/test_cli.py), [ontology tests](../tests/test_ontology.py) and
[retained baseline evidence](data/ontology-tier1-baseline.json).
