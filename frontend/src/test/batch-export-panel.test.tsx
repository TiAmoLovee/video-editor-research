import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api/batchExport';
import { BatchExportPanel } from '../components/BatchExportPanel';
import type { BatchExportHistory, BatchExportRecord } from '../types/batchExport';

const task = '11111111-1111-4111-8111-111111111111';
const otherTask = '44444444-4444-4444-8444-444444444444';
const revision = '22222222-2222-4222-8222-222222222222';
const anotherRevision = '55555555-5555-4555-8555-555555555555';
const exported = '33333333-3333-4333-8333-333333333333';
const hash = 'a'.repeat(64);
const record = (rev = revision): BatchExportRecord => ({ format_version: 1, artifact_kind: 'edl_batch_clip_export', task_id: task,
  revision_id: rev, export_id: exported, request_id: exported, created_at: '2026-10-13T00:00:00Z', edl_file_sha256: hash,
  edl_content_sha256: hash, source_sha256: hash, profile: { width: 64, height: 48, fps: { num: 30, den: 1 } },
  clip_count: 3, total_frames: 28, output_bytes: 5000, files: { 'clips.zip': hash, 'source.edl.json': hash, 'manifest.json': hash } });
const empty: BatchExportHistory = { items: [], unavailable_count: 0 };
beforeEach(() => {
  vi.restoreAllMocks(); sessionStorage.clear();
  vi.spyOn(api, 'listBatchExports').mockResolvedValue(empty);
  vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(exported);
});
afterEach(() => vi.unstubAllGlobals());

describe('batch export panel', () => {
  it('does not generate until clicked and downloads only this saved revision ZIP', async () => {
    const create = vi.spyOn(api, 'createBatchExport').mockResolvedValue(record());
    const busy = vi.fn();
    render(<BatchExportPanel taskId={task} revisionId={revision} onBusyChange={busy} />);
    await screen.findByText('此保存版本还没有已核验的批量 ZIP');
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '生成批量片段 ZIP' }));
    const link = await screen.findByRole('link', { name: '下载此版本片段 ZIP' });
    expect(create).toHaveBeenCalledWith(task, revision, exported, expect.any(AbortSignal));
    expect(link).toHaveAttribute('href', `/tasks/${task}/edl/${revision}/exports/batch/${exported}/files/clips.zip`);
    expect(busy).toHaveBeenCalledWith(true);
    await waitFor(() => expect(busy).toHaveBeenLastCalledWith(false));
  });
  it('generates a valid request UUID on HTTP browsers without randomUUID', async () => {
    const fallbackId = '00000000-0000-4000-8000-000000000000';
    const randomValues = vi.fn((bytes: Uint8Array) => bytes.fill(0));
    vi.stubGlobal('crypto', { getRandomValues: randomValues });
    const create = vi.spyOn(api, 'createBatchExport').mockImplementation(async (_task, _revision, request) => ({ ...record(), export_id: request, request_id: request }));
    render(<BatchExportPanel taskId={task} revisionId={revision} />);
    await screen.findByText('此保存版本还没有已核验的批量 ZIP');
    fireEvent.click(screen.getByRole('button', { name: '生成批量片段 ZIP' }));
    const link = await screen.findByRole('link', { name: '下载此版本片段 ZIP' });
    expect(randomValues).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith(task, revision, fallbackId, expect.any(AbortSignal));
    expect(link).toHaveAttribute('href', `/tasks/${task}/edl/${revision}/exports/batch/${fallbackId}/files/clips.zip`);
  });
  it('shows a local error and sends no POST if random number generation is unavailable', async () => {
    vi.stubGlobal('crypto', {});
    const create = vi.spyOn(api, 'createBatchExport');
    render(<BatchExportPanel taskId={task} revisionId={revision} />);
    await screen.findByText('此保存版本还没有已核验的批量 ZIP');
    fireEvent.click(screen.getByRole('button', { name: '生成批量片段 ZIP' }));
    await screen.findByText('无法创建批量请求编号，请使用支持随机数生成的浏览器。');
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '生成批量片段 ZIP' })).toBeEnabled();
  });
  it('recovers unknown results through history without retrying POST', async () => {
    const create = vi.spyOn(api, 'createBatchExport').mockRejectedValue(new api.MutationUnknownError('结果未知'));
    const supplied = { taskId: task, revisionId: revision };
    const view = render(<BatchExportPanel {...supplied} />);
    await screen.findByText('此保存版本还没有已核验的批量 ZIP');
    fireEvent.click(screen.getByRole('button', { name: '生成批量片段 ZIP' }));
    await screen.findByRole('button', { name: '继续同一批量请求' });
    expect(create).toHaveBeenCalledTimes(1);
    view.unmount();
    vi.mocked(api.listBatchExports).mockResolvedValue({ items: [record()], unavailable_count: 0 });
    render(<BatchExportPanel {...supplied} />);
    await screen.findByRole('link', { name: '下载此版本片段 ZIP' });
    expect(create).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: '继续同一批量请求' })).not.toBeInTheDocument();
  });
  it('a deliberate continuation keeps the original UUID and cannot duplicate output', async () => {
    const create = vi.spyOn(api, 'createBatchExport').mockRejectedValueOnce(new api.MutationUnknownError('结果未知')).mockResolvedValueOnce(record());
    render(<BatchExportPanel taskId={task} revisionId={revision} />);
    await screen.findByText('此保存版本还没有已核验的批量 ZIP');
    fireEvent.click(screen.getByRole('button', { name: '生成批量片段 ZIP' }));
    fireEvent.click(await screen.findByRole('button', { name: '继续同一批量请求' }));
    await screen.findByRole('link', { name: '下载此版本片段 ZIP' });
    expect(create).toHaveBeenCalledTimes(2);
    expect(create.mock.calls.every(call => call[2] === exported)).toBe(true);
  });
  it('keeps an unknown session request recoverable after a 404 history read and explicitly retries the same UUID', async () => {
    sessionStorage.setItem(`clipforge:batch-recovery:v1:${task}:${revision}`, JSON.stringify({ task_id: task, revision_id: revision, request_id: exported }));
    vi.mocked(api.listBatchExports).mockRejectedValueOnce(new api.ApiError('保存记录暂时不存在', 404));
    const create = vi.spyOn(api, 'createBatchExport').mockResolvedValue(record());
    render(<BatchExportPanel taskId={task} revisionId={revision} />);
    await screen.findByText('保存记录暂时不存在');
    const button = screen.getByRole('button', { name: '继续同一批量请求' });
    expect(button).toBeEnabled();
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(button);
    await screen.findByRole('link', { name: '下载此版本片段 ZIP' });
    expect(create).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith(task, revision, exported, expect.any(AbortSignal));
    expect(sessionStorage.getItem(`clipforge:batch-recovery:v1:${task}:${revision}`)).toBeNull();
  });
  it('switching versions immediately removes old ZIPs and ignores late old history', async () => {
    let resolveOld: (history: BatchExportHistory) => void = () => {};
    vi.mocked(api.listBatchExports).mockImplementation((_task, rev) => rev === revision
      ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(empty));
    const view = render(<BatchExportPanel taskId={task} revisionId={revision} />);
    await waitFor(() => expect(api.listBatchExports).toHaveBeenCalled());
    view.rerender(<BatchExportPanel taskId={otherTask} revisionId={anotherRevision} />);
    await screen.findByText('此保存版本还没有已核验的批量 ZIP');
    await act(async () => resolveOld({ items: [record()], unavailable_count: 0 }));
    expect(screen.queryByRole('link', { name: '下载此版本片段 ZIP' })).not.toBeInTheDocument();
  });
  it('hides unavailable downloads and respects unsaved-edit disabling', async () => {
    vi.mocked(api.listBatchExports).mockResolvedValue({ items: [], unavailable_count: 2 });
    render(<BatchExportPanel taskId={task} revisionId={revision} disabled />);
    await screen.findByText('有 2 条记录未通过校验，已隐藏下载。');
    expect(screen.getByRole('button', { name: '生成批量片段 ZIP' })).toBeDisabled();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
