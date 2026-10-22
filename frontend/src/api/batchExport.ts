import { ApiError, validId } from './tasks';
import { MutationUnknownError } from './editing';
import { newReviewRequestId } from './reviews';
import type { BatchExportHistory, BatchExportRecord } from '../types/batchExport';

export { ApiError, MutationUnknownError };
// Use the same HTTP-compatible UUID implementation as explicit review writes.
export const newBatchExportRequestId = newReviewRequestId;
function base(task: string, revision: string): string {
  if (!validId(task) || !validId(revision)) throw new Error('任务或保存版本编号格式异常');
  return `/tasks/${task}/edl/${revision}/exports/batch`;
}
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
const integer = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && (value as number) >= min && (value as number) <= max;
const hash = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
function record(value: unknown, task: string, revision: string, requestId?: string): boolean {
  if (!object(value) || value.format_version !== 1 || value.artifact_kind !== 'edl_batch_clip_export'
    || value.task_id !== task || value.revision_id !== revision || typeof value.export_id !== 'string' || !validId(value.export_id)
    || value.request_id !== value.export_id || (requestId && value.request_id !== requestId)
    || typeof value.created_at !== 'string' || !Number.isFinite(Date.parse(value.created_at))
    || !integer(value.clip_count, 1, 100) || !integer(value.total_frames, 1, 18000)
    || !integer(value.output_bytes, 1, 512 * 1024 * 1024) || !hash(value.edl_file_sha256)
    || !hash(value.edl_content_sha256) || !hash(value.source_sha256)
    || !object(value.profile) || !integer(value.profile.width, 1) || !integer(value.profile.height, 1)
    || !object(value.profile.fps) || !integer(value.profile.fps.num, 1) || !integer(value.profile.fps.den, 1)
    || !object(value.files) || Object.keys(value.files).length !== 3) return false;
  return ['clips.zip', 'source.edl.json', 'manifest.json'].every(name => hash((value.files as Record<string, unknown>)[name]));
}

// A browser timeout does not mean the server stopped. No POST is retried here.
async function request<T>(path: string, signal: AbortSignal, validate: (value: unknown) => boolean, body?: object): Promise<T> {
  const mutation = body !== undefined;
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timer = window.setTimeout(abort, 120000);
  try {
    const response = await fetch(path, { method: mutation ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
      ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
    let value: unknown;
    try { value = await response.json(); }
    catch { throw mutation ? new MutationUnknownError('批量导出结果尚未确认，请刷新本版本的批量导出记录。') : new Error('批量导出记录格式异常，请刷新记录。'); }
    if (!response.ok) {
      const detail = object(value) && typeof value.detail === 'string' ? value.detail : '批量导出服务暂不可用。';
      if (mutation && (response.status >= 500 || response.status === 409)) throw new MutationUnknownError(detail);
      throw new ApiError(detail, response.status);
    }
    if ((mutation && response.status !== 201) || !validate(value)) {
      throw mutation ? new MutationUnknownError('批量导出结果所属版本或请求异常，请刷新记录。') : new Error('批量导出记录所属版本或格式异常。');
    }
    return value as T;
  } catch (error) {
    if (mutation && !(error instanceof ApiError) && !(error instanceof MutationUnknownError)) throw new MutationUnknownError('连接中断，批量导出结果尚未确认，请刷新本版本记录。');
    throw error;
  } finally {
    window.clearTimeout(timer);
    signal.removeEventListener('abort', abort);
  }
}
export function createBatchExport(task: string, revision: string, requestId: string, signal: AbortSignal): Promise<BatchExportRecord> {
  if (!validId(requestId)) throw new Error('批量请求编号格式异常');
  return request(base(task, revision), signal, value => record(value, task, revision, requestId), { request_id: requestId });
}
export function listBatchExports(task: string, revision: string, signal: AbortSignal): Promise<BatchExportHistory> {
  return request(base(task, revision), signal, value => object(value) && integer(value.unavailable_count)
    && Array.isArray(value.items) && value.items.length <= 200 && value.items.every(item => record(item, task, revision))
    && new Set(value.items.map(item => (item as BatchExportRecord).export_id)).size === value.items.length);
}
export function batchExportFileUrl(task: string, revision: string, exported: string, filename: 'clips.zip' | 'source.edl.json' | 'manifest.json'): string {
  if (!validId(exported) || !['clips.zip', 'source.edl.json', 'manifest.json'].includes(filename)) throw new Error('批量下载编号或文件名格式异常');
  return `${base(task, revision)}/${exported}/files/${filename}`;
}
