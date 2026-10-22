import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getReviews, getReviewRequest, newReviewRequestId, ReviewUnknownError, saveReview } from '../api/reviews';
import { getTranscript, taskSourceUrl } from '../api/transcript';
import { auditDownloadUrl, getAudit } from '../api/audit';
import type { ReviewWrite } from '../types/reviews';
import { taskId } from './fixtures';

const requestId = '00000000-0000-4000-8000-000000000003', digest = 'a'.repeat(64);
const body: ReviewWrite = { candidate_id: 'c1', decision: 'accepted', actor: '小林', reason: null, request_id: requestId, expected_snapshot_sha256: digest };
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }); }
function event() { return { event_id: '00000000-0000-4000-8000-000000000004', task_id: taskId, kind: 'candidate_review',
  candidate_id: 'c1', decision: 'accepted', actor: '小林', actor_scope: 'user_supplied_name', reason: null, created_at: '2026-10-13T00:00:00Z',
  request_id: requestId, candidate_snapshot_sha256: digest, before: { decision: 'undecided', actor: null, reason: null },
  after: { decision: 'accepted', actor: '小林', reason: null } }; }
beforeEach(() => vi.restoreAllMocks());
describe('review API guards', () => {
  it('creates a valid operation UUID on HTTP browsers without randomUUID', () => {
    vi.stubGlobal('crypto', { getRandomValues: (value: Uint8Array) => value.fill(31) });
    try { expect(newReviewRequestId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/); }
    finally { vi.unstubAllGlobals(); }
  });
  it('validates belonging to the requested task before showing records', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({ format_version: 1, task_id: '00000000-0000-4000-8000-000000000002', candidate_snapshot_sha256: digest, items: [] }));
    await expect(getReviews(taskId, new AbortController().signal)).rejects.toThrow('所属任务异常');
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it('treats a response to another write as unknown and does not retry the POST', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({ ...event(), candidate_id: 'wrong-candidate' }, 201));
    await expect(saveReview(taskId, body, new AbortController().signal)).rejects.toBeInstanceOf(ReviewUnknownError);
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it('returns unknown on connection loss and keeps the original request ID in the single write', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('lost connection'));
    await expect(saveReview(taskId, body, new AbortController().signal)).rejects.toBeInstanceOf(ReviewUnknownError);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string)).toEqual(body);
  });
  it('reads only the original operation when reconciling and rejects malformed paths', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(event()));
    await expect(getReviewRequest(taskId, requestId, new AbortController().signal)).resolves.toMatchObject({ request_id: requestId });
    expect(fetcher.mock.calls[0][0]).toBe(`/tasks/${taskId}/reviews/requests/${requestId}`);
    expect(() => getReviewRequest(taskId, '../secret', new AbortController().signal)).toThrow();
    expect(() => saveReview(taskId, { ...body, actor: '   ' }, new AbortController().signal)).toThrow();
    expect(fetcher).toHaveBeenCalledOnce();
  });
});
describe('transcript and audit API guards', () => {
  it('accepts the task analysis sentence timestamps and blocks foreign video/download paths', async () => {
    const transcript = { result_kind: 'measured', media: { duration_seconds: 40, time_reference: 'normalized_video', normalized_sha256: digest },
      sentences: [{ id: 's1', start: 12.5, end: 18, text: '原文' }] };
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(transcript));
    await expect(getTranscript(taskId, `/tasks/${taskId}/files/analysis.json`, new AbortController().signal)).resolves.toMatchObject({ sentences: transcript.sentences });
    await expect(getTranscript(taskId, 'https://other.example/analysis.json', new AbortController().signal)).rejects.toThrow('地址异常');
    expect(() => taskSourceUrl('../secret')).toThrow();
    expect(fetcher).toHaveBeenCalledOnce();
    fetcher.mockResolvedValue(response({ ...transcript, sentences: [{ id: 's1', start: 45, end: 50, text: '越界' }] }));
    await expect(getTranscript(taskId, `/tasks/${taskId}/files/analysis.json`, new AbortController().signal)).rejects.toThrow('格式异常');
  });
  it('supports adopted EDL history without inventing a parent revision or a missing historical actor', async () => {
    const adopted = { ...event(), kind: 'edl_adoption', actor: null, actor_scope: 'unrecorded', revision_id: requestId,
      before: { duration_frames: 0, clips: [] }, after: { duration_frames: 10, clips: [{ candidate_id: 'c1', position: 0, source_in_frame: 5, source_out_frame: 15 }] },
      operations: [{ kind: 'added', candidate_id: 'c1', before: null, after: { candidate_id: 'c1', position: 0, source_in_frame: 5, source_out_frame: 15 } }] };
    const value = { format_version: 1, task_id: taskId, events: [event(), adopted], unrecorded_actor_edit_count: 0, unrecorded_actor_event_count: 1, identity_notice: '操作人自填' };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(value));
    await expect(getAudit(taskId, new AbortController().signal)).resolves.toEqual(value);
    expect(auditDownloadUrl(taskId)).toBe(`/tasks/${taskId}/audit/download`);
    expect(() => auditDownloadUrl('../secret')).toThrow();
  });
});
