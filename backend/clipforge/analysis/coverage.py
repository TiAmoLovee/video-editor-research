"""Audit disagreements between detected speech and ASR word coverage.

A detector can mistake music/noise for speech. These intervals therefore mean
"unverified coverage", never confirmed missing dialogue or invented words.
Sentence units are deliberately ignored: a unit may itself span an ASR hole.
"""

import math


def _intervals(rows, duration, name):
    intervals = []
    for row in rows:
        start, end = row["start"], row["end"]
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or start > end or end > duration):
            raise ValueError(f"Invalid {name} interval for ASR coverage audit")
        if start != end:
            intervals.append((float(start), float(end)))
    # Union avoids counting overlapping detector intervals more than once.
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def audit_word_coverage(analysis, *, min_gap_seconds=3.0, min_speech_seconds=3.0):
    """Return long word-free intervals containing independently detected speech.

    This is a conservative candidate safety audit, not transcription repair.
    The existing ``gap > 3 seconds`` policy supplies the default gap threshold.
    A second minimum requires three detected seconds; small VAD padding or
    a single short noise burst must not turn a long genuine pause into a hole.
    Timings are seconds on the normalized media timeline. Inputs are unchanged.
    """
    duration = analysis["media"]["duration_seconds"]
    for name, value in (("duration", duration), ("min_gap_seconds", min_gap_seconds),
                        ("min_speech_seconds", min_speech_seconds)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
                or (name == "min_speech_seconds" and value == 0)):
            raise ValueError(f"Invalid {name} for ASR coverage audit")
    words = analysis.get("words", [])
    # Validate word intervals before sorting; equal starts and overlapping
    # measured words are handled as coverage, without editing their timestamps.
    _intervals(words, duration, "word")
    positive_words = sorted((w for w in words if w["end"] > w["start"]),
                            key=lambda row: (row["start"], row["end"]))
    detectors = {}
    if "speech" in analysis:
        detectors["webrtc_vad"] = _intervals(analysis["speech"], duration, "WebRTC VAD")
    gate = analysis.get("analyzers", {}).get("asr", {}).get("parameters", {}).get("speech_gate")
    if gate is not None and "speech_intervals" in gate:
        detectors["silero_vad"] = _intervals(gate["speech_intervals"], duration, "Silero VAD")

    gaps = []
    cursor, previous_id, has_previous_word = 0.0, None, False
    for word in positive_words:
        start, end = float(word["start"]), float(word["end"])
        if start - cursor > min_gap_seconds:
            gaps.append((cursor, start, previous_id, word.get("id"),
                         "internal" if has_previous_word else "prefix"))
        if end > cursor:
            cursor, previous_id = end, word.get("id")
        has_previous_word = True
    if duration - cursor > min_gap_seconds:
        gaps.append((cursor, float(duration), previous_id, None,
                     "whole_media" if not positive_words else "suffix"))

    suspects = []
    for start, end, left_id, right_id, position in gaps:
        measured = {}
        for detector, intervals in detectors.items():
            overlap = sum(max(0.0, min(end, b) - max(start, a)) for a, b in intervals)
            measured[detector] = {"speech_seconds": round(overlap, 9)}
        speech_seconds = max((row["speech_seconds"] for row in measured.values()), default=0.0)
        if speech_seconds + 1e-9 >= min_speech_seconds:
            suspects.append({
                "start": start, "end": end, "gap_seconds": round(end - start, 9),
                "speech_seconds": speech_seconds,
                "speech_fraction": round(speech_seconds / (end - start), 9),
                "detectors": measured, "left_word_id": left_id, "right_word_id": right_id,
                "position": position, "reason": "detected_speech_without_word_coverage",
            })
    return {
        "version": "asr-word-coverage-v1",
        "status": "suspect_coverage" if suspects else ("clear" if detectors else "unobserved"),
        "parameters": {"min_gap_seconds": min_gap_seconds,
                       "min_speech_seconds": min_speech_seconds,
                       "gap_comparison": "strictly_greater_than"},
        "available_detectors": sorted(detectors), "long_word_gap_count": len(gaps),
        "suspect_interval_count": len(suspects), "suspect_intervals": suspects,
        "interpretation": "VAD/ASR disagreement only; speech, transcription and boundaries remain unverified.",
        "words_changed": False,
    }
