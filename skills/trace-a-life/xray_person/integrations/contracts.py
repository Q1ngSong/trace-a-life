"""Immutable handoff objects at the external-executor trust boundary.

``ToolCall`` is only a request. A successful ``ToolResult`` remains
``reported-only`` unless it carries an executor attestation whose request and
raw-response digests match the call. The Leader must additionally allow-list
the executor identity; this module cannot establish host trust by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping


TOOL_CALL_SCHEMA = "x-ray-tool-call/1"
TOOL_RESULT_SCHEMA = "x-ray-tool-result/1"
EXECUTOR_ATTESTATION_SCHEMA = "xray-executor-attestation/1"
COLLECTION_RECEIPT_SCHEMA = "xray-collection-receipt/1"


class IntegrationContractError(ValueError):
    """Raised when an integration handoff violates the local contract."""


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntegrationContractError(f"{field} must be a non-empty string")
    return value.strip()


def _require_sha256(value: object, field: str) -> str:
    digest = _require_text(value, field).lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise IntegrationContractError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _require_aware_iso8601(value: object, field: str) -> str:
    text = _require_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrationContractError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IntegrationContractError(f"{field} must include a timezone")
    return text


def _freeze_json(value: Any, path: str = "payload") -> Any:
    """Copy JSON-compatible data into recursively immutable containers."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise IntegrationContractError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise IntegrationContractError(f"{path} contains a non-string key")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[]") for item in value)
    raise IntegrationContractError(
        f"{path} contains unsupported type {type(value).__name__}; use JSON data"
    )


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Return stable UTF-8 JSON used for hashes and audit logs."""

    return json.dumps(
        _thaw_json(_freeze_json(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolCall:
    """A requested external tool call; creation is not proof of execution."""

    call_id: str
    provider: str
    action: str
    tool_name: str
    arguments: Mapping[str, Any]
    contract_version: str = TOOL_CALL_SCHEMA

    def __post_init__(self) -> None:
        if self.contract_version != TOOL_CALL_SCHEMA:
            raise IntegrationContractError(
                f"unsupported ToolCall contract_version: {self.contract_version}"
            )
        object.__setattr__(self, "call_id", _require_text(self.call_id, "call_id"))
        object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
        object.__setattr__(self, "action", _require_text(self.action, "action"))
        object.__setattr__(self, "tool_name", _require_text(self.tool_name, "tool_name"))
        if not isinstance(self.arguments, Mapping):
            raise IntegrationContractError("arguments must be a mapping")
        object.__setattr__(self, "arguments", _freeze_json(self.arguments, "arguments"))

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        action: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> "ToolCall":
        identity = {
            "provider": provider,
            "action": action,
            "tool_name": tool_name,
            "arguments": arguments,
        }
        call_id = f"{provider}:{payload_sha256(identity)[:20]}"
        return cls(call_id, provider, action, tool_name, arguments)

    @property
    def request_sha256(self) -> str:
        return payload_sha256(self.execution_request())

    def execution_request(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "action": self.action,
            "tool_name": self.tool_name,
            "arguments": _thaw_json(self.arguments),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "call_id": self.call_id,
            **self.execution_request(),
            "request_sha256": self.request_sha256,
            "execution_status": "requested",
        }


@dataclass(frozen=True)
class ExecutorAttestation:
    """Digest-addressed statement produced at the real executor boundary."""

    executor: str
    attempt_id: str
    transport: str
    executed_at: str
    request_sha256: str
    response_sha256: str
    status_code: int
    server_version: str
    contract_version: str = EXECUTOR_ATTESTATION_SCHEMA

    def __post_init__(self) -> None:
        if self.contract_version != EXECUTOR_ATTESTATION_SCHEMA:
            raise IntegrationContractError(
                f"unsupported attestation contract_version: {self.contract_version}"
            )
        for field in ("executor", "attempt_id", "transport", "server_version"):
            object.__setattr__(self, field, _require_text(getattr(self, field), field))
        object.__setattr__(
            self, "executed_at", _require_aware_iso8601(self.executed_at, "executed_at")
        )
        object.__setattr__(
            self, "request_sha256", _require_sha256(self.request_sha256, "request_sha256")
        )
        object.__setattr__(
            self,
            "response_sha256",
            _require_sha256(self.response_sha256, "response_sha256"),
        )
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise IntegrationContractError("status_code must be an HTTP-like integer 100..599")

    @classmethod
    def create(
        cls,
        call: ToolCall,
        raw_response: Any,
        *,
        executor: str,
        attempt_id: str,
        transport: str,
        executed_at: str,
        status_code: int,
        server_version: str,
    ) -> "ExecutorAttestation":
        return cls(
            executor=executor,
            attempt_id=attempt_id,
            transport=transport,
            executed_at=executed_at,
            request_sha256=call.request_sha256,
            response_sha256=payload_sha256(raw_response),
            status_code=status_code,
            server_version=server_version,
        )

    @property
    def attestation_id(self) -> str:
        return f"attestation:{payload_sha256(self.to_dict())[:20]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "executor": self.executor,
            "attempt_id": self.attempt_id,
            "transport": self.transport,
            "executed_at": self.executed_at,
            "request_sha256": self.request_sha256,
            "response_sha256": self.response_sha256,
            "status_code": self.status_code,
            "server_version": self.server_version,
        }


@dataclass(frozen=True)
class ToolResult:
    """Executor response; unattested instances are always reported-only."""

    call_id: str
    provider: str
    tool_name: str
    ok: bool
    payload: Any = None
    error: str | None = None
    attestation: ExecutorAttestation | None = None
    contract_version: str = TOOL_RESULT_SCHEMA

    def __post_init__(self) -> None:
        if self.contract_version != TOOL_RESULT_SCHEMA:
            raise IntegrationContractError(
                f"unsupported ToolResult contract_version: {self.contract_version}"
            )
        object.__setattr__(self, "call_id", _require_text(self.call_id, "call_id"))
        object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
        object.__setattr__(self, "tool_name", _require_text(self.tool_name, "tool_name"))
        if not isinstance(self.ok, bool):
            raise IntegrationContractError("ok must be boolean")
        if self.ok and self.error:
            raise IntegrationContractError("a successful result cannot contain error")
        if not self.ok and (not isinstance(self.error, str) or not self.error.strip()):
            raise IntegrationContractError("a failed result requires a non-empty error")
        object.__setattr__(self, "payload", _freeze_json(self.payload, "payload"))
        if self.error is not None:
            object.__setattr__(self, "error", self.error.strip())
        if self.attestation is not None:
            if not isinstance(self.attestation, ExecutorAttestation):
                raise IntegrationContractError("attestation must be ExecutorAttestation")
            if self.attestation.response_sha256 != payload_sha256(self.payload):
                raise IntegrationContractError("attestation response digest does not match payload")

    @classmethod
    def success(cls, call: ToolCall, payload: Any) -> "ToolResult":
        """Record a local report; it cannot produce completed/accepted receipts."""

        return cls(call.call_id, call.provider, call.tool_name, True, payload)

    @classmethod
    def attested_success(
        cls, call: ToolCall, payload: Any, attestation: ExecutorAttestation
    ) -> "ToolResult":
        result = cls(
            call.call_id,
            call.provider,
            call.tool_name,
            True,
            payload,
            attestation=attestation,
        )
        assert_result_matches(call, result)
        return result

    @classmethod
    def failure(
        cls,
        call: ToolCall,
        error: str,
        payload: Any = None,
        *,
        attestation: ExecutorAttestation | None = None,
    ) -> "ToolResult":
        result = cls(
            call.call_id,
            call.provider,
            call.tool_name,
            False,
            payload,
            error,
            attestation,
        )
        if attestation is not None:
            assert_result_matches(call, result)
        return result

    @property
    def execution_status(self) -> str:
        return "attested" if self.attestation is not None else "reported-only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "call_id": self.call_id,
            "provider": self.provider,
            "tool_name": self.tool_name,
            "ok": self.ok,
            "payload": _thaw_json(self.payload),
            "error": self.error,
            "execution_status": self.execution_status,
            "attestation": self.attestation.to_dict() if self.attestation else None,
        }


@dataclass(frozen=True)
class Receipt:
    """Collection audit receipt; it is not itself historical evidence."""

    receipt_id: str
    call_id: str
    provider: str
    action: str
    tool_name: str
    status: str
    epistemic_status: str
    payload_sha256: str
    resource_ids: tuple[str, ...] = ()
    run_id: str | None = None
    workspace: str | None = None
    notes: tuple[str, ...] = ()
    attestation: ExecutorAttestation | None = None
    contract_version: str = COLLECTION_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        if self.contract_version != COLLECTION_RECEIPT_SCHEMA:
            raise IntegrationContractError(
                f"unsupported Receipt contract_version: {self.contract_version}"
            )
        for field in (
            "receipt_id",
            "call_id",
            "provider",
            "action",
            "tool_name",
            "status",
            "epistemic_status",
            "payload_sha256",
        ):
            object.__setattr__(self, field, _require_text(getattr(self, field), field))
        object.__setattr__(
            self, "payload_sha256", _require_sha256(self.payload_sha256, "payload_sha256")
        )
        if self.status not in {"accepted", "completed", "failed", "reported-only"}:
            raise IntegrationContractError(f"unsupported receipt status: {self.status}")
        if self.status in {"accepted", "completed"} and self.attestation is None:
            raise IntegrationContractError(
                f"receipt status {self.status} requires executor attestation"
            )
        if self.attestation is not None:
            if not isinstance(self.attestation, ExecutorAttestation):
                raise IntegrationContractError("attestation must be ExecutorAttestation")
            if self.attestation.response_sha256 != self.payload_sha256:
                raise IntegrationContractError("receipt payload digest does not match attestation")
        if not isinstance(self.resource_ids, (list, tuple)):
            raise IntegrationContractError("resource_ids must be a list or tuple")
        if not isinstance(self.notes, (list, tuple)):
            raise IntegrationContractError("notes must be a list or tuple")
        object.__setattr__(self, "resource_ids", tuple(str(item) for item in self.resource_ids))
        object.__setattr__(self, "notes", tuple(str(item) for item in self.notes))

    @classmethod
    def create(
        cls,
        *,
        call: ToolCall,
        status: str,
        epistemic_status: str,
        payload_digest: str,
        resource_ids: tuple[str, ...] = (),
        run_id: str | None = None,
        workspace: str | None = None,
        notes: tuple[str, ...] = (),
        attestation: ExecutorAttestation | None = None,
    ) -> "Receipt":
        identity = {
            "call_id": call.call_id,
            "status": status,
            "payload_sha256": payload_digest,
            "resource_ids": resource_ids,
            "run_id": run_id,
            "workspace": workspace,
            "attestation_id": attestation.attestation_id if attestation else None,
        }
        receipt_id = f"receipt:{payload_sha256(identity)[:20]}"
        return cls(
            receipt_id=receipt_id,
            call_id=call.call_id,
            provider=call.provider,
            action=call.action,
            tool_name=call.tool_name,
            status=status,
            epistemic_status=epistemic_status,
            payload_sha256=payload_digest,
            resource_ids=resource_ids,
            run_id=run_id,
            workspace=workspace,
            notes=notes,
            attestation=attestation,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "receipt_id": self.receipt_id,
            "call_id": self.call_id,
            "provider": self.provider,
            "action": self.action,
            "tool_name": self.tool_name,
            "status": self.status,
            "epistemic_status": self.epistemic_status,
            "payload_sha256": self.payload_sha256,
            "resource_ids": list(self.resource_ids),
            "run_id": self.run_id,
            "workspace": self.workspace,
            "notes": list(self.notes),
            "attestation": self.attestation.to_dict() if self.attestation else None,
        }


def assert_result_matches(call: ToolCall, result: ToolResult) -> None:
    """Reject an unrelated response or a digest-mismatched attestation."""

    if (
        call.call_id != result.call_id
        or call.provider != result.provider
        or call.tool_name != result.tool_name
    ):
        raise IntegrationContractError("tool result does not match the requested call")
    if result.attestation is not None:
        if result.attestation.request_sha256 != call.request_sha256:
            raise IntegrationContractError("attestation request digest does not match call")
        if result.attestation.response_sha256 != payload_sha256(result.payload):
            raise IntegrationContractError("attestation response digest does not match result")


__all__ = [
    "EXECUTOR_ATTESTATION_SCHEMA",
    "ExecutorAttestation",
    "IntegrationContractError",
    "Receipt",
    "COLLECTION_RECEIPT_SCHEMA",
    "TOOL_CALL_SCHEMA",
    "TOOL_RESULT_SCHEMA",
    "ToolCall",
    "ToolResult",
    "assert_result_matches",
    "canonical_json",
    "payload_sha256",
]
