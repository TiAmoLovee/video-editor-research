import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api/batchExport';

const task = '11111111-1111-4111-8111-111111111111';
const revision = '22222222-2222-4222-8222-222222222222';
const exported = '33333333-3333-4333-8333-333333333333';
const hash = 'a'.repeat(64);
const record = () => ({ format_version: 1, artifact_kind: 'edl_batch_clip_export', task_id: task, revision_id: revision,
  export_id: exported, request_id: exported, created_at: '2026-10-13T00:00:00Z', edl_file_sha256: hash,
  edl_content_sha256: hash, source_sha256: hash, profile: { width: 64, height: 48, fps: { num: 30, den: 1 } },
  clip_count: 3, total_frames: 28, output_bytes: 5000, files: { 'clips.zip': hash, 'source.edl.json': hash, 'manifest.json': hash } });
const signal = () => new AbortController().signal;
afterEach(() => vi.restoreAllMocks());

describe('batch export API', () => {
  it('explicitly posts the request UUID once and validates the saved revision result', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(record()), { status: 201 }));
    expect(await api.createBatchExport(task, revision, exported, signal())).toEqual(record());
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe(`/tasks/${task}/edl/${revision}/exports/batch`);
    expect(fetch.mock.calls[0][1]?.method).toBe('POST');
    expect(fetch.mock.calls[0][1]?.body).toBe(JSON.stringify({ request_id: exported }));
  });
  it('never retries POST after network loss, processing conflict, bad response or server error', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch');
    const cases = [() => Promise.reject(new TypeError('network')),
      () => Promise.resolve(new Response(JSON.stringify({ detail: '仍在处理' }), { status: 409 })),
      () => Promise.resolve(new Response(JSON.stringify({ detail: '超时' }), { status: 503 })),
      () => Promise.resolve(new Response('{', { status: 201 })),
      () => Promise.resolve(new Response(JSON.stringify({ ...record(), revision_id: task }), { status: 201 }))];
    for (const response of cases) {
      fetch.mockReset().mockImplementation(response);
      await expect(api.createBatchExport(task, revision, exported, signal())).rejects.toBeInstanceOf(api.MutationUnknownError);
      expect(fetch).toHaveBeenCalledTimes(1);
    }
  });
  it('rejects history belonging to another revision, duplicate IDs and invalid hashes', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch');
    for (const items of [[{ ...record(), revision_id: task }], [record(), record()], [{ ...record(), source_sha256: 'bad' }]]) {
      fetch.mockResolvedValueOnce(new Response(JSON.stringify({ items, unavailable_count: 0 }), { status: 200 }));
      await expect(api.listBatchExports(task, revision, signal())).rejects.toThrow();
    }
  });
  it('allowlists UUIDs and download names', () => {
    expect(api.batchExportFileUrl(task, revision, exported, 'clips.zip')).toBe(`/tasks/${task}/edl/${revision}/exports/batch/${exported}/files/clips.zip`);
    expect(() => api.batchExportFileUrl(task, revision, '../bad', 'clips.zip')).toThrow();
    expect(() => api.batchExportFileUrl(task, revision, exported, '../normalized.mp4' as 'clips.zip')).toThrow();
  });
});
