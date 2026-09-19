#!/usr/bin/env python3
"""Publish selected case conclusions; never copy case directories or raw material.

--sync-cases reads the explicit selection from local cases. Normal builds use
only committed public snapshots, so GitHub Actions needs no private research.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "website"
sys.path.insert(0, str(ROOT / "skills/trace-a-life/scripts"))
from render_case import render
from validate_case import validate


def fields(names):
    return {name: str for name in names.split()}


SOURCES = fields("id title url publisher published_at accessed_at type role origin_cluster notes")
SCHEMA = {
    "subject": fields("name anchor lifespan known_for summary"),
    "report": fields("title eyebrow dek thesis as_of post_exit_thesis post_exit_limits"),
    "fact_freeze": fields("status frozen_at notes"),
    "chapters": [fields("id label years title summary")],
    "events": [{**fields("id chapter_id date date_label title summary epistemic_status post_exit_phase"), "source_ids": [str], "post_exit": bool}],
    "decisions": [{**fields("id date title decision outcome interpretation interpretation_status falsifier"),
                   **{key: [str] for key in ("source_ids", "known_at_time", "alternatives", "constraints", "alternative_explanations")}}],
    "contexts": [{**fields("id period title type summary"), "source_ids": [str]}],
    "relationships": {
        "nodes": [fields("id label type description")],
        "edges": [{**fields("id source target label period epistemic_status"), "source_ids": [str]}],
    },
    "wealth": {"summary": str,
               "stages": [{**fields("id period title summary mechanism"), "source_ids": [str]}],
               "transfers": [{**fields("id period title summary"), "source_ids": [str]}]},
    # Keep short, already-researched quotes when a local case provides them.
    # Existing public snapshots without quotes remain source-only and render an
    # explicit disclosure instead of inventing citation text.
    "claims": [{**fields("id text importance status explanation counterevidence"), "evidence": [fields("source_id quote")]}],
    "sources": [SOURCES],
    "unresolved_questions": [fields("id question searched next_query impact")],
    "scenarios": [{**fields("id title summary epistemic_status"), "signals": [str], "limitations": [str]}],
}


def project(value, schema):
    """Field allowlist at every depth; unknown metadata never reaches HTML/JSON."""
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            raise ValueError("expected a mapping")
        return {key: project(value[key], child) for key, child in schema.items()
                if key in value and value[key] is not None}
    if isinstance(schema, list):
        if not isinstance(value, list):
            raise ValueError("expected a list")
        return [project(item, schema[0]) for item in value]
    if type(value) is not schema:
        raise ValueError("unexpected public field type")
    return value


def scan_public(text):
    patterns = [r"(?:/Users/|/Volumes/|/private/|file://|raw/interviews/)",
                r"(?:audio_path|transcript_path|source_document_id|SecretId|SecretKey)",
                r"(?:X-Amz-Signature|q-signature)=", r"AKID[A-Za-z0-9]{20,}"]
    for pattern in patterns:
        if re.search(pattern, text, re.I):
            raise ValueError(f"publication contains local/private metadata ({pattern})")


def public_case(data):
    result = project(data, SCHEMA)
    for source in result.get("sources", []):
        url = urlsplit(source.get("url", ""))
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("source must use a public HTTP(S) URL without credentials")
    # Missing source references would make the public evidence drawer misleading.
    ids = {s["id"] for s in result.get("sources", [])}
    def check_refs(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "source_ids" and not set(value) <= ids:
                    raise ValueError("unknown public source reference")
                if key == "source_id" and value not in ids:
                    raise ValueError("unknown public source reference")
                check_refs(value)
        elif isinstance(node, list):
            for item in node:
                check_refs(item)
    check_refs(result)
    scan_public(json.dumps(result, ensure_ascii=False))
    return result


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selection():
    entries = json.loads((ROOT / "publishing/cases.json").read_text())
    slugs = [entry["slug"] for entry in entries]
    if len(slugs) != len(set(slugs)) or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", s) for s in slugs):
        raise ValueError("case slugs must be unique safe names")
    return entries


def sync_cases():
    # Validate all selected cases before replacing any public snapshot.
    pending = []
    for entry in selection():
        path = ROOT / "cases" / entry["slug"] / "case.json"
        if path.is_symlink():
            raise ValueError("case source must be a regular local file")
        data = json.loads(path.read_text())
        validation = validate(data, strict=False)
        if validation.errors:
            raise ValueError(f"{entry['slug']}: {validation.errors}")
        pending.append((entry, {"schema_version": "public-research/1", "kind": entry["kind"],
                               "data": public_case(data)}))
    for entry, snapshot in pending:
        dump(SITE / "cases" / entry["slug"] / "results.json", snapshot)


PUBLIC_NOTE = """<div class="publication-note" style="padding:18px 5%;background:#231e19;color:#fff;font:14px/1.7 system-ui">
<a href="../../index.html#cases" style="color:#fff">← 穷原竟委 · 返回案例</a>　／　公开研究结果<br>
保留原有研究日期、结论、自述与争议标签；不包含原始音频、完整转写或采集日志。
“事实冻结”表示案件版本已固定，不表示所有主张均已证实。本次发布未重新核查外部来源。
<a href="results.json" style="color:#fff">下载公开结论 JSON</a></div>"""


def source_archive():
    dest = SITE / "downloads/trace-a-life-source.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    files = [ROOT / name for name in ("README.md", "LICENSE", ".env.example")]
    files += [p for p in (ROOT / "skills/trace-a-life").rglob("*") if p.is_file()
              and not {"tests", "__pycache__"}.intersection(p.parts) and p.suffix in {".py", ".md", ".yaml", ".txt"}]
    files += [ROOT / "skills/trace-a-life/scripts/vendor/grounded_citations/LICENSE"]
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            if path.is_symlink():
                raise ValueError("archive inputs cannot be symlinks")
            # Fixed timestamp makes repeated builds deterministic.
            info = zipfile.ZipInfo("trace-a-life/" + path.relative_to(ROOT).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def build():
    cards = []
    for entry in selection():
        folder = SITE / "cases" / entry["slug"]
        snapshot = json.loads((folder / "results.json").read_text())
        if snapshot.get("schema_version") != "public-research/1":
            raise ValueError("unknown public schema")
        data = public_case(snapshot["data"])
        if data != snapshot["data"]:
            raise ValueError("snapshot contains fields outside the publication allowlist")
        page = render(data).replace("<body>", "<body>" + PUBLIC_NOTE, 1)
        page = page.replace("由 trace-a-life 从结构化案件生成", "由穷原竟委从结构化案件生成")
        # Publish the existing visual/interaction design, without asserting full local delivery attestation.
        page = page.replace("</head>", "<style>.reveal{opacity:1;transform:none}html{scroll-behavior:smooth}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}html{scroll-behavior:auto}}</style></head>")
        scan_public(page)
        (folder / "index.html").write_text(page, encoding="utf-8")
        status = "版本已冻结 · 含未决问题" if data["fact_freeze"]["status"] == "frozen" else "研究草稿 · 尚未冻结"
        esc = html.escape
        cards.append(f'''<article class="case-row" data-kind="{esc(entry['kind'])}">
<div class="case-id">0{len(cards)+1}<span>{esc(entry['kind'])}</span></div>
<div><p class="case-status">{status} <span>研究截至 {esc(data['report']['as_of'])}</span></p>
<h3><a href="cases/{entry['slug']}/index.html">{esc(data['report']['title'])} <span aria-hidden="true">↗</span></a></h3>
<p>{esc(data['report']['dek'])}</p></div>
<a class="read-case" href="cases/{entry['slug']}/index.html">阅读研究 <span aria-hidden="true">↗</span></a></article>''')
    template = (ROOT / "publishing/index.template.html").read_text()
    (SITE / "index.html").write_text(template.replace("{{CASES}}", "\n".join(cards)), encoding="utf-8")
    source_archive()
    allowed = ["index.html", "styles.css", "site.js", ".nojekyll", "downloads/trace-a-life-source.zip"]
    allowed += [f"cases/{entry['slug']}/{name}" for entry in selection() for name in ("index.html", "results.json")]
    dump(SITE / "manifest.json", {"schema_version": "public-site/1", "files": {name: sha(SITE/name) for name in sorted(allowed)}})
    check()


def check():
    manifest = json.loads((SITE / "manifest.json").read_text())
    expected = set(manifest["files"]) | {"manifest.json"}
    actual = {p.relative_to(SITE).as_posix() for p in SITE.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError(f"unregistered/missing publication files: {actual ^ expected}")
    for name, digest in manifest["files"].items():
        path = SITE / name
        if not path.resolve().is_relative_to(SITE.resolve()) or path.is_symlink() or sha(path) != digest:
            raise ValueError(f"publication drift: {name}")
        if path.suffix in {".html", ".json", ".js", ".css"}:
            scan_public(path.read_text())
    with zipfile.ZipFile(SITE / "downloads/trace-a-life-source.zip") as archive:
        for name in archive.namelist():
            parts = Path(name).parts
            if {"cases", "raw", ".venv", "__pycache__", ".env"}.intersection(parts):
                raise ValueError("private content in download archive")
    print(f"Public site OK: {len(actual)} files; raw research excluded")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync-cases", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
    else:
        if args.sync_cases:
            sync_cases()
        build()
