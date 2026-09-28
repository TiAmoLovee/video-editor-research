"""真实模型的语音/非语音回归；预期类别由人工指定，不计算识别准确率。"""

import argparse
import hashlib
import json
from pathlib import Path

from clipforge.analysis.asr import transcribe_video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech-video", type=Path, action="append", default=[])
    parser.add_argument("--no-speech-video", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [(p, True) for p in args.speech_video] + [(p, False) for p in args.no_speech_video]
    if not cases or args.output.exists() or any(not p.is_file() for p, _ in cases):
        parser.error("须提供存在的视频，输出须为新文件")
    results = []
    for path, expected_speech in cases:
        result = transcribe_video(path)
        if bool(result["words"]) != expected_speech:
            raise AssertionError(f"{path.name}: 词条是否为空与人工指定预期不符")
        with path.open("rb") as file:
            source_hash = hashlib.file_digest(file, "sha256").hexdigest()
        results.append({"source_name": path.name, "source_sha256": source_hash,
                        "expected_speech": expected_speech, "word_count": len(result["words"]),
                        "media": result["media"], "sentences": result["sentences"],
                        "analyzer": result["analyzer"], "elapsed_seconds": result["elapsed_seconds"]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"scope": "local real-model ASR; no accuracy or Docker acceptance",
                                       "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {len(results)} speech/non-speech regressions -> {args.output}")


if __name__ == "__main__":
    main()
