"""从既有下载结果预览新分句，不重新识别、不改写原结果或服务任务。"""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from clipforge.analysis.sentences import SENTENCE_PARAMETERS, split_sentences
from clipforge.analysis.validation import validate_analysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asr', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出须为新文件')
    asr = json.loads(args.asr.read_text(encoding='utf-8'))
    analysis = json.loads(args.analysis.read_text(encoding='utf-8'))
    schema = json.loads((Path(__file__).resolve().parents[2] / 'schemas/analysis.schema.json').read_text(encoding='utf-8'))
    validate_analysis(analysis, schema)
    assert asr['words'] == analysis['words'] and asr['sentences'] == analysis['sentences']
    assert asr['analyzer'] == analysis['analyzers']['asr']
    assert asr['media']['normalized_sha256'] == analysis['media']['normalized_sha256']
    original_words = copy.deepcopy(asr['words'])
    sentences = split_sentences(asr['words'])
    assert asr['words'] == original_words
    preview = copy.deepcopy(analysis)
    preview['sentences'] = sentences
    preview['analyzers']['asr']['parameters']['sentence_rules'] = dict(SENTENCE_PARAMETERS)
    validate_analysis(preview, schema)
    assert [w for s in sentences for w in s['word_ids']] == [w['id'] for w in original_words]
    report = {'scope': 'sentence regrouping preview; not a new service task or inference result',
              'input_asr_sha256': hashlib.sha256(args.asr.read_bytes()).hexdigest(),
              'input_analysis_sha256': hashlib.sha256(args.analysis.read_bytes()).hexdigest(),
              'media': analysis['media'], 'model': asr['analyzer']['model'],
              'sentence_rules': dict(SENTENCE_PARAMETERS), 'words_unchanged': True,
              'all_word_references_preserved': True, 'analysis_contract_passed': True,
              'before': asr['sentences'], 'after': sentences,
              'word_count': len(original_words),
              'limits': ['No text correction, new inference, word-alignment acceptance or semantic boundary scoring.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f"PASS: {len(asr['sentences'])} -> {len(sentences)} sentences; words unchanged; {args.output}")


if __name__ == '__main__':
    main()
