# Offline formal review tools

These scripts preserve the validated Week 7 review UI and summary implementation. They use local files and Python 3.11's standard library. They do not upload media, run models, download files, or modify individual answers. No real media, answers, or completed human evaluation is included here; the two reviewers still need to supply their independent answers.

## Build the local review pages

~~~text
python tools/week7_review/build_formal_review_ui.py CAMPAIGN_ROOT/review/G01/manifest.public.json
~~~

Input is a frozen `week7-dual-review-v2` public manifest and its adjacent `media/Rxxxx.mp4` files. The builder verifies media hashes and creates `rater-A.html`, `rater-B.html`, `compare.html`, and instructions beside that manifest. Keep the HTML and media directory together. Reviewers use separate pages and independently export their own `week7-dual-review-result-v2` JSON. Each clip has six questions; `accept` is an independent answer, not derived from the other five. Empty groups are not applicable and do not count as completed human review.

The builder writes generated pages beside the input manifest; use an intended local review package directory. Both Python tools are byte-identical to the validated campaign versions.

## Summarize future answer exports

~~~text
python tools/week7_review/summarize_human_reviews.py --root CAMPAIGN_ROOT --answers ANSWER_EXPORT_DIRECTORY --output NEW_SUMMARY.json
~~~

The campaign root must contain its frozen protocol and source inventory, `source-classification.private.json`, six `review/G01` through `G06` public manifests and media, six `group-XX-mapping.private.json` files, and the 30 source status records. The tool validates exact individual export fields, campaign and batch identities, raw manifest hashes, clip hashes, sample sets, question names, completeness, and reviewer A/B.

Overall rates and kappa remain `null` until all six manifests exist, all 30 sources are terminal, and every nonempty group has one unambiguous complete result from each reviewer. A normal terminal source requires `stage=finished`; an explicit controller failure with `stage=worker_exit` requires an integer return code. An unfinished source in an already published group fails validation. Empty groups need no fabricated answers or compulsory empty exports. In-progress files are reported without counting as complete; other campaigns are reported and ignored. Different complete answers for the same group/reviewer require the user to select one export; identical answer duplicates are deduplicated without choosing by outcome.

The source denominator stays 30. The 150 planned clip slots and actual rateable clips are reported separately. Failed or empty sources never become invented human rejections. The summary pools original paired rows for nominal three-category `accept` Cohen kappa, retains `uncertain`, and reports yes-versus-rest sensitivity, per-question agreement, and each reviewer's boundary, five-item semantic completeness, and accept rates. The 21 development-known and 9 newly added sources are separate descriptive strata; they are not presented as an all-unseen holdout. A new output path is required; existing output and answer files are never overwritten.

## Synthetic regression tests

~~~text
python -m unittest discover -s backend/tests -p test_week7_review_summary.py -v
python -m ruff check tools/week7_review backend/tests/test_week7_review_summary.py
~~~

All 21 tests generate fabricated manifests, source statuses, non-video placeholder bytes, and answers in temporary directories. They use no real campaign data, external libraries, models, browsers, or network services. Paths resolve from the test file, and fixtures use the operating system's temporary directory for Windows/Linux CI.

