import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ExportWorkbench } from '../components/ExportWorkbench';
import * as editing from '../api/editing';
import * as batch from '../api/batchExport';
import type { SavedEdl } from '../types/editing';
import type { BatchExportRecord } from '../types/batchExport';

const task = '11111111-1111-4111-8111-111111111111';
const landscape = '22222222-2222-4222-8222-222222222222';
const fitted = '33333333-3333-4333-8333-333333333333';
const hash = 'a'.repeat(64);
function saved(revision: string): SavedEdl {
  const isFitted = revision === fitted;
  return { revision: { task_id: task, revision_id: revision, created_at: '2026-10-22T00:00:00Z', clip_count: 1,
    duration_frames: 300, ...(isFitted ? { derivation: { kind: 'fit_pad_9_16', parent_revision_id: landscape } } : {}) },
  edl: { schema_version: isFitted ? '0.3.0' : '0.1.0', duration_frames: 300,
    profile: { width: isFitted ? 1080 : 1920, height: isFitted ? 1920 : 1080, fps: { num: 30, den: 1 } },
    sources: [{ id: 'source', width: 1920, height: 1080 }],
    ...(isFitted ? { framing: { mode: 'fit_pad' as const, background: 'black' as const,
      sources: [{ source_id: 'source', x: 0, y: 656, width: 1080, height: 608 }] } } : {}),
    clips: [{ id: 'clip-1', source_id: 'source', source_in_frame: 0, source_out_frame: 300,
      timeline_in_frame: 0, timeline_out_frame: 300, provenance: { candidate_id: 'candidate-1' } }] }, manifest: {} };
}
function record(revision: string, request: string): BatchExportRecord {
  return { format_version: 1, artifact_kind: 'edl_batch_clip_export', task_id: task, revision_id: revision,
    export_id: request, request_id: request, created_at: '2026-10-22T00:00:00Z', edl_file_sha256: hash,
    edl_content_sha256: hash, source_sha256: hash, profile: saved(revision).edl.profile,
    clip_count: 1, total_frames: 300, output_bytes: 5000,
    files: { 'clips.zip': hash, 'source.edl.json': hash, 'manifest.json': hash } };
}
beforeEach(() => {
  localStorage.setItem('clipforge:actor:v1', '测试编辑者'); sessionStorage.clear();
  vi.spyOn(editing, 'listEdls').mockResolvedValue({ items: [landscape, fitted].map(id => ({ ...saved(id).revision,
    profile: saved(id).edl.profile, selected_ids: ['candidate-1'] })), total: 2, offset: 0, limit: 10, has_more: false, unavailable_count: 0 });
  vi.spyOn(editing, 'getEdl').mockImplementation((_task, revision) => Promise.resolve(saved(revision)));
  vi.spyOn(editing, 'listArtifacts').mockResolvedValue({ subtitles: [], renders: [], mlt_exports: [], unavailable_count: 0 });
  vi.spyOn(batch, 'listBatchExports').mockResolvedValue({ items: [], unavailable_count: 0 });
});

describe('fitted portrait and week six batch integration', () => {
  it('locks framing and version changes during a batch export, then exports the selected fitted revision', async () => {
    let finish!: (value: BatchExportRecord) => void;
    const create = vi.spyOn(batch, 'createBatchExport').mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockImplementation((_task, revision, request) => Promise.resolve(record(revision, request)));
    render(<ExportWorkbench taskId={task} selected={[]} onRemove={vi.fn()} onMove={vi.fn()} />);
    await screen.findByText('此保存版本还没有已核验的批量 ZIP');
    const version = screen.getByLabelText('导出版本');
    expect(version.querySelector(`option[value="${fitted}"]`)?.textContent).toContain('完整画面 9:16');
    fireEvent.click(screen.getByRole('button', { name: '生成批量片段 ZIP' }));
    expect(create).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '完整画面 9:16' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '居中裁切 9:16' })).toBeDisabled();
    expect(version).toBeDisabled();
    const reads = vi.mocked(editing.getEdl).mock.calls.length;
    fireEvent.change(version, { target: { value: fitted } });
    expect(editing.getEdl).toHaveBeenCalledTimes(reads);
    expect(screen.getByLabelText('导出版本')).toHaveValue(landscape);
    const firstRequest = create.mock.calls[0][2];
    await act(async () => finish(record(landscape, firstRequest)));
    await screen.findByRole('link', { name: '下载此版本片段 ZIP' });
    await waitFor(() => expect(version).toBeEnabled());
    fireEvent.change(version, { target: { value: fitted } });
    await screen.findByText('完整画面 9:16 · 等比缩放，黑色留边，保留画面内容。');
    await screen.findByText('此保存版本还没有已核验的批量 ZIP');
    fireEvent.click(screen.getByRole('button', { name: '生成批量片段 ZIP' }));
    const link = await screen.findByRole('link', { name: '下载此版本片段 ZIP' });
    expect(create.mock.calls[1]).toEqual([task, fitted, expect.any(String), expect.any(AbortSignal)]);
    expect(link).toHaveAttribute('href', batch.batchExportFileUrl(task, fitted, create.mock.calls[1][2], 'clips.zip'));
    expect(screen.getByLabelText('导出版本')).toHaveValue(fitted);
  }, 15000);
});
