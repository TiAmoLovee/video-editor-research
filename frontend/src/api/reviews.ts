import { ApiError, validId } from './tasks';
import type { CandidateReview, ReviewDecision, ReviewEvent, ReviewPage, ReviewWrite } from '../types/reviews';

export class ReviewUnknownError extends Error {
  constructor() { super('未能确认复核结果，请先核对这次操作记录。'); }
}

export function newReviewRequestId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // getRandomValues is also available on HTTP deployments where randomUUID is not.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
const decision = (value: unknown): value is ReviewDecision => typeof value === 'string' && ['accepted', 'rejected', 'undecided'].includes(value);
const candidateId = (value: unknown): value is string => typeof value === 'string' && !!value.trim() && value.length <= 200;
const digest = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const timestamp = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value));
export function reviewActorError(value: string): string {
  return !value.trim() || value.trim().length > 80 ? '请填写 1–80 字的操作人姓名。' : '';
}
function reviewsPath(task: string): string {
  if (!validId(task)) throw new Error('任务编号格式异常');
  return `/tasks/${task}/reviews`;
}
function record(value: unknown): value is CandidateReview {
  if (!object(value) || !candidateId(value.candidate_id) || !decision(value.decision)) return false;
  const empty = value.actor === null && value.reason === null && value.created_at === null && value.request_id === null;
  return empty ? value.decision === 'undecided' : typeof value.actor === 'string' && !reviewActorError(value.actor)
    && (value.reason === null || (typeof value.reason === 'string' && value.reason.length <= 2000))
    && timestamp(value.created_at) && typeof value.request_id === 'string' && validId(value.request_id);
}
function event(value: unknown, task: string, request?: string): value is ReviewEvent {
  return object(value) && record(value) && value.task_id === task && value.kind === 'candidate_review'
    && typeof value.event_id === 'string' && validId(value.event_id) && value.actor_scope === 'user_supplied_name'
    && digest(value.candidate_snapshot_sha256) && typeof value.actor === 'string' && timestamp(value.created_at)
    && typeof value.request_id === 'string' && (!request || value.request_id === request)
    && object(value.before) && decision(value.before.decision) && object(value.after) && value.after.decision === value.decision;
}
async function request<T>(path: string, signal: AbortSignal, validate: (value: unknown) => boolean, body?: ReviewWrite): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timer = window.setTimeout(abort, 30000);
  try {
    const response = await fetch(path, { method: body ? 'POST' : 'GET', signal: controller.signal, cache: 'no-store',
      ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
    let value: unknown;
    try { value = await response.json(); }
    catch { throw body ? new ReviewUnknownError() : new Error('复核记录暂时无法读取。'); }
    if (!response.ok) {
      if (body && response.status >= 500) throw new ReviewUnknownError();
      throw new ApiError(object(value) && typeof value.detail === 'string' ? value.detail : '复核记录暂时无法读取。', response.status);
    }
    if ((body && response.status !== 201) || !validate(value)) throw body ? new ReviewUnknownError() : new Error('复核记录格式或所属任务异常。');
    return value as T;
  } catch (error) {
    if (body && !(error instanceof ApiError) && !(error instanceof ReviewUnknownError)) throw new ReviewUnknownError();
    throw error;
  } finally {
    window.clearTimeout(timer);
    signal.removeEventListener('abort', abort);
  }
}

export function getReviews(task: string, signal: AbortSignal): Promise<ReviewPage> {
  return request(reviewsPath(task), signal, value => object(value) && value.format_version === 1 && value.task_id === task
    && digest(value.candidate_snapshot_sha256) && Array.isArray(value.items) && value.items.every(record)
    && new Set(value.items.map(item => item.candidate_id)).size === value.items.length);
}
export function saveReview(task: string, body: ReviewWrite, signal: AbortSignal): Promise<ReviewEvent> {
  if (!candidateId(body.candidate_id) || !decision(body.decision) || reviewActorError(body.actor)
    || (body.reason !== null && (typeof body.reason !== 'string' || body.reason.length > 2000)) || !validId(body.request_id)
    || !digest(body.expected_snapshot_sha256)) throw new Error('复核信息格式异常，请检查姓名和理由。');
  return request(reviewsPath(task), signal, value => event(value, task, body.request_id) && value.candidate_id === body.candidate_id
    && value.decision === body.decision && value.actor === body.actor && value.reason === body.reason
    && value.candidate_snapshot_sha256 === body.expected_snapshot_sha256, body);
}
export function getReviewRequest(task: string, requestId: string, signal: AbortSignal): Promise<ReviewEvent> {
  if (!validId(requestId)) throw new Error('操作编号格式异常');
  return request(`${reviewsPath(task)}/requests/${requestId}`, signal, value => event(value, task, requestId));
}
