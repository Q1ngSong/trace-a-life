# Demo factcheck worktree

This worktree adds a local material check on top of the existing research
verifier. It does not search the web, change a claim's status, or merge two
people with the same Chinese name.

`factcheck_case.py` reads `research/collection/sources.jsonl` and, when needed,
the per-source Markdown files written by Wave 1. For every claim evidence item
it reports `matched`, `not_found`, `missing_source`, or `empty_quote`.

Identity candidates from `case.json.identity_candidates`, plus the subject name
and aliases, are written with an explicit `pending`, `accepted`, or `rejected`
status. A repeated name remains repeated; the tool never infers that two
records are the same person.

```bash
python3 skills/trace-a-life/scripts/factcheck_case.py cases/person-slug
```

The report is written to `research/factcheck/report.json` and the candidate
queue to `research/factcheck/identity-candidates.json`.

Unverified, self-reported, contradicted, and conflict states are copied from
the existing `verification.assess_claim` output and remain visible in the
report. The actual quality of the saved source material and any external
search/executor behavior remain outside this offline check.
