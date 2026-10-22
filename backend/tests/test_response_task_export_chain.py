"""In-process ASGI adoption/export over tiny measured synthetic media."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from clipforge.api import app
from clipforge.config import media_tool
from clipforge.decision.candidates import generate_candidates
from clipforge.decision.scoring import score_candidates
from clipforge.services import rendering
from clipforge.storage import jobs
from test_relation_event_plan import analysis_with_words, proposal


def request(method, path, body=None):
    """Exercise parsing/routing/response handling without an HTTP server."""
    async def perform():
        incoming = json.dumps(body).encode("utf-8") if body is not None else b""
        messages = []
        body_received = False
        response_completed = asyncio.Event()
        async def receive():
            nonlocal body_received
            if not body_received:
                body_received = True
                return {"type": "http.request", "body": incoming, "more_body": False}
            await response_completed.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_completed.set()
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                 "raw_path": path.encode("ascii"), "query_string": b"", "root_path": "",
                 "headers": [(b"content-type", b"application/json")],
                 "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80)}
        await asyncio.wait_for(app(scope, receive, send), timeout=30)
        start = next(m for m in messages if m["type"] == "http.response.start")
        payload = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return start["status"], json.loads(payload)
    return asyncio.run(perform())


class ResponseTaskExportChainTests(unittest.TestCase):
    def setUp(self):
        if os.name == "nt":
            # Windows asyncio internally uses loopback sockets. Check with a
            # bounded handshake before its unbounded socketpair initialization.
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.settimeout(.5)
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(1)
                    with socket.create_connection(listener.getsockname(), timeout=.5):
                        connection, _ = listener.accept()
                        connection.close()
            except OSError:
                self.skipTest("Windows asyncio requires permitted localhost sockets")
        self.ffmpeg, self.ffprobe = media_tool("ffmpeg"), media_tool("ffprobe")
        if not shutil.which(self.ffmpeg) or not shutil.which(self.ffprobe):
            self.skipTest("FFmpeg/FFprobe required for measured ASGI media test")
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"CLIPFORGE_DATA_DIR": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.task = str(uuid4())
        self.folder = jobs.job_dir(self.task)
        self.folder.mkdir(parents=True)
        media = self.folder/"normalized.mp4"
        subprocess.run([self.ffmpeg, "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
            "color=c=navy:s=96x64:r=30:d=21", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=21",
            "-vf", "setsar=1,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-shortest", str(media)],
            check=True, capture_output=True, timeout=30)
        self.analysis = analysis_with_words(6)
        for name in ("words", "sentences", "shots", "speech"):
            for row in self.analysis[name]:
                row["start"] = round(row["start"]*3, 9)
                row["end"] = round(row["end"]*3, 9)
        self.analysis["media"]["duration_seconds"] = 21
        self.analysis["media"]["normalized_sha256"] = hashlib.sha256(media.read_bytes()).hexdigest()
        self.proposal = proposal(self.analysis, relations=[["q", 1, 1, 2, 2]])
        candidates = score_candidates(generate_candidates(self.analysis), self.analysis)
        for name, value in (("analysis.json", self.analysis), ("candidates.json", candidates)):
            (self.folder/name).write_text(json.dumps(value), encoding="utf-8")
        jobs.create_job(self.task, "synthetic-fixture.mp4", "source.mp4")
        jobs.claim_job(self.task)
        jobs.update_job(self.task, "SUCCEEDED", "done", 100, result={"files": {
            "analysis.json": "analysis.json", "candidates.json": "candidates.json"}})
        no_provider = patch("clipforge.decision.llm.http_transport", side_effect=AssertionError("provider call"))
        no_provider.start()
        self.addCleanup(no_provider.stop)

    def test_preview_adopt_saved_read_history_mp4_and_mlt_share_exact_saved_selection(self):
        original = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        original_job = jobs.get_job(self.task)
        base = f"/tasks/{self.task}"
        status, preview = request("POST", base+"/response-candidates", {"proposal": self.proposal})
        self.assertEqual(status, 200, preview)
        self.assertFalse((self.folder/"edits").exists())
        self.assertFalse(preview["media_verified"])
        selected = [c["id"] for c in preview["candidates"]["candidates"]]
        self.assertEqual(len(selected), 1)
        payload = {"proposal": self.proposal, "preview_sha256": preview["preview_sha256"], "selected_ids": selected}
        status, record = request("POST", base+"/response-edl", payload)
        self.assertEqual(status, 201, record)
        revision_id = record["revision_id"]
        version_path = base+"/edl/"+revision_id
        status, saved = request("GET", version_path)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["edl"]["duration_frames"], 513)
        clip = saved["edl"]["clips"][0]
        self.assertEqual((clip["source_in_frame"], clip["source_out_frame"]), (9, 522))
        self.assertEqual(saved["available_clips"][0]["candidate_id"], selected[0])
        status, history = request("GET", base+"/edl")
        self.assertEqual(status, 200, history)
        self.assertEqual(history["items"][0]["revision_id"], revision_id)
        status, subtitles = request("POST", version_path+"/subtitles")
        self.assertEqual(status, 201, subtitles)
        self.assertEqual(subtitles["revision_id"], revision_id)
        status, render = request("POST", version_path+"/renders")
        self.assertEqual(status, 201, render)
        stored = rendering.load_task_render(self.task, revision_id, render["render_id"])
        self.assertEqual(stored["verification"]["output"]["total_frames"], 513)
        status, mlt = request("POST", version_path+"/exports/mlt")
        self.assertEqual(status, 201, mlt)
        status, artifacts = request("GET", version_path+"/artifacts")
        self.assertEqual(status, 200, artifacts)
        self.assertEqual(len(artifacts["renders"]), 1)
        self.assertEqual(len(artifacts["mlt_exports"]), 1)
        self.assertEqual(len(artifacts["subtitles"]), 1)
        self.assertTrue(all((self.folder/name).read_bytes() == data for name, data in original.items()))
        self.assertEqual(jobs.get_job(self.task), original_job)

    def test_asgi_rejects_stale_token_unselected_and_extra_fields_without_saved_version(self):
        base = f"/tasks/{self.task}"
        status, preview = request("POST", base+"/response-candidates", {"proposal": self.proposal})
        self.assertEqual(status, 200, preview)
        selected = [c["id"] for c in preview["candidates"]["candidates"]]
        payload = {"proposal": self.proposal, "preview_sha256": "0"*64, "selected_ids": selected}
        status, _ = request("POST", base+"/response-edl", payload)
        self.assertEqual(status, 409)
        payload["preview_sha256"] = preview["preview_sha256"]
        payload["selected_ids"] = []
        self.assertEqual(request("POST", base+"/response-edl", payload)[0], 422)
        self.assertEqual(request("POST", base+"/response-candidates", {"proposal": self.proposal, "api_key": "reject"})[0], 422)
        self.assertFalse((self.folder/"edits").exists())


if __name__ == "__main__":
    unittest.main()
