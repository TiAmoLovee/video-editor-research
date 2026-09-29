"""已知切点合成视频的工具冒烟测试；不作为人工真实评测成绩。"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from clipforge.config import media_tool
from clipforge.evaluation.shots import match_cuts, prepare, read_json, sha256, write_new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出已存在，请另选路径')
    ffmpeg, ffprobe = media_tool('ffmpeg'), media_tool('ffprobe')
    with tempfile.TemporaryDirectory(prefix='clipforge-evaluation-') as temp:
        folder = Path(temp)
        source = folder / 'known-cuts.mp4'
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error']
        for color in ('black', 'white', 'black'):
            command += ['-f', 'lavfi', '-i', f'color=c={color}:s=160x96:r=30:d=2']
        command += ['-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
                    '-map', '[v]', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)]
        subprocess.run(command, check=True, capture_output=True, timeout=60)
        prepare(source, folder / 'prepared', ffmpeg, ffprobe)
        annotation = read_json(folder / 'prepared/annotation.json')
        prediction = read_json(folder / 'prepared/prediction.json')
        assert annotation['status'] == 'draft' and annotation['cut_frames'] is None
        assert annotation['media']['normalized_sha256'] == sha256(folder / 'prepared/normalized.mp4')
        assert annotation['source_sha256'] == sha256(source)
        assert prediction['cut_frames'] == [60, 120]
        score = match_cuts([60, 120], prediction['cut_frames'], 180)
        assert score['f1'] == 1
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        result = subprocess.run([sys.executable, '-m', 'clipforge.evaluation.shots', 'score',
                                 '--annotation', str(folder / 'prepared/annotation.json'),
                                 '--prediction', str(folder / 'prepared/prediction.json'),
                                 '--output', str(folder / 'must-not-exist.json')],
                                env=env, capture_output=True, timeout=30)
        assert result.returncode == 1 and not (folder / 'must-not-exist.json').exists()
    write_new(args.output, {'scope': 'synthetic known-boundary smoke; not real human F1',
                           'score': score, 'blank_draft_template': True,
                           'media_hashes_checked': True, 'draft_cli_rejected': True})
    print(f'PASS: {args.output}')


if __name__ == '__main__':
    main()
