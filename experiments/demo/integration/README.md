# Demo integration worktree

This thin entry point connects the accepted local capabilities for a saved
ToolCall/ToolResult fixture:

```bash
python3 skills/trace-a-life/scripts/run_demo_flow.py cases/person \
  --call /tmp/tool-call.json --result /tmp/tool-result.json --mode replay
```

It writes an isolated output directory containing collection raw data and
receipts, a copy of the case with source candidates registered, a local
factcheck report, a source-bounded analysis proposal, a self-contained HTML
page, and `manifest.json`. The input case is not modified. The command never
performs network I/O; `live` requires an executor-attested result supplied by
the host.
