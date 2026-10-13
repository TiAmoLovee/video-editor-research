import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Tag } from 'antd';
import { ApiError, validId } from '../api/tasks';
import { getReviewRequest, getReviews, newReviewRequestId, reviewActorError, ReviewUnknownError, saveReview } from '../api/reviews';
import type { CandidateReview, ReviewDecision, ReviewEvent, ReviewPage, ReviewWrite } from '../types/reviews';
import { useActor } from '../hooks/useActor';

const labels: Record<ReviewDecision, string> = { accepted: '人工采纳', rejected: '人工驳回', undecided: '待人工复核' };
const pendingKey = (taskId: string) => `clipforge:review-pending:v1:${taskId}`;
const pendingFallback = new Map<string, ReviewWrite>();
const fallbackOnly = new Set<string>();
function readPending(taskId: string): ReviewWrite | null {
  if (fallbackOnly.has(taskId)) return pendingFallback.get(taskId) ?? null;
  try {
    const raw = sessionStorage.getItem(pendingKey(taskId));
    if (!raw) { pendingFallback.delete(taskId); return null; }
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object') return pendingFallback.get(taskId) ?? null;
    const body = value as ReviewWrite;
    const valid = typeof body.candidate_id === 'string' && !!body.candidate_id.trim() && body.candidate_id.length <= 200
      && ['accepted', 'rejected', 'undecided'].includes(body.decision) && typeof body.actor === 'string' && !reviewActorError(body.actor)
      && (body.reason === null || (typeof body.reason === 'string' && body.reason.length <= 2000))
      && typeof body.request_id === 'string' && validId(body.request_id)
      && typeof body.expected_snapshot_sha256 === 'string' && /^[a-f0-9]{64}$/.test(body.expected_snapshot_sha256);
    if (valid) { pendingFallback.set(taskId, body); return body; }
    return pendingFallback.get(taskId) ?? null;
  } catch { fallbackOnly.add(taskId); return pendingFallback.get(taskId) ?? null; }
}
function persistPending(taskId: string, body: ReviewWrite | null) {
  if (body) pendingFallback.set(taskId, body); else pendingFallback.delete(taskId);
  try {
    if (body) sessionStorage.setItem(pendingKey(taskId), JSON.stringify(body)); else sessionStorage.removeItem(pendingKey(taskId));
    fallbackOnly.delete(taskId);
  } catch { fallbackOnly.add(taskId); }
}

export function useCandidateReviews(taskId: string) {
  const [page, setPage] = useState<ReviewPage | null>(null);
  const [actor, setActor] = useActor();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [unknown, setUnknown] = useState<ReviewWrite | null>(() => readPending(taskId));
  const [canResubmit, setCanResubmit] = useState(false);
  const mounted = useRef(false);
  const lock = useRef(false);
  const activeController = useRef<AbortController>();
  const unknownWrite = useRef<ReviewWrite | null>(unknown);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    getReviews(taskId, controller.signal).then(value => {
      if (!controller.signal.aborted) setPage(value);
    }).catch(error => {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : '复核记录暂时无法读取。');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { mounted.current = false; controller.abort(); activeController.current?.abort(); };
  }, [taskId]);
  function publish(event: ReviewEvent, current?: ReviewPage | null) {
    if (!mounted.current) return;
    setPage(previous => current !== undefined ? current : previous && previous.candidate_snapshot_sha256 === event.candidate_snapshot_sha256
        ? { ...previous, items: previous.items.map(item => item.candidate_id === event.candidate_id
          ? { candidate_id: event.candidate_id, decision: event.decision, actor: event.actor, reason: event.reason, created_at: event.created_at, request_id: event.request_id } : item) }
        : null);
    unknownWrite.current = null; persistPending(taskId, null); setUnknown(null); setCanResubmit(false); setError('');
    setNotice(page?.candidate_snapshot_sha256 === event.candidate_snapshot_sha256
      ? `已保存${labels[event.decision]}，操作人：${event.actor}。`
      : '已核对这次操作记录；候选内容已改变，请刷新复核记录后重新核对。');
    window.dispatchEvent(new CustomEvent('clipforge:audit-changed', { detail: { taskId } }));
  }
  async function refresh() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    const controller = new AbortController(); activeController.current = controller;
    try {
      const value = await getReviews(taskId, controller.signal);
      if (mounted.current && !controller.signal.aborted) { setPage(value); setNotice('已刷新复核记录。'); }
    } catch (error) {
      if (mounted.current && !controller.signal.aborted) setError(error instanceof Error ? error.message : '复核记录暂时无法读取。');
    } finally { lock.current = false; if (mounted.current && !controller.signal.aborted) { setBusy(false); setLoading(false); } }
  }
  async function save(candidateId: string, decision: ReviewDecision, reason: string) {
    if (lock.current || unknownWrite.current) return;
    const invalidActor = reviewActorError(actor);
    if (invalidActor) { setError(invalidActor); return; }
    if (reason.trim().length > 2000) { setError('复核理由最多 2000 字。'); return; }
    if (!page || !page.items.some(item => item.candidate_id === candidateId)) { setError('请先刷新复核记录再提交。'); return; }
    const body: ReviewWrite = { candidate_id: candidateId, decision, actor: actor.trim(), reason: reason.trim() || null,
      request_id: newReviewRequestId(), expected_snapshot_sha256: page.candidate_snapshot_sha256 };
    await submit(body);
  }
  async function refreshConfirmed(result: ReviewEvent, controller: AbortController) {
    // Recovery acknowledges a past request. Another window may have changed
    // the decision since then, so only a fresh state read unlocks the controls.
    publish(result, null);
    try {
      const current = await getReviews(taskId, controller.signal);
      if (mounted.current && !controller.signal.aborted) {
        setPage(current); setNotice('已核对这次操作记录，并刷新当前复核状态。');
      }
    } catch (error) {
      if (mounted.current && !controller.signal.aborted) setError(`这次操作已确认，但当前复核状态暂时无法读取。请刷新复核记录。${error instanceof Error ? ` ${error.message}` : ''}`);
    }
  }
  async function submit(body: ReviewWrite, recovery = false) {
    persistPending(taskId, body);
    lock.current = true; setBusy(true); setCanResubmit(false); setError(''); setNotice('');
    const controller = new AbortController(); activeController.current = controller;
    try {
      const result = await saveReview(taskId, body, controller.signal);
      if (mounted.current && !controller.signal.aborted) {
        if (recovery) await refreshConfirmed(result, controller);
        else publish(result);
      }
    } catch (error) {
      if (mounted.current && !controller.signal.aborted) {
        if (error instanceof ReviewUnknownError) { unknownWrite.current = body; setUnknown(body); setError(error.message); }
        else {
          persistPending(taskId, null); unknownWrite.current = null; setUnknown(null);
          if (error instanceof ApiError && error.status === 409) {
            setPage(null); setError(`${error.message} 请刷新复核记录，重新核对候选后再提交。`);
          } else setError(error instanceof Error ? error.message : '复核暂时无法保存。');
        }
      }
    } finally { lock.current = false; if (mounted.current && !controller.signal.aborted) setBusy(false); }
  }
  async function confirmUnknown() {
    const body = unknownWrite.current;
    if (!body || lock.current) return;
    lock.current = true; setBusy(true); setError('');
    const controller = new AbortController(); activeController.current = controller;
    try {
      const result = await getReviewRequest(taskId, body.request_id, controller.signal);
      if (result.candidate_id !== body.candidate_id || result.decision !== body.decision || result.actor !== body.actor
        || result.reason !== body.reason || result.candidate_snapshot_sha256 !== body.expected_snapshot_sha256) throw new Error('操作记录与这次提交不一致，请检查后台记录。');
      if (mounted.current && !controller.signal.aborted) await refreshConfirmed(result, controller);
    } catch (error) {
      if (mounted.current && !controller.signal.aborted) {
        const notFound = error instanceof ApiError && error.status === 404;
        setCanResubmit(notFound);
        setError(notFound ? '尚未查到这次操作，结果仍待确认。可再次核对，或使用相同操作编号重新提交这一次操作。'
          : error instanceof Error ? error.message : '这次操作仍待确认，请稍后核对。');
      }
    } finally { lock.current = false; if (mounted.current && !controller.signal.aborted) setBusy(false); }
  }
  async function resubmitUnknown() {
    const body = unknownWrite.current;
    if (!body || lock.current || !canResubmit) return;
    await submit(body, true);
  }
  return { page, actor, setActor, error, notice, loading, busy, unknown, canResubmit, save, refresh, confirmUnknown, resubmitUnknown };
}

type ReviewController = ReturnType<typeof useCandidateReviews>;
export function CandidateReviewHeader({ reviews }: { reviews: ReviewController }) {
  return <section aria-label="候选人工复核设置">
    <h3>候选人工复核</h3>
    <p className="hint">填写操作人姓名后，可保存采纳、驳回和撤销记录。此姓名用于操作记录，不代表登录身份。</p>
    <p className="hint">采纳后可点击“加入成片”；复核决定与剪辑清单独立，修改后仍需保存剪辑版本。</p>
    <label htmlFor="candidate-review-actor">复核操作人姓名</label>
    <Input id="candidate-review-actor" aria-label="复核操作人姓名" placeholder="填写你的名字" maxLength={80} value={reviews.actor}
      disabled={reviews.busy} onChange={event => reviews.setActor(event.target.value)} style={{ maxWidth: 280, margin: '8px' }} />
    <Button size="small" disabled={reviews.busy || reviews.loading} onClick={reviews.refresh}>刷新复核记录</Button>
    {reviews.loading && <p role="status">正在读取复核记录…</p>}
    {reviews.error && <Alert type="warning" message={reviews.error} />}
    {reviews.unknown && <Alert type="warning" message="这次复核结果待确认，暂时停止新的复核提交。"
      description={`操作编号 ${reviews.unknown.request_id}。核对只读取已有记录。`}
      action={<div><Button size="small" disabled={reviews.busy} onClick={reviews.confirmUnknown}>核对这次复核</Button>
        {reviews.canResubmit && <Button size="small" disabled={reviews.busy} onClick={reviews.resubmitUnknown}>重新提交同一次操作</Button>}</div>} />}
    {reviews.notice && <p role="status" aria-live="polite">{reviews.notice}</p>}
  </section>;
}

export function CandidateReviewControls({ candidateId, rank, reviews, disabled }: { candidateId: string; rank: number; reviews: ReviewController; disabled: boolean }) {
  const review: CandidateReview | undefined = reviews.page?.items.find(item => item.candidate_id === candidateId);
  const [reason, setReason] = useState(review?.reason ?? '');
  useEffect(() => { setReason(review?.reason ?? ''); }, [review?.request_id, review?.reason]);
  const blocked = disabled || reviews.loading || reviews.busy || !!reviews.unknown || !review;
  return <div aria-label={`候选 #${rank} 人工复核`} style={{ margin: '12px 0' }}>
    {review ? <><Tag color={review.decision === 'accepted' ? 'green' : review.decision === 'rejected' ? 'red' : 'default'}>{labels[review.decision]}</Tag>
      {review.actor && <p className="hint">{review.actor} · {new Date(review.created_at!).toLocaleString('zh-CN')} {review.reason && `· ${review.reason}`}</p>}</> : <Tag>复核记录未加载</Tag>}
    <Input.TextArea aria-label={`候选 #${rank} 复核理由（可选）`} placeholder="复核理由（可选）" value={reason} maxLength={2000} rows={2}
      disabled={blocked} onChange={event => setReason(event.target.value)} style={{ marginBottom: 8 }} />
    <div className="candidate-pager">
      <Button size="small" disabled={blocked || review?.decision === 'accepted'} aria-label={`采纳候选 #${rank}`} onClick={() => reviews.save(candidateId, 'accepted', reason)}>采纳</Button>
      <Button size="small" disabled={blocked || review?.decision === 'rejected'} aria-label={`驳回候选 #${rank}`} onClick={() => reviews.save(candidateId, 'rejected', reason)}>驳回</Button>
      <Button size="small" disabled={blocked || review?.decision === 'undecided'} aria-label={`撤销候选 #${rank} 为待复核`} onClick={() => reviews.save(candidateId, 'undecided', reason)}>撤销为待复核</Button>
    </div>
  </div>;
}
