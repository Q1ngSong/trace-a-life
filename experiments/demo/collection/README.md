# Demo collection worktree

This worktree implements the smallest host-result ingestion loop for Wave 1.

The code does not perform network, MCP, or browser calls. A host
executes the existing `ToolCall`, returns a `ToolResult`, and
`xray_person.collection.source_ingest.ingest_tool_result` does the following:

1. Uses the generic collection receipt normalizer.
2. Requires an attested completed receipt for `mode=live`.
3. Stores an unattested saved response as `reported-only` for `mode=replay`.
4. Validates source ID, URL/file path, title, timezone-aware access time, and body/snippet.
5. Writes raw response, receipt JSONL, normalized `sources.jsonl`, and one readable Markdown file per source under the case's `research/collection/` directory.

`reported-only` and `evidence-candidate` are collection states. They are not
claim verification and must still pass the existing research verification
stage before appearing as factual claims.

## Example integration

```python
from xray_person.collection import ingest_tool_result

report = ingest_tool_result(
    "cases/person-slug",
    tool_call,
    tool_result,
    mode="live",  # or "replay" for a saved fixture
)
print(report.sources_path)
```

The external dependency that remains unverified is the host executor and its
payload shape. Tests cover representative JSON result envelopes and do not
claim live network access.

For a saved ToolCall/ToolResult fixture, the file boundary is also available:

```bash
python3 skills/trace-a-life/scripts/ingest_collection.py cases/person-slug \
  --call /tmp/tool-call.json --result /tmp/tool-result.json --mode replay
```
