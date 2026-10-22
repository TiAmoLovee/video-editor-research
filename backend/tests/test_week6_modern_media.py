"""Real fit-pad ZIP exports retain exact source frames and black portrait margins."""

import hashlib
import json
import os
import shutil
import subprocess
import unittest
from uuid import uuid4
from zipfile import ZipFile

from backend.tests import test_editing_service as fixtures
from backend.tests.test_candidates import fixture
from clipforge.decision.candidates import generate_candidates
from clipforge.decision.scoring import score_candidates
from clipforge.services import batch_export, editing, editing_changes, reviewing

FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("FFPROBE") or shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "set FFMPEG/FFPROBE for real fit-pad batch verification")
class Week6ModernMediaTests(unittest.TestCase):
    setUp = fixtures.EditingServiceTests.setUp

    def test_fitted_batch_decodes_each_intended_frame_inside_unchanged_black_margins(self):
        width, height = 160, 48
        def marker(number):
            return [224 if number & (1 << bit) else 32 for bit in range(10)]
        raw = b"".join(b"".join(bytes([value]) * 16 * 3 for value in marker(number)) * height
                       for number in range(930))
        source = self.folder / "normalized.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
                        "-video_size", f"{width}x{height}", "-framerate", "30", "-i", "pipe:0", "-f", "lavfi",
                        "-i", "sine=frequency=440:sample_rate=48000:duration=31", "-vf",
                        "setsar=1,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
                        "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", "-color_primaries", "bt709",
                        "-color_trc", "bt709", "-colorspace", "bt709", "-c:a", "aac", "-shortest", str(source)],
                       input=raw, capture_output=True, timeout=30, check=True)
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        analysis = fixture([(0, 10), (10, 20), (20, 30)])
        analysis["media"]["normalized_sha256"] = source_hash
        candidates = score_candidates(generate_candidates(analysis), analysis)
        for name, value in (("analysis.json", analysis), ("candidates.json", candidates)):
            (self.folder / name).write_text(json.dumps(value), encoding="utf-8")
        selected = [candidate["id"] for candidate in candidates["candidates"][:2]]
        original = editing.create_task_edl(self.task_id, selected, ffprobe=FFPROBE, actor="媒体验收")
        ranges = [(1, 6), (37, 43)]
        clips = [{"candidate_id": identifier, "source_in_frame": start, "source_out_frame": end}
                 for identifier, (start, end) in zip(selected, ranges, strict=True)]
        edited = editing_changes.create_task_edit(self.task_id, original["revision_id"], clips, actor="媒体验收")
        fitted = editing.create_task_portrait(self.task_id, edited["revision_id"], mode="fit_pad")
        saved = editing.load_task_edl(self.task_id, fitted["revision_id"])
        project = saved["edl"]
        self.assertEqual(project["schema_version"], "0.3.0")
        exported = batch_export.create_task_batch_export(self.task_id, fitted["revision_id"], str(uuid4()),
                                                         ffmpeg=FFMPEG, ffprobe=FFPROBE, timeout=90)
        loaded = batch_export.load_task_batch_export(self.task_id, fitted["revision_id"], exported["export_id"])
        archive_path = self.folder / "edits" / fitted["revision_id"] / "exports" / "batch" / exported["export_id"] / "clips.zip"
        rectangle = project["framing"]["sources"][0]
        output_w, output_h = project["profile"]["width"], project["profile"]["height"]
        frame_bytes = output_w * output_h
        row = rectangle["y"] + rectangle["height"] // 2
        points = [int(rectangle["x"] + rectangle["width"] * (bit + .5) / 10) for bit in range(10)]
        with ZipFile(archive_path) as archive:
            self.assertEqual(json.loads(archive.read("source.edl.json")), project)
            for item, (start, end) in zip(loaded["manifest"]["clips"], ranges, strict=True):
                path = self.folder / f"portrait-check-{item['index']}.mp4"
                path.write_bytes(archive.read(item["filename"]))
                decoded = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0",
                                          "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                                         check=True, capture_output=True, timeout=30).stdout
                self.assertEqual(len(decoded), (end - start) * frame_bytes)
                observed = []
                for offset in range(end - start):
                    values = [decoded[offset * frame_bytes + row * output_w + point] for point in points]
                    observed.append(sum(int(value >= 128) << bit for bit, value in enumerate(values)))
                    self.assertLessEqual(decoded[offset * frame_bytes + output_w // 2], 20)
                    self.assertLessEqual(decoded[(offset + 1) * frame_bytes - output_w // 2], 20)
                self.assertEqual(observed, list(range(start, end)))
                self.assertEqual(item["verification"]["framing"], project["framing"])
                self.assertEqual(item["verification"]["audio"]["pre_encode_total_samples"], (end - start) * 1600)
        self.assertEqual(exported["total_frames"], 11)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
        self.assertEqual([event["actor"] for event in reviewing.task_audit(self.task_id)["events"]], ["媒体验收", "媒体验收"])


if __name__ == "__main__":
    unittest.main()
