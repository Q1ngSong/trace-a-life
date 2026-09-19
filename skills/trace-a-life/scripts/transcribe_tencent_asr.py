#!/usr/bin/env python3
"""Submit interview audio to Tencent Cloud recording-file ASR.

The script keeps credentials in a local .env file, uses inline data only for
files up to 5 MiB, and otherwise uploads to a private COS object with a
short-lived presigned URL.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


INLINE_LIMIT_BYTES = 5 * 1024 * 1024
TERMINAL_STATUSES = {"success", "failed"}


def env_bool(value: str, default: bool = False) -> bool:
    if not value:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


def env_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


@dataclass(frozen=True)
class TencentAsrConfig:
    secret_id: str
    secret_key: str
    region: str = ""
    endpoint: str = "asr.tencentcloudapi.com"
    engine_model_type: str = "16k_zh"
    channel_num: int = 1
    result_text_format: int = 3
    speaker_diarization: int = 1
    speaker_number: int = 0
    callback_url: str = ""
    poll_interval_seconds: int = 5
    result_timeout_seconds: int = 10_800
    hotword_list: str = ""
    cos_region: str = ""
    cos_bucket: str = ""
    cos_prefix: str = "trace-a-life/asr-input"
    cos_url_expiry_seconds: int = 14_400
    cos_delete_after_completion: bool = True

    @classmethod
    def from_env(cls, values: Mapping[str, str]) -> "TencentAsrConfig":
        secret_id = values.get("TENCENTCLOUD_SECRET_ID", "").strip()
        secret_key = values.get("TENCENTCLOUD_SECRET_KEY", "").strip()
        missing = [
            name
            for name, value in (
                ("TENCENTCLOUD_SECRET_ID", secret_id),
                ("TENCENTCLOUD_SECRET_KEY", secret_key),
            )
            if not value
        ]
        if missing:
            raise ValueError("missing required environment variables: " + ", ".join(missing))

        config = cls(
            secret_id=secret_id,
            secret_key=secret_key,
            region=values.get("TENCENTCLOUD_REGION", "").strip(),
            endpoint=values.get("TENCENT_ASR_ENDPOINT", "asr.tencentcloudapi.com").strip(),
            engine_model_type=values.get(
                "TENCENT_ASR_ENGINE_MODEL_TYPE", "16k_zh"
            ).strip(),
            channel_num=env_int(values, "TENCENT_ASR_CHANNEL_NUM", 1),
            result_text_format=env_int(values, "TENCENT_ASR_RES_TEXT_FORMAT", 3),
            speaker_diarization=env_int(values, "TENCENT_ASR_SPEAKER_DIARIZATION", 1),
            speaker_number=env_int(values, "TENCENT_ASR_SPEAKER_NUMBER", 0),
            callback_url=values.get("TENCENT_ASR_CALLBACK_URL", "").strip(),
            poll_interval_seconds=env_int(values, "TENCENT_ASR_POLL_INTERVAL_SECONDS", 5),
            result_timeout_seconds=env_int(values, "TENCENT_ASR_RESULT_TIMEOUT_SECONDS", 10_800),
            hotword_list=values.get("TENCENT_ASR_HOTWORD_LIST", "").strip(),
            cos_region=values.get("TENCENT_COS_REGION", "").strip(),
            cos_bucket=values.get("TENCENT_COS_BUCKET", "").strip(),
            cos_prefix=values.get("TENCENT_COS_PREFIX", "trace-a-life/asr-input").strip(" /"),
            cos_url_expiry_seconds=env_int(
                values, "TENCENT_COS_PRESIGNED_URL_EXPIRES_SECONDS", 14_400
            ),
            cos_delete_after_completion=env_bool(
                values.get("TENCENT_COS_DELETE_AFTER_COMPLETION", "true"), True
            ),
        )
        if config.channel_num != 1:
            raise ValueError("16 kHz interview audio requires TENCENT_ASR_CHANNEL_NUM=1")
        if config.result_text_format not in {0, 1, 2, 3}:
            raise ValueError("free-path TENCENT_ASR_RES_TEXT_FORMAT must be 0, 1, 2, or 3")
        if config.speaker_diarization not in {0, 1}:
            raise ValueError("free-path TENCENT_ASR_SPEAKER_DIARIZATION must be 0 or 1")
        if not 0 <= config.speaker_number <= 10:
            raise ValueError("TENCENT_ASR_SPEAKER_NUMBER must be between 0 and 10")
        if config.poll_interval_seconds < 1:
            raise ValueError("TENCENT_ASR_POLL_INTERVAL_SECONDS must be at least 1")
        if config.result_timeout_seconds < config.poll_interval_seconds:
            raise ValueError("ASR result timeout must be longer than the poll interval")
        return config

    def public_summary(self) -> dict[str, object]:
        return {
            "credentials": "configured",
            "region": self.region or None,
            "endpoint": self.endpoint,
            "engine_model_type": self.engine_model_type,
            "channel_num": self.channel_num,
            "result_text_format": self.result_text_format,
            "speaker_diarization": self.speaker_diarization,
            "speaker_number": self.speaker_number,
            "callback_configured": bool(self.callback_url),
            "cos_region": self.cos_region or None,
            "cos_bucket_configured": bool(self.cos_bucket),
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(env_file: Path) -> TencentAsrConfig:
    try:
        from dotenv import dotenv_values
    except ImportError as error:
        raise RuntimeError(
            "python-dotenv is missing; install requirements-asr.txt in the project .venv"
        ) from error
    values = {key: value or "" for key, value in dotenv_values(env_file).items()}
    return TencentAsrConfig.from_env(values)


def base_request(config: TencentAsrConfig) -> dict[str, Any]:
    params: dict[str, Any] = {
        "EngineModelType": config.engine_model_type,
        "ChannelNum": config.channel_num,
        "ResTextFormat": config.result_text_format,
        "SpeakerDiarization": config.speaker_diarization,
        "SpeakerNumber": config.speaker_number,
    }
    if config.callback_url:
        params["CallbackUrl"] = config.callback_url
    if config.hotword_list:
        params["HotwordList"] = config.hotword_list
    return params


def inline_request(config: TencentAsrConfig, audio_path: Path) -> dict[str, Any]:
    size = audio_path.stat().st_size
    if size > INLINE_LIMIT_BYTES:
        raise ValueError("local audio exceeds Tencent's 5 MiB inline-data limit")
    params = base_request(config)
    params.update(
        {
            "SourceType": 1,
            "Data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
            "DataLen": size,
        }
    )
    return params


def url_request(config: TencentAsrConfig, audio_url: str) -> dict[str, Any]:
    parsed = urlparse(audio_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("audio URL must be an absolute HTTPS URL")
    params = base_request(config)
    params.update({"SourceType": 0, "Url": audio_url})
    return params


def sanitized_request(params: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(params)
    if "Data" in result:
        encoded = str(result.pop("Data"))
        result["Data"] = f"<redacted base64: {len(encoded)} chars>"
    if "Url" in result:
        parsed = urlparse(str(result["Url"]))
        # A query string usually marks a private presigned URL. Redact its host
        # as well as the signature so receipts do not disclose bucket names.
        host = "<redacted-presigned-host>" if parsed.query else parsed.netloc
        result["Url"] = f"{parsed.scheme}://{host}{parsed.path}"
    return result


def create_asr_client(config: TencentAsrConfig) -> Any:
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.asr.v20190614 import asr_client
    except ImportError as error:
        raise RuntimeError(
            "Tencent Cloud SDK is missing; install requirements-asr.txt in the project .venv"
        ) from error
    cred = credential.Credential(config.secret_id, config.secret_key)
    http_profile = HttpProfile(endpoint=config.endpoint)
    client_profile = ClientProfile(httpProfile=http_profile)
    return asr_client.AsrClient(cred, config.region, client_profile)


def submit_task(client: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    from tencentcloud.asr.v20190614 import models
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
        TencentCloudSDKException,
    )

    request = models.CreateRecTaskRequest()
    request.from_json_string(json.dumps(dict(params), ensure_ascii=False))
    try:
        return json.loads(client.CreateRecTask(request).to_json_string())
    except TencentCloudSDKException as error:
        raise RuntimeError(
            f"Tencent Cloud API {error.code}: {error.message}"
        ) from error


def describe_task(client: Any, task_id: int) -> dict[str, Any]:
    from tencentcloud.asr.v20190614 import models
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
        TencentCloudSDKException,
    )

    request = models.DescribeTaskStatusRequest()
    request.from_json_string(json.dumps({"TaskId": task_id}))
    try:
        return json.loads(client.DescribeTaskStatus(request).to_json_string())
    except TencentCloudSDKException as error:
        raise RuntimeError(
            f"Tencent Cloud API {error.code}: {error.message}"
        ) from error


def wait_for_task(client: Any, task_id: int, config: TencentAsrConfig) -> dict[str, Any]:
    deadline = time.monotonic() + config.result_timeout_seconds
    while True:
        response = describe_task(client, task_id)
        status = str(response.get("Data", {}).get("StatusStr", "")).lower()
        if status in TERMINAL_STATUSES:
            return response
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Tencent ASR task {task_id} did not finish before timeout")
        time.sleep(config.poll_interval_seconds)


def cos_client(config: TencentAsrConfig) -> Any:
    if not config.cos_region or not config.cos_bucket:
        raise ValueError(
            "audio exceeds 5 MiB; set TENCENT_COS_REGION and TENCENT_COS_BUCKET in .env"
        )
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError as error:
        raise RuntimeError(
            "COS SDK is missing; install requirements-asr.txt in the project .venv"
        ) from error
    cos_config = CosConfig(
        Region=config.cos_region,
        SecretId=config.secret_id,
        SecretKey=config.secret_key,
        Scheme="https",
    )
    return CosS3Client(cos_config)


def upload_private_audio(config: TencentAsrConfig, audio_path: Path) -> tuple[Any, str, str]:
    client = cos_client(config)
    digest = sha256_file(audio_path)
    prefix = config.cos_prefix.strip("/")
    key = f"{prefix}/{digest[:16]}-{audio_path.name}" if prefix else f"{digest[:16]}-{audio_path.name}"
    client.upload_file(
        Bucket=config.cos_bucket,
        LocalFilePath=str(audio_path),
        Key=key,
        EnableMD5=True,
    )
    url = client.get_presigned_url(
        Method="GET",
        Bucket=config.cos_bucket,
        Key=key,
        Expired=config.cos_url_expiry_seconds,
    )
    return client, key, url


def normalize_segments(result_detail: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for index, item in enumerate(result_detail or (), start=1):
        words = [
            {
                "text": word.get("Word") or "",
                "start_ms": word.get("OffsetStartMs"),
                "end_ms": word.get("OffsetEndMs"),
            }
            for word in item.get("Words") or ()
        ]
        segments.append(
            {
                "segment_id": f"seg-{index:06d}",
                "start_ms": item.get("StartMs"),
                "end_ms": item.get("EndMs"),
                "speaker_id": f"speaker-{int(item.get('SpeakerId') or 0):02d}",
                "speaker_name": None,
                "text": item.get("FinalSentence") or "",
                "confidence": None,
                "provider": "tencent-cloud-asr",
                "words": words,
            }
        )
    return segments


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[3]
    argument_parser = argparse.ArgumentParser(
        description="Transcribe an interview with Tencent Cloud ASR and speaker diarization."
    )
    argument_parser.add_argument("input", nargs="?", help="local audio path or HTTPS audio URL")
    argument_parser.add_argument("--env-file", type=Path, default=project_root / ".env")
    argument_parser.add_argument("--output", "-o", type=Path)
    argument_parser.add_argument("--check-config", action="store_true")
    argument_parser.add_argument("--dry-run", action="store_true")
    return argument_parser


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_config(args.env_file)
        if args.check_config:
            print(json.dumps(config.public_summary(), ensure_ascii=False, indent=2))
            return 0
        if not args.input:
            raise ValueError("input is required unless --check-config is used")

        local_path: Path | None = None
        cos = None
        cos_key = ""
        source_sha256: str | None = None
        if args.input.startswith("https://"):
            params = url_request(config, args.input)
            default_output = Path("tencent-asr-result.json")
        else:
            local_path = Path(args.input).expanduser().resolve()
            if not local_path.is_file():
                raise ValueError(f"input audio does not exist: {local_path}")
            source_sha256 = sha256_file(local_path)
            default_output = local_path.with_suffix(".tencent-asr.json")
            if local_path.stat().st_size <= INLINE_LIMIT_BYTES:
                params = inline_request(config, local_path)
            else:
                if args.dry_run:
                    if not config.cos_bucket:
                        raise ValueError(
                            "audio exceeds 5 MiB; set TENCENT_COS_BUCKET before using COS"
                        )
                    params = url_request(config, "https://private-cos.example.invalid/presigned")
                else:
                    cos, cos_key, presigned_url = upload_private_audio(config, local_path)
                    params = url_request(config, presigned_url)

        if args.dry_run:
            print(
                json.dumps(
                    {"executed": False, "config": config.public_summary(), "request": sanitized_request(params)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        client = create_asr_client(config)
        submitted = submit_task(client, params)
        task_id = int(submitted["Data"]["TaskId"])
        completed = wait_for_task(client, task_id, config)

        # Tencent fetches the presigned URL asynchronously. Keep the COS object
        # when polling times out or is interrupted; only a terminal ASR response
        # proves that the backend no longer needs the input object.
        cleanup_error = None
        cos_object_deleted = False
        if cos is not None and config.cos_delete_after_completion and cos_key:
            try:
                cos.delete_object(Bucket=config.cos_bucket, Key=cos_key)
                cos_object_deleted = True
            except Exception as error:  # COS SDK exposes several transport exceptions.
                cleanup_error = str(error)

        data = completed.get("Data", {})
        receipt = {
            "schema_version": "trace-a-life/tencent-asr/1",
            "provider": "tencent-cloud-asr",
            "task_id": task_id,
            "request_id": completed.get("RequestId") or submitted.get("RequestId"),
            "status": data.get("StatusStr"),
            "error": data.get("ErrorMsg") or None,
            "audio_duration_seconds": data.get("AudioDuration"),
            "source": local_path.name if local_path else "remote-url",
            "source_sha256": source_sha256,
            "request": sanitized_request(params),
            "transcript": data.get("Result") or "",
            "segments": normalize_segments(data.get("ResultDetail")),
            "cleanup": {
                "cos_object_deleted": cos_object_deleted,
                "error": cleanup_error,
            },
            "raw_response": completed,
        }
        output = args.output or default_output
        atomic_write_json(output, receipt)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "task_id": task_id,
                    "status": receipt["status"],
                    "segments": len(receipt["segments"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if receipt["status"] == "success" else 3
    except (KeyError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
