"""Measure final net EDL changes, without inferring labor or content quality.

The immutable source/candidate provenance identifies a clip. Timeline positions
and nearest source times never identify clips. Seconds are accumulated as exact
rational values; the JSON report includes both numeric and exact representations.
"""

from bisect import bisect_left
from copy import deepcopy
from fractions import Fraction
import hashlib
import json

from clipforge.editing.edl import frame_rate, validate_edl


REPORT_VERSION = "1.0.0"


def _identity(clip):
    provenance = clip["provenance"]
    return (clip["source_id"], provenance["original_source_sha256"],
            provenance["candidate_document_sha256"], provenance["candidate_id"])


def _identity_report(key):
    return dict(zip(("source_id", "source_sha256", "candidate_document_sha256",
                     "candidate_id"), key, strict=True))


def _exact(value):
    return {"num": value.numerator, "den": value.denominator}


def _digest(edl):
    content = json.dumps(edl, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _range(clip):
    return {"in_frame": clip["source_in_frame"], "out_frame": clip["source_out_frame"]}


def _boundary_delta(before_in, before_out, final_clip, fps):
    in_delta = final_clip["source_in_frame"] - before_in
    out_delta = final_clip["source_out_frame"] - before_out
    in_seconds, out_seconds = Fraction(in_delta) / fps, Fraction(out_delta) / fps
    absolute_seconds = abs(in_seconds) + abs(out_seconds)
    return {
        "in_delta_frames": in_delta, "out_delta_frames": out_delta,
        "in_delta_seconds": float(in_seconds), "out_delta_seconds": float(out_seconds),
        "in_delta_seconds_exact": _exact(in_seconds),
        "out_delta_seconds_exact": _exact(out_seconds),
        "absolute_boundary_adjustment_seconds": float(absolute_seconds),
        "absolute_boundary_adjustment_seconds_exact": _exact(absolute_seconds),
    }, absolute_seconds


def _minimum_moves(baseline_keys, final_keys):
    """Unique common identities turn LCS into LIS, without quadratic storage."""
    positions = {key: index for index, key in enumerate(baseline_keys)}
    tails = []
    for key in final_keys:
        position = positions[key]
        index = bisect_left(tails, position)
        if index == len(tails):
            tails.append(position)
        else:
            tails[index] = position
    return len(baseline_keys) - len(tails)


def compare_edit_effort(baseline_edl, final_edl):
    """Return JSON-serializable net changes between two valid EDL snapshots.

    Source declarations must match by id, including their native FPS. Profile
    dimensions/framing may differ. Shared identities must keep every provenance
    field unchanged. Added clips are compared with their own provenance frames
    in a separate metric, never included in the shared-clip net boundary total.

    A restored boundary has zero net change. This does not count operations,
    measure editing time, establish acceptance, or estimate time saved.
    """
    validate_edl(baseline_edl)
    validate_edl(final_edl)
    baseline_sources = {source["id"]: source for source in baseline_edl["sources"]}
    final_sources = {source["id"]: source for source in final_edl["sources"]}
    if baseline_sources != final_sources:
        raise ValueError("edit effort requires identical source declarations and native fps")

    baseline = {_identity(clip): (index, clip)
                for index, clip in enumerate(baseline_edl["clips"])}
    final = {_identity(clip): (index, clip)
             for index, clip in enumerate(final_edl["clips"])}
    common = baseline.keys() & final.keys()
    for key in common:
        if baseline[key][1]["provenance"] != final[key][1]["provenance"]:
            raise ValueError("shared candidate identity must retain its entire baseline provenance")

    common_reports, added_reports, removed_reports = [], [], []
    common_seconds, added_seconds = Fraction(0), Fraction(0)
    adjusted_count = 0
    for key, (index, clip) in baseline.items():
        if key not in common:
            removed_reports.append({
                "identity": _identity_report(key), "provenance": deepcopy(clip["provenance"]),
                "baseline_clip_id": clip["id"], "baseline_index": index,
                "baseline_source_range": _range(clip),
            })
            continue
        final_index, final_clip = final[key]
        delta, absolute_seconds = _boundary_delta(
            clip["source_in_frame"], clip["source_out_frame"], final_clip,
            frame_rate(baseline_sources[clip["source_id"]]["fps"]))
        common_seconds += absolute_seconds
        adjusted_count += absolute_seconds != 0
        common_reports.append({
            "identity": _identity_report(key), "provenance": deepcopy(clip["provenance"]),
            "baseline_clip_id": clip["id"], "final_clip_id": final_clip["id"],
            "baseline_index": index, "final_index": final_index,
            "baseline_source_range": _range(clip), "final_source_range": _range(final_clip),
            **delta,
        })

    for key, (index, clip) in final.items():
        if key in common:
            continue
        provenance = clip["provenance"]
        delta, absolute_seconds = _boundary_delta(
            provenance["original_in_frame"], provenance["original_out_frame"], clip,
            frame_rate(final_sources[clip["source_id"]]["fps"]))
        added_seconds += absolute_seconds
        added_reports.append({
            "identity": _identity_report(key), "provenance": deepcopy(provenance),
            "final_clip_id": clip["id"], "final_index": index,
            "provenance_source_range": {"in_frame": provenance["original_in_frame"],
                                        "out_frame": provenance["original_out_frame"]},
            "final_source_range": _range(clip), **delta,
        })

    baseline_common = [key for key in baseline if key in common]
    final_common = [key for key in final if key in common]
    return {
        "report_version": REPORT_VERSION, "metric_scope": "final_net_edl_change",
        "baseline_edl_sha256": _digest(baseline_edl), "final_edl_sha256": _digest(final_edl),
        "baseline_clip_count": len(baseline), "final_clip_count": len(final),
        "common_clip_count": len(common), "adjusted_clip_count": adjusted_count,
        "added_clip_count": len(added_reports), "removed_clip_count": len(removed_reports),
        "minimum_moved_clip_count": _minimum_moves(baseline_common, final_common),
        "net_boundary_adjustment_seconds": float(common_seconds),
        "net_boundary_adjustment_seconds_exact": _exact(common_seconds),
        "added_clip_boundary_adjustment_seconds": float(added_seconds),
        "added_clip_boundary_adjustment_seconds_exact": _exact(added_seconds),
        "common_clips": common_reports, "added_clips": added_reports,
        "removed_clips": removed_reports,
        "limitations": [
            "Final net changes between snapshots, not cumulative editing operations or labor.",
            "Boundary seconds sum absolute in/out shifts for common identities at native source fps.",
            "Added-clip boundary changes are separate and relative to their own provenance frames.",
            "Minimum moved clips is an LCS lower bound on common clips; insertions/deletions are excluded.",
            "Profile/framing changes do not count as boundary adjustment.",
            "Active editing time, saved time and content quality/acceptance are not measured.",
        ],
    }
