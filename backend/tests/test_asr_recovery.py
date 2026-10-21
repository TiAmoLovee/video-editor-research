import copy
from types import SimpleNamespace as Object
import unittest
from unittest.mock import Mock

from clipforge.analysis.asr import collect_words, recover_word_coverage


class FakeAudio:
    def __init__(self, samples):
        self.samples = samples

    def __len__(self):
        return self.samples

    def __getitem__(self, crop):
        return FakeAudio(crop.stop - crop.start)


def word(name, start, end, text="original"):
    return {"id": name, "start": start, "end": end, "text": text, "probability": .97}


def segment(*rows):
    return Object(start=min(row[0] for row in rows), end=max(row[1] for row in rows),
                  text="".join(row[2] for row in rows),
                  words=[Object(start=a, end=b, word=t, probability=.81) for a, b, t in rows])


def model(*responses):
    return Object(detect_language=Mock(return_value=('zh', .99, [('zh', .99)])),
                  transcribe=Mock(side_effect=[
        (iter(rows), Object(language="zh", language_probability=.99)) for rows in responses]))


class ASRRecoveryTests(unittest.TestCase):
    def test_normal_words_and_detector_silence_make_no_extra_call(self):
        base = [word("a", 0, 2), word("b", 10, 12)]
        recognizer = model()
        recovered, report = recover_word_coverage(
            object(), recognizer, base, {"speech_intervals": [{"start": 0, "end": 2}]}, 12)
        recognizer.transcribe.assert_not_called()
        self.assertEqual(recovered, base)
        self.assertEqual(report["status"], "not_needed")

    def test_whole_gap_words_only_preserve_originals_and_record_raw_offset(self):
        base = [word("a", 7, 8), word("b", 16, 17)]
        before = copy.deepcopy(base)
        recognizer = model([segment((4.8, 5.2, "crossing_left"), (5.2, 6.2, "safe"),
                                   (12.8, 13.2, "crossing_right"), (14, 15, "context"))])
        recovered, report = recover_word_coverage(
            FakeAudio(20 * 16000), recognizer, base,
            {"speech_intervals": [{"start": 8, "end": 16}]}, 20, language="zh")
        self.assertEqual(base, before)
        self.assertEqual(len(recovered), 3)
        self.assertEqual((recovered[1]["start"], recovered[1]["end"], recovered[1]["text"]), (8.2, 9.2, "safe"))
        self.assertEqual(recovered[1]["probability"], .81)
        self.assertEqual([row["id"] for row in recovered], ["w000001", "w000002", "w000003"])
        self.assertEqual(report["original_words"], before)
        for original_index, new_index in ((0, 0), (1, 2)):
            self.assertEqual({k: v for k, v in recovered[new_index].items() if k != "id"},
                             {k: v for k, v in base[original_index].items() if k != "id"})
        attempt = report["attempts"][0]
        self.assertEqual((attempt["crop_start"], attempt["crop_end"]), (3, 20))
        self.assertEqual(attempt["raw_segments"][0]["start"], 7.8)
        self.assertEqual(attempt["local_raw_segments"][0]["start"], 4.8)
        self.assertEqual(len(attempt["excluded_words"]), 3)
        self.assertEqual(attempt["excluded_words"][0]["word"]["start"], 7.8)
        self.assertTrue(report["original_raw_segments_preserved"])
        self.assertEqual(recognizer.transcribe.call_args.kwargs["language"], "zh")

    def test_exception_stops_without_retry_or_second_gap(self):
        recognizer = model()
        recognizer.transcribe.side_effect = RuntimeError('decode failure')
        base = [word("a", 0, 1), word("b", 9, 10), word("c", 18, 20)]
        recovered, report = recover_word_coverage(
            FakeAudio(20 * 16000), recognizer, base,
            {"speech_intervals": [{"start": 0, "end": 20}]}, 20)
        self.assertEqual(recognizer.transcribe.call_count, 1)
        self.assertEqual(report["status"], "stopped_with_unverified_coverage")
        self.assertEqual(report["inserted_word_count"], 0)
        self.assertEqual(report["attempts"][0]["error_type"], "RuntimeError")
        self.assertEqual([r["text"] for r in recovered], [r["text"] for r in base])

    def test_empty_decode_stops_and_keeps_coverage_risk(self):
        recognizer = model([])
        base = [word("a", 0, 1), word("b", 9, 10)]
        recovered, report = recover_word_coverage(
            FakeAudio(10 * 16000), recognizer, base,
            {"speech_intervals": [{"start": 0, "end": 10}]}, 10)
        self.assertEqual(recognizer.transcribe.call_count, 1)
        self.assertEqual(report["attempts"][0]["reason"], "no_safe_words_to_insert")
        self.assertEqual(report["coverage_after"]["suspect_interval_count"], 1)

    def test_at_most_two_crops_and_never_redecodes_remaining_gap(self):
        base = [word("a", 0, 1), word("b", 9, 10), word("c", 18, 19),
                word("d", 27, 28), word("e", 39, 40)]
        # Longest gap [28,39] cropped at23, then earliest tied [1,9] at0.
        recognizer = model([segment((10, 11, "first"))], [segment((3, 4, "second"))])
        recovered, report = recover_word_coverage(
            FakeAudio(40 * 16000), recognizer, base,
            {"speech_intervals": [{"start": 0, "end": 40}]}, 40)
        self.assertEqual(recognizer.transcribe.call_count, 2)
        self.assertEqual(report["attempt_count"], 2)
        self.assertEqual(report["inserted_word_count"], 2)
        self.assertEqual(sum(row.get("reason") == "bounded_attempt_limit" for row in report["attempts"]), 2)
        self.assertEqual(report["status"], "partially_recovered")

    def test_long_crop_is_skipped_without_inference(self):
        recognizer = model()
        base = [word("a", 0, 1), word("b", 99, 100)]
        _, report = recover_word_coverage(
            FakeAudio(100 * 16000), recognizer, base,
            {"speech_intervals": [{"start": 0, "end": 100}]}, 100)
        recognizer.transcribe.assert_not_called()
        self.assertEqual(report["attempts"][0]["reason"], "crop_exceeds_bounded_duration")
        self.assertEqual(report["status"], "unresolved")

    def test_crop_clamping_never_inserts_a_shortened_word(self):
        recognizer = model([segment((1, 10.02, "crop_clamped"))])
        base = [word("a", 0, 1), word("b", 9, 10)]
        _, report = recover_word_coverage(
            FakeAudio(10 * 16000), recognizer, base,
            {"speech_intervals": [{"start": 0, "end": 10}]}, 10)
        self.assertEqual(report["inserted_word_count"], 0)
        self.assertEqual(report["attempts"][0]["excluded_words"][0]["reason"], "crop_timing_was_clamped")

    def test_wrong_full_audio_length_fails_before_inference(self):
        recognizer = model()
        with self.assertRaisesRegex(ValueError, "PCM length"):
            recover_word_coverage(FakeAudio(1), recognizer, [word("a", 0, 1), word("b", 9, 10)],
                                  {"speech_intervals": [{"start": 0, "end": 10}]}, 10)
        recognizer.transcribe.assert_not_called()

    def test_zero_time_tokens_after_long_gap_attach_to_next_word(self):
        words, raw, _ = collect_words([segment((1, 2, "before"), (12, 12, "prefix"),
                                              (12, 13, "after"))], 15)
        self.assertEqual([row["text"] for row in words], ["before", "prefixafter"])
        self.assertEqual((words[1]["start"], words[1]["end"]), (12, 13))
        self.assertIsNone(words[1]["probability"])
        self.assertEqual(raw[0]["words"][1]["text"], "prefix")

    def test_missing_span_language_is_detected_without_context_before_decoding(self):
        recognizer = model([segment((6, 7, ' English answer'))])
        recognizer.detect_language.return_value = ('en', .96, [('en', .96)])
        base = [word('a', 7, 8, '中文问题'), word('b', 16, 17, '后续中文')]
        recovered, report = recover_word_coverage(
            FakeAudio(20 * 16000), recognizer, base,
            {'speech_intervals': [{'start': 8, 'end': 16}]}, 20)
        self.assertEqual(len(recognizer.detect_language.call_args.kwargs['audio']), 8 * 16000)
        self.assertEqual(recognizer.transcribe.call_args.kwargs['language'], 'en')
        self.assertEqual(report['attempts'][0]['gap_language_detection']['first_sample'], 8 * 16000)
        self.assertEqual(recovered[1]['text'], ' English answer')

    def test_explicit_user_language_is_honored_without_detection(self):
        recognizer = model([segment((3, 4, '词'))])
        recover_word_coverage(FakeAudio(10 * 16000), recognizer,
                              [word('a', 0, 1), word('b', 9, 10)],
                              {'speech_intervals': [{'start': 0, 'end': 10}]}, 10,
                              language='zh')
        recognizer.detect_language.assert_not_called()
        self.assertEqual(recognizer.transcribe.call_args.kwargs['language'], 'zh')

    def test_language_switch_excludes_foreign_context_and_offsets_core_words(self):
        recognizer = model([segment((2, 3, ' actual answer'))])
        recognizer.detect_language.return_value = ('en', .98, [('en', .98)])
        recovered, report = recover_word_coverage(FakeAudio(20 * 16000), recognizer,
            [word('a', 7, 8, '问题'), word('b', 16, 17, '接下来')],
            {'speech_intervals': [{'start': 8, 'end': 16}]}, 20,
            context_language='zh')
        self.assertEqual(len(recognizer.transcribe.call_args.args[0]), 8 * 16000)
        self.assertEqual(recognizer.transcribe.call_args.kwargs['language'], 'en')
        self.assertEqual((recovered[1]['start'], recovered[1]['end']), (10, 11))
        attempt = report['attempts'][0]
        self.assertEqual((attempt['crop_start'], attempt['crop_end']), (8, 16))
        self.assertEqual(attempt['raw_segments'][0]['start'], 10)
        self.assertTrue(attempt['context_removed_for_language_switch'])

    def test_uncertain_gap_language_stops_before_decode_without_retry(self):
        recognizer = model()
        recognizer.detect_language.return_value = ('en', .3, [('en', .3)])
        _, report = recover_word_coverage(FakeAudio(20 * 16000), recognizer,
            [word('a', 0, 1), word('b', 9, 10), word('c', 18, 20)],
            {'speech_intervals': [{'start': 0, 'end': 20}]}, 20)
        self.assertEqual(recognizer.detect_language.call_count, 1)
        recognizer.transcribe.assert_not_called()
        self.assertEqual(report['status'], 'stopped_with_unverified_coverage')
        self.assertEqual(report['inserted_word_count'], 0)

    def test_short_terminal_utterance_can_recover_without_second_gate(self):
        base = [word('a', 8, 9, '3 2')]
        original = copy.deepcopy(base)
        recognizer = model([segment((5.2, 5.7, ' 1'))])
        recovered, report = recover_word_coverage(FakeAudio(15 * 16000), recognizer, base,
            {'speech_intervals': [{'start': 8, 'end': 9.5}]}, 15)
        self.assertEqual(base, original)
        self.assertEqual(report['coverage_before']['suspect_interval_count'], 0)
        self.assertEqual(report['inserted_word_count'], 1)
        self.assertEqual(recovered[1]['text'], ' 1')
        self.assertAlmostEqual(recovered[1]['start'], 9.2)
        self.assertFalse(recognizer.transcribe.call_args.kwargs['vad_filter'])
        self.assertNotIn('vad_parameters', recognizer.transcribe.call_args.kwargs)
        recognizer.detect_language.assert_not_called()
        self.assertEqual(report['status'], 'recovered')

    def test_vad_padding_or_later_unrelated_activity_cannot_trigger_suffix_decode(self):
        for intervals in ([{'start': 8, 'end': 9.2}], [{'start': 9.3, 'end': 9.8}]):
            recognizer = model()
            recovered, report = recover_word_coverage(object(), recognizer,
                [word('a', 8, 9)], {'speech_intervals': intervals}, 15)
            recognizer.transcribe.assert_not_called()
            recognizer.detect_language.assert_not_called()
            self.assertEqual(report['status'], 'not_needed')

    def test_empty_terminal_decode_retains_specific_unresolved_gap(self):
        recognizer = model([])
        _, report = recover_word_coverage(FakeAudio(15 * 16000), recognizer,
            [word('a', 8, 9)], {'speech_intervals': [{'start': 8, 'end': 9.5}]}, 15)
        self.assertEqual(report['status'], 'stopped_with_unverified_coverage')
        self.assertEqual(report['remaining_recovery_gaps'][0]['reason'], 'terminal_speech_beyond_last_word')
        self.assertEqual(recognizer.transcribe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
