import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CandidateResults } from '../components/CandidateResults';
import * as tasks from '../api/tasks';
import * as reviews from '../api/reviews';
import type { CandidatePage } from '../types/tasks';
import type { ReviewEvent, ReviewPage, ReviewWrite } from '../types/reviews';
import { taskId } from './fixtures';

vi.mock('../components/ExportWorkbench', () => ({ ExportWorkbench: ({ selected }: { selected: { id: string }[] }) => <p data-testid="draft">{selected.map(item => item.id).join(',')}</p> }));
const candidatePage: CandidatePage = { items: [{ id: 'c1', rank: 1, start: 1, end: 21, duration_seconds: 20, text: '候选正文', score: 70,
  scorer: 'rule', scoring_status: 'scored', reasons: ['原评分原因'], source_sentences: ['s1'] }], total: 1, limit: 10, offset: 0, has_more: false, scorer: 'rule', audio_status: 'measured' };
const digest = 'a'.repeat(64);
function page(task = taskId): ReviewPage { return { format_version: 1, task_id: task, candidate_snapshot_sha256: digest,
  items: [{ candidate_id: 'c1', decision: 'undecided', actor: null, reason: null, created_at: null, request_id: null }] }; }
function event(body: ReviewWrite, task = taskId): ReviewEvent { return { event_id: '00000000-0000-4000-8000-000000000003', kind: 'candidate_review', task_id: task,
  candidate_id: body.candidate_id, decision: body.decision, actor: body.actor, actor_scope: 'user_supplied_name', reason: body.reason,
  created_at: '2026-10-13T00:00:00Z', request_id: body.request_id, candidate_snapshot_sha256: body.expected_snapshot_sha256, before: { decision: 'undecided' }, after: { decision: body.decision } }; }
function component(id = taskId) { return <CandidateResults taskId={id} download={`/tasks/${id}/files/candidates.json`} />; }
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  vi.spyOn(tasks, 'getCandidates').mockResolvedValue(candidatePage);
  vi.spyOn(reviews, 'getReviews').mockImplementation(task => Promise.resolve(page(task)));
});
describe('candidate human review', () => {
  it('requires a supplied actor and keeps review separate from EDL selection', async () => {
    const save = vi.spyOn(reviews, 'saveReview').mockImplementation((task, body) => Promise.resolve(event(body, task)));
    render(component());
    const accept = await screen.findByRole('button', { name: '采纳候选 #1' });
    await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.click(accept);
    expect(await screen.findByText('请填写 1–80 字的操作人姓名。')).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('复核操作人姓名'), { target: { value: '  小林  ' } });
    fireEvent.change(screen.getByLabelText('候选 #1 复核理由（可选）'), { target: { value: '  首尾完整  ' } });
    fireEvent.click(accept);
    expect(await screen.findByText('人工采纳')).toBeInTheDocument();
    expect(save).toHaveBeenCalledWith(taskId, expect.objectContaining({ candidate_id: 'c1', decision: 'accepted', actor: '小林', reason: '首尾完整', expected_snapshot_sha256: digest }), expect.any(AbortSignal));
    expect(screen.getByText('规则分 70.00')).toBeInTheDocument();
    expect(screen.getByText('原评分原因')).toBeInTheDocument();
    expect(screen.getByTestId('draft')).toBeEmptyDOMElement();
    fireEvent.click(screen.getByRole('button', { name: '加入候选 #1' }));
    expect(screen.getByTestId('draft')).toHaveTextContent('c1');
    fireEvent.click(screen.getByRole('button', { name: '驳回候选 #1' }));
    expect(await screen.findByText('人工驳回')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '撤销候选 #1 为待复核' }));
    expect(await screen.findByText('待人工复核')).toBeInTheDocument();
    expect(save.mock.calls.map(call => call[1].decision)).toEqual(['accepted', 'rejected', 'undecided']);
  }, 15000);
  it('restores a persisted decision and reason when the page is opened again', async () => {
    const saved = page(); saved.items[0] = { candidate_id: 'c1', decision: 'rejected', actor: '小林', reason: '结尾未说完', created_at: '2026-10-13T00:00:00Z', request_id: '00000000-0000-4000-8000-000000000003' };
    vi.mocked(reviews.getReviews).mockResolvedValue(saved);
    const view = render(component());
    expect(await screen.findByText('人工驳回')).toBeInTheDocument();
    expect(screen.getByLabelText('候选 #1 复核理由（可选）')).toHaveValue('结尾未说完');
    view.unmount(); render(component());
    expect(await screen.findByText('人工驳回')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '驳回候选 #1' })).toBeDisabled();
  });
  it('persists uncertain operations and reconciles by request ID without automatically posting again', async () => {
    const save = vi.spyOn(reviews, 'saveReview').mockRejectedValue(new reviews.ReviewUnknownError());
    const confirm = vi.spyOn(reviews, 'getReviewRequest');
    const view = render(component());
    const accept = await screen.findByRole('button', { name: '采纳候选 #1' });
    await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.change(screen.getByLabelText('复核操作人姓名'), { target: { value: '小林' } }); fireEvent.click(accept);
    await screen.findByText('这次复核结果待确认，暂时停止新的复核提交。');
    const body = save.mock.calls[0][1];
    expect(JSON.parse(sessionStorage.getItem(`clipforge:review-pending:v1:${taskId}`)!)).toEqual(body);
    expect(screen.getByRole('button', { name: '驳回候选 #1' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '重新提交同一次操作' })).not.toBeInTheDocument();
    view.unmount(); render(component());
    await screen.findByText('这次复核结果待确认，暂时停止新的复核提交。');
    confirm.mockResolvedValue(event(body));
    const saved = page(); saved.items[0] = { candidate_id: 'c1', decision: 'accepted', actor: body.actor, reason: body.reason,
      created_at: '2026-10-13T00:00:00Z', request_id: body.request_id }; vi.mocked(reviews.getReviews).mockResolvedValue(saved);
    fireEvent.click(screen.getByRole('button', { name: '核对这次复核' }));
    expect(await screen.findByText('人工采纳')).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledWith(taskId, body.request_id, expect.any(AbortSignal));
    expect(save).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(`clipforge:review-pending:v1:${taskId}`)).toBeNull();
  });
  it('only offers an explicit replay after 404 and reuses the exact same write', async () => {
    const save = vi.spyOn(reviews, 'saveReview').mockRejectedValueOnce(new reviews.ReviewUnknownError()).mockImplementation((task, body) => Promise.resolve(event(body, task)));
    vi.spyOn(reviews, 'getReviewRequest').mockRejectedValue(new tasks.ApiError('未找到', 404));
    render(component());
    const accept = await screen.findByRole('button', { name: '采纳候选 #1' });
    await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.change(screen.getByLabelText('复核操作人姓名'), { target: { value: '小林' } }); fireEvent.click(accept);
    fireEvent.click(await screen.findByRole('button', { name: '核对这次复核' }));
    const body = save.mock.calls[0][1]; const saved = page(); saved.items[0] = { candidate_id: 'c1', decision: 'accepted', actor: body.actor,
      reason: body.reason, created_at: '2026-10-13T00:00:00Z', request_id: body.request_id }; vi.mocked(reviews.getReviews).mockResolvedValue(saved);
    fireEvent.click(await screen.findByRole('button', { name: '重新提交同一次操作' }));
    expect(await screen.findByText('人工采纳')).toBeInTheDocument();
    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][1]).toEqual(save.mock.calls[0][1]);
  });
  it('keeps a newer server decision when reconciling an older accepted request', async () => {
    const save = vi.spyOn(reviews, 'saveReview').mockRejectedValue(new reviews.ReviewUnknownError());
    const confirm = vi.spyOn(reviews, 'getReviewRequest');
    render(component());
    const accept = await screen.findByRole('button', { name: '采纳候选 #1' }); await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.change(screen.getByLabelText('复核操作人姓名'), { target: { value: '小林' } }); fireEvent.click(accept);
    await screen.findByRole('button', { name: '核对这次复核' });
    const body = save.mock.calls[0][1]; confirm.mockResolvedValue(event(body));
    const current = page(); current.items[0] = { candidate_id: 'c1', decision: 'rejected', actor: '小周', reason: '后续复核驳回',
      created_at: '2026-10-13T00:00:01Z', request_id: '00000000-0000-4000-8000-000000000009' };
    vi.mocked(reviews.getReviews).mockResolvedValue(current);
    fireEvent.click(screen.getByRole('button', { name: '核对这次复核' }));
    expect(await screen.findByText('人工驳回')).toBeInTheDocument();
    expect(screen.queryByText('人工采纳')).not.toBeInTheDocument();
    expect(screen.getByLabelText('候选 #1 复核理由（可选）')).toHaveValue('后续复核驳回');
    expect(screen.getByRole('button', { name: '驳回候选 #1' })).toBeDisabled();
    expect(save).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(`clipforge:review-pending:v1:${taskId}`)).toBeNull();
  });
  it('requires a state refresh when the operation is confirmed but current state cannot be read', async () => {
    const save = vi.spyOn(reviews, 'saveReview').mockRejectedValue(new reviews.ReviewUnknownError());
    const confirm = vi.spyOn(reviews, 'getReviewRequest');
    render(component());
    const accept = await screen.findByRole('button', { name: '采纳候选 #1' }); await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.change(screen.getByLabelText('复核操作人姓名'), { target: { value: '小林' } }); fireEvent.click(accept);
    await screen.findByRole('button', { name: '核对这次复核' });
    const body = save.mock.calls[0][1]; confirm.mockResolvedValue(event(body));
    vi.mocked(reviews.getReviews).mockRejectedValue(new Error('连接中断'));
    fireEvent.click(screen.getByRole('button', { name: '核对这次复核' }));
    await screen.findByText('这次操作已确认，但当前复核状态暂时无法读取。请刷新复核记录。 连接中断');
    expect(screen.getByRole('button', { name: '采纳候选 #1' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '驳回候选 #1' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '核对这次复核' })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(`clipforge:review-pending:v1:${taskId}`)).toBeNull();
    expect(save).toHaveBeenCalledTimes(1);
  });
  it('retains the unknown request across task switches when browser storage is blocked', async () => {
    const fallbackTask = '00000000-0000-4000-8000-000000000007', nextTask = '00000000-0000-4000-8000-000000000008';
    const nativeGet = Storage.prototype.getItem, nativeSet = Storage.prototype.setItem, nativeRemove = Storage.prototype.removeItem;
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key: string) {
      if (this === sessionStorage) throw new DOMException('Storage blocked', 'SecurityError');
      return nativeGet.call(this, key);
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key: string, value: string) {
      if (this === sessionStorage) throw new DOMException('Storage blocked', 'SecurityError');
      nativeSet.call(this, key, value);
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (this: Storage, key: string) {
      if (this === sessionStorage) throw new DOMException('Storage blocked', 'SecurityError');
      nativeRemove.call(this, key);
    });
    const save = vi.spyOn(reviews, 'saveReview').mockRejectedValueOnce(new reviews.ReviewUnknownError()).mockImplementation((task, body) => Promise.resolve(event(body, task)));
    vi.spyOn(reviews, 'getReviewRequest').mockRejectedValue(new tasks.ApiError('未找到', 404));
    const view = render(component(fallbackTask));
    const accept = await screen.findByRole('button', { name: '采纳候选 #1' }); await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.change(screen.getByLabelText('复核操作人姓名'), { target: { value: '小林' } }); fireEvent.click(accept);
    await screen.findByRole('button', { name: '核对这次复核' });
    const body = save.mock.calls[0][1];
    view.rerender(component(nextTask)); await screen.findByText('待人工复核');
    expect(screen.queryByRole('button', { name: '核对这次复核' })).not.toBeInTheDocument();
    view.rerender(component(fallbackTask)); await screen.findByRole('button', { name: '核对这次复核' });
    expect(screen.getByRole('button', { name: '采纳候选 #1' })).toBeDisabled();
    expect(screen.getByText(`操作编号 ${body.request_id}。核对只读取已有记录。`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '核对这次复核' }));
    const saved = page(fallbackTask); saved.items[0] = { candidate_id: 'c1', decision: 'accepted', actor: body.actor,
      reason: body.reason, created_at: '2026-10-13T00:00:00Z', request_id: body.request_id };
    vi.mocked(reviews.getReviews).mockImplementation(task => Promise.resolve(task === fallbackTask ? saved : page(task)));
    fireEvent.click(await screen.findByRole('button', { name: '重新提交同一次操作' }));
    expect(await screen.findByText('人工采纳')).toBeInTheDocument();
    expect(save).toHaveBeenCalledTimes(2); expect(save.mock.calls[1][1]).toEqual(body);
    view.rerender(component(nextTask)); await screen.findByText('待人工复核');
    view.rerender(component(fallbackTask)); await screen.findByText('人工采纳');
    expect(screen.queryByRole('button', { name: '核对这次复核' })).not.toBeInTheDocument();
  });
  it('ignores an old mutation response after selecting a new task', async () => {
    let resolveOld!: (value: ReviewEvent) => void;
    const save = vi.spyOn(reviews, 'saveReview').mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const view = render(component());
    const accept = await screen.findByRole('button', { name: '采纳候选 #1' });
    await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.change(screen.getByLabelText('复核操作人姓名'), { target: { value: '小林' } }); fireEvent.click(accept);
    const next = '00000000-0000-4000-8000-000000000002'; view.rerender(component(next));
    await screen.findByText('待人工复核');
    await act(async () => resolveOld(event(save.mock.calls[0][1])));
    expect(screen.queryByText('人工采纳')).not.toBeInTheDocument();
    expect(screen.getByText('待人工复核')).toBeInTheDocument();
    expect(save.mock.calls[0][2].aborted).toBe(true);
    expect(screen.queryByText('这次复核结果待确认，暂时停止新的复核提交。')).not.toBeInTheDocument();
  });
  it('requires reloading the candidate snapshot after a replay conflicts', async () => {
    const save = vi.spyOn(reviews, 'saveReview').mockRejectedValueOnce(new reviews.ReviewUnknownError())
      .mockRejectedValueOnce(new tasks.ApiError('候选内容已改变。', 409)).mockImplementation((task, body) => Promise.resolve(event(body, task)));
    vi.spyOn(reviews, 'getReviewRequest').mockRejectedValue(new tasks.ApiError('未找到', 404));
    render(component());
    const accept = await screen.findByRole('button', { name: '采纳候选 #1' });
    await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.change(screen.getByLabelText('复核操作人姓名'), { target: { value: '小林' } }); fireEvent.click(accept);
    fireEvent.click(await screen.findByRole('button', { name: '核对这次复核' }));
    fireEvent.click(await screen.findByRole('button', { name: '重新提交同一次操作' }));
    await screen.findByText('候选内容已改变。 请刷新复核记录，重新核对候选后再提交。');
    expect(screen.getByRole('button', { name: '采纳候选 #1' })).toBeDisabled();
    expect(sessionStorage.getItem(`clipforge:review-pending:v1:${taskId}`)).toBeNull();
    const latest = page(); latest.candidate_snapshot_sha256 = 'b'.repeat(64); vi.mocked(reviews.getReviews).mockResolvedValue(latest);
    fireEvent.click(screen.getByRole('button', { name: '刷新复核记录' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '采纳候选 #1' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '采纳候选 #1' }));
    await screen.findByText('人工采纳');
    expect(save.mock.calls[2][1].expected_snapshot_sha256).toBe('b'.repeat(64));
    expect(save.mock.calls[2][1].request_id).not.toBe(save.mock.calls[0][1].request_id);
  });
  it('ignores a late review fetch from the previously selected task', async () => {
    let resolveOld!: (value: ReviewPage) => void;
    vi.mocked(reviews.getReviews).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const view = render(component());
    const next = '00000000-0000-4000-8000-000000000002'; view.rerender(component(next));
    await screen.findByText('待人工复核');
    const old = page(); old.items[0] = { ...old.items[0], decision: 'accepted', actor: '旧任务姓名', created_at: '2026-10-13T00:00:00Z', request_id: '00000000-0000-4000-8000-000000000003' };
    await act(async () => resolveOld(old));
    expect(screen.queryByText('人工采纳')).not.toBeInTheDocument();
    expect(screen.queryByText(/旧任务姓名/)).not.toBeInTheDocument();
  });
});
