import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CandidateResults } from '../components/CandidateResults';
import { ClipResults } from '../components/ClipResults';
import { TaskDetail } from '../components/TaskDetail';
import * as api from '../api/tasks';
import type { CandidatePage } from '../types/tasks';
import { completedTask, taskId } from './fixtures';
import { useTasks } from '../store/tasks';

const page = (offset = 0): CandidatePage => ({
  items: [{ id: `c-${offset}`, rank: offset + 1, start: offset + 1, end: offset + 21, duration_seconds: 20,
    text: `候选正文 ${offset + 1}`, score: 48.759475, scorer: 'rule', scoring_status: 'scored',
    reasons: ['关键词贡献 10 分。', '时长扣除 6 分。'], source_sentences: ['s1', 's2'] }],
  total: 11, limit: 10, offset, has_more: offset === 0, scorer: 'rule', audio_status: 'measured',
});
const download = `/tasks/${taskId}/files/candidates.json`;
beforeEach(() => { vi.restoreAllMocks(); useTasks.setState({ task: null, detailError: '', detailLoading: false }); });

describe('candidate results', () => {
  it('filters groups globally, resets pagination, and exposes duplicates without changing scores', async () => {
    const data = page();
    const groupId = 'topic_0123456789abcdef';
    data.topics = { version: 'lexical-topics-v1', summary: { input_count: 3, group_count: 2, duplicate_count: 1, recommended_count: 2 },
      groups: [{ id: groupId, label: '人工智能', member_count: 2, recommended_count: 1, member_ids: ['a', 'b'], recommended_ids: ['a'] }] };
    data.items[0].selection = { retained: true, suppressed_by: null, boundary_status: 'not_verified', issues: [], proposal: null,
      topic: { group_id: groupId, group_label: '人工智能', duplicate_of: 'a', duplicate_rank: 1, duplicate_similarity: .96 } };
    const request = vi.spyOn(api, 'getCandidates').mockResolvedValue(data);
    render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText('合集分组（试用）')).toBeInTheDocument();
    expect(screen.getByText(/疑似重复候选 #1/)).toBeInTheDocument();
    expect(screen.getByText(/尚未生成合集视频/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页候选' }));
    await waitFor(() => expect(request).toHaveBeenLastCalledWith(taskId, 10, expect.any(AbortSignal)));
    fireEvent.click(await screen.findByRole('button', { name: '组 1 · 人工智能（1）' }));
    await waitFor(() => expect(request).toHaveBeenLastCalledWith(taskId, 0, expect.any(AbortSignal), 'topics', groupId));
    expect(await screen.findByText('规则分 48.76')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '按组查看 · 折叠疑似重复' }));
    await waitFor(() => expect(request).toHaveBeenLastCalledWith(taskId, 0, expect.any(AbortSignal), 'topics', undefined));
  });
  it('keeps approved media separate from original scores and guards media URLs', async () => {
    const data = page();
    data.accepted_versions = [{ id: 'a'.repeat(64), candidate_id: 'reviewed', start: 1.43, end: 17.83,
      duration_seconds: 16.4, text: '验收版本', score: 70, scorer: 'llm', score_scope: 'rendered_range',
      score_start: 1.43, score_end: 17.83, feedback: '结尾完整', reasons: ['分项合计 70'],
      video_url: `/tasks/${taskId}/accepted/${'a'.repeat(64)}.mp4` }];
    const request = vi.spyOn(api, 'getCandidates').mockResolvedValue(data);
    data.selection_summary = { original_count: 11, retained_count: 2, suppressed_count: 9, retained_review_count: 1, proposal_count: 0 };
    render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText('已试听通过')).toBeInTheDocument();
    expect(screen.getByText('此修正版模型分 70.00')).toBeInTheDocument();
    expect(screen.getByText('规则分 48.76')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载已验收成品' })).toHaveAttribute('href', data.accepted_versions[0].video_url);
    expect(api.acceptedVideoUrl('https://example.com/video.mp4', taskId)).toBeUndefined();
    expect(api.acceptedVideoUrl(`/tasks/${taskId}/accepted/../../secret.mp4`, taskId)).toBeUndefined();
    const player = screen.getByLabelText('已验收片段播放器');
    let finish!: (value: CandidatePage) => void;
    request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: '只看去重保留' }));
    expect(screen.getByLabelText('已验收片段播放器')).toBe(player);
    await act(async () => finish({ ...data, total: 2 }));
    expect(screen.getByLabelText('已验收片段播放器')).toBe(player);
  });
  it('shows tail evidence and requests the global review filter', async () => {
    const data = page();
    data.selection_summary = { original_count: 11, retained_count: 2, suppressed_count: 9, retained_review_count: 1, proposal_count: 0 };
    data.items[0].selection = { retained: true, suppressed_by: null, boundary_status: 'review_required',
      issues: ['conditional_tail_needs_review'], proposal: null,
      text_review: { signal: { quote: '如果超出范围', reason: '需核对后文' },
        existing_extension: { id: 'longer', start: 1, end: 35, score: 42, rank: 6, scorer: 'rule' } } };
    const request = vi.spyOn(api, 'getCandidates').mockResolvedValue(data);
    render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText('结尾待核对：“如果超出范围”')).toBeInTheDocument();
    expect(screen.getByText(/这个更长版本仍需试听/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '只看待复核' }));
    await waitFor(() => expect(request).toHaveBeenLastCalledWith(taskId, 0, expect.any(AbortSignal), 'review'));
  });
  it('separates unscored boundary proposals from original scores and filters globally', async () => {
    const data = page();
    data.selection_summary = { original_count: 11, retained_count: 2, suppressed_count: 9, retained_review_count: 1, proposal_count: 1 };
    data.items[0].selection = { retained: true, suppressed_by: null, boundary_status: 'review_required', issues: ['word_crosses_visual_cut'], proposal: { start: 1.43, end: 17.9, duration_seconds: 16.47, text: '原词保留', score: null, status: 'review_then_rescore' } };
    const request = vi.spyOn(api, 'getCandidates').mockResolvedValue(data);
    render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText('修正草案：1.43–17.90 秒')).toBeInTheDocument();
    expect(screen.getByText(/没有沿用原分数/)).toBeInTheDocument();
    expect(screen.getByText(/不能直接按画面截断/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '只看去重保留' }));
    await waitFor(() => expect(request).toHaveBeenLastCalledWith(taskId, 0, expect.any(AbortSignal), 'retained'));
    expect(await screen.findByRole('link', { name: '下载边界与去重记录' })).toHaveAttribute('href', `/tasks/${taskId}/selection`);
  });
  it('identifies the saved scoring version and never labels an unknown version as new', async () => {
    const data = page(); data.scorer = 'llm';
    data.items[0] = { ...data.items[0], scorer: 'llm' };
    const request = vi.spyOn(api, 'getCandidates');
    for (const version of ['llm-v1', 'llm-v2', undefined] as const) {
      request.mockResolvedValue({ ...data, scoring_version: version });
      const view = render(<CandidateResults taskId={taskId} download={download} />);
      await screen.findByText('模型分 48.76');
      expect(!!screen.queryByText('旧版文本评分')).toBe(version === 'llm-v1');
      expect(!!screen.queryByText('新版分项评分')).toBe(version === 'llm-v2');
      view.unmount();
    }
  });
  it('explains rejected assessments without presenting the model version as effective', async () => {
    vi.spyOn(api, 'getCandidates').mockResolvedValue({ ...page(), scoring_version: 'rule-v1', requested_scorer: 'llm', fallback_reason: 'invalid_assessment' });
    render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText(/分项分数或原文引用未通过校验/)).toBeInTheDocument();
    expect(screen.queryByText('新版分项评分')).not.toBeInTheDocument();
  });
  it('distinguishes model scores from the rule baseline', async () => {
    const data = page(); data.scorer = 'llm'; data.requested_scorer = 'llm';
    data.items[0] = { ...data.items[0], scorer: 'llm', score: 81, rule_score: 48.75 };
    vi.spyOn(api, 'getCandidates').mockResolvedValue(data);
    render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText('模型分 81.00')).toBeInTheDocument();
    expect(screen.getByText('规则基线 48.75')).toBeInTheDocument();
    expect(screen.getByText(/未评估画面和声音/)).toBeInTheDocument();
  });
  it('explains whole-task fallback without showing a model score', async () => {
    vi.spyOn(api, 'getCandidates').mockResolvedValue({ ...page(), requested_scorer: 'llm', fallback_reason: 'invalid_key' });
    render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText(/本次全部候选已使用规则评分/)).toBeInTheDocument();
    expect(screen.getByText('规则分 48.76')).toBeInTheDocument();
    expect(screen.queryByText(/模型分 \d/)).not.toBeInTheDocument();
  });
  it('shows score, original text, explanations and a safe download', async () => {
    vi.spyOn(api, 'getCandidates').mockResolvedValue(page());
    render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText('候选正文 1')).toBeInTheDocument();
    expect(screen.getByText('规则分 48.76')).toBeInTheDocument();
    expect(screen.getByText('关键词贡献 10 分。')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载评分结果' })).toHaveAttribute('href', download);
  });
  it('pages candidates without retaining the previous page', async () => {
    const request = vi.spyOn(api, 'getCandidates').mockImplementation((_id, offset) => Promise.resolve(page(offset)));
    render(<CandidateResults taskId={taskId} download={download} />);
    await screen.findByText('候选正文 1');
    expect(screen.getByRole('button', { name: '上一页候选' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '下一页候选' }));
    expect(await screen.findByText('候选正文 11')).toBeInTheDocument();
    expect(screen.queryByText('候选正文 1')).not.toBeInTheDocument();
    expect(request).toHaveBeenLastCalledWith(taskId, 10, expect.any(AbortSignal));
    expect(screen.getByRole('button', { name: '下一页候选' })).toBeDisabled();
  });
  it('shows an explicit empty result and missing volume information', async () => {
    const request = vi.spyOn(api, 'getCandidates').mockResolvedValue({ ...page(), items: [], total: 0, has_more: false });
    const view = render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText(/没有符合 15–90 秒条件/)).toBeInTheDocument();
    view.unmount();
    request.mockResolvedValue({ ...page(), audio_status: 'unavailable' });
    render(<CandidateResults taskId={taskId} download={download} />);
    expect(await screen.findByText(/没有可用音量证据/)).toBeInTheDocument();
  });
  it('retries after failure while keeping clip downloads usable', async () => {
    const request = vi.spyOn(api, 'getCandidates').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(page());
    const task = completedTask(); task.result!.downloads['candidates.json'] = download;
    render(<ClipResults task={task} result={task.result!} />);
    expect(await screen.findByText(/候选暂时加载失败/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /下载全部切片/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试候选' }));
    expect(await screen.findByText('候选正文 1')).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
  });
  it('does not request candidates for a legacy task', () => {
    const request = vi.spyOn(api, 'getCandidates');
    const task = completedTask(); render(<ClipResults task={task} result={task.result!} />);
    expect(screen.getByText(/此历史任务没有候选评分记录/)).toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: /下载全部切片/ })).toBeInTheDocument();
  });
  it('ignores late responses from an unselected task', async () => {
    let resolveOld!: (value: CandidatePage) => void;
    const request = vi.spyOn(api, 'getCandidates').mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; })).mockResolvedValue(page(10));
    const nextId = '00000000-0000-4000-8000-000000000002';
    const view = render(<CandidateResults taskId={taskId} download={download} />);
    view.rerender(<CandidateResults taskId={nextId} download={`/tasks/${nextId}/files/candidates.json`} />);
    expect(await screen.findByText('候选正文 11')).toBeInTheDocument();
    await act(async () => resolveOld(page()));
    expect(screen.queryByText('候选正文 1')).not.toBeInTheDocument();
    expect(request.mock.calls[0][2].aborted).toBe(true);
  });
  it('labels the scoring stage without publishing results early', async () => {
    useTasks.setState({ task: { ...completedTask(), status: 'RUNNING', stage: 'scoring_candidates', progress: 68, result: null } });
    render(<TaskDetail />);
    await waitFor(() => expect(screen.getByText('生成候选并评分')).toBeInTheDocument());
    expect(screen.queryByRole('link', { name: '下载评分结果' })).not.toBeInTheDocument();
  });
});
