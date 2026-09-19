"""Create and verify reproducible, non-empty delivery manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


MANIFEST_SCHEMA_VERSION = "xray-delivery-manifest/1"
_REQUIRED_ARTIFACTS = frozenset({"case", "view_model", "html"})
_FILE_ARTIFACTS = frozenset({"case", "view_model", "html"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ManifestDrift:
    """One structured mismatch between a manifest and files on disk."""

    artifact: str
    code: str
    path: str
    expected: str | int | None = None
    actual: str | int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_members(path: Path) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    for member in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if member.is_symlink():
            raise ValueError(f"delivery packages cannot contain symlinks: {member}")
        if member.is_file():
            if member.stat().st_size == 0:
                raise ValueError(f"delivery packages cannot contain empty files: {member}")
            members.append(
                {
                    "path": member.relative_to(path).as_posix(),
                    "sha256": sha256_file(member),
                    "bytes": member.stat().st_size,
                }
            )
    if not members:
        raise ValueError(f"delivery directory cannot be empty: {path}")
    return members


def _directory_digest(members: list[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for member in members:
        digest.update(str(member["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(member["sha256"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(member["bytes"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _is_safe_relative_path(value: str) -> bool:
    if not value or value == "." or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _relative_path(path: Path, base_dir: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"delivery artifacts cannot be symlinks: {path}")
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(base_dir.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"artifact must be below manifest base directory: {path}") from error
    if not _is_safe_relative_path(relative):
        raise ValueError(f"artifact path must be a non-root normalized relative path: {path}")
    return relative


def _artifact_entry(path: Path, base_dir: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"delivery artifacts cannot be symlinks: {path}")
    if not path.exists():
        raise FileNotFoundError(path)
    relative_path = _relative_path(path, base_dir)
    if path.is_file():
        size = path.stat().st_size
        if size == 0:
            raise ValueError(f"delivery artifact cannot be empty: {path}")
        return {
            "path": relative_path,
            "kind": "file",
            "sha256": sha256_file(path),
            "bytes": size,
        }
    if path.is_dir():
        members = _directory_members(path)
        return {
            "path": relative_path,
            "kind": "directory",
            "sha256": _directory_digest(members),
            "member_count": len(members),
            "members": members,
        }
    raise ValueError(f"unsupported artifact type: {path}")


def _common_base(paths: list[Path]) -> Path:
    return Path(os.path.commonpath([str(path.resolve().parent) for path in paths]))


def build_delivery_manifest(
    *,
    case_path: str | Path,
    view_model_path: str | Path,
    html_path: str | Path,
    simulation_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Hash every required non-empty delivery surface.

    The three required artifacts must be distinct regular files. A simulation
    artifact is optional, but when present it must be a non-empty file or a
    directory containing at least one non-empty regular file.
    """

    named_paths: dict[str, Path] = {
        "case": Path(case_path),
        "view_model": Path(view_model_path),
        "html": Path(html_path),
    }
    if simulation_path is not None:
        named_paths["simulation"] = Path(simulation_path)
    root = Path(base_dir) if base_dir is not None else _common_base(list(named_paths.values()))

    resolved: dict[Path, str] = {}
    for name, path in named_paths.items():
        if path.is_symlink():
            raise ValueError(f"{name} artifact cannot be a symlink: {path}")
        identity = path.resolve()
        if identity in resolved:
            raise ValueError(f"artifacts {resolved[identity]} and {name} alias {path}")
        resolved[identity] = name
        if name in _FILE_ARTIFACTS and not path.is_file():
            raise ValueError(f"{name} artifact must be a regular file: {path}")

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "hash_algorithm": "sha256",
        "artifacts": {
            name: _artifact_entry(path, root) for name, path in sorted(named_paths.items())
        },
    }


def write_delivery_manifest(path: str | Path, manifest: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def _entry_shape_drifts(name: str, entry: Mapping[str, Any]) -> list[ManifestDrift]:
    path = entry.get("path")
    kind = entry.get("kind")
    digest = entry.get("sha256")
    drifts: list[ManifestDrift] = []
    if not isinstance(path, str) or not _is_safe_relative_path(path):
        drifts.append(ManifestDrift(name, "manifest_path_invalid", str(path or "")))
    if kind not in {"file", "directory"}:
        drifts.append(ManifestDrift(name, "manifest_kind_invalid", str(path or "")))
    if name in _FILE_ARTIFACTS and kind != "file":
        drifts.append(ManifestDrift(name, "manifest_required_file_invalid", str(path or "")))
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        drifts.append(ManifestDrift(name, "manifest_digest_invalid", str(path or "")))
    if kind == "file":
        size = entry.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            drifts.append(ManifestDrift(name, "manifest_empty_or_invalid_file", str(path or "")))
    elif kind == "directory":
        count = entry.get("member_count")
        members = entry.get("members")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            drifts.append(ManifestDrift(name, "manifest_empty_or_invalid_directory", str(path or "")))
        if not isinstance(members, list) or not members:
            drifts.append(ManifestDrift(name, "manifest_members_invalid", str(path or "")))
        elif isinstance(count, int) and not isinstance(count, bool) and count != len(members):
            drifts.append(
                ManifestDrift(
                    name,
                    "manifest_member_count_mismatch",
                    str(path or ""),
                    count,
                    len(members),
                )
            )
        if isinstance(members, list):
            member_paths: set[str] = set()
            for member in members:
                if not isinstance(member, Mapping):
                    drifts.append(ManifestDrift(name, "manifest_member_invalid", str(path or "")))
                    continue
                member_path = member.get("path")
                member_digest = member.get("sha256")
                member_size = member.get("bytes")
                if not isinstance(member_path, str) or not _is_safe_relative_path(member_path):
                    drifts.append(ManifestDrift(name, "manifest_member_path_invalid", str(member_path or "")))
                elif member_path in member_paths:
                    drifts.append(ManifestDrift(name, "manifest_member_path_duplicate", member_path))
                else:
                    member_paths.add(member_path)
                if not isinstance(member_digest, str) or _SHA256.fullmatch(member_digest) is None:
                    drifts.append(ManifestDrift(name, "manifest_member_digest_invalid", str(member_path or "")))
                if not isinstance(member_size, int) or isinstance(member_size, bool) or member_size <= 0:
                    drifts.append(ManifestDrift(name, "manifest_member_empty_or_invalid", str(member_path or "")))
    return drifts


def verify_delivery_manifest(
    manifest: Mapping[str, Any], base_dir: str | Path
) -> list[ManifestDrift]:
    """Return all structural and disk drifts without raising on bad input."""

    root = Path(base_dir)
    drifts: list[ManifestDrift] = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        drifts.append(
            ManifestDrift(
                "manifest",
                "manifest_schema_mismatch",
                "",
                MANIFEST_SCHEMA_VERSION,
                str(manifest.get("schema_version", "")),
            )
        )
    if manifest.get("hash_algorithm") != "sha256":
        drifts.append(
            ManifestDrift(
                "manifest",
                "manifest_hash_algorithm_invalid",
                "",
                "sha256",
                str(manifest.get("hash_algorithm", "")),
            )
        )
    artifacts = manifest.get("artifacts", {})
    if not isinstance(artifacts, Mapping):
        return drifts + [ManifestDrift("manifest", "manifest_artifacts_invalid", "")]

    for missing in sorted(_REQUIRED_ARTIFACTS - set(artifacts)):
        drifts.append(ManifestDrift(missing, "manifest_required_artifact_missing", ""))

    seen_paths: dict[str, str] = {}
    for name_value, expected in sorted(artifacts.items(), key=lambda item: str(item[0])):
        name = str(name_value)
        if not isinstance(expected, Mapping):
            drifts.append(ManifestDrift(name, "manifest_entry_invalid", ""))
            continue
        drifts.extend(_entry_shape_drifts(name, expected))
        relative_path = expected.get("path")
        if not isinstance(relative_path, str) or not _is_safe_relative_path(relative_path):
            continue
        normalized = PurePosixPath(relative_path).as_posix()
        if normalized in seen_paths:
            drifts.append(
                ManifestDrift(
                    name,
                    "manifest_artifact_path_alias",
                    relative_path,
                    seen_paths[normalized],
                    name,
                )
            )
            continue
        seen_paths[normalized] = name
        path = root / relative_path
        try:
            path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            drifts.append(ManifestDrift(name, "manifest_path_outside_base", relative_path))
            continue
        if path.is_symlink():
            drifts.append(ManifestDrift(name, "manifest_artifact_symlink", relative_path))
            continue
        if not path.exists():
            drifts.append(ManifestDrift(name, "manifest_artifact_missing", relative_path))
            continue
        try:
            actual = _artifact_entry(path, root)
        except (OSError, ValueError) as error:
            code = "manifest_artifact_empty" if "empty" in str(error) else "manifest_artifact_unreadable"
            drifts.append(ManifestDrift(name, code, relative_path, actual=str(error)))
            continue
        if actual.get("kind") != expected.get("kind"):
            drifts.append(
                ManifestDrift(
                    name,
                    "manifest_kind_mismatch",
                    relative_path,
                    str(expected.get("kind", "")),
                    str(actual.get("kind", "")),
                )
            )
            continue
        if actual.get("sha256") != expected.get("sha256"):
            drifts.append(
                ManifestDrift(
                    name,
                    "manifest_digest_mismatch",
                    relative_path,
                    str(expected.get("sha256", "")),
                    str(actual.get("sha256", "")),
                )
            )
        if expected.get("kind") == "directory" and actual.get("members") != expected.get("members"):
            drifts.append(ManifestDrift(name, "manifest_members_mismatch", relative_path))
    return drifts
