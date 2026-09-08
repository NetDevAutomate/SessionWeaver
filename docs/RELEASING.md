# Releasing Session Weaver

Session Weaver releases are evidence-gated. For clarity, source-tree tests do not prove installed artifacts, and a tag must never be created before the exact commit intended for release has green
default-branch CI. The standalone installer does not provision user-managed `session-export`
freshness, MCP registration, or MCP liveness; doctor reports registration as a report-only
diagnostic.

## Protect `main` after merge

The coordinator, not an A7 implementer, applies protection. This exact command requires the
stable aggregate `gates`, `package`, and `pre-commit` check names while preserving pull-request
review and administrator enforcement:

```bash
gh api --method PUT \
  repos/NetDevAutomate/SessionWeaver/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -F required_status_checks[strict]=true \
  -f 'required_status_checks[contexts][]=gates' \
  -f 'required_status_checks[contexts][]=package' \
  -f 'required_status_checks[contexts][]=pre-commit' \
  -F enforce_admins=true \
  -F required_pull_request_reviews[dismiss_stale_reviews]=true \
  -F required_pull_request_reviews[required_approving_review_count]=1 \
  -F restrictions=null
```

Verify the resulting rule with:

```bash
gh api repos/NetDevAutomate/SessionWeaver/branches/main/protection
```

## Release sequence

1. Complete the mandatory independent whole-branch review. Close and re-review every Critical or
   Important finding.
2. Merge to `main`; do not tag yet.
3. Record the exact merge SHA, then wait for the `gates`, `package`, `pre-commit`, and
   `fixture-e2e` jobs to finish successfully at that SHA. Retain the CI run ID and the wheel/sdist
   artifact receipts.

   ```bash
   git fetch origin main
   release_sha=$(git rev-parse origin/main)
   gh run list --repo NetDevAutomate/SessionWeaver --commit "$release_sha"
   ```

4. Run the release guard against the completed run's jobs and verify version, dated CHANGELOG
   heading, compare links, and the prospective tag target. The tag-SHA job in CI omits
   `--tag-sha`, resolves the real tag itself, and repeats the proof after tag creation.

   ```bash
   release_run=<green-run-id>
   gh api --paginate \
     "repos/NetDevAutomate/SessionWeaver/actions/runs/${release_run}/jobs" \
     > /tmp/sessionweaver-release-jobs.json
   uv run python -m session_weaver.release_guard \
     --tag vX.Y.Z \
     --tag-sha "$release_sha" \
     --green-sha "$release_sha" \
     --jobs-json /tmp/sessionweaver-release-jobs.json
   ```

5. Create an annotated tag **only after** exact-SHA green CI:

   ```bash
   git tag -a vX.Y.Z "$release_sha" -m "Session Weaver X.Y.Z"
   git push origin vX.Y.Z
   ```

6. Copy the `## [X.Y.Z]` section from `CHANGELOG.md` into `/tmp/session-weaver-X.Y.Z.md`, then
   create the GitHub release from that CHANGELOG text:

   ```bash
   gh release create vX.Y.Z \
     --repo NetDevAutomate/SessionWeaver \
     --verify-tag \
     --title "Session Weaver X.Y.Z" \
     --notes-file /tmp/session-weaver-X.Y.Z.md
   ```

7. Complete `docs/data/release-evidence-0.2.0.json` with artifact hashes, both clean-install
   environments, dependency and Session Weaver SHAs, CI run IDs, fixture/live e2e receipts,
   independent-review disposition, protection state, and release-guard result.

## Installation qualification

An unqualified pre-merge Git installation follows the repository default branch and cannot prove
an unmerged change. Review with a reviewed local checkout or explicitly pinned revision. A
post-merge/default-branch install is qualified only after default-branch CI is green; released
operators should pin the immutable version tag. Executable-name collisions remain possible, and
`uv tool install --force` transfers executable-link ownership from the existing uv tool.

Legacy-unbound records remain recall-visible historical signal, not accepted or exact-cited
assertions. Ontology remains outside normal/global delta-sync allow lists, while the whole-file
seed exception remains until Phase B sanitization. Doctor is diagnostic, not package/e2e
certification or MCP liveness proof. StudyLoop retrofit, MCP registration, seed sanitization,
cross-machine concept replication or convergence, propagated forgetting, embeddings,
ontology-backed recall, and target-band quality remain Phase B or later exclusions.

## Rollback

1. Stop installation guidance and mark the GitHub release as affected.
2. If the release object or tag was created from the wrong SHA, remove the release first, then the
   remote and local tag. These actions are destructive and require explicit coordinator approval:

   ```bash
   gh release delete vX.Y.Z --repo NetDevAutomate/SessionWeaver --yes
   git push origin :refs/tags/vX.Y.Z
   git tag -d vX.Y.Z
   ```

3. Revert the bad merge with a new commit; do not rewrite shared history. Push the named target,
   wait for exact-SHA green CI, prepare a patch version and CHANGELOG entry, rerun independent
   wheel/sdist provenance and representative fixture/live evidence, then follow the normal
   sequence with a new tag.
4. Preserve the failed release evidence and document why rollback was required; never reuse or
   move the deleted version tag.
