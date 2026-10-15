import { ApiError, validId } from './tasks';
import type { AuditPage } from '../types/audit';

function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const id = (value: unknown): value is string => typeof value === 'string' && !!value.trim() && value.length <= 200;
const decision = (value: unknown) => typeof value === 'string' && ['accepted', 'rejected', 'undecided'].includes(value);
function clip(value: unknown): boolean { return object(value) && id(value.candidate_id) && integer(value.position)
  && integer(value.source_in_frame) && integer(value.source_out_frame) && value.source_out_frame > value.source_in_frame; }
function timeline(value: unknown): boolean { return object(value) && integer(value.duration_frames) && Array.isArray(value.clips) && value.clips.every(clip); }
function state(value: unknown): boolean { return object(value) && decision(value.decision)
  && (value.actor === null || (typeof value.actor === 'string' && !!value.actor.trim() && value.actor.length <= 80))
  && (value.reason === null || (typeof value.reason === 'string' && value.reason.length <= 2000)); }
function validAudit(value: unknown, task: string): boolean {
  return object(value) && value.format_version === 1 && value.task_id === task && integer(value.unrecorded_actor_edit_count)
    && (value.unrecorded_actor_event_count === undefined || integer(value.unrecorded_actor_event_count))
    && typeof value.identity_notice === 'string' && Array.isArray(value.events) && value.events.every(event => {
      if (!object(event) || event.task_id !== task || typeof event.event_id !== 'string' || !validId(event.event_id)
        || typeof event.created_at !== 'string' || !Number.isFinite(Date.parse(event.created_at))
        || typeof event.candidate_snapshot_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(event.candidate_snapshot_sha256)
        || !['user_supplied_name', 'unrecorded'].includes(event.actor_scope as string)
        || (event.actor_scope === 'user_supplied_name' ? typeof event.actor !== 'string' || !event.actor.trim() || event.actor.length > 80 : event.actor !== null)) return false;
      if (event.kind === 'candidate_review') return id(event.candidate_id) && decision(event.decision) && state(event.before) && state(event.after)
        && typeof event.request_id === 'string' && validId(event.request_id);
      return ['manual_edit', 'edl_adoption'].includes(event.kind as string) && typeof event.revision_id === 'string' && validId(event.revision_id)
        && (event.kind === 'edl_adoption' || (typeof event.parent_revision_id === 'string' && validId(event.parent_revision_id))) && timeline(event.before) && timeline(event.after)
        && Array.isArray(event.operations) && event.operations.every(operation => object(operation) && (operation.kind === 'reordered'
          ? Array.isArray(operation.before) && operation.before.every(id) && Array.isArray(operation.after) && operation.after.every(id)
          : ['boundary_changed', 'added', 'removed'].includes(operation.kind as string) && id(operation.candidate_id)
            && (operation.before === null || clip(operation.before)) && (operation.after === null || clip(operation.after))));
    });
}
function auditPath(task: string): string { if (!validId(task)) throw new Error('任务编号格式异常'); return `/tasks/${task}/audit`; }
export const auditDownloadUrl = (task: string) => `${auditPath(task)}/download`;
export async function getAudit(task: string, signal: AbortSignal): Promise<AuditPage> {
  const path = auditPath(task), controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true }); if (signal.aborted) abort();
  const timer = window.setTimeout(abort, 30000);
  try {
    const response = await fetch(path, { signal: controller.signal, cache: 'no-store' });
    const value: unknown = await response.json();
    if (!response.ok) throw new ApiError(object(value) && typeof value.detail === 'string' ? value.detail : '操作记录暂时无法读取。', response.status);
    if (!validAudit(value, task)) throw new Error('操作记录格式或所属任务异常，请刷新记录。');
    return value as AuditPage;
  } finally { window.clearTimeout(timer); signal.removeEventListener('abort', abort); }
}
