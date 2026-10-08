"""Shared evidence for interrogative cues embedded in conditional expressions."""
import re


def question_context(words, anchor):
    """A conservative generation/review hint, never a semantic verdict."""
    index = anchor['word_index']
    before = ''.join(''.join(w['text'].split()) for w in words[max(0,index-10):index])[-16:]
    following = ''.join(''.join(w['text'].split()) for w in words[index:index+30])[:48]
    if anchor['kind'] != 'question_prompt':
        return None
    prefix = re.search(r'(?:如果|假如|要是|倘若|当)$',before)
    suffix = re.search(r'(?:的时候|的话)',following[:32])
    interrogative = bool(re.search(r'[?？]|吗|呢',following[:suffix.start()] if suffix else following))
    if prefix or (suffix and not interrogative):
        return dict(code='question_cue_in_condition',before=before,following=following,
                    reason='提问词附近有条件表达，可能只是句内成分，不能据此认定新问答开始')
    return None
