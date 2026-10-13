import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Skeleton } from 'antd';
import { auditDownloadUrl, getAudit } from '../api/audit';
import type { AuditClip, AuditEvent, AuditPage, EditOperation } from '../types/audit';
import type { ReviewDecision } from '../types/reviews';

const decisions: Record<ReviewDecision, string> = { accepted: '采纳', rejected: '驳回', undecided: '待复核' };
function range(clip: AuditClip | null): string { return clip ? `第 ${clip.source_in_frame}–${clip.source_out_frame} 帧（末帧不含）` : '无'; }
function operationText(operation: EditOperation): string {
  if (operation.kind === 'reordered') return `调整顺序：${operation.before.join(' → ')} 改为 ${operation.after.join(' → ')}`;
  const label = operation.kind === 'boundary_changed' ? '修改边界' : operation.kind === 'added' ? '加入片段' : '移除片段';
  return `${label} ${operation.candidate_id}：${range(operation.before)} → ${range(operation.after)}`;
}
function AuditEventDetails({ event }: { event: AuditEvent }) {
  return <li className="candidate-card" style={{ margin: '8px 0' }}>
    <strong>{event.kind === 'candidate_review' ? '候选复核' : event.kind === 'edl_adoption' ? '保存剪辑清单' : '人工编辑'} · {event.actor ?? '历史记录未填写姓名'}</strong>
    <p className="hint"><time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString('zh-CN')}</time></p>
    {event.kind === 'candidate_review' ? <>
      <p>片段 {event.candidate_id}：{decisions[event.before.decision]} → {decisions[event.after.decision]}</p>
      {(event.before.reason || event.after.reason) && <p>理由：{event.before.reason || '未填写'} → {event.after.reason || '未填写'}</p>}
    </> : <>
      <p>剪辑清单：{event.before.clips.length} 段 / {event.before.duration_frames} 帧 → {event.after.clips.length} 段 / {event.after.duration_frames} 帧</p>
      {event.operations.length ? <ul>{event.operations.map((operation, index) => <li key={index}>{operationText(operation)}</li>)}</ul> : <p>本次没有片段内容变化。</p>}
      <details><summary>查看修改前后清单</summary><p>修改前</p><ol>{event.before.clips.map(clip => <li key={clip.candidate_id}>{clip.candidate_id} · {range(clip)}</li>)}</ol>
        <p>修改后</p><ol>{event.after.clips.map(clip => <li key={clip.candidate_id}>{clip.candidate_id} · {range(clip)}</li>)}</ol></details>
    </>}
  </li>;
}
export function AuditPanel({ taskId }: { taskId: string }) { return <AuditForTask key={taskId} taskId={taskId} />; }
function AuditForTask({ taskId }: { taskId: string }) {
  const [page, setPage] = useState<AuditPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [visible, setVisible] = useState(50);
  useEffect(() => {
    const changed = (event: Event) => {
      if (event instanceof CustomEvent && event.detail?.taskId === taskId) setRefresh(value => value + 1);
    };
    window.addEventListener('clipforge:audit-changed', changed);
    return () => window.removeEventListener('clipforge:audit-changed', changed);
  }, [taskId]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    getAudit(taskId, controller.signal).then(value => { if (!controller.signal.aborted) setPage(value); })
      .catch(error => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : '操作记录暂时无法读取。'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [taskId, refresh]);
  let download: string;
  try { download = auditDownloadUrl(taskId); }
  catch { return <Alert type="warning" message="操作记录地址异常。" />; }
  return <details className="more" aria-label="人工操作记录">
    <summary>人工操作记录 · 复核与剪辑修改</summary>
    <p className="hint">记录谁在何时修改了哪个片段，并保留修改前后内容。姓名由操作人填写。</p>
    <div className="candidate-pager"><Button size="small" disabled={loading} onClick={() => setRefresh(value => value + 1)}>刷新操作记录</Button>
      <Button size="small" href={download} download>下载完整操作记录</Button></div>
    {error && <Alert type="warning" message={error} />}
    {loading && !page ? <Skeleton active title={false} paragraph={{ rows: 2 }} aria-label="正在加载操作记录" /> : page && <>
      {(page.unrecorded_actor_event_count ?? page.unrecorded_actor_edit_count) > 0 && <p className="hint">有 {page.unrecorded_actor_event_count ?? page.unrecorded_actor_edit_count} 次历史操作未填写姓名。</p>}
      {!page.events.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有保存的人工操作记录。" /> : <>
        <p className="hint">共 {page.events.length} 次操作，最新记录在前。</p>
        <ol style={{ paddingLeft: 20 }}>{[...page.events].reverse().slice(0, visible).map(event => <AuditEventDetails key={`${event.kind}:${event.event_id}`} event={event} />)}</ol>
        {visible < page.events.length && <Button size="small" onClick={() => setVisible(value => value + 50)}>显示更多操作记录</Button>}
      </>}
    </>}
  </details>;
}
