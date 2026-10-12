import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ExportWorkbench } from '../components/ExportWorkbench';
import * as api from '../api/editing';
import type { ArtifactHistory, EdlPage, EdlRevision, SavedEdl, SubtitleRecord } from '../types/editing';
import type { Candidate } from '../types/tasks';

const task = '11111111-1111-4111-8111-111111111111';
const otherTask = '22222222-2222-4222-8222-222222222222';
const revision = '33333333-3333-4333-8333-333333333333';
const portraitRevision = '44444444-4444-4444-8444-444444444444';
const oldSubtitle = '55555555-5555-4555-8555-555555555555';
const newSubtitle = '66666666-6666-4666-8666-666666666666';
const renderId = '77777777-7777-4777-8777-777777777777';
const exportId = '88888888-8888-4888-8888-888888888888';
const profile = { width: 1920, height: 1080, fps: { num: 30, den: 1 } };
const candidates: Candidate[] = [2, 1].map((rank, index) => ({ id: `candidate-${rank}`, rank, start: index * 20, end: index * 20 + 20,
  duration_seconds: 20, text: `候选正文 ${rank}`, score: 60, scorer: 'rule', scoring_status: 'scored', reasons: [], source_sentences: [] }));
const record = (id = revision): EdlRevision => ({ task_id: task, revision_id: id, created_at: '2026-10-12T08:00:00Z', clip_count: 2, duration_frames: 1200 });
const saved = (id = revision): SavedEdl => ({ revision: record(id), edl: { schema_version: '0.2', profile: id === portraitRevision ? { ...profile, width: 1080, height: 1920 } : profile,
  duration_frames: 1200, clips: candidates.map((candidate, index) => ({ id: `clip-${index}`, source_id: 'media', source_in_frame: index * 600,
    source_out_frame: index * 600 + 600, timeline_in_frame: index * 600, timeline_out_frame: index * 600 + 600, provenance: { candidate_id: candidate.id } })) }, manifest: {} });
const edlPage = (ids: string[] = [revision]): EdlPage => ({ items: ids.map(id => ({ ...record(id), profile: saved(id).edl.profile, selected_ids: candidates.map(item => item.id) })),
  total: ids.length, offset: 0, limit: 20, has_more: false, unavailable_count: 0 });
const subtitle = (id = oldSubtitle): SubtitleRecord => ({ task_id: task, revision_id: revision, subtitle_id: id, created_at: '2026-10-12T08:01:00Z', cue_count: 21, font_name: 'Microsoft YaHei' });
const history = (): ArtifactHistory => ({ subtitles: [], renders: [], mlt_exports: [], unavailable_count: 0 });
const props = () => ({ taskId: task, selected: candidates, onRemove: vi.fn(), onMove: vi.fn(), onBusyChange: vi.fn() });

beforeEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
  vi.spyOn(api, 'listEdls').mockResolvedValue(edlPage());
  vi.spyOn(api, 'getEdl').mockImplementation(async (_task, id) => saved(id));
  vi.spyOn(api, 'listArtifacts').mockResolvedValue(history());
});

describe('export workbench', () => {
  it('saves the displayed candidate order once and fetches the returned saved revision before enabling downloads', async () => {
    vi.mocked(api.listEdls).mockResolvedValueOnce(edlPage([]));
    let finish!: (value: EdlRevision) => void;
    const create = vi.spyOn(api, 'createEdl').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const supplied = props();
    render(<ExportWorkbench {...supplied} />);
    await screen.findByText('尚未保存剪辑版本');
    fireEvent.click(screen.getByRole('button', { name: '下移片段 1' }));
    expect(supplied.onMove).toHaveBeenCalledWith('candidate-2', 1);
    fireEvent.click(screen.getByRole('button', { name: '移除片段 2' }));
    expect(supplied.onRemove).toHaveBeenCalledWith('candidate-1');
    const save = screen.getByRole('button', { name: '保存选择为剪辑版本' });
    fireEvent.click(save); fireEvent.click(save);
    expect(create).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith(task, ['candidate-2', 'candidate-1'], expect.any(AbortSignal));
    expect(screen.getByRole('button', { name: '刷新已保存记录' })).toBeDisabled();
    expect(supplied.onBusyChange).toHaveBeenCalledWith(true);
    await act(async () => finish(record()));
    expect(await screen.findByRole('link', { name: '下载 EDL' })).toHaveAttribute('href', api.edlFileUrl(task, revision, 'project.edl.json'));
    expect(api.getEdl).toHaveBeenCalledWith(task, revision, expect.any(AbortSignal));
    expect(within(screen.getByRole('list', { name: '已保存片段顺序' })).getAllByRole('listitem').map(item => item.textContent))
      .toEqual(['片段 1 · 成片内 0.00–20.00 秒', '片段 2 · 成片内 20.00–40.00 秒']);
    expect(supplied.onBusyChange).toHaveBeenLastCalledWith(false);
  });

  it('switches portrait exports to the new revision and clears the previous subtitle binding', async () => {
    vi.mocked(api.listArtifacts).mockImplementation(async (_task, id) => ({ ...history(), subtitles: id === revision ? [subtitle()] : [] }));
    const createPortrait = vi.spyOn(api, 'createPortrait').mockResolvedValue(record(portraitRevision));
    render(<ExportWorkbench {...props()} />);
    await screen.findByRole('link', { name: '下载 SRT 字幕' });
    fireEvent.click(screen.getByRole('checkbox', { name: '导出时带字幕' }));
    expect(screen.getByRole('checkbox', { name: '导出时带字幕' })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '生成 9:16 版本' }));
    await waitFor(() => expect(screen.getByRole('link', { name: '下载 EDL' })).toHaveAttribute('href', api.edlFileUrl(task, portraitRevision, 'project.edl.json')));
    expect(createPortrait).toHaveBeenCalledWith(task, revision, expect.any(AbortSignal));
    expect(screen.getByLabelText('字幕版本')).toHaveValue('');
    expect(screen.getByRole('checkbox', { name: '导出时带字幕' })).not.toBeChecked();
    expect(screen.queryByRole('link', { name: '下载 SRT 字幕' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '生成 9:16 版本' })).not.toBeInTheDocument();
  });

  it('binds subtitle, MP4 and MLT requests and download URLs to the displayed saved revision', async () => {
    let stored: ArtifactHistory = { ...history(), subtitles: [subtitle()] };
    vi.mocked(api.listArtifacts).mockImplementation(async () => stored);
    const createSubtitle = vi.spyOn(api, 'createSubtitles').mockImplementation(async () => {
      const item = subtitle(newSubtitle); stored = { ...stored, subtitles: [item, subtitle()] }; return item;
    });
    const renderRecord = { task_id: task, revision_id: revision, render_id: renderId, created_at: '2026-10-12T08:02:00Z', profile, total_frames: 1200, output_bytes: 1000, subtitle_id: newSubtitle };
    const mltRecord = { task_id: task, revision_id: revision, export_id: exportId, created_at: '2026-10-12T08:03:00Z', profile, total_frames: 1200, clip_count: 2, subtitle_id: newSubtitle };
    const createMp4 = vi.spyOn(api, 'createMp4').mockImplementation(async () => { stored = { ...stored, renders: [renderRecord] }; return renderRecord; });
    const createMlt = vi.spyOn(api, 'createMlt').mockImplementation(async () => { stored = { ...stored, mlt_exports: [mltRecord] }; return mltRecord; });
    render(<ExportWorkbench {...props()} />);
    await screen.findByRole('link', { name: '下载 EDL' });
    fireEvent.click(screen.getByRole('button', { name: '生成字幕' }));
    await waitFor(() => expect(screen.getByRole('checkbox', { name: '导出时带字幕' })).toBeChecked());
    expect(createSubtitle).toHaveBeenCalledWith(task, revision, expect.any(AbortSignal));
    expect(screen.getByRole('link', { name: '下载 SRT 字幕' })).toHaveAttribute('href', api.subtitleFileUrl(task, revision, newSubtitle, 'captions.srt'));
    fireEvent.click(screen.getByRole('button', { name: '导出 MP4' }));
    expect(await screen.findByRole('link', { name: '下载 MP4 1' })).toHaveAttribute('href', api.renderFileUrl(task, revision, renderId, 'output.mp4'));
    expect(createMp4).toHaveBeenCalledWith(task, revision, newSubtitle, expect.any(AbortSignal));
    expect(screen.getByLabelText('最新保存成片播放器')).toHaveAttribute('src', api.renderFileUrl(task, revision, renderId, 'output.mp4'));
    fireEvent.error(screen.getByLabelText('最新保存成片播放器'));
    expect(screen.getByText('网页预览暂不可用，请下载 MP4 查看。')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '导出 Shotcut 工程' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: '导出 Shotcut 工程' }));
    expect(await screen.findByRole('link', { name: '下载 Shotcut 工程 1' })).toHaveAttribute('href', api.mltFileUrl(task, revision, exportId, 'project.mlt'));
    expect(createMlt).toHaveBeenCalledWith(task, revision, newSubtitle, expect.any(AbortSignal));
  });

  it('keeps saved-version exports independent of later draft choices', async () => {
    const supplied = props();
    const create = vi.spyOn(api, 'createMp4').mockResolvedValue({ task_id: task, revision_id: revision, render_id: renderId, created_at: '2026-10-12T08:02:00Z', profile, total_frames: 1200, output_bytes: 1000 });
    const component = render(<ExportWorkbench {...supplied} />);
    await screen.findByRole('link', { name: '下载 EDL' });
    component.rerender(<ExportWorkbench {...supplied} selected={[candidates[1]]} />);
    expect(screen.getByText('当前草稿与已保存版本不同')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '导出 MP4' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(task, revision, undefined, expect.any(AbortSignal)));
  });

  it('blocks unknown mutations until a read-only refresh succeeds and the user acknowledges the saved records', async () => {
    vi.mocked(api.listEdls).mockResolvedValueOnce(edlPage([]));
    const create = vi.spyOn(api, 'createEdl').mockRejectedValue(new api.MutationUnknownError());
    render(<ExportWorkbench {...props()} />);
    await screen.findByText('尚未保存剪辑版本');
    fireEvent.click(screen.getByRole('button', { name: '保存选择为剪辑版本' }));
    await screen.findByText('上次操作的结果尚未确认');
    expect(screen.getByRole('button', { name: '保存选择为剪辑版本' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '保存选择为剪辑版本' }));
    expect(create).toHaveBeenCalledTimes(1);
    const reads = vi.mocked(api.listEdls).mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '刷新已保存记录' }));
    await screen.findByRole('link', { name: '下载 EDL' });
    await waitFor(() => expect(screen.getByRole('button', { name: '已核对记录，继续操作' })).not.toBeDisabled());
    expect(screen.getByText('上次操作的结果尚未确认')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导出 MP4' })).toBeDisabled();
    expect(api.listEdls).toHaveBeenCalledTimes(reads + 1);
    expect(create).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '已核对记录，继续操作' }));
    await waitFor(() => expect(screen.queryByText('上次操作的结果尚未确认')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: '导出 MP4' })).not.toBeDisabled();
  });

  it('ignores a late mutation result after switching tasks and cancels the old request', async () => {
    vi.mocked(api.listEdls).mockResolvedValue(edlPage([]));
    let finish!: (value: EdlRevision) => void;
    const create = vi.spyOn(api, 'createEdl').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const supplied = props();
    const component = render(<ExportWorkbench {...supplied} />);
    await screen.findByText('尚未保存剪辑版本');
    fireEvent.click(screen.getByRole('button', { name: '保存选择为剪辑版本' }));
    const oldSignal = create.mock.calls[0][2]!;
    component.rerender(<ExportWorkbench {...supplied} taskId={otherTask} />);
    expect(oldSignal.aborted).toBe(true);
    await screen.findByText('尚未保存剪辑版本');
    await act(async () => finish(record()));
    expect(screen.queryByRole('link', { name: '下载 EDL' })).not.toBeInTheDocument();
    expect(api.getEdl).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '保存选择为剪辑版本' })).not.toBeDisabled();
  });

  it('restores existing histories without a POST and changes all downloads when a saved version is selected', async () => {
    vi.mocked(api.listEdls).mockResolvedValue(edlPage([revision, portraitRevision]));
    const create = vi.spyOn(api, 'createEdl');
    render(<ExportWorkbench {...props()} selected={[]} />);
    await screen.findByRole('link', { name: '下载 EDL' });
    fireEvent.change(screen.getByLabelText('导出版本'), { target: { value: portraitRevision } });
    await waitFor(() => expect(screen.getByRole('link', { name: '下载 EDL' })).toHaveAttribute('href', api.edlFileUrl(task, portraitRevision, 'project.edl.json')));
    expect(screen.getByRole('link', { name: '下载工程所需源素材' })).toHaveAttribute('href', api.sourceUrl(task, portraitRevision));
    expect(create).not.toHaveBeenCalled();
  });

  it('keeps an unknown mutation blocked when the refresh fails and when only the version is switched', async () => {
    vi.mocked(api.listEdls).mockResolvedValue(edlPage([revision, portraitRevision]));
    const create = vi.spyOn(api, 'createMp4').mockRejectedValue(new api.MutationUnknownError());
    render(<ExportWorkbench {...props()} />);
    await screen.findByRole('link', { name: '下载 EDL' });
    fireEvent.click(screen.getByRole('button', { name: '导出 MP4' }));
    await screen.findByText('上次操作的结果尚未确认');
    vi.mocked(api.listEdls).mockRejectedValueOnce(new Error('读取暂时失败'));
    fireEvent.click(screen.getByRole('button', { name: '刷新已保存记录' }));
    await screen.findByText('读取暂时失败');
    expect(screen.getByRole('button', { name: '导出 MP4' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('导出版本'), { target: { value: portraitRevision } });
    await waitFor(() => expect(screen.getByRole('link', { name: '下载 EDL' })).toHaveAttribute('href', api.edlFileUrl(task, portraitRevision, 'project.edl.json')));
    expect(screen.getByText('上次操作的结果尚未确认')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导出 MP4' })).toBeDisabled();
    expect(create).toHaveBeenCalledTimes(1);
  });

  it('locks creation when a successful POST cannot be followed by the saved record read', async () => {
    const create = vi.spyOn(api, 'createPortrait').mockResolvedValue(record(portraitRevision));
    render(<ExportWorkbench {...props()} />);
    await screen.findByRole('link', { name: '下载 EDL' });
    vi.mocked(api.getEdl).mockRejectedValueOnce(new Error('读取失败'));
    fireEvent.click(screen.getByRole('button', { name: '生成 9:16 版本' }));
    await screen.findByText('操作已完成，但记录暂未载入。请先刷新已保存记录，确认结果后再继续。');
    expect(screen.getByRole('button', { name: '生成 9:16 版本' })).toBeDisabled();
    expect(create).toHaveBeenCalledTimes(1);
  });

  it('retains a pending operation across a real task unmount and remount without issuing another POST', async () => {
    vi.mocked(api.listEdls).mockResolvedValue(edlPage([]));
    let finish!: (value: EdlRevision) => void;
    const create = vi.spyOn(api, 'createEdl').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const old = render(<ExportWorkbench {...props()} />);
    await screen.findByText('尚未保存剪辑版本');
    fireEvent.click(screen.getByRole('button', { name: '保存选择为剪辑版本' }));
    expect(sessionStorage.getItem(`clipforge:export-recovery:v1:${task}`)).toContain('operation_id');
    old.unmount();
    expect(create.mock.calls[0][2]!.aborted).toBe(true);
    const different = render(<ExportWorkbench {...props()} taskId={otherTask} />);
    await screen.findByText('尚未保存剪辑版本');
    expect(screen.queryByText('上次操作的结果尚未确认')).not.toBeInTheDocument();
    different.unmount();
    render(<ExportWorkbench {...props()} />);
    await screen.findByText('上次操作的结果尚未确认');
    await waitFor(() => expect(screen.queryByText('正在读取已保存的版本与导出记录…')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: '保存选择为剪辑版本' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '已核对记录，继续操作' })).toBeDisabled();
    await act(async () => finish(record()));
    expect(screen.getByText('上次操作的结果尚未确认')).toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(1);
  });

  it('restores a session recovery receipt and requires refresh plus acknowledgement before enabling creation', async () => {
    sessionStorage.setItem(`clipforge:export-recovery:v1:${task}`, JSON.stringify({ version: 1, task_id: task, operation_id: 'saved-session', preferred_revision: portraitRevision }));
    const create = vi.spyOn(api, 'createPortrait');
    render(<ExportWorkbench {...props()} />);
    await waitFor(() => expect(screen.getByRole('link', { name: '下载 EDL' })).toHaveAttribute('href', api.edlFileUrl(task, portraitRevision, 'project.edl.json')));
    expect(screen.getByRole('button', { name: '导出 MP4' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '已核对记录，继续操作' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '刷新已保存记录' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '已核对记录，继续操作' })).not.toBeDisabled());
    expect(screen.getByRole('button', { name: '导出 MP4' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '已核对记录，继续操作' }));
    expect(sessionStorage.getItem(`clipforge:export-recovery:v1:${task}`)).toBeNull();
    expect(screen.getByRole('button', { name: '导出 MP4' })).not.toBeDisabled();
    expect(create).not.toHaveBeenCalled();
  });

  it('recovers the exact created revision after a 201 receipt followed by a failed GET', async () => {
    const create = vi.spyOn(api, 'createPortrait').mockResolvedValue(record(portraitRevision));
    const first = render(<ExportWorkbench {...props()} />);
    await screen.findByRole('link', { name: '下载 EDL' });
    vi.mocked(api.getEdl).mockRejectedValueOnce(new Error('读取失败'));
    fireEvent.click(screen.getByRole('button', { name: '生成 9:16 版本' }));
    await screen.findByText('操作已完成，但记录暂未载入。请先刷新已保存记录，确认结果后再继续。');
    expect(JSON.parse(sessionStorage.getItem(`clipforge:export-recovery:v1:${task}`)!)).toMatchObject({ preferred_revision: portraitRevision });
    first.unmount();
    render(<ExportWorkbench {...props()} />);
    await waitFor(() => expect(screen.getByRole('link', { name: '下载 EDL' })).toHaveAttribute('href', api.edlFileUrl(task, portraitRevision, 'project.edl.json')));
    expect(screen.getByRole('button', { name: '导出 MP4' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '刷新已保存记录' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '已核对记录，继续操作' })).not.toBeDisabled());
    expect(api.getEdl).toHaveBeenLastCalledWith(task, portraitRevision, expect.any(AbortSignal));
    expect(create).toHaveBeenCalledTimes(1);
  });

  it('keeps the pending lock in memory if session storage cannot accept writes', async () => {
    const storage = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('quota', 'QuotaExceededError'); });
    const create = vi.spyOn(api, 'createMp4').mockRejectedValue(new api.MutationUnknownError());
    const first = render(<ExportWorkbench {...props()} />);
    await screen.findByRole('link', { name: '下载 EDL' });
    fireEvent.click(screen.getByRole('button', { name: '导出 MP4' }));
    await screen.findByText('上次操作的结果尚未确认');
    first.unmount();
    storage.mockRestore();
    render(<ExportWorkbench {...props()} />);
    await screen.findByRole('link', { name: '下载 EDL' });
    expect(screen.getByText('上次操作的结果尚未确认')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导出 MP4' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '刷新已保存记录' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '已核对记录，继续操作' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: '已核对记录，继续操作' }));
    expect(create).toHaveBeenCalledTimes(1);
  });
});
