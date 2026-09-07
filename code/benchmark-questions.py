"""Gold-standard benchmark questions for the storage PoC.

Each question carries `evidence`: SQL LIKE patterns (ANDed) that identify the
gold sessions in the derived retrieval view (kind='conversation' AND dup=0).
Golds are resolved and VERIFIED at build time — a question is only usable if
it has 1..12 gold sessions. Types: K=keyword-friendly, P=paraphrase (question
shares few/no distinctive terms with the evidence), R=relational/multi-hop.

Run: python3 benchmark-questions.py  -> writes gold.json + prints the table.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path.home() / ".local/share/sessionweaver/poc-storage-decision"

QUESTIONS = [
    # --- keyword-friendly ---
    ("K01", "K", "What is the SHA-256 of the pinned sessionweaver production wheel?",
     ["%6ef71b215e700a3104d9ab9a71666f67880947f718099b04bd02b0631d3d3412%"]),
    ("K02", "K", "Which commit is the Session Weaver production pin built from?",
     ["%production-pins%5dfe0f9b%"]),
    ("K03", "K", "What did grok call itself during the council review calls?",
     ["%Cursor Grok 4.5%"]),
    ("K04", "K", "What is ADR-0011 grok-is-capture-only about?",
     ["%0011-grok-is-capture-only%"]),
    ("K05", "K", "How many tests passed in the full workspace regression suite?",
     ["%4884 passed%"]),
    ("K06", "K", "Which NAS is the Time Machine network destination?",
     ["%DXP4800GT%"]),
    ("K07", "K", "What voice does kokoro text-to-speech use for study speak?",
     ["%am_michael%", "%kokoro%"]),
    ("K08", "K", "Where do the litellm-cost estimate results have to be written before gateway calls?",
     ["%estimate.json%", "%before the first gateway call%"]),
    ("K09", "K", "What does the check-commit-author pre-commit hook enforce?",
     ["%check-commit-author%"]),
    ("K10", "K", "Which test asserts that sync_all defaults to reconcile?",
     ["%test_sync_all_default%"]),
    ("K11", "K", "What tool converts PDFs into Obsidian notes?",
     ["%pdf2obsidian%"]),
    ("K12", "K", "What is the two-Mac gate 2b runbook evidence file called?",
     ["%two-Mac%runbook%"]),
    # --- paraphrase / semantic ---
    ("P01", "P", "Why can a session that changed on both machines never settle when syncing in the mode that only moves newer things?",
     ["%cannot converge%"]),
    ("P02", "P", "Which model burned budget by failing every one of its review attempts?",
     ["%grok%", "%hollow%"]),
    ("P03", "P", "Why do headless one-shot Claude runs never show up in the session database?",
     ["%Stop hook%", "%async%"]),
    ("P04", "P", "Which two exporters keep claiming to add the same rows every time the repair inspection runs?",
     ["%opencode%", "%pi%", "%idempoten%"]),
    ("P05", "P", "How do we make sure rows we deleted during the cleanup can't sneak back in from another machine?",
     ["%resurrect%", "%empty%"]),
    ("P06", "P", "What stops an agent from accidentally trashing work when the repo has uncommitted changes during a wheel build?",
     ["%dirty%", "%refuse%build%"]),
    ("P07", "P", "What's the rule about how many study topics can be active at once for focus reasons?",
     ["%MAX_ACTIVE_TOPICS%"]),
    ("P08", "P", "Which machine holds the old username's files and can only dial out, not be dialled into?",
     ["%taylaand%"]),
    ("P09", "P", "What happened when two computers both edited the same message and then synchronised?",
     ["%sync_content_conflict%"]),
    ("P10", "P", "Why was the second museum-quality copy of the database taken before any cross-machine testing?",
     ["%divergent%", "%second Mac%"]),
    # --- relational / multi-hop ---
    ("R01", "R", "Which sessions discuss both the pinned wheel install and the symlink relinking?",
     ["%uv tool install%", "%symlink%", "%pin%"]),
    ("R02", "R", "Where was the decision made that connects Grok capture-only status to the release harness exclusion test?",
     ["%grok%", "%test_release_harnesses%"]),
    ("R03", "R", "Which discussion links the FTS lag to the unknown-role rows?",
     ["%FTS%", "%26,314%"]),
    ("R04", "R", "What connects the machine_id/seq design to fixing incremental sync convergence?",
     ["%machine_id%", "%seq%", "%tiebreak%"]),
    ("R05", "R", "Which conversations tie the council gate verdict to the conditions the human must complete?",
     ["%validation gate%", "%judge%", "%phase 0%"]),
    ("R06", "R", "What links the secrets baseline extension to the pre-commit staging requirement?",
     ["%.secrets.baseline%", "%unstaged%"]),
]


def main() -> None:
    conn = sqlite3.connect(ROOT / "corpus-derived.db")
    usable = []
    for qid, qtype, question, evidence in QUESTIONS:
        where = " AND ".join("text LIKE ?" for _ in evidence)
        golds = [
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT session_id FROM messages_derived"
                f" WHERE kind='conversation' AND dup=0 AND {where}",
                evidence,
            )
        ]
        status = "OK" if 1 <= len(golds) <= 12 else ("EMPTY" if not golds else "TOO_BROAD")
        print(f"{qid} {qtype} golds={len(golds):>3} {status:<9} {question[:70]}")
        if status == "OK":
            usable.append(
                {"id": qid, "type": qtype, "question": question, "gold": golds}
            )
    (ROOT / "gold.json").write_text(json.dumps(usable, indent=1))
    print(f"\nusable questions: {len(usable)} -> gold.json")


if __name__ == "__main__":
    main()
