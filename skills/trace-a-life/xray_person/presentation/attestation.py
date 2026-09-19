"""Bind rendered HTML to the exact case and view-model inputs.

This is a lightweight derivation attestation, not a signature.  Delivery QA
also checks rendered semantic content, so copying fresh digest tags into a stale
page is not sufficient to pass the gate.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from .view_model import canonical_json_bytes


ATTESTATION_VERSION = "xray-html-attestation/1"
CASE_DIGEST_META = "xray-case-sha256"
VIEW_MODEL_DIGEST_META = "xray-view-model-sha256"
ATTESTATION_VERSION_META = "xray-attestation-version"

_ATTESTATION_META = re.compile(
    r"<meta\s+[^>]*name\s*=\s*([\"'])(?:xray-case-sha256|"
    r"xray-view-model-sha256|xray-attestation-version)\1[^>]*>\s*",
    flags=re.IGNORECASE,
)


def json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def attestation_values(
    case_data: Mapping[str, Any], view_model: Mapping[str, Any]
) -> dict[str, str]:
    return {
        ATTESTATION_VERSION_META: ATTESTATION_VERSION,
        CASE_DIGEST_META: json_sha256(case_data),
        VIEW_MODEL_DIGEST_META: json_sha256(view_model),
    }


def attest_html(
    html: str, case_data: Mapping[str, Any], view_model: Mapping[str, Any]
) -> str:
    """Insert or refresh deterministic derivation metadata in an HTML page."""

    if not isinstance(html, str) or not html.strip():
        raise ValueError("cannot attest empty HTML")
    cleaned = _ATTESTATION_META.sub("", html)
    head_end = re.search(r"</head\s*>", cleaned, flags=re.IGNORECASE)
    if head_end is None:
        raise ValueError("HTML attestation requires a closing </head>")
    values = attestation_values(case_data, view_model)
    tags = "".join(
        f'<meta name="{name}" content="{value}">'
        for name, value in (
            (ATTESTATION_VERSION_META, values[ATTESTATION_VERSION_META]),
            (CASE_DIGEST_META, values[CASE_DIGEST_META]),
            (VIEW_MODEL_DIGEST_META, values[VIEW_MODEL_DIGEST_META]),
        )
    )
    return cleaned[: head_end.start()] + tags + cleaned[head_end.start() :]
