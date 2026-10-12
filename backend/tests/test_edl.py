"""EDL source binding, explicit selection, exact frame math and editing invariants."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator

from clipforge.editing.edl import ROOT, candidates_to_edl, frame_rate, seconds_to_frame, validate_edl


def source_fixture(**changes):
    source = {"id": "normalized-1", "ref": "视频 & source.mp4", "sha256": hashlib.sha256(b"").hexdigest(),
              "total_frames": 3000, "width": 960, "height": 600, "fps": {"num": 30, "den": 1},
              "cfr": True, "progressive": True, "sample_aspect_num": 1, "sample_aspect_den": 1,
              "rotation_degrees": 0, "colorspace": 709, "video_stream_index": 0,
              "audio_stream_index": 1}
    source.update(changes)
    return source


def candidate_fixture(intervals=((0, 20), (30.6, 41.266666666666666)), source=None):
    source = source or source_fixture()
    return {"media": {"normalized_sha256": source["sha256"], "has_audio": source["audio_stream_index"] is not None,
                      "time_reference": "normalized_video", "duration_seconds": 100},
            "candidates": [{"id": f"c{i}", "start": start, "end": end,
                            "source_sentences": [f"s{i}"], "score": 99 - i}
                           for i, (start, end) in enumerate(intervals)]}


def edl_fixture():
    return candidates_to_edl(candidate_fixture(), ["c0", "c1"], source_fixture())


class EDLTests(unittest.TestCase):
    def test_versioned_schema_is_valid(self):
        schema = json.loads((ROOT / "schemas/edl.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(edl_fixture())
        self.assertEqual(edl_fixture()["schema_version"], "0.1.0")

    def test_explicit_selection_order_and_no_auto_acceptance(self):
        candidates, source = candidate_fixture(), source_fixture()
        before = deepcopy((candidates, source))
        data = candidates_to_edl(candidates, ["c1", "c0"], source)
        self.assertEqual([c["provenance"]["candidate_id"] for c in data["clips"]], ["c1", "c0"])
        self.assertEqual([(c["source_in_frame"], c["source_out_frame"]) for c in data["clips"]],
                         [(918, 1238), (0, 600)])
        self.assertEqual([(c["timeline_in_frame"], c["timeline_out_frame"]) for c in data["clips"]],
                         [(0, 320), (320, 920)])
        self.assertEqual((candidates, source), before)
        self.assertEqual(len(candidates_to_edl(candidates, ["c1"], source)["clips"]), 1)

    def test_provenance_survives_manual_trim_and_reorder(self):
        data = edl_fixture()
        baselines = {c["id"]: deepcopy(c["provenance"]) for c in data["clips"]}
        data["clips"].reverse()
        data["clips"][0]["source_in_frame"] += 2
        cursor = 0
        for clip in data["clips"]:
            clip["timeline_in_frame"] = cursor
            cursor += clip["source_out_frame"] - clip["source_in_frame"]
            clip["timeline_out_frame"] = cursor
        data["duration_frames"] = cursor
        validate_edl(data)
        self.assertEqual({c["id"]: c["provenance"] for c in data["clips"]}, baselines)

    def test_source_swap_cannot_retain_another_media_baseline(self):
        data = edl_fixture()
        second = deepcopy(data["sources"][0])
        second.update(id="another-source", sha256="b" * 64, ref="another.mp4")
        data["sources"].append(second)
        data["clips"][0]["source_id"] = second["id"]
        with self.assertRaisesRegex(ValueError, "original source"):
            validate_edl(data)
        data = edl_fixture()
        data["sources"][0]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "original source"):
            validate_edl(data)
        data = edl_fixture()
        data["sources"][0]["ref"] = "relinked/same-media.mp4"
        validate_edl(data)

    def test_half_up_rational_fps_and_quantization_bound(self):
        fps = {"num": 30000, "den": 1001}
        self.assertEqual(seconds_to_frame(0.05005, fps), 2)  # exactly 1.5 frames
        self.assertEqual(seconds_to_frame(0.5, {"num": 1, "den": 1}), 1)
        for seconds in (0, 0.001, 0.050049, 0.05005, 7.349, 99.4999):
            frame = seconds_to_frame(seconds, fps)
            error_frames = abs(Fraction(frame) - Fraction(str(seconds)) * frame_rate(fps))
            self.assertLessEqual(error_frames, Fraction(1, 2))
        source = source_fixture(fps=fps)
        data = candidates_to_edl(candidate_fixture(((0.05005, 0.08341666666666667),), source),
                                 ["c0"], source)
        self.assertEqual(data["clips"][0]["source_in_frame"], 2)
        self.assertEqual(data["clips"][0]["source_out_frame"], 3)
        self.assertEqual(data["duration_frames"], 1)

    def test_invalid_selection_duplicate_inputs_and_zero_frame_range(self):
        for selected in ([], None, [True], ["c0", "c0"], ["missing"]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                candidates_to_edl(candidate_fixture(), selected, source_fixture())
        candidates = candidate_fixture()
        candidates["candidates"].append(deepcopy(candidates["candidates"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate candidate"):
            candidates_to_edl(candidates, ["c0"], source_fixture())
        with self.assertRaisesRegex(ValueError, "zero video frames"):
            candidates_to_edl(candidate_fixture(((0.001, 0.002),)), ["c0"], source_fixture())

    def test_candidate_media_binding_and_projection_errors(self):
        mutations = [lambda d: d["media"].update(normalized_sha256="b" * 64),
                     lambda d: d["media"].update(time_reference="original"),
                     lambda d: d["media"].update(has_audio=False),
                     lambda d: d["media"].update(duration_seconds=0),
                     lambda d: d["media"].update(duration_seconds=float("inf")),
                     lambda d: d.update(candidates={}),
                     lambda d: d["candidates"][0].update(start=-1),
                     lambda d: d["candidates"][0].update(end=101),
                     lambda d: d["candidates"][0].update(end=float("nan")),
                     lambda d: d["candidates"][0].update(start=True),
                     lambda d: d["candidates"][0].update(source_sentences=["s0", "s0"])]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                candidates = candidate_fixture()
                mutation(candidates)
                candidates_to_edl(candidates, ["c0"], source_fixture())
        with self.assertRaises(ValueError):
            candidates_to_edl([], ["c0"], source_fixture())
        source = source_fixture(total_frames=600)
        with self.assertRaisesRegex(ValueError, "bounds"):
            candidates_to_edl(candidate_fixture(((0, 20.00001),), source), ["c0"], source)

    def test_semantic_corruption_and_unsupported_media_rejected(self):
        mutations = [lambda d: d.update(duration_frames=1),
                     lambda d: d.update(schema_version="1.0.0"),
                     lambda d: d.update(unsupported={}),
                     lambda d: d["sources"].append(deepcopy(d["sources"][0])),
                     lambda d: d["clips"][1].update(id=d["clips"][0]["id"]),
                     lambda d: d["clips"][0].update(source_id="missing"),
                     lambda d: d["clips"][0].update(source_out_frame=3001),
                     lambda d: d["clips"][0].update(source_out_frame=0),
                     lambda d: d["clips"][0].update(source_in_frame=-1),
                     lambda d: d["clips"][0].update(source_in_frame=0.0),
                     lambda d: d["clips"][0].update(source_in_frame=True),
                     lambda d: d["clips"][1].update(timeline_in_frame=599),
                     lambda d: d["clips"][1].update(timeline_in_frame=601),
                     lambda d: d["clips"][1].update(timeline_out_frame=921),
                     lambda d: d["sources"][0].update(cfr=False),
                     lambda d: d["sources"][0].update(progressive=False),
                     lambda d: d["sources"][0].update(sample_aspect_num=2),
                     lambda d: d["sources"][0].update(rotation_degrees=90),
                     lambda d: d["sources"][0].update(colorspace=601),
                     lambda d: d["sources"][0].update(width=640),
                     lambda d: d["sources"][0].update(height=480),
                     lambda d: d["sources"][0].update(total_frames=True),
                     lambda d: d["sources"][0].update(audio_stream_index=0),
                     lambda d: d["sources"][0].update(fps={"num": 25, "den": 1}),
                     lambda d: d["sources"][0].update(ref="https://example.invalid/video.mp4"),
                     lambda d: d["clips"][0]["provenance"].update(original_out_frame=1),
                     lambda d: d["clips"][0]["provenance"].update(original_end_seconds=101),
                     lambda d: d["clips"][1]["provenance"].update(**d["clips"][0]["provenance"])]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                data = edl_fixture()
                mutation(data)
                validate_edl(data)

    def test_fps_and_seconds_fail_closed(self):
        for fps in ({"num": 60, "den": 2}, {"num": True, "den": 1},
                    {"num": 30.0, "den": 1}, {"num": 30, "den": 0},
                    {"num": 121, "den": 1}, {"num": 1, "den": 2}, {}, None):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                frame_rate(fps)
        for seconds in (True, "1", None, -1, float("nan"), float("inf"), 2147483648):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                seconds_to_frame(seconds, {"num": 30, "den": 1})

    def test_cli_import_and_existing_output_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "candidates.json"
            manifest = folder / "manifest.json"
            output = folder / "edl.json"
            candidates, media = candidate_fixture(), source_fixture()
            source.write_text(json.dumps(candidates), encoding="utf-8")
            manifest.write_text(json.dumps({"source": media, "profile": {
                "width": media["width"], "height": media["height"], "fps": media["fps"]}}), encoding="utf-8")
            command = [sys.executable, "-m", "clipforge.editing.edl", str(source),
                       "--media-profile", str(manifest), "--selected-id", "c1", "--selected-id", "c0",
                       "--output", str(output)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(output.read_text(encoding="utf-8"))
            validate_edl(result)
            self.assertEqual(result["clips"][0]["provenance"]["candidate_id"], "c1")
            before = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), before)
            manifest.write_text('{}', encoding="utf-8")
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
