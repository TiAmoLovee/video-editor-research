# Fixed small-sample accuracy tools

These Python 3.11 standard-library tools score an **authorized local validation packet**. The packet, media, machine transcripts, private source mapping and human answers are not included in this repository. Importing the modules does not read real data. The tools do not run models, decode media, download files or contact a service.

The packet fixes three new-source excerpts of 20 seconds each, one whole source for shot boundaries, and twenty word-time targets (7/7/6). It supplements the completed 141-clip quality review; it does not replace that review or establish reference accuracy over all thirty sources.

## Score actual exported references

Run from the repository root, substituting your local paths:

~~~text
python tools/week7_validation/accuracy/score_human_accuracy.py --validation-root PACKET_ACCURACY_ROOT --blind-reference PHASE1.json --word-reference PHASE2.json --output NEW_PRIVATE_REPORT.json
~~~

`--validation-root` is required. It points to the accuracy packet root containing:

~~~text
human-reference-pack/manifest.public.json
human-reference-pack/media/...
new-reference-selection.private.json
new-excerpt-asr/summary.private.json
new-excerpt-asr/A01-asr.private.json   # and A02/A03 when those attempts succeeded
rescored-existing-refs-v2/accuracy-summary.public.json
word-timing-release.private.json      # after the gated second-stage release
~~~

With the optional `--campaign-root CAMPAIGN_ROOT`, the whole-source shot prediction is read from `CAMPAIGN_ROOT/sources/VIDEO_ID/shots.json`. Otherwise the tool uses `PACKET_ACCURACY_ROOT/S01-shot-prediction.private.json` if present, then the original path recorded in the authorized private selection. To relocate the packet to another computer, provide `--campaign-root` or retain the standalone prediction beside the packet. No machine-specific drive path is built into these tools.

The prediction must match the exact whole normalized video hash, frame count and original frozen detector configuration. Historical nine-source development results and the new one-source result remain separate; their mixed descriptive result is not presented as thirty-source or wholly unseen accuracy.

Omit `--word-reference` to score the valid first-stage reference while leaving word timing pending. Omit both reference arguments to inspect packet readiness; human metrics remain `null`. Existing output files are never overwritten. Invalid inputs produce a blocked report and exit code 2.

The report contains `public_summary` and private audit fields. Review the former before publishing it; keep individual answers, machine transcripts and `per_word_private` local.

## Preserve the blind first stage

Only after the completed blind transcript and whole-video shot reference have been exported, release the predeclared word targets:

~~~text
python tools/week7_validation/accuracy/release_word_timing_form.py --validation-root PACKET_ACCURACY_ROOT --campaign-root CAMPAIGN_ROOT --blind-reference PHASE1.json --output PACKET_ACCURACY_ROOT/human-reference-pack/word-timing.html
~~~

The generator validates the exact first-stage schema, campaign, manifest hash, three media identities, completion fields, transcript statuses, whole-video shot list and the reviewer's independence declaration. This checks the recorded declaration; it does not independently establish how the human completed the work. The output must sit beside the frozen manifest/media and must be new. A release receipt binds the original first-stage file hash, all twenty targets and the generated HTML.

The full-source ASR files supply the already frozen target identities and short context. Their hashes must match the private selection. `--campaign-root` supports a relocated campaign; otherwise the authorized metadata's original paths are used. All twenty targets are retained. Predicted start/end times are absent from the released HTML. Human time fields and uncertainty start blank; explicit playhead-capture buttons are available.

## Metric rules

- CER uses NFKC/casefold, removes whitespace and Unicode punctuation, and preserves numeral/letter differences. It compares new ASR hypotheses from the exact twenty-second excerpts with actual human text. A partial transcript is reported as a reference gap; `[听不清]` is not scored as characters. Primary pooled CER stays `null` if any fixed excerpt lacks a full reference or successful prediction. An available complete-reference subset is explicitly descriptive.
- A human-confirmed no-speech excerpt has zero reference characters. Machine text becomes insertions. Its per-excerpt CER is undefined, while those insertions remain in a pooled numerator if other references contain characters. A zero reference denominator is never automatically perfect accuracy.
- Shot F1 uses ordered one-to-one internal cut matching at ±3 frames (0.1 seconds at 30 fps). Zero-cut/zero-prediction F1 is undefined; negative-control correctness is reported separately.
- Word-time evaluation uses the original twenty predeclared full-source ASR targets translated to the excerpt timeline. The new excerpt ASR supplies CER hypotheses, not replacement word targets. `mismatch` and `unlocatable` remain within the fixed twenty-target denominator; timestamps are not imputed.
- Measured human uncertainty expands the signed endpoint-error interval. Both endpoints must remain within ±0.3 seconds for a verified pass. Definite failures and threshold-overlap uncertainty stay separate. MAE/max point error describe only measured locatable endpoints, with their denominator shown. Word-time accuracy is distinct from rendered subtitle alignment.

## Synthetic checks

~~~text
python -m unittest discover -s backend/tests -p test_week7_accuracy_tools.py -v
python -m ruff check tools/week7_validation/accuracy backend/tests/test_week7_accuracy_tools.py
~~~

The 24 checks use fictional text, boundaries, word times and temporary packet metadata. They cover partial/missing references, no-speech insertions, zero denominators, uncertainty, wrong identities, missing/duplicate targets, explicit local-root requirements and relocation. They require no real media, model files, human answers, browser, GPU or network.
