from __future__ import annotations

import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from extract_audio import build_ffmpeg_command, extract_audio, main, sha256_file  # noqa: E402


class ExtractAudioTests(unittest.TestCase):
    def test_command_uses_first_audio_track_and_asr_defaults(self) -> None:
        command = build_ffmpeg_command(
            "/usr/local/bin/ffmpeg",
            Path("interview video.mp4"),
            Path("interview.asr.wav"),
        )
        self.assertIn("0:a:0", command)
        self.assertIn("-vn", command)
        self.assertEqual("1", command[command.index("-ac") + 1])
        self.assertEqual("16000", command[command.index("-ar") + 1])
        self.assertEqual("pcm_s16le", command[command.index("-c:a") + 1])
        self.assertEqual("interview video.mp4", command[command.index("-i") + 1])

    def test_fake_ffmpeg_produces_atomic_output_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "interview video.mp4"
            output = root / "audio" / "interview.wav"
            fake_ffmpeg = root / "ffmpeg"
            source.write_bytes(b"fake-video-with-audio")
            fake_ffmpeg.write_text(
                "#!/bin/sh\nfor last do :; done\nprintf 'RIFFfake-wave' > \"$last\"\n",
                encoding="utf-8",
            )
            fake_ffmpeg.chmod(fake_ffmpeg.stat().st_mode | stat.S_IXUSR)

            receipt = extract_audio(source, output, ffmpeg=str(fake_ffmpeg))

            self.assertTrue(output.is_file())
            self.assertEqual(sha256_file(output), receipt["output_sha256"])
            self.assertEqual(16_000, receipt["sample_rate_hz"])
            self.assertEqual(1, receipt["channels"])
            self.assertEqual("interview video.mp4", receipt["source"])
            self.assertEqual([], list(output.parent.glob(".*.wav")))

    def test_existing_output_is_not_overwritten_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            output = root / "output.wav"
            source.write_bytes(b"video")
            output.write_bytes(b"keep-me")
            with self.assertRaises(FileExistsError):
                extract_audio(source, output, ffmpeg="ffmpeg")
            self.assertEqual(b"keep-me", output.read_bytes())

    def test_dry_run_emits_json_without_touching_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            output = root / "output.wav"
            fake_ffmpeg = root / "ffmpeg"
            manifest = root / "receipt.json"
            source.write_bytes(b"video")
            fake_ffmpeg.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            fake_ffmpeg.chmod(fake_ffmpeg.stat().st_mode | stat.S_IXUSR)

            code = main(
                [
                    str(source),
                    "--output",
                    str(output),
                    "--ffmpeg",
                    str(fake_ffmpeg),
                    "--manifest",
                    str(manifest),
                    "--dry-run",
                ]
            )

            self.assertEqual(0, code)
            self.assertFalse(output.exists())
            self.assertFalse(json.loads(manifest.read_text(encoding="utf-8"))["executed"])


if __name__ == "__main__":
    unittest.main()
