"""真实 HTTP -> Redis -> worker 验证。使用标准库，从 Windows 虚拟环境运行。"""

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8200")
    args = parser.parse_args()
    # 本地服务测试明确不走系统代理。
    opener = build_opener(ProxyHandler({}))
    base = args.base_url.rstrip("/")

    def request(path, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = Request(base + path, data=body, headers={"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=10) as response:
                return response.status, json.load(response), response.headers
        except HTTPError as exc:
            return exc.code, json.load(exc), exc.headers

    try:
        code, _, _ = request("/health")
        if code != 200:
            raise ValueError(f"Health returned {code}")
        code, submitted, headers = request("/demo/tasks", {"left": 2, "right": 3})
        if code != 202:
            raise ValueError(f"Submit returned {code}: {submitted}")
        status_url = submitted["status_url"]
        if headers.get("Location") != status_url:
            raise ValueError("Missing/mismatched Location header")
        print(f"task_id: {submitted['task_id']}", flush=True)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            code, result, _ = request(status_url)
            if code != 200:
                raise ValueError(f"Lookup returned {code}: {result}")
            print(f"state: {result['state']}", flush=True)
            if result["state"] == "SUCCESS":
                if result["result"] != 5:
                    raise ValueError(f"Unexpected result: {result}")
                break
            if result["state"] in {"FAILURE", "REVOKED"}:
                raise ValueError(f"Task did not succeed: {result}")
            time.sleep(1)
        else:
            raise ValueError("No completed result within 45 seconds; inspect worker logs")
        code, _, _ = request("/demo/tasks", {"left": "invalid", "right": 3})
        if code != 422:
            raise ValueError(f"Invalid input should return 422, got {code}")
        code, _, _ = request("/demo/tasks/not-a-uuid")
        if code != 422:
            raise ValueError(f"Invalid task ID should return 422, got {code}")
        code, unknown, _ = request(f"/demo/tasks/{uuid4()}")
        if code != 200 or unknown["state"] != "PENDING" or not unknown.get("note"):
            raise ValueError(f"Unknown-ID behavior differs from documentation: {unknown}")
        print("PASS: HTTP 202 -> worker -> SUCCESS, result=5; input validation passed")
        return 0
    except (URLError, OSError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
