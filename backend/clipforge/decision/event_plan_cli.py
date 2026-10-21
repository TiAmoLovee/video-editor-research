"""Prepare compact event requests or validate saved responses, entirely offline.

There is deliberately no remote execution command. A prepared request is not
authorization to send it. Output directories must be new; invalid responses
produce no output bundle and never overwrite any input or existing project.
"""

import argparse
import json
from pathlib import Path

from clipforge.decision.llm import strict_json
from clipforge.decision.model_event_plan_v2 import (
    MAX_ANALYSIS_BYTES, MAX_LOCAL_BYTES, MAX_RESPONSE_BYTES, VERSION,
    build_model_event_request, parse_model_event_response,
)


def _read(path, limit):
    with path.open("rb") as source:
        body = source.read(limit + 1)
    if len(body) > limit:
        raise ValueError("input exceeds fixed byte limit")
    return strict_json(body.decode("utf-8-sig"))


def _write_bundle(directory, files, receipt):
    prepared = {name: json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
                for name, value in files.items()}
    prepared["bundle-receipt.json"] = json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    directory.mkdir(parents=True, exist_ok=False)
    # Receipt is written last, so a partial filesystem write cannot appear to
    # be a completed bundle. No replace/delete operation is required.
    for name, body in prepared.items():
        with (directory / name).open("x", encoding="utf-8", newline="\n") as target:
            target.write(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--analysis", type=Path, required=True)
    prepare.add_argument("--evidence", type=Path)
    prepare.add_argument("--out", type=Path, required=True)
    imported = sub.add_parser("import-response")
    imported.add_argument("--analysis", type=Path, required=True)
    imported.add_argument("--request", type=Path, required=True)
    imported.add_argument("--response", type=Path, required=True)
    imported.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.out.exists():
            raise ValueError("output directory already exists")
        analysis = _read(args.analysis, MAX_ANALYSIS_BYTES)
        if args.command == "prepare":
            evidence = _read(args.evidence, MAX_RESPONSE_BYTES) if args.evidence else []
            request = build_model_event_request(analysis, non_dialogue_evidence=evidence)
            files = {"request.json": request, "payload.json": request["payload"]}
            stage = "request_prepared_not_sent"
        else:
            request = _read(args.request, MAX_LOCAL_BYTES)
            response = _read(args.response, MAX_RESPONSE_BYTES)
            result = parse_model_event_response(response, analysis, request)
            files = {"response-receipt.json": result, "plan.json": result["plan"]}
            stage = "saved_response_structurally_valid_not_semantically_approved"
        receipt = {"status": "completed", "stage": stage, "strategy_version": VERSION,
                   "request_sha256": request["request_sha256"], "network_requests": 0,
                   "origin": "offline_file_processing_not_provider_attestation",
                   "production_changed": False, "semantic_completeness_verified": False,
                   "usage_settled_to_campaign_ledger": False}
        _write_bundle(args.out, files, receipt)
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"status": "rejected", "network_requests": 0,
                          "completed_output_bundle": False}))
        return 1
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
