"""上传真实视频，等待后台切片并下载、检查 ZIP。默认访问本机 8200。"""

import argparse
import http.client
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4
import zipfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8200")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--expected-clips", type=int)
    parser.add_argument("--output-dir", type=Path, default=Path("downloads"))
    args = parser.parse_args()
    base = urlsplit(args.base_url)
    if base.scheme not in {"http", "https"} or not base.hostname or base.path not in {"", "/"}:
        parser.error("base-url 必须是 http(s)://主机:端口")
    if not args.video.is_file() or args.timeout <= 0:
        parser.error("请指定存在的视频文件与正数超时时间")

    def connect():
        cls = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
        return cls(base.hostname, base.port, timeout=60)

    def get_json(path):
        connection = connect()
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            data = json.load(response)
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}: {data}")
            return data
        finally:
            connection.close()

    try:
        boundary = "clipforge" + uuid4().hex
        name = args.video.name.replace('"', '_').replace("\r", "_").replace("\n", "_")
        prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                  "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        connection = connect()
        try:
            connection.putrequest("POST", "/tasks")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(len(prefix) + args.video.stat().st_size + len(suffix)))
            connection.endheaders()
            connection.send(prefix)
            with args.video.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    connection.send(chunk)
            connection.send(suffix)
            response = connection.getresponse()
            submitted = json.load(response)
            if response.status != 202:
                raise ValueError(f"Upload HTTP {response.status}: {submitted}")
        finally:
            connection.close()
        print(f"task_id: {submitted['task_id']}", flush=True)
        deadline = time.monotonic() + args.timeout
        previous = None
        while time.monotonic() < deadline:
            job = get_json(submitted["status_url"])
            snapshot = (job["status"], job["stage"], job["progress"])
            if snapshot != previous:
                print(f"state={snapshot[0]}, stage={snapshot[1]}, stage_progress={snapshot[2]}%", flush=True)
                previous = snapshot
            if job["status"] == "SUCCEEDED":
                break
            if job["status"] in {"FAILED", "SUBMISSION_UNKNOWN"}:
                raise ValueError(f"Task needs attention: {job}")
            time.sleep(1)
        else:
            raise ValueError("等待超时，任务可能仍在执行；请使用上面的编号继续查询，不要重复上传。")
        count = job["result"]["clip_count"]
        if args.expected_clips is not None and count != args.expected_clips:
            raise ValueError(f"Expected {args.expected_clips} clips, got {count}")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        target = args.output_dir / f"{submitted['task_id']}-result.zip"
        connection = connect()
        created = False
        try:
            connection.request("GET", job["result"]["downloads"]["result.zip"])
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"Download HTTP {response.status}")
            with target.open("xb") as output:
                created = True
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            with zipfile.ZipFile(target) as archive:
                if archive.testzip() is not None:
                    raise ValueError("ZIP integrity check failed")
                plan = json.loads(archive.read("clip_plan.json"))
                names = set(archive.namelist())
                expected = {"media_meta.json", "normalized_media_meta.json", "clip_plan.json"}
                expected.update(clip["file"] for clip in plan["clips"])
                if names != expected or len(plan["clips"]) != count:
                    raise ValueError("ZIP files do not match clip plan")
                if sum(clip["frame_count"] for clip in plan["clips"]) != plan["total_frames"]:
                    raise ValueError("Frame counts do not match")
                print(f"clip_frames: {[clip['frame_count'] for clip in plan['clips']]}")
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
        finally:
            connection.close()
        print(f"download: {target.resolve()}")
        print(f"PASS: upload -> worker -> {count} clips -> ZIP verified")
        return 0
    except (OSError, ValueError, KeyError, http.client.HTTPException, zipfile.BadZipFile) as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
