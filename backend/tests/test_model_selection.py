import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clipforge.analysis.model import model_spec, model_directory, verify_model
from clipforge.analysis.asr import _load_model, transcribe_video


class ModelSelectionTests(unittest.TestCase):
    def test_default_is_base_and_explicit_choice_beats_environment(self):
        with patch.dict(os.environ, {"CLIPFORGE_ASR_MODEL": ""}):
            self.assertEqual(model_spec()["name"], "base")
        with patch.dict(os.environ, {"CLIPFORGE_ASR_MODEL": "small", "CLIPFORGE_ASR_MODEL_DIR": ""}):
            self.assertEqual(model_spec()["name"], "small")
            self.assertEqual(model_spec("base")["name"], "base")
            self.assertEqual(model_directory().name, "faster-whisper-small")
            self.assertEqual(model_directory(model_name="base").name, "faster-whisper-base")

    def test_bad_model_name_fails_before_reading_video(self):
        with patch.dict(os.environ, {"CLIPFORGE_ASR_MODEL": "not-supported"}):
            with self.assertRaisesRegex(ValueError, "base 或 small"):
                transcribe_video("does-not-exist.mp4")

    def test_hashes_are_checked_for_selected_model(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "model.bin").write_bytes(b"small weights")
            digest = hashlib.sha256(b"small weights").hexdigest()
            spec = {"repository": "Systran/faster-whisper-small", "revision": "test", "sha256": {"model.bin": digest}}
            with patch("clipforge.analysis.model.SMALL_SPEC", spec), \
                 patch("clipforge.analysis.model.MODEL_HASHES", {"model.bin": "0" * 64}):
                identity = verify_model(folder, "small")
                self.assertEqual(identity["repository"], "Systran/faster-whisper-small")
                with self.assertRaisesRegex(ValueError, "校验失败"):
                    verify_model(folder, "base")

    def test_cache_key_includes_model_name_and_inference_never_downloads(self):
        _load_model.cache_clear()
        self.addCleanup(_load_model.cache_clear)
        with patch("clipforge.analysis.asr.verify_model", return_value={}) as verify, \
             patch("faster_whisper.WhisperModel") as model:
            _load_model("same-directory", "base")
            _load_model("same-directory", "base")
            _load_model("same-directory", "small")
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(model.call_count, 2)
        self.assertTrue(model.call_args.kwargs["local_files_only"])

    def test_metadata_tracks_selected_model_even_without_audio(self):
        raw = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "avg_frame_rate": "30/1"}]}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "video.mp4"
            source.write_bytes(b"fake")
            with patch.dict(os.environ, {"CLIPFORGE_ASR_MODEL": "small"}), \
                 patch("clipforge.analysis.asr.probe_video", return_value=raw), \
                 patch("clipforge.analysis.asr.read_frame_count", return_value=30), \
                 patch("clipforge.analysis.asr._load_model") as load:
                result = transcribe_video(source)
            load.assert_not_called()
            self.assertEqual(result["analyzer"]["model"], model_spec("small")["repository"])
            self.assertEqual(result["analyzer"]["parameters"]["model_revision"], model_spec("small")["revision"])
            self.assertEqual(result["analyzer"]["status"], "no_audio")


if __name__ == "__main__":
    unittest.main()
