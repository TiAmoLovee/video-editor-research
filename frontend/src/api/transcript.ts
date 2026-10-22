import { ApiError, validId } from './tasks';

export interface TranscriptSentence { id: string; start: number; end: number; text: string }
export interface Transcript { media: { duration_seconds: number; time_reference: 'normalized_video'; normalized_sha256: string }; sentences: TranscriptSentence[] }

export function taskSourceUrl(taskId: string): string {
  if (!validId(taskId)) throw new Error('任务编号格式异常');
  return `/tasks/${taskId}/source`;
}
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function finite(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value); }
function validTranscript(value: unknown): value is Transcript {
  if (!object(value) || value.result_kind !== 'measured' || !object(value.media) || value.media.time_reference !== 'normalized_video'
    || typeof value.media.normalized_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.media.normalized_sha256)
    || !finite(value.media.duration_seconds) || value.media.duration_seconds <= 0 || !Array.isArray(value.sentences)) return false;
  const duration = value.media.duration_seconds, ids = new Set<string>();
  let previous = -1;
  return value.sentences.every(sentence => {
    if (!object(sentence) || typeof sentence.id !== 'string' || !sentence.id || ids.has(sentence.id)
      || typeof sentence.text !== 'string' || !finite(sentence.start) || !finite(sentence.end)
      || sentence.start < previous || sentence.start < 0 || sentence.end <= sentence.start || sentence.end > duration) return false;
    ids.add(sentence.id); previous = sentence.start; return true;
  });
}
export async function getTranscript(taskId: string, download: string, signal: AbortSignal): Promise<Transcript> {
  if (!validId(taskId) || download !== `/tasks/${taskId}/files/analysis.json`) throw new Error('文字时间轴地址异常。');
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timer = window.setTimeout(abort, 30000);
  try {
    const response = await fetch(download, { signal: controller.signal, cache: 'no-store' });
    const value: unknown = await response.json();
    if (!response.ok) throw new ApiError(object(value) && typeof value.detail === 'string' ? value.detail : '文字时间轴暂时无法读取。', response.status);
    if (!validTranscript(value)) throw new Error('文字时间轴格式异常，请刷新记录。');
    return value;
  } finally { window.clearTimeout(timer); signal.removeEventListener('abort', abort); }
}
