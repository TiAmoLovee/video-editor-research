import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Skeleton, Tag } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { downloadUrl, getCandidates } from '../api/tasks';
import type { CandidatePage } from '../types/tasks';

export function CandidateResults({ taskId, download }: { taskId: string; download: string }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<CandidatePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false); setPage(null);
    getCandidates(taskId, offset, controller.signal).then(result => {
      if (!controller.signal.aborted) setPage(result);
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [taskId, offset, retry]);
  let url: string;
  try { url = downloadUrl(download, taskId); }
  catch { return <Alert type="error" message="候选下载地址异常，请刷新任务。" />; }
  return <section className="candidate-results" aria-label="候选片段推荐">
    <div className="result-head"><div><h2>候选片段推荐</h2><p className="hint">规则分仅用于同一任务内排序。片段可能相互重叠，当前展示推荐时间范围。</p></div><Button href={url} download aria-label="下载评分结果" icon={<DownloadOutlined />}>下载评分结果</Button></div>
    {loading ? <Skeleton active title={false} paragraph={{ rows: 3 }} aria-label="正在加载候选" /> : error ? <Alert type="warning" message="候选暂时加载失败，切片下载仍可使用。" action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试候选</Button>} /> : page && <>
      {page.total === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合 15–90 秒条件的完整句子片段。视频可能较短、没有转写内容，或句间停顿较长。" /> : <>
        {page.audio_status === 'unavailable' && <Alert type="info" message="本次没有可用音量证据，音量项记 0 分，其余评分正常。" />}
        <p className="hint" role="status">显示 {page.offset + 1}–{page.offset + page.items.length} / {page.total} 个候选</p>
        <ol className="candidate-list" start={page.offset + 1}>{page.items.map(candidate => <li key={candidate.id} className="candidate-card">
          <div className="head"><strong>#{candidate.rank} · {candidate.start.toFixed(2)}–{candidate.end.toFixed(2)} 秒</strong><Tag color="purple">规则分 {candidate.score.toFixed(2)}</Tag></div>
          <p className="hint">时长 {candidate.duration_seconds.toFixed(2)} 秒</p>
          <p className="candidate-text">{candidate.text}</p>
          <details><summary>查看评分原因</summary><ul>{candidate.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></details>
        </li>)}</ol>
        <div className="candidate-pager"><Button disabled={page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - 10))}>上一页候选</Button><Button disabled={!page.has_more} onClick={() => setOffset(page.offset + 10)}>下一页候选</Button></div>
      </>}
    </>}
  </section>;
}
