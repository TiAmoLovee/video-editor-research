# Independent subtitle timing comparison

This standard-library tool compares explicitly supplied human speech ranges with
decoded caption-transition evidence. Importing it reads no campaign data and
starts no media tools, models, browsers, servers, or network requests. Private
media, reference answers, and prepared measurement packets are not in this repository.

Run from a source checkout with Python 3.11:

~~~text
python tools/week7_validation/subtitle_sync/score_subtitle_sync.py --root AUTHORIZED_PACKET_DIRECTORY --observations HUMAN_OBSERVATIONS.json --output NEW_PRIVATE_RESULT.json
~~~

The packet directory must contain the original `manifest.private.json`,
`caption-boundaries.private.json`, and the listening audio files identified by
the manifest. The human input uses `week7-subtitle-speech-observations-v1` and
binds the campaign, raw manifest SHA256, ten fixed target identifiers, and each
listening file's SHA256. A named observer and explicit independent audio
annotation confirmation are required. Every one of the twenty endpoints must
be marked measured, unlocatable, or cropped; missing inputs are never filled in.

Measured local audio ranges are translated to the output timeline using the
packet's 48 kHz sample-clock offset. For ordinary transitions, the signed error
interval is `[caption earliest − speech latest, caption latest − speech earliest]`.
The exact `3/10` second threshold passes only when that entire interval lies
within the tolerance; an interval wholly outside fails, and a crossing interval
remains uncertain. These are observational brackets, not confidence intervals.

All ten cues and twenty endpoints remain in the results. The two file-edge
caption transitions have no adjacent outside-file frames and are explicitly
retained as unbracketed rather than automatically passing. Original-source
context for the two edge cues can yield human speech times before output zero
or after output EOF. It does not manufacture an outside-file caption frame.
Unlocatable or cropped speech likewise remains unmeasured. The three older
calibration cues are separate evidence and are not counted as new references.

The result is **private**: it includes the observer and per-cue human timing
ranges, alongside input/tool fingerprints. Do not upload it as an aggregate
report. Input files are unchanged; the output must name a new file and is opened
exclusively. Only an explicit human submission can produce timing measurements.

The comparison math is preserved from the prepared offline packet's scorer;
this portable CLI additionally requires an explicit `--root` path. Report dates
and the scorer's own SHA256 naturally differ between copies or runs.

Synthetic checks:

~~~text
python -m unittest discover -s backend/tests -p test_week7_subtitle_sync.py -v
python -m ruff check tools/week7_validation/subtitle_sync backend/tests/test_week7_subtitle_sync.py
~~~

The eight tests use fabricated JSON and non-audio placeholder bytes only. They
cover exact threshold decisions, all fixed endpoints and edge limits, missing
or duplicate targets, incomplete human input, unlocatable speech, fingerprint
changes, the required packet path, and exclusive output/input preservation.
