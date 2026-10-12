import { ApiError, validId } from './tasks';
import type { ArtifactHistory, EdlPage, EdlRevision, MltRecord, RenderRecord, SavedEdl, SubtitleRecord } from '../types/editing';

export { ApiError };
export class MutationUnknownError extends Error {
  constructor(message = '未能确认操作结果，请先刷新已保存记录，避免重复生成。') { super(message); }
}

function taskPath(task: string): string {
  if (!validId(task)) throw new Error('任务编号格式异常');
  return `/tasks/${task}/edl`;
}
function revisionPath(task: string, revision: string): string {
  if (!validId(revision)) throw new Error('保存版本编号格式异常');
  return `${taskPath(task)}/${revision}`;
}
function versionPath(task: string, revision: string, kind: string, id: string): string {
  if (!validId(id)) throw new Error('导出版本编号格式异常');
  return `${revisionPath(task, revision)}/${kind}/${id}`;
}

// A mutation can outlive a lost browser connection. It is never retried here.
async function request<T>(path: string, signal: AbortSignal, validate: (value: unknown) => boolean, body?: object, mutation = false): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timer = window.setTimeout(abort, mutation ? 120000 : 30000);
  try {
    const response = await fetch(path, {
      method: mutation ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
      ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
    });
    let value: unknown;
    try { value = await response.json(); }
    catch { throw mutation ? new MutationUnknownError() : new Error('保存记录暂时无法读取，请刷新记录。'); }
    if (!response.ok) {
      const detail = object(value) && typeof value.detail === 'string' ? value.detail : '服务暂时无法响应，请检查后台。';
      if (mutation && response.status >= 500) throw new MutationUnknownError(detail + ' 请先刷新已保存记录。');
      throw new ApiError(detail, response.status);
    }
    if ((mutation && response.status !== 201) || !validate(value)) {
      throw mutation ? new MutationUnknownError() : new Error('保存记录格式或所属任务异常，请刷新记录。');
    }
    return value as T;
  } catch (error) {
    if (mutation && !(error instanceof ApiError) && !(error instanceof MutationUnknownError)) throw new MutationUnknownError();
    throw error;
  } finally {
    window.clearTimeout(timer);
    signal.removeEventListener('abort', abort);
  }
}
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function integer(value: unknown): value is number { return Number.isSafeInteger(value) && (value as number) >= 0; }
function identity(value: unknown, task: string, revision?: string): value is Record<string, unknown> {
  return object(value) && value.task_id === task && typeof value.revision_id === 'string' && validId(value.revision_id) && (!revision || value.revision_id === revision);
}
function revisionRecord(value: unknown, task: string, revision?: string): boolean {
  return identity(value, task, revision) && typeof value.created_at === 'string' && integer(value.clip_count) && integer(value.duration_frames);
}
function profile(value: unknown): boolean {
  return object(value) && integer(value.width) && value.width > 0 && integer(value.height) && value.height > 0 && object(value.fps)
    && integer(value.fps.num) && value.fps.num > 0 && integer(value.fps.den) && value.fps.den > 0;
}
function artifact(value: unknown, task: string, revision: string, key: 'subtitle_id' | 'render_id' | 'export_id'): boolean {
  if (!identity(value, task, revision) || typeof value[key] !== 'string' || !validId(value[key] as string) || typeof value.created_at !== 'string') return false;
  if (key === 'subtitle_id') return integer(value.cue_count) && typeof value.font_name === 'string';
  return profile(value.profile) && integer(value.total_frames) && (!value.subtitle_id || (typeof value.subtitle_id === 'string' && validId(value.subtitle_id)));
}

export function listEdls(task: string, offset: number, signal: AbortSignal): Promise<EdlPage> {
  if (!integer(offset)) throw new Error('分页参数格式异常');
  return request(`${taskPath(task)}?limit=10&offset=${offset}`, signal, value => object(value) && Array.isArray(value.items)
    && integer(value.total) && value.offset === offset && value.limit === 10 && typeof value.has_more === 'boolean' && integer(value.unavailable_count)
    && value.items.every(item => revisionRecord(item, task) && object(item) && profile(item.profile) && Array.isArray(item.selected_ids) && item.selected_ids.every(id => typeof id === 'string')));
}
export function getEdl(task: string, revision: string, signal: AbortSignal): Promise<SavedEdl> {
  return request(revisionPath(task, revision), signal, value => object(value) && revisionRecord(value.revision, task, revision)
    && validTimeline(value.edl));
}
function validTimeline(value: unknown): boolean {
  if (!object(value) || !['0.1.0', '0.2.0'].includes(value.schema_version as string) || !profile(value.profile)
    || !integer(value.duration_frames) || !Array.isArray(value.clips) || !value.clips.length || value.clips.length > 128) return false;
  let end = 0;
  const ids = new Set<string>();
  for (const clip of value.clips) {
    if (!object(clip) || typeof clip.id !== 'string' || !clip.id || ids.has(clip.id) || typeof clip.source_id !== 'string' || !clip.source_id
      || !integer(clip.source_in_frame) || !integer(clip.source_out_frame) || clip.source_out_frame <= clip.source_in_frame
      || !integer(clip.timeline_in_frame) || !integer(clip.timeline_out_frame) || clip.timeline_in_frame !== end
      || clip.timeline_out_frame - clip.timeline_in_frame !== clip.source_out_frame - clip.source_in_frame
      || (clip.provenance !== undefined && (!object(clip.provenance) || typeof clip.provenance.candidate_id !== 'string'))) return false;
    ids.add(clip.id);
    end = clip.timeline_out_frame;
  }
  return end === value.duration_frames;
}
export function listArtifacts(task: string, revision: string, signal: AbortSignal): Promise<ArtifactHistory> {
  return request(`${revisionPath(task, revision)}/artifacts`, signal, value => object(value) && integer(value.unavailable_count)
    && Array.isArray(value.subtitles) && value.subtitles.every(item => artifact(item, task, revision, 'subtitle_id'))
    && Array.isArray(value.renders) && value.renders.every(item => artifact(item, task, revision, 'render_id'))
    && Array.isArray(value.mlt_exports) && value.mlt_exports.every(item => artifact(item, task, revision, 'export_id')));
}
export function createEdl(task: string, ids: string[], signal: AbortSignal): Promise<EdlRevision> {
  if (!ids.length || ids.length > 100 || new Set(ids).size !== ids.length || ids.some(id => typeof id !== 'string' || !id.trim() || id.length > 200)) throw new Error('请选择 1–100 个不重复的候选。');
  return request(taskPath(task), signal, value => revisionRecord(value, task), { selected_ids: ids }, true);
}
export const createPortrait = (task: string, revision: string, signal: AbortSignal): Promise<EdlRevision> => request(`${revisionPath(task, revision)}/portrait`, signal, value => revisionRecord(value, task), undefined, true);
export const createSubtitles = (task: string, revision: string, signal: AbortSignal): Promise<SubtitleRecord> => request(`${revisionPath(task, revision)}/subtitles`, signal, value => artifact(value, task, revision, 'subtitle_id'), undefined, true);
export function createMp4(task: string, revision: string, subtitle: string | undefined, signal: AbortSignal): Promise<RenderRecord> {
  const base = subtitle ? versionPath(task, revision, 'subtitles', subtitle) : revisionPath(task, revision);
  return request(`${base}/renders`, signal, value => artifact(value, task, revision, 'render_id') && object(value) && value.subtitle_id === subtitle, undefined, true);
}
export function createMlt(task: string, revision: string, subtitle: string | undefined, signal: AbortSignal): Promise<MltRecord> {
  const base = subtitle ? versionPath(task, revision, 'subtitles', subtitle) : revisionPath(task, revision);
  return request(`${base}/exports/mlt`, signal, value => artifact(value, task, revision, 'export_id') && object(value) && value.subtitle_id === subtitle, undefined, true);
}

function file(base: string, name: string, allowed: readonly string[]): string {
  if (!allowed.includes(name)) throw new Error('下载文件名格式异常');
  return `${base}/files/${name}`;
}
export const edlFileUrl = (task: string, revision: string, name: 'project.edl.json' | 'media-profile.json') => file(revisionPath(task, revision), name, ['project.edl.json', 'media-profile.json']);
export const sourceUrl = (task: string, revision: string) => `${revisionPath(task, revision)}/source`;
export const subtitleFileUrl = (task: string, revision: string, subtitle: string, name: 'captions.srt' | 'captions.ass') => file(versionPath(task, revision, 'subtitles', subtitle), name, ['captions.srt', 'captions.ass']);
export const renderFileUrl = (task: string, revision: string, render: string, name: 'output.mp4' | 'verification.json' | 'source.edl.json') => file(versionPath(task, revision, 'renders', render), name, ['output.mp4', 'verification.json', 'source.edl.json']);
export const mltFileUrl = (task: string, revision: string, exported: string, name: 'project.mlt' | 'source.edl.json') => file(versionPath(task, revision, 'exports/mlt', exported), name, ['project.mlt', 'source.edl.json']);
