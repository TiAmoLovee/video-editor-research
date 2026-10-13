import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api/editing';
import { taskId } from './fixtures';

const revisionId = '11111111-1111-4111-8111-111111111111';
const subtitleId = '22222222-2222-4222-8222-222222222222';
const record = { task_id: taskId, revision_id: revisionId, created_at: '2026-10-12T08:00:00Z', clip_count: 2, duration_frames: 300 };
const reply = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const signal = () => new AbortController().signal;
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('saved export API', () => {
  it('sends the user supplied actor for adoption and edits without inventing an identity', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(reply(record, 201)).mockResolvedValueOnce(reply({ ...record, revision_id: subtitleId }, 201));
    vi.stubGlobal('fetch', fetcher);
    await api.createEdl(taskId, ['first'], signal(), ' 小明 ');
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ selected_ids: ['first'], actor: '小明' });
    const clips = [{ candidate_id: 'first', source_in_frame: 1, source_out_frame: 20 }];
    await api.saveEdits(taskId, revisionId, clips, signal(), ' 小明 ');
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ clips, editing_elapsed_ms: null, actor: '小明' });
    expect(() => api.saveEdits(taskId, revisionId, clips, signal(), ' ')).toThrow('姓名');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('preserves explicit selection order and sends one POST', async () => {
    const fetcher = vi.fn().mockResolvedValue(reply(record, 201)); vi.stubGlobal('fetch', fetcher);
    expect(await api.createEdl(taskId, ['second', 'first'], signal())).toEqual(record);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith(`/tasks/${taskId}/edl`, expect.objectContaining({ method: 'POST', cache: 'no-store', body: '{"selected_ids":["second","first"]}' }));
  });
  it('rejects duplicate and empty choices before a request', () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    expect(() => api.createEdl(taskId, [], signal())).toThrow();
    expect(() => api.createEdl(taskId, ['same', 'same'], signal())).toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('saves exact source frames in the requested order as one child-version POST without inventing edit duration', async () => {
    const child = { ...record, revision_id: subtitleId };
    const fetcher = vi.fn().mockResolvedValue(reply(child, 201)); vi.stubGlobal('fetch', fetcher);
    const clips = [{ candidate_id: 'second', source_in_frame: 601, source_out_frame: 1007 },
      { candidate_id: 'first', source_in_frame: 1, source_out_frame: 599 }];
    expect(await api.saveEdits(taskId, revisionId, clips, signal())).toEqual(child);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith(`/tasks/${taskId}/edl/${revisionId}/edits`, expect.objectContaining({
      method: 'POST', body: JSON.stringify({ clips, editing_elapsed_ms: null }),
    }));
  });
  it('rejects empty, repeated, fractional and reversed edit ranges before sending', () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    const clip = { candidate_id: 'first', source_in_frame: 0, source_out_frame: 20 };
    for (const clips of [[], [clip, clip], [{ ...clip, source_in_frame: 0.5 }], [{ ...clip, source_in_frame: -1 }],
      [{ ...clip, source_out_frame: 0 }], [{ ...clip, source_out_frame: Number.MAX_SAFE_INTEGER + 1 }],
      Array.from({ length: 101 }, (_, index) => ({ ...clip, candidate_id: `clip-${index}` }))]) {
      expect(() => api.saveEdits(taskId, revisionId, clips, signal())).toThrow('整数帧范围');
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('does not retry an edit POST whose result is unknown', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('connection lost')); vi.stubGlobal('fetch', fetcher);
    await expect(api.saveEdits(taskId, revisionId, [{ candidate_id: 'first', source_in_frame: 1, source_out_frame: 20 }], signal()))
      .rejects.toBeInstanceOf(api.MutationUnknownError);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('does not accept the parent version as a successful immutable edit receipt', async () => {
    const fetcher = vi.fn().mockResolvedValue(reply(record, 201)); vi.stubGlobal('fetch', fetcher);
    await expect(api.saveEdits(taskId, revisionId, [{ candidate_id: 'first', source_in_frame: 1, source_out_frame: 20 }], signal()))
      .rejects.toBeInstanceOf(api.MutationUnknownError);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('reads optional objective edit reports and rejects invalid measurements while preserving legacy records', async () => {
    const old = { revision: record, edl: { schema_version: '0.1.0', duration_frames: 300,
      profile: { width: 1920, height: 1080, fps: { num: 30000, den: 1001 } }, clips: [{ id: 'clip', source_id: 'source',
        source_in_frame: 1, source_out_frame: 301, timeline_in_frame: 0, timeline_out_frame: 300, provenance: { candidate_id: 'first' } }] }, manifest: {} };
    const effort = { net_boundary_adjustment_seconds: 1001 / 30000, adjusted_clip_count: 1, added_clip_count: 0,
      removed_clip_count: 0, minimum_moved_clip_count: 0 };
    const edited = { ...old, edit_record: { baseline_revision_id: revisionId, parent_revision_id: subtitleId,
      editing_elapsed_ms: null, effort, step_effort: effort } };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(reply(old)).mockResolvedValueOnce(reply(edited))
      .mockResolvedValueOnce(reply({ ...edited, edit_record: { ...edited.edit_record, effort: { ...effort, net_boundary_adjustment_seconds: -1 } } })));
    expect((await api.getEdl(taskId, revisionId, signal())).edit_record).toBeUndefined();
    expect((await api.getEdl(taskId, revisionId, signal())).edit_record).toEqual(edited.edit_record);
    await expect(api.getEdl(taskId, revisionId, signal())).rejects.toThrow('格式');
  });
  it('accepts exact saved candidate projections and rejects ambiguous or fractional projections', async () => {
    const saved = { revision: record, edl: { schema_version: '0.1.0', duration_frames: 300,
      profile: { width: 1920, height: 1080, fps: { num: 30000, den: 1001 } }, clips: [{ id: 'clip', source_id: 'source',
        source_in_frame: 1, source_out_frame: 301, timeline_in_frame: 0, timeline_out_frame: 300, provenance: { candidate_id: 'first' } }] }, manifest: {} };
    const projection = { candidate_id: 'extra', source_in_frame: 1001, source_out_frame: 1499 };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(reply({ ...saved, available_clips: [projection] }))
      .mockResolvedValueOnce(reply({ ...saved, available_clips: [projection, projection] }))
      .mockResolvedValueOnce(reply({ ...saved, available_clips: [{ ...projection, source_in_frame: 1001.5 }] })));
    expect((await api.getEdl(taskId, revisionId, signal())).available_clips).toEqual([projection]);
    await expect(api.getEdl(taskId, revisionId, signal())).rejects.toThrow('格式');
    await expect(api.getEdl(taskId, revisionId, signal())).rejects.toThrow('格式');
  });
  it('rejects malformed audit identity or timestamp before rendering saved edits', async () => {
    const effort = { net_boundary_adjustment_seconds: 0, adjusted_clip_count: 0, added_clip_count: 0,
      removed_clip_count: 0, minimum_moved_clip_count: 0 };
    const saved = { revision: record, edl: { schema_version: '0.1.0', duration_frames: 300,
      profile: { width: 160, height: 90, fps: { num: 30, den: 1 } }, clips: [{ id: 'clip', source_id: 'source',
        source_in_frame: 0, source_out_frame: 300, timeline_in_frame: 0, timeline_out_frame: 300 }] },
      edit_record: { baseline_revision_id: revisionId, parent_revision_id: subtitleId, editing_elapsed_ms: null,
        effort, step_effort: effort, actor: '测试编辑者', created_at: '2026-10-13T07:00:00Z' } };
    const fetcher = vi.fn().mockResolvedValueOnce(reply(saved)); vi.stubGlobal('fetch', fetcher);
    expect((await api.getEdl(taskId, revisionId, signal())).edit_record?.actor).toBe('测试编辑者');
    for (const patch of [{ actor: {} }, { actor: ' ' }, { actor: 'a'.repeat(81) }, { created_at: {} }, { created_at: 'invalid' }]) {
      fetcher.mockResolvedValueOnce(reply({ ...saved, edit_record: { ...saved.edit_record, ...patch } }));
      await expect(api.getEdl(taskId, revisionId, signal())).rejects.toThrow('格式');
    }
  });
  it('marks lost creation responses as unknown without retry', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('connection lost')); vi.stubGlobal('fetch', fetcher);
    await expect(api.createEdl(taskId, ['one'], signal())).rejects.toBeInstanceOf(api.MutationUnknownError);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('gives synchronous exports 120 seconds and never repeats a timed-out POST', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    })); vi.stubGlobal('fetch', fetcher);
    const outcome = api.createEdl(taskId, ['one'], signal()).catch(error => error);
    await vi.advanceTimersByTimeAsync(119999);
    expect((fetcher.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await outcome).toBeInstanceOf(api.MutationUnknownError);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('rejects another task or wrong subtitle in creation receipts', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(reply({ ...record, task_id: revisionId }, 201))
      .mockResolvedValueOnce(reply({ ...record, render_id: subtitleId, subtitle_id: revisionId, total_frames: 300, profile: { width: 1920, height: 1080, fps: { num: 30, den: 1 } } }, 201));
    vi.stubGlobal('fetch', fetcher);
    await expect(api.createEdl(taskId, ['one'], signal())).rejects.toBeInstanceOf(api.MutationUnknownError);
    await expect(api.createMp4(taskId, revisionId, subtitleId, signal())).rejects.toBeInstanceOf(api.MutationUnknownError);
  });
  it('keeps backend compatibility errors readable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ detail: 'MLT 暂不支持含音轨的单帧片段，请使用同一 EDL 导出 MP4。' }, 409)));
    await expect(api.createMlt(taskId, revisionId, undefined, signal())).rejects.toMatchObject({ status: 409, message: expect.stringContaining('单帧片段') });
  });
  it('treats gateway failures as uncertain rather than permission to create again', async () => {
    const fetcher = vi.fn().mockResolvedValue(reply({ detail: 'upstream timeout' }, 504)); vi.stubGlobal('fetch', fetcher);
    await expect(api.createMlt(taskId, revisionId, undefined, signal())).rejects.toBeInstanceOf(api.MutationUnknownError);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('rejects malformed saved frame ranges before showing exports', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ revision: record, edl: { schema_version: '0.1.0', duration_frames: 300,
      profile: { width: 1920, height: 1080, fps: { num: 30, den: 1 } }, clips: [{ id: 'clip', source_id: 'source', source_in_frame: 0,
        source_out_frame: 300, timeline_in_frame: 1, timeline_out_frame: 301 }] }, manifest: {} })));
    await expect(api.getEdl(taskId, revisionId, signal())).rejects.toThrow('格式');
  });
  it('allows valid subtitle versions with zero cues', async () => {
    const subtitle = { ...record, subtitle_id: subtitleId, cue_count: 0, font_name: 'Noto Sans CJK SC' };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ subtitles: [subtitle], renders: [], mlt_exports: [], unavailable_count: 0 })));
    expect((await api.listArtifacts(taskId, revisionId, signal())).subtitles[0].cue_count).toBe(0);
  });
  it('rejects malformed successful POST receipts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not json', { status: 201 })));
    await expect(api.createEdl(taskId, ['one'], signal())).rejects.toBeInstanceOf(api.MutationUnknownError);
  });
  it('builds download URLs from strict IDs and whitelisted names', () => {
    expect(api.mltFileUrl(taskId, revisionId, subtitleId, 'project.mlt')).toBe(`/tasks/${taskId}/edl/${revisionId}/exports/mlt/${subtitleId}/files/project.mlt`);
    expect(api.sourceUrl(taskId, revisionId)).toBe(`/tasks/${taskId}/edl/${revisionId}/source`);
    expect(() => api.sourceUrl('https://example.com', revisionId)).toThrow();
    expect(() => api.sourceUrl(taskId, '../secret')).toThrow();
    expect(() => api.mltFileUrl(taskId, revisionId, subtitleId, '../secret' as 'project.mlt')).toThrow();
  });
});
