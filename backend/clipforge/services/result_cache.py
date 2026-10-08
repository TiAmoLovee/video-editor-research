"""成功任务的持久化结果索引；按内容和完整处理配置复用，不共享可写文件。"""
from contextlib import contextmanager
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path, PurePosixPath
import platform
import subprocess
import tempfile
import time
from uuid import UUID, uuid4

from clipforge.analysis.combine import combine_analysis
from clipforge.analysis.model import model_directory, model_spec, verify_model
from clipforge.analysis.options import parse_options
from clipforge.decision.candidates import content_hash
from clipforge.decision.scoring import load_config, validate_scored
from clipforge.decision.validation import validate_candidates
from clipforge.decision.llm import safe_settings_snapshot
from clipforge.storage.jobs import data_root, get_job, job_dir

FORMAT = 2
EXCLUDED = {'cache.json', 'result.zip', 'scoring_usage.json'}


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def fingerprint(source, options, ffmpeg, ffprobe):
    package = Path(__file__).resolve().parents[1]
    schema = package.parents[1] / 'schemas/analysis.schema.json'
    # 源码、Schema、依赖或工具变化均失效，宁可少命中，不复用过期分析。
    code = {str(p.relative_to(package)).replace('\\', '/'): sha256(p)
            for p in sorted(package.rglob('*.py'))}
    spec = model_spec()
    identity = verify_model(model_directory(model_name=spec['name']), spec['name'])
    tools = {}
    for name, executable in [('ffmpeg', ffmpeg), ('ffprobe', ffprobe)]:
        tools[name] = subprocess.run([executable, '-version'], capture_output=True, text=True,
                                     encoding='utf-8', errors='replace', timeout=15, check=True).stdout
    dependencies = {name: version(name) for name in [
        'scenedetect', 'opencv-python-headless', 'numpy', 'webrtcvad-wheels',
        'faster-whisper', 'ctranslate2', 'onnxruntime', 'av', 'tokenizers',
        'opencc-python-reimplemented', 'jieba', 'jsonschema']}
    descriptor = {'format': FORMAT, 'source_sha256': sha256(source),
                  'source_suffix': Path(source).suffix.lower(), 'shot_options': options,
                  'model': identity, 'language': os.environ.get('CLIPFORGE_ASR_LANGUAGE') or None,
                  'implementation': code, 'schema_sha256': sha256(schema),
                  'decision_schema_sha256': {name: sha256(schema.parent / name) for name in
                                            ('candidates.schema.json', 'scored_candidates.schema.json', 'llm_candidates.schema.json')},
                  'scoring_config': load_config().to_dict(),
                  'decision_settings': safe_settings_snapshot(),
                  'dependencies': dependencies, 'tools': tools,
                  'platform': [platform.system(), platform.machine(), platform.python_version()]}
    return hashlib.sha256(canonical(descriptor).encode('utf-8')).hexdigest(), descriptor


def request_fingerprint(source, options_path, ffmpeg, ffprobe):
    options = parse_options(options_path.read_text(encoding='utf-8')) if options_path.is_file() else None
    return fingerprint(source, options, ffmpeg, ffprobe)


@contextmanager
def key_lock(key, timeout=900):
    """由操作系统释放进程锁，进程崩溃不会留下永久占用的锁。"""
    root = data_root() / 'result-cache-v2'
    root.mkdir(parents=True, exist_ok=True)
    with (root / (key + '.lock')).open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        started = time.monotonic()
        while True:
            try:
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() - started >= timeout:
                    raise TimeoutError('等待相同视频的缓存任务超时')
                time.sleep(.1)
        try:
            yield root / (key + '.json')
        finally:
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def safe_path(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError('缓存文件路径无效')
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('缓存文件路径无效')
    target = root.joinpath(*path.parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('缓存文件路径超出任务目录')
    return target


def artifact_files(result):
    files = {name: path for name, path in result['files'].items() if name not in EXCLUDED}
    required = {'media_meta.json', 'normalized_media_meta.json', 'clip_plan.json',
                'shots.json', 'vad.json', 'asr.json', 'analysis.json',
                'candidate_windows.json', 'candidates.json'}
    if not required.issubset(files) or len(set(files.values())) != len(files):
        raise ValueError('缓存结果不完整')
    # 只接受流水线的固定 JSON 和切片；永不复制上传原片或缓存状态文件。
    allowed = required | {'shot_options.json'} | {clip['file'] for clip in result['clips']}
    if set(files) != allowed - ({'shot_options.json'} if 'shot_options.json' not in files else set()):
        raise ValueError('缓存下载清单异常')
    for name, relative in files.items():
        expected = 'clips/' + name if name == 'clip_plan.json' or name.endswith('.mp4') else name
        if relative != expected or Path(name).name != name or '/' in name:
            raise ValueError('缓存文件映射异常')
    return files


def validate_bundle(folder, source, result, descriptor):
    read = lambda name: json.loads((folder / name).read_text(encoding='utf-8'))
    options_path = folder / 'shot_options.json'
    options = parse_options(options_path.read_text(encoding='utf-8')) if options_path.is_file() else None
    if options != descriptor['shot_options']:
        raise ValueError('缓存选区或模式不匹配')
    analysis = combine_analysis(source, read('shots.json'), read('vad.json'), read('asr.json'))
    if analysis != read('analysis.json') or analysis['media']['source_sha256'] != descriptor['source_sha256']:
        raise ValueError('缓存分析身份或内容不匹配')
    windows, candidates = read('candidate_windows.json'), read('candidates.json')
    validate_candidates(windows, analysis)
    validate_scored(candidates, analysis)
    if candidates['windows_sha256'] != content_hash(windows):
        raise ValueError('缓存候选及评分不是同一批结果')
    if 'scoring_config' in descriptor and candidates['scoring']['config'] != descriptor['scoring_config']:
        raise ValueError('缓存评分配置不匹配')
    settings = descriptor.get('decision_settings',{'mode':'rule'})
    llm = candidates['scoring'].get('llm')
    if settings['mode'] == 'llm':
        if (not llm or llm['config'] != settings['config'] or llm['prompt_sha256'] != settings['prompt_sha256']
                or llm['fallback_reason'] is not None):
            raise ValueError('LLM 配置、提示词不匹配或结果已降级')
    elif llm:
        raise ValueError('规则缓存不能复用 LLM 模式')
    plan = read('clips/clip_plan.json')
    if (plan['clips'] != result['clips'] or plan['total_frames'] != result['total_frames']
            or result['clip_count'] != len(result['clips'])):
        raise ValueError('缓存切片清单不一致')


def restore(index, descriptor, source, destination):
    """全部验证后才发布到新任务；任何缺失/损坏抛出异常，由调用方重算。"""
    if not index.is_file():
        return None
    entry = json.loads(index.read_text(encoding='utf-8'))
    if entry['descriptor'] != descriptor or entry['format'] != FORMAT:
        raise ValueError('缓存版本或配置不一致')
    task_id = str(UUID(entry['task_id']))
    job = get_job(task_id)
    if not job or job['status'] != 'SUCCEEDED' or not job['result']:
        raise ValueError('缓存源任务没有完整成功')
    result = job['result']
    files = artifact_files(result)
    if set(entry['files']) != set(files):
        raise ValueError('缓存文件清单不完整')
    origin = job_dir(task_id)
    with tempfile.TemporaryDirectory(prefix='.cache-restore-', dir=destination) as directory:
        staged = Path(directory)
        for name, relative in files.items():
            item = entry['files'][name]
            path = safe_path(origin, relative)
            if item['path'] != relative or path.stat().st_size != item['size']:
                raise ValueError('缓存文件大小或路径改变')
            target = safe_path(staged, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with path.open('rb') as incoming, target.open('xb') as outgoing:
                while block := incoming.read(1024 * 1024):
                    digest.update(block)
                    outgoing.write(block)
            if digest.hexdigest() != item['sha256']:
                raise ValueError('缓存文件校验失败')
        validate_bundle(staged, source, result, descriptor)
        # 用户这次请求设置已经存在，须逐值一致，不能用缓存覆盖当前配置。
        options = destination / 'shot_options.json'
        if options.exists() and json.loads(options.read_text(encoding='utf-8')) != json.loads((staged/'shot_options.json').read_text(encoding='utf-8')):
            raise ValueError('缓存框选设置不一致')
        for name, relative in files.items():
            target = safe_path(destination, relative)
            if target.exists() and name != 'shot_options.json':
                raise ValueError('任务输出已存在，不覆盖')
        for name, relative in files.items():
            if name == 'shot_options.json':
                continue
            target = safe_path(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                safe_path(staged, relative).rename(target)
            except OSError as error:
                # 已发布部分文件时不可假装普通缓存未命中继续覆盖重算。
                raise RuntimeError('缓存文件发布失败，停止本次任务') from error
    return {'task_id': task_id, 'result': result, 'files': files}


def publish(index, descriptor, task_id, source):
    job = get_job(task_id)
    if not job or job['status'] != 'SUCCEEDED':
        raise ValueError('只缓存成功任务')
    folder = job_dir(task_id)
    scoring = json.loads((folder/'candidates.json').read_text(encoding='utf-8'))['scoring']
    if scoring.get('llm',{}).get('fallback_reason'):
        # 下次恢复网络/密钥后应再次尝试模型，不让临时降级永久成为命中结果。
        return
    result = job['result']
    files = artifact_files(result)
    validate_bundle(folder, source, result, descriptor)
    entry = {'format': FORMAT, 'descriptor': descriptor, 'task_id': task_id,
             'files': {name: {'path': relative, 'size': safe_path(folder, relative).stat().st_size,
                              'sha256': sha256(safe_path(folder, relative))}
                       for name, relative in files.items()}}
    temporary = index.with_name(index.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(canonical(entry), encoding='utf-8')
        temporary.replace(index)
    finally:
        temporary.unlink(missing_ok=True)
