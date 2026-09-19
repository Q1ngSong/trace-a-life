from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from transcribe_tencent_asr import (  # noqa: E402
    TencentAsrConfig,
    inline_request,
    normalize_segments,
    sanitized_request,
    url_request,
)


class TencentAsrTests(unittest.TestCase):
    def config(self) -> TencentAsrConfig:
        return TencentAsrConfig.from_env(
            {
                "TENCENTCLOUD_SECRET_ID": "secret-id-not-real",
                "TENCENTCLOUD_SECRET_KEY": "secret-key-not-real",
                "TENCENT_ASR_ENGINE_MODEL_TYPE": "16k_zh",
                "TENCENT_ASR_RES_TEXT_FORMAT": "3",
                "TENCENT_ASR_SPEAKER_DIARIZATION": "1",
                "TENCENT_ASR_SPEAKER_NUMBER": "0",
            }
        )

    def test_public_summary_never_exposes_credentials(self) -> None:
        summary = self.config().public_summary()
        rendered = str(summary)
        self.assertNotIn("secret-id-not-real", rendered)
        self.assertNotIn("secret-key-not-real", rendered)
        self.assertEqual("configured", summary["credentials"])

    def test_inline_request_uses_free_diarized_timestamp_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "sample.wav"
            audio.write_bytes(b"RIFF-test")
            request = inline_request(self.config(), audio)
        self.assertEqual(1, request["SourceType"])
        self.assertEqual(1, request["SpeakerDiarization"])
        self.assertEqual(0, request["SpeakerNumber"])
        self.assertEqual(3, request["ResTextFormat"])
        self.assertEqual(len(b"RIFF-test"), request["DataLen"])
        self.assertNotIn("UklG", str(sanitized_request(request)))

    def test_url_must_be_https_and_signature_is_redacted(self) -> None:
        with self.assertRaises(ValueError):
            url_request(self.config(), "http://example.com/audio.wav")
        request = url_request(
            self.config(),
            "https://bucket.cos.example/audio.wav?sign=private-signature",
        )
        self.assertEqual(
            "https://<redacted-presigned-host>/audio.wav",
            sanitized_request(request)["Url"],
        )

    def test_normalize_segments_preserves_speakers_and_timecodes(self) -> None:
        segments = normalize_segments(
            [
                {
                    "FinalSentence": "主持人提问。",
                    "StartMs": 20,
                    "EndMs": 980,
                    "SpeakerId": 0,
                    "Words": [],
                },
                {
                    "FinalSentence": "方言回答。",
                    "StartMs": 1000,
                    "EndMs": 2380,
                    "SpeakerId": 1,
                    "Words": [{"Word": "方言", "OffsetStartMs": 1000, "OffsetEndMs": 1300}],
                },
            ]
        )
        self.assertEqual("speaker-00", segments[0]["speaker_id"])
        self.assertEqual("speaker-01", segments[1]["speaker_id"])
        self.assertEqual(1000, segments[1]["start_ms"])
        self.assertIsNone(segments[1]["speaker_name"])

    def test_paid_result_modes_are_rejected_by_free_path(self) -> None:
        values = {
            "TENCENTCLOUD_SECRET_ID": "x",
            "TENCENTCLOUD_SECRET_KEY": "y",
            "TENCENT_ASR_RES_TEXT_FORMAT": "4",
        }
        with self.assertRaises(ValueError):
            TencentAsrConfig.from_env(values)


if __name__ == "__main__":
    unittest.main()
