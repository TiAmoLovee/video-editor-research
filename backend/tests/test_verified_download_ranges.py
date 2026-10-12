"""Byte ranges keep the previously verified handle and bound every read."""

from contextlib import contextmanager
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect

from clipforge.routes import editing
from clipforge.services.editing import ProjectError

TASK = "00000000-0000-4000-8000-000000000001"
REVISION = "00000000-0000-4000-8000-000000000002"
RENDER = "00000000-0000-4000-8000-000000000003"
CONTENT = b"0123456789abcdefghij"


class TrackedBytes(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.reads = []

    def read(self, size=-1):
        self.reads.append((self.tell(), size))
        return super().read(size)


class VerifiedDownloadRangeTests(unittest.TestCase):
    @staticmethod
    def deliver(response, headers=None, disconnect=False):
        """Exercise the response's ASGI wire output without an OS event loop."""
        events = []

        async def immediate_chunks():
            for chunk in response.iter_bytes():
                yield chunk

        # Only thread scheduling is replaced; the route and ASGI response run intact.
        response.body_iterator = immediate_chunks()

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if disconnect and message["type"] == "http.response.body":
                raise OSError("simulated disconnected client")
            events.append(message)

        raw_headers = [(key.lower().encode("ascii"), value.encode("ascii"))
                       for key, value in (headers or {}).items()]
        scope = {"type": "http", "method": "GET", "headers": raw_headers,
                 "asgi": {"spec_version": "2.4"}}
        coroutine = response(scope, receive, send)
        try:
            coroutine.send(None)
        except StopIteration:
            pass
        else:
            coroutine.close()
            raise AssertionError("Unexpected event-loop dependency in direct ASGI probe")
        start = events[0]
        return SimpleNamespace(status_code=start["status"], headers=Headers(raw=start["headers"]),
                               content=b"".join(event["body"] for event in events[1:]))

    def request(self, headers=None, content=CONTENT, source=False):
        incoming = TrackedBytes(content)
        closed = []

        @contextmanager
        def resource():
            try:
                yield incoming
            finally:
                closed.append(True)
                incoming.close()

        service = editing.editing_service if source else editing.rendering_service
        method = "open_revision_source" if source else "open_render_file"
        with patch.object(service, method, return_value=resource()) as opened:
            response = (editing.download_edl_source(UUID(TASK), UUID(REVISION)) if source else
                        editing.download_render_file(UUID(TASK), UUID(REVISION), UUID(RENDER), "output.mp4"))
            response = self.deliver(response, headers)
        if source:
            opened.assert_called_once_with(TASK, REVISION)
        else:
            opened.assert_called_once_with(TASK, REVISION, RENDER, "output.mp4")
        self.assertTrue(incoming.closed)
        self.assertEqual(closed, [True])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        return response, incoming.reads

    def test_complete_download_is_unchanged_for_source_and_render(self):
        for source in (False, True):
            with self.subTest(source=source):
                response, reads = self.request(source=source)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, CONTENT)
                self.assertEqual(response.headers["content-length"], "20")
                self.assertNotIn("content-range", response.headers)
                self.assertEqual(response.headers["content-type"], "video/mp4")
                self.assertEqual(response.headers["content-disposition"],
                                 'attachment; filename="normalized.mp4"' if source else
                                 'attachment; filename="output.mp4"')
                self.assertEqual(reads, [(0, 20)])

    def test_source_and_render_ranges_use_only_the_held_selected_bytes(self):
        for source in (False, True):
            with self.subTest(source=source):
                response, reads = self.request({"Range": "bytes=3-7"}, source=source)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, b"34567")
                self.assertEqual(response.headers["content-length"], "5")
                self.assertEqual(response.headers["content-range"], "bytes 3-7/20")
                self.assertEqual(reads, [(3, 5)])

    def test_open_ended_suffix_and_clamped_end_ranges(self):
        cases = [
            ("bytes=17-", b"hij", "bytes 17-19/20", [(17, 3)]),
            ("bytes=-4", b"ghij", "bytes 16-19/20", [(16, 4)]),
            ("bytes=-200", CONTENT, "bytes 0-19/20", [(0, 20)]),
            ("bytes=18-200", b"ij", "bytes 18-19/20", [(18, 2)]),
            ("bytes=0-0", b"0", "bytes 0-0/20", [(0, 1)]),
            ("BYTES= 1-2 ", b"12", "bytes 1-2/20", [(1, 2)]),
        ]
        for value, expected, content_range, expected_reads in cases:
            with self.subTest(range=value):
                response, reads = self.request({"Range": value})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, expected)
                self.assertEqual(response.headers["content-length"], str(len(expected)))
                self.assertEqual(response.headers["content-range"], content_range)
                self.assertEqual(reads, expected_reads)

    def test_unsatisfiable_ranges_close_without_reading_any_file_bytes(self):
        for value in ("bytes=20-", "bytes=200-300", "bytes=-0", "bytes=" + "9" * 200 + "-"):
            with self.subTest(range=value):
                response, reads = self.request({"Range": value})
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response.headers["content-range"], "bytes */20")
                self.assertEqual(reads, [])

    def test_zero_length_complete_download_and_unsatisfiable_range(self):
        response, reads = self.request(content=b"")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(reads, [])
        for value in ("bytes=0-", "bytes=-1"):
            with self.subTest(range=value):
                response, reads = self.request({"Range": value}, content=b"")
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response.headers["content-range"], "bytes */0")
                self.assertEqual(reads, [])

    def test_malformed_and_oversized_ranges_are_safe_errors(self):
        for value in ("bytes", "bytes=", "bytes=-", "bytes=a-3", "bytes=5-2",
                      "bytes=1--2", "bytes=+1-3", "bytes=" + "0" * 1024 + "-"):
            with self.subTest(range=value[:40]):
                response, reads = self.request({"Range": value})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("content-range", response.headers)
                self.assertEqual(json.loads(response.content), {"detail": "无效或不可满足的字节范围。"})
                self.assertEqual(reads, [])

    def test_multipart_or_unknown_unit_falls_back_to_complete_download(self):
        for value in ("bytes=0-1,4-5", "items=1-2"):
            with self.subTest(range=value):
                response, reads = self.request({"Range": value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, CONTENT)
                self.assertNotIn("content-range", response.headers)
                self.assertEqual(reads, [(0, 20)])

    def test_if_range_without_a_known_strong_validator_returns_complete_file(self):
        for validator in ('"unknown"', 'W/"weak"', "Mon, 12 Oct 2026 00:00:00 GMT", ""):
            with self.subTest(validator=validator):
                response, reads = self.request({"Range": "bytes=2-4", "If-Range": validator})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, CONTENT)
                self.assertNotIn("content-range", response.headers)
                self.assertEqual(reads, [(0, 20)])

    def test_chunks_never_read_past_range_end(self):
        size = 1024 * 1024
        content = b"a" * (size * 2 + 100)
        response, reads = self.request({"Range": f"bytes=7-{size + 9}"}, content=content)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), size + 3)
        self.assertEqual(reads, [(7, size), (7 + size, 3)])

    def test_hash_verification_failure_is_not_bypassed_by_a_range(self):
        with patch.object(editing.rendering_service, "open_render_file",
                          side_effect=ProjectError(409, "成片校验失败。")) as opened:
            with self.assertRaises(HTTPException) as caught:
                editing.download_render_file(UUID(TASK), UUID(REVISION), UUID(RENDER), "output.mp4")
        opened.assert_called_once_with(TASK, REVISION, RENDER, "output.mp4")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, "成片校验失败。")

    def test_disconnect_closes_the_same_verified_handle_once(self):
        incoming = TrackedBytes(CONTENT)
        closed = []

        @contextmanager
        def resource():
            try:
                yield incoming
            finally:
                closed.append(True)
                incoming.close()

        response = editing.VerifiedDownload(resource(), "output.mp4", "video/mp4")

        with self.assertRaises(ClientDisconnect):
            self.deliver(response, {"Range": "bytes=3-7"}, disconnect=True)
        response.close()
        self.assertTrue(incoming.closed)
        self.assertEqual(closed, [True])
        self.assertEqual(incoming.reads, [(3, 5)])


if __name__ == "__main__":
    unittest.main()
