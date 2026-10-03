"""重放已评分候选；默认不调用外部服务，显式 --enable-remote 才读取环境密钥。"""
import argparse
import json
from pathlib import Path

from clipforge.decision.candidates import ROOT, content_hash
from clipforge.decision.llm import LLMConfig, apply_llm


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('analysis',type=Path)
    parser.add_argument('rule_candidates',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=ROOT/'config/scoring/llm-v1.json')
    parser.add_argument('--enable-remote',action='store_true')
    args=parser.parse_args()
    try:
        if args.output.exists() or args.output.resolve() in {p.resolve() for p in (args.analysis,args.rule_candidates,args.config)}:
            raise ValueError('输出已存在或指向输入，请选择新文件')
        cfg=LLMConfig(**json.loads(args.config.read_text(encoding='utf-8-sig')))
        prompt=(ROOT/f'prompts/scoring/{cfg.version}.txt').read_text(encoding='utf-8')
        rule=json.loads(args.rule_candidates.read_text(encoding='utf-8-sig'))
        if rule.get('schema_version') != '0.1.0-draft.2':
            raise ValueError('输入须为 draft.2 规则基线')
        result=apply_llm(rule,json.loads(args.analysis.read_text(encoding='utf-8-sig')),
                         {'mode':'llm','config':cfg.to_dict(),'prompt':prompt,'prompt_sha256':content_hash(prompt)},
                         api_key=None if args.enable_remote else '')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    except (OSError,ValueError,TypeError):
        parser.exit(1,'FAIL: 输入、配置或输出路径无效；未覆盖已有文件。\n')
    llm=result['scoring']['llm']
    print(f"PASS: {result['candidate_count']} candidates; effective={llm['effective']}; fallback={llm['fallback_reason']}; requests={llm['usage']['requests']}")


if __name__=='__main__':main()
