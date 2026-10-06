import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Skeleton, Tag } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { acceptedVideoUrl, downloadUrl, getCandidates, validId } from '../api/tasks';
import type { CandidatePage, CandidateView } from '../types/tasks';

const fallbackDetails: Record<string, string> = {
  invalid_assessment: '模型返回的分项分数或原文引用未通过校验。',
  invalid_output: '模型返回的评分格式未通过校验。',
  incomplete_or_refused: '模型没有返回完整、可用的评分。',
  timeout: '模型响应超时。',
  task_timeout: '模型评分超出本次等待时间。',
  free_quota_exhausted: '百炼免费额度已耗尽或到期，已停止模型调用。',
  missing_key: '尚未配置可用的模型密钥。',
  invalid_key: '模型密钥验证失败。',
  network_error: '暂时无法连接模型服务。',
  token_budget: '已达到本次模型用量限制。',
};

const boundaryDetails: Record<string, string> = {
  model_boundary_uncertain: '模型认为首尾完整性或上下文仍需核对。',
  topic_change_inside: '人工确认的场景与话题切换落在此范围内。',
  scene_change_near_edge: '开头或结尾附近存在镜头切换，需核对是否混入另一段内容。',
  word_crosses_visual_cut: '画面切点与字词时间冲突，不能直接按画面截断。',
  no_nearby_word_safe_boundary: '附近没有可用的完整字词边界，暂不提供自动修正范围。',
  proposal_outside_duration_limits: '调整后不足 15 秒或超过 90 秒，暂不采用。',
  conditional_tail_needs_review: '结尾疑似只说了条件，后面的结果或解释可能还没讲完。',
  connective_tail_needs_review: '结尾疑似停在连接表达上，请核对后文。',
  demonstrative_tail_needs_review: '结尾可能省略了后文，也可能是完整的口语表达，请试听确认。',
};

export function CandidateResults({ taskId, download }: { taskId: string; download: string }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<CandidatePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [view, setView] = useState<CandidateView>('all');
  const [topic, setTopic] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false);
    const request = view === 'all' ? getCandidates(taskId, offset, controller.signal) : view === 'topics' ? getCandidates(taskId, offset, controller.signal, view, topic) : getCandidates(taskId, offset, controller.signal, view);
    request.then(result => {
      if (!controller.signal.aborted) setPage(result);
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [taskId, offset, retry, view, topic]);
  let url: string;
  try { url = downloadUrl(download, taskId); }
  catch { return <Alert type="error" message="候选下载地址异常，请刷新任务。" />; }
  return <section className="candidate-results" aria-label="候选片段推荐" aria-busy={loading}>
    <div className="result-head"><div><h2>候选片段推荐</h2><p className="hint">评分仅用于同一任务内排序。片段可能相互重叠，当前展示推荐时间范围。</p></div><Button href={url} download aria-label="下载评分结果" icon={<DownloadOutlined />}>下载评分结果</Button></div>
    {loading && page && <p role="status">正在更新候选列表…</p>}
    {loading && !page ? <Skeleton active title={false} paragraph={{ rows: 3 }} aria-label="正在加载候选" /> : error ? <Alert type="warning" message="候选暂时加载失败，切片下载仍可使用。" action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试候选</Button>} /> : page && <>
      {!!page.accepted_versions?.length && <section aria-label="已试听通过的成品">
        <h3>已试听通过的成品</h3>
        <p className="hint">这里保留人工确认过的具体版本。下方原始候选的分数与风险记录仍独立保留。</p>
        {page.accepted_versions.map(item => {
          const media = acceptedVideoUrl(item.video_url, taskId);
          return <article key={item.id} className="candidate-card">
            <div className="head"><strong>{item.start.toFixed(2)}–{item.end.toFixed(2)} 秒 · {item.duration_seconds.toFixed(2)} 秒</strong><Tag color="green">已试听通过</Tag></div>
            <p>{item.feedback}</p>
            <p>{item.score_scope === 'original_candidate_range' ? '原候选' : '此修正版'}{item.scorer === 'llm' ? '模型分' : '规则分'} {item.score.toFixed(2)}</p>
            {item.score_scope === 'original_candidate_range' && <p className="hint">评分范围 {item.score_start.toFixed(2)}–{item.score_end.toFixed(2)} 秒；试听版按视频帧对齐，未另行重评分。</p>}
            {media ? <><video controls preload="metadata" src={media} style={{ width: '100%', maxHeight: 420 }} aria-label="已验收片段播放器" /><Button href={media} download>下载已验收成品</Button></> : <Alert type="warning" message="成品地址异常，请重新加载。" />}
            <details><summary>查看此版本的文字与评分原因</summary><p>{item.text}</p><ul>{item.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></details>
          </article>;
        })}
      </section>}
      {page.selection_summary && <>
        <Alert type="info" message={`去重保留 ${page.selection_summary.retained_count} / ${page.selection_summary.original_count} 个，其中 ${page.selection_summary.retained_review_count} 个仍需边界复核。`} description="去重依据原范围的时间重叠，不代表语义完整性已通过。修正范围只是待复核草案，尚未重新评分或生成视频。" />
        <div className="candidate-pager"><Button type={view === 'all' ? 'primary' : 'default'} onClick={() => { setOffset(0); setView('all'); }}>全部候选</Button><Button type={view === 'retained' ? 'primary' : 'default'} onClick={() => { setOffset(0); setView('retained'); }}>只看去重保留</Button><Button type={view === 'review' ? 'primary' : 'default'} onClick={() => { setOffset(0); setView('review'); }}>只看待复核</Button>{validId(taskId) && <Button href={`/tasks/${taskId}/selection`} download>下载边界与去重记录</Button>}</div>
      </>}
      {page.fallback_reason && <Alert type="info" message="模型评分未完成，本次全部候选已使用规则评分。" description={`${fallbackDetails[page.fallback_reason] || '详细原因请查看处理记录。'} 未采用部分模型分数。`} />}
      {page.topics && <section aria-label="合集分组">
        <h3>合集分组（试用）</h3>
        <p className="hint">时间去重后的 {page.topics.summary.input_count} 个片段，按文字相似程度分为 {page.topics.summary.group_count} 组；疑似重复 {page.topics.summary.duplicate_count} 个。组名摘自原文，分组仍需人工核对。</p>
        <div className="candidate-pager"><Button type={view === 'topics' && !topic ? 'primary' : 'default'} onClick={() => { setOffset(0); setTopic(undefined); setView('topics'); }}>按组查看 · 折叠疑似重复</Button>
          {page.topics.groups.map((group, index) => <Button key={group.id} type={view === 'topics' && topic === group.id ? 'primary' : 'default'} onClick={() => { setOffset(0); setTopic(group.id); setView('topics'); }}>组 {index + 1} · {group.label}（{group.recommended_count}）</Button>)}
        </div>
        <p className="hint">同组不代表内容重复，也不代表首尾完整。折叠只影响此视图，可在“只看去重保留”查看全部分组成员；尚未生成合集视频。</p>
      </section>}
      {page.scorer === 'llm' && page.scoring_version === 'llm-v1' && <Alert type="info" message="旧版文本评分" description="这是此任务生成时保存的结果。更新程序不会改写历史评分；要验证新版，请新建一次任务。" />}
      {page.scorer === 'llm' && page.scoring_version === 'llm-v2' && <p className="hint"><Tag color="blue">新版分项评分</Tag>总分由四项相加，理由引用原文；评分是否合理仍需人工判断。</p>}
      {page.scorer === 'llm' && <p className="hint">本次使用模型文本评分；未评估画面和声音。规则分保留供对比，两者不能直接当作同一评分标准。</p>}
      {page.total === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={view === 'review' ? '当前规则未标出待复核项，不代表所有候选都已人工验收。' : '没有符合 15–90 秒条件的完整句子片段。视频可能较短、没有转写内容，或句间停顿较长。'} /> : <>
        {page.audio_status === 'unavailable' && <Alert type="info" message="本次没有可用音量证据，音量项记 0 分，其余评分正常。" />}
        <p className="hint" role="status">显示 {page.offset + 1}–{page.offset + page.items.length} / {page.total} 个候选</p>
        <ol className="candidate-list" start={page.offset + 1}>{page.items.map(candidate => <li key={candidate.id} className="candidate-card">
          <div className="head"><strong>#{candidate.rank} · {candidate.start.toFixed(2)}–{candidate.end.toFixed(2)} 秒</strong><Tag color="purple">{candidate.scorer === 'llm' ? '模型分' : '规则分'} {candidate.score.toFixed(2)}</Tag></div>
          {candidate.scorer === 'llm' && candidate.rule_score != null && <p className="hint">规则基线 {candidate.rule_score.toFixed(2)}</p>}
          <p className="hint">时长 {candidate.duration_seconds.toFixed(2)} 秒</p>
          <p className="candidate-text">{candidate.text}</p>
          {candidate.selection && <>
            {candidate.selection.topic && <p><Tag color="cyan">文字分组：{candidate.selection.topic.group_label}</Tag>{candidate.selection.topic.duplicate_of && <Tag>疑似重复候选 #{candidate.selection.topic.duplicate_rank} · 文字相似度 {Math.round((candidate.selection.topic.duplicate_similarity ?? 0) * 100)}%</Tag>}</p>}
            <p><Tag color={candidate.selection.retained ? 'blue' : 'default'}>{candidate.selection.retained ? '去重保留' : '重叠已折叠'}</Tag><Tag color={candidate.selection.boundary_status === 'review_required' ? 'orange' : 'default'}>{candidate.selection.boundary_status === 'review_required' ? '边界待复核' : '完整性未人工验收'}</Tag></p>
            {candidate.selection.issues.map(issue => <p className="hint" key={issue}>{boundaryDetails[issue] || '请查看边界复核记录。'}</p>)}
            {candidate.selection.text_review && <Alert type="warning" message={`结尾待核对：“${candidate.selection.text_review.signal.quote}”`} description={<>
              <p>{candidate.selection.text_review.signal.reason}。这是文字线索，需要试听判断。</p>
              {candidate.selection.text_review.existing_extension && <p>可参考已有候选 #{candidate.selection.text_review.existing_extension.rank}：{candidate.selection.text_review.existing_extension.start.toFixed(2)}–{candidate.selection.text_review.existing_extension.end.toFixed(2)} 秒，{candidate.selection.text_review.existing_extension.scorer === 'llm' ? '模型分' : '规则分'} {candidate.selection.text_review.existing_extension.score.toFixed(2)}。这个更长版本仍需试听。</p>}
              {candidate.selection.text_review.existing_contraction && <p>可参考较短候选 #{candidate.selection.text_review.existing_contraction.rank}：{candidate.selection.text_review.existing_contraction.start.toFixed(2)}–{candidate.selection.text_review.existing_contraction.end.toFixed(2)} 秒，{candidate.selection.text_review.existing_contraction.scorer === 'llm' ? '模型分' : '规则分'} {candidate.selection.text_review.existing_contraction.score.toFixed(2)}。仅供对比，不自动替换原片段，仍需试听确认。</p>}
            </>} />}
            {candidate.selection.proposal && <Alert type="warning" message={`修正草案：${candidate.selection.proposal.start.toFixed(2)}–${candidate.selection.proposal.end.toFixed(2)} 秒`} description={<><p>{candidate.selection.proposal.text}</p><p>上方分数只属于原范围。此草案待试听确认及重新评分，没有沿用原分数。</p></>} />}
            {candidate.selection.end_context && <details>
              <summary>查看结尾上下文</summary>
              <p className="hint">以下是原转写，可能有错字。后文不在本候选中；间隔和语音检测都不能证明一句话是否说完。</p>
              <strong>片段内最后两句（不足两句时按实际显示）</strong>
              {candidate.selection.end_context.ending_sentences.map(sentence => <p key={sentence.id}>{sentence.start.toFixed(2)}–{sentence.end.toFixed(2)} 秒：{sentence.text}</p>)}
              <strong>片段外后文 · 仅供对照</strong>
              {candidate.selection.end_context.following_sentences.length ? <>
                <p>到下一句转写起点的间隔：{candidate.selection.end_context.next_sentence_gap_seconds?.toFixed(2)} 秒（不等于实际静音时长）。</p>
                {candidate.selection.end_context.following_sentences.map(sentence => <p key={sentence.id}>{sentence.start.toFixed(2)}–{sentence.end.toFixed(2)} 秒：{sentence.text}</p>)}
              </> : <p>没有后续转写，不能据此判断结尾完整。</p>}
              <p className="hint">语音检测{candidate.selection.end_context.vad_speech_at_end ? '在切点仍标记了语音' : '在切点未标记语音'}；这是检测结果，不能直接作为裁切依据。</p>
            </details>}
          </>}
          <details><summary>查看评分原因</summary><ul>{candidate.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></details>
        </li>)}</ol>
        <div className="candidate-pager"><Button disabled={page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - 10))}>上一页候选</Button><Button disabled={!page.has_more} onClick={() => setOffset(page.offset + 10)}>下一页候选</Button></div>
      </>}
    </>}
  </section>;
}
