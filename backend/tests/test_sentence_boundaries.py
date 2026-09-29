import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from clipforge.analysis.sentences import SENTENCE_PARAMETERS, split_sentences


def tokens(texts):
    return [{"id": f"w{i:06d}", "text": text, "start": i * .2, "end": (i + 1) * .2,
             "probability": .9} for i, text in enumerate(texts)]


class SentenceBoundaryTests(unittest.TestCase):
    def verify(self, words, sentences):
        self.assertEqual([k for s in sentences for k in s['word_ids']], [w['id'] for w in words])
        by_id = {w['id']: w for w in words}
        for sentence in sentences:
            members = [by_id[k] for k in sentence['word_ids']]
            self.assertEqual(sentence['start'], min(w['start'] for w in members))
            self.assertEqual(sentence['end'], max(w['end'] for w in members))
            self.assertEqual(sentence['text'], ''.join(w['text'] for w in members).strip())

    def assert_not_split(self, words, sentences, phrase):
        text = ''.join(w['text'] for w in words)
        self.assertIn(phrase, text)
        offsets, count = {}, 0
        for word in words:
            count += len(word['text'])
            offsets[word['id']] = count
        cuts = {offsets[s['word_ids'][-1]] for s in sentences[:-1]}
        start = text.find(phrase)
        while start >= 0:
            self.assertFalse(cuts.intersection(range(start + 1, start + len(phrase))))
            start = text.find(phrase, start + 1)

    def test_real_base_and_small_words_preserved_and_known_compounds_protected(self):
        cases = json.loads((Path(__file__).parent / 'fixtures/sentence_test3.json').read_text(encoding='utf-8'))
        for label, case in cases.items():
            with self.subTest(model=label):
                words = case['words']
                original = copy.deepcopy(words)
                result = split_sentences(words)
                self.verify(words, result)
                self.assertEqual(words, original)
                for phrase in ('智能', '抽空'):
                    self.assert_not_split(words, result, phrase)
                if label == 'small':
                    self.assert_not_split(words, result, '小伙伴')
                    self.assertTrue(any('抽空跟我们聊两句' in s['text'] for s in result))

    def test_length_limit_cannot_cut_compound(self):
        words = tokens(['学习'] * 24 + ['智', '能', '技术。'])
        result = split_sentences(words)
        self.verify(words, result)
        self.assert_not_split(words, result, '智能')

    def test_long_pause_inside_recognized_word_cannot_split_word(self):
        words = tokens(['欢迎', '小伙', '伴', '参加。'])
        for w in words[2:]:
            w['start'] += 2
            w['end'] += 2
        result = split_sentences(words)
        self.verify(words, result)
        self.assert_not_split(words, result, '小伙伴')

    def test_split_closing_quote_stays_with_sentence(self):
        words = tokens(['“你好', '。', '”', '欢迎。'])
        result = split_sentences(words)
        self.assertEqual([s['text'] for s in result], ['“你好。”', '欢迎。'])
        self.verify(words, result)

    def test_english_word_and_decimal_are_not_split(self):
        words = tokens([' Open', 'AI', ' costs ', '3', '.', '14', ' dollars.'])
        with patch.dict(SENTENCE_PARAMETERS, {'max_characters': 5}):
            result = split_sentences(words)
        self.assert_not_split(words, result, 'OpenAI')
        self.assert_not_split(words, result, '3.14')
        self.verify(words, result)

    def test_long_model_token_is_preserved_even_if_over_soft_limit(self):
        words = tokens(['长' * 80, '结束。'])
        result = split_sentences(words)
        self.verify(words, result)
        self.assertEqual(result[0]['word_ids'], [words[0]['id']])
        self.assertEqual(len(result[0]['text']), 80)

    def test_nearby_pause_can_extend_soft_character_limit(self):
        words = tokens(['学习'] * 27 + ['继续。'])
        words[-1]['start'] += .2
        words[-1]['end'] += .2
        result = split_sentences(words)
        self.assertEqual(len(result[0]['text']), 54)
        self.verify(words, result)

    def test_empty_input_needs_no_tokenizer(self):
        with patch('clipforge.analysis.sentences._tokenizer') as tokenizer:
            self.assertEqual(split_sentences([]), [])
        tokenizer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
