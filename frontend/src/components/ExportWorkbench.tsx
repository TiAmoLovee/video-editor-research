import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Empty, Tag } from 'antd';
import * as editing from '../api/editing';
import type { ArtifactHistory, EdlPage, EdlSummary, SavedEdl } from '../types/editing';
import type { Candidate } from '../types/tasks';

interface Props {
  taskId: string;
  selected: Candidate[];
  onRemove: (id: string) => void;
  onMove: (id: string, direction: -1 | 1) => void;
  onBusyChange?: (busy: boolean) => void;
}
const noArtifacts: ArtifactHistory = { subtitles: [], renders: [], mlt_exports: [], unavailable_count: 0 };
const noPage: EdlPage = { items: [], total: 0, offset: 0, limit: 10, has_more: false, unavailable_count: 0 };
interface RecoveryRecord { version: 1; task_id: string; operation_id: string; preferred_revision?: string }
const recoveryFallback = new Map<string, RecoveryRecord>();
const storageFallbackOnly = new Set<string>();
const recoveryKey = (task: string) => `clipforge:export-recovery:v1:${task}`;
let operationCounter = 0;
function readRecovery(task: string): RecoveryRecord | undefined {
  if (storageFallbackOnly.has(task)) return recoveryFallback.get(task);
  try {
    const raw = sessionStorage.getItem(recoveryKey(task));
    if (!raw) { recoveryFallback.delete(task); return undefined; }
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object') return recoveryFallback.get(task);
    const item = value as Partial<RecoveryRecord>;
    if (item.version !== 1 || item.task_id !== task || typeof item.operation_id !== 'string'
      || (item.preferred_revision !== undefined && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(item.preferred_revision))) return recoveryFallback.get(task);
    recoveryFallback.set(task, item as RecoveryRecord);
    return item as RecoveryRecord;
  } catch { return recoveryFallback.get(task); }
}
function writeRecovery(item: RecoveryRecord) {
  recoveryFallback.set(item.task_id, item);
  try { sessionStorage.setItem(recoveryKey(item.task_id), JSON.stringify(item)); storageFallbackOnly.delete(item.task_id); }
  catch { storageFallbackOnly.add(item.task_id); }
}
function clearRecovery(task: string, operationId: string) {
  if (readRecovery(task)?.operation_id !== operationId) return;
  recoveryFallback.delete(task);
  try { sessionStorage.removeItem(recoveryKey(task)); storageFallbackOnly.delete(task); }
  catch { storageFallbackOnly.add(task); }
}
const shortId = (value: string) => value.slice(0, 8);
const dateLabel = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
};
const messageOf = (error: unknown) => error instanceof Error ? error.message : '暂时无法读取，请稍后刷新已保存记录。';

export function ExportWorkbench({ taskId, selected, onRemove, onMove, onBusyChange }: Props) {
  const [page, setPage] = useState<EdlPage>(noPage);
  const [saved, setSaved] = useState<SavedEdl | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactHistory>(noArtifacts);
  const [subtitleId, setSubtitleId] = useState('');
  const [withSubtitles, setWithSubtitles] = useState(false);
  const [reading, setReading] = useState(true);
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [unknown, setUnknown] = useState(() => !!readRecovery(taskId));
  const [reviewReady, setReviewReady] = useState(false);
  const [previewFailed, setPreviewFailed] = useState('');
  const currentTask = useRef(taskId);
  currentTask.current = taskId;
  const view = useRef(0);
  const readController = useRef<AbortController>();
  const operation = useRef<{ controller: AbortController; task: string; view: number; id: string }>();
  const savedRef = useRef(saved);
  savedRef.current = saved;
  const busyCallback = useRef(onBusyChange);
  busyCallback.current = onBusyChange;
  const stillCurrent = (task: string, token: number) => currentTask.current === task && view.current === token;

  async function readRecords(task: string, token: number, signal: AbortSignal, preferred?: string) {
    const nextPage = await editing.listEdls(task, 0, signal);
    const revision = preferred ?? nextPage.items[0]?.revision_id;
    let nextSaved: SavedEdl | null = null;
    let nextArtifacts = noArtifacts;
    if (revision) {
      [nextSaved, nextArtifacts] = await Promise.all([
        editing.getEdl(task, revision, signal), editing.listArtifacts(task, revision, signal),
      ]);
    }
    if (!stillCurrent(task, token) || signal.aborted) return;
    const sameRevision = savedRef.current?.revision.revision_id === nextSaved?.revision.revision_id;
    setPage(nextPage); setSaved(nextSaved); setArtifacts(nextArtifacts);
    setSubtitleId(previous => sameRevision && nextArtifacts.subtitles.some(item => item.subtitle_id === previous)
      ? previous : nextArtifacts.subtitles[0]?.subtitle_id ?? '');
    if (!sameRevision) setWithSubtitles(false);
    setError('');
  }

  async function refreshRecords() {
    if (operation.current) return;
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    const token = ++view.current;
    const task = taskId;
    const recovery = readRecovery(task);
    const preferred = recovery ? recovery.preferred_revision : savedRef.current?.revision.revision_id;
    setReading(true); setNotice(''); setReviewReady(false);
    try {
      await readRecords(task, token, controller.signal, preferred);
      if (stillCurrent(task, token) && !controller.signal.aborted) {
        setReviewReady(!!readRecovery(task));
        setNotice('记录已刷新，请核对新增版本和文件后继续。');
      }
    }
    catch (caught) { if (stillCurrent(task, token) && !controller.signal.aborted) setError(messageOf(caught)); }
    finally { if (stillCurrent(task, token)) setReading(false); }
  }

  useEffect(() => {
    if (operation.current) {
      operation.current.controller.abort(); operation.current = undefined;
    }
    busyCallback.current?.(false);
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    const token = ++view.current;
    setPage(noPage); setSaved(null); savedRef.current = null; setArtifacts(noArtifacts);
    setSubtitleId(''); setWithSubtitles(false); setBusy(''); setError(''); setNotice('');
    const recovery = readRecovery(taskId);
    setUnknown(!!recovery); setReviewReady(false); setPreviewFailed(''); setReading(true);
    readRecords(taskId, token, controller.signal, recovery?.preferred_revision).catch(caught => {
      if (stillCurrent(taskId, token) && !controller.signal.aborted) setError(messageOf(caught));
    }).finally(() => { if (stillCurrent(taskId, token)) setReading(false); });
    return () => {
      controller.abort();
      readController.current?.abort();
      if (operation.current?.task === taskId) {
        operation.current.controller.abort(); operation.current = undefined;
        busyCallback.current?.(false);
      }
    };
  }, [taskId]);

  async function chooseRevision(revision: string) {
    if (operation.current || reading || !revision) return;
    readController.current?.abort();
    const controller = new AbortController(); readController.current = controller;
    const token = ++view.current; const task = taskId;
    setReading(true); setError(''); setNotice(''); setReviewReady(false);
    try {
      const [nextSaved, nextArtifacts] = await Promise.all([
        editing.getEdl(task, revision, controller.signal), editing.listArtifacts(task, revision, controller.signal),
      ]);
      if (!stillCurrent(task, token) || controller.signal.aborted) return;
      setSaved(nextSaved); setArtifacts(nextArtifacts);
      setSubtitleId(nextArtifacts.subtitles[0]?.subtitle_id ?? ''); setWithSubtitles(false);
    } catch (caught) { if (stillCurrent(task, token) && !controller.signal.aborted) setError(messageOf(caught)); }
    finally { if (stillCurrent(task, token)) setReading(false); }
  }

  async function loadOlder() {
    if (operation.current || reading || !page.has_more) return;
    const controller = new AbortController(); readController.current = controller;
    const token = view.current; const task = taskId;
    setReading(true);
    try {
      const next = await editing.listEdls(task, page.offset + page.limit, controller.signal);
      if (stillCurrent(task, token) && !controller.signal.aborted) {
        setPage(previous => ({ ...next, items: [...previous.items, ...next.items.filter(item => !previous.items.some(old => old.revision_id === item.revision_id))] }));
      }
    } catch (caught) { if (stillCurrent(task, token) && !controller.signal.aborted) setError(messageOf(caught)); }
    finally { if (stillCurrent(task, token)) setReading(false); }
  }

  async function mutate(label: string, action: (signal: AbortSignal) => Promise<{ revision: string; subtitle?: string }>, createsRevision = false) {
    if (operation.current || reading || readRecovery(taskId)) return;
    const task = taskId; const token = view.current;
    const active = { controller: new AbortController(), task, view: token, id: `${Date.now()}-${++operationCounter}` };
    operation.current = active;
    // Persist before POST so leaving this task or reloading the page cannot lose the pending operation.
    writeRecovery({ version: 1, task_id: task, operation_id: active.id, preferred_revision: createsRevision ? undefined : savedRef.current?.revision.revision_id });
    setBusy(label); setError(''); setNotice(''); setReviewReady(false); busyCallback.current?.(true);
    let created = false;
    try {
      const result = await action(active.controller.signal);
      created = true;
      if (readRecovery(task)?.operation_id === active.id) {
        writeRecovery({ version: 1, task_id: task, operation_id: active.id, preferred_revision: result.revision });
      }
      if (!stillCurrent(task, token) || active.controller.signal.aborted) return;
      await readRecords(task, token, active.controller.signal, result.revision);
      if (!stillCurrent(task, token) || active.controller.signal.aborted) return;
      if (result.subtitle) { setSubtitleId(result.subtitle); setWithSubtitles(true); }
      clearRecovery(task, active.id); setUnknown(false);
      setNotice('已保存，可以下载或继续导出。');
    } catch (caught) {
      if (!stillCurrent(task, token) || active.controller.signal.aborted) return;
      if (caught instanceof editing.MutationUnknownError || created) {
        setUnknown(true);
        setError(created ? '操作已完成，但记录暂未载入。请先刷新已保存记录，确认结果后再继续。' : messageOf(caught));
      } else { clearRecovery(task, active.id); setError(messageOf(caught)); }
    } finally {
      if (operation.current === active) {
        operation.current = undefined; setBusy(''); busyCallback.current?.(false);
      }
    }
  }

  const revision = saved?.revision.revision_id;
  const profile = saved?.edl.profile;
  const disabled = !!busy || reading || unknown;
  const selectedSubtitle = artifacts.subtitles.find(item => item.subtitle_id === subtitleId);
  const boundSubtitle = withSubtitles ? selectedSubtitle?.subtitle_id : undefined;
  const storedIds = saved?.edl.clips.map(clip => clip.provenance?.candidate_id);
  const dirty = selected.length > 0 && !!storedIds && (storedIds.length !== selected.length || storedIds.some((id, index) => id !== selected[index]?.id));
  const versions: EdlSummary[] = [...page.items];
  if (saved && !versions.some(item => item.revision_id === revision)) {
    versions.unshift({ ...saved.revision, profile: saved.edl.profile, selected_ids: storedIds?.filter((id): id is string => !!id) ?? [] });
  }
  const seconds = profile && saved ? (saved.edl.duration_frames * profile.fps.den / profile.fps.num).toFixed(2) : '';

  return <section className="export-workbench" aria-label="剪辑与导出工作台" aria-busy={!!busy || reading}>
    <div className="result-head"><div><h2>剪辑与导出</h2><p className="hint">按顺序保存所选片段，再从同一版本导出成片、字幕和 Shotcut 工程。</p></div>
      <Button disabled={!!busy || reading} onClick={() => void refreshRecords()}>刷新已保存记录</Button></div>
    <section aria-label="待保存的片段顺序">
      <h3>待保存的片段 · {selected.length} 条</h3>
      {!selected.length ? <p className="hint">先在候选列表中选择片段。已有保存版本仍可直接导出。</p> : <ol className="export-draft-list">{selected.map((candidate, index) => <li key={candidate.id}>
        <div><strong>片段 {index + 1} · 候选 #{candidate.rank}</strong><p>{candidate.start.toFixed(2)}–{candidate.end.toFixed(2)} 秒</p><p className="export-draft-text">{candidate.text}</p></div>
        <div className="export-actions"><Button size="small" disabled={!!busy || index === 0} aria-label={`上移片段 ${index + 1}`} onClick={() => onMove(candidate.id, -1)}>上移</Button>
          <Button size="small" disabled={!!busy || index === selected.length - 1} aria-label={`下移片段 ${index + 1}`} onClick={() => onMove(candidate.id, 1)}>下移</Button>
          <Button size="small" disabled={!!busy} aria-label={`移除片段 ${index + 1}`} onClick={() => onRemove(candidate.id)}>移除</Button></div>
      </li>)}</ol>}
      <Button type="primary" disabled={disabled || !selected.length} onClick={() => {
        const ids = selected.map(item => item.id);
        void mutate('正在核验媒体并保存剪辑版本…', async signal => ({ revision: (await editing.createEdl(taskId, ids, signal)).revision_id }), true);
      }}>保存选择为剪辑版本</Button>
    </section>
    {unknown && <Alert type="warning" message="上次操作的结果尚未确认" description={<>已暂停新的导出。上次处理可能仍在继续，请等待并刷新已保存记录；核对新增版本和文件后，点击下方按钮继续。<div className="export-actions"><Button disabled={!reviewReady || reading || !!busy} onClick={() => {
      if (!reviewReady || reading || operation.current) return;
      const recovery = readRecovery(taskId);
      if (recovery) clearRecovery(taskId, recovery.operation_id);
      setUnknown(false); setReviewReady(false); setNotice('已核对记录，可以继续操作。');
    }}>已核对记录，继续操作</Button></div></>} />}
    {error && <Alert type="error" message={error} />}
    {!!busy && <p role="status" className="export-progress">{busy} 处理可能需要一到两分钟，请等待本次结果。</p>}
    {reading && <p role="status">正在读取已保存的版本与导出记录…</p>}
    {notice && <p role="status" className="export-success">{notice}</p>}
    {dirty && <Alert type="info" message="当前草稿与已保存版本不同" description="调整上方选择不会改写已保存版本。下方导出始终使用明确显示的保存版本；需要采用新顺序时，请先保存。" />}
    <section aria-label="已保存的剪辑版本">
      <h3>已保存的剪辑版本</h3>
      {versions.length > 0 && <div className="export-version-picker"><label htmlFor={`edl-${taskId}`}>导出版本</label><select id={`edl-${taskId}`} value={revision ?? ''} disabled={!!busy || reading} onChange={event => void chooseRevision(event.target.value)}>
        {!revision && <option value="">请选择版本</option>}{versions.map(item => <option key={item.revision_id} value={item.revision_id}>{dateLabel(item.created_at)} · {item.clip_count} 条 · {item.profile.width}×{item.profile.height} · {shortId(item.revision_id)}</option>)}
      </select>{page.has_more && <Button size="small" disabled={!!busy || reading} onClick={() => void loadOlder()}>载入更早版本</Button>}</div>}
      {(page.unavailable_count > 0 || artifacts.unavailable_count > 0) && <p className="hint">部分旧记录暂时无法读取，未放入可下载列表。</p>}
      {!saved && !reading ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未保存剪辑版本" /> : saved && profile && revision && <>
        <p className="export-current"><Tag color="purple">当前导出版本 {shortId(revision)}</Tag>{saved.edl.clips.length} 条 · {profile.width}×{profile.height} · {seconds} 秒</p>
        {saved.revision.derivation && <p className="hint">此版本由 {shortId(saved.revision.derivation.parent_revision_id)} 生成。</p>}
        <ol className="export-saved-list" aria-label="已保存片段顺序">{saved.edl.clips.map((clip, index) => <li key={clip.id}>片段 {index + 1} · 成片内 {(clip.timeline_in_frame * profile.fps.den / profile.fps.num).toFixed(2)}–{(clip.timeline_out_frame * profile.fps.den / profile.fps.num).toFixed(2)} 秒</li>)}</ol>
        <div className="export-actions"><Button href={editing.edlFileUrl(taskId, revision, 'project.edl.json')} download>下载 EDL</Button><Button href={editing.edlFileUrl(taskId, revision, 'media-profile.json')} download>下载媒体描述</Button><Button href={editing.sourceUrl(taskId, revision)} download>下载工程所需源素材</Button>
          {profile.width > profile.height && <Button disabled={disabled} onClick={() => void mutate('正在保存 9:16 竖屏版本…', async signal => ({ revision: (await editing.createPortrait(taskId, revision, signal)).revision_id }), true)}>生成 9:16 版本</Button>}
          <Button disabled={disabled} onClick={() => void mutate('正在生成并保存字幕…', async signal => { const item = await editing.createSubtitles(taskId, revision, signal); return { revision, subtitle: item.subtitle_id }; })}>生成字幕</Button></div>
        <p className="hint">竖屏使用居中裁切。字幕来自原转写，可能有错字，下载后可以修改。</p>
        <div className="export-subtitle-picker"><label htmlFor={`subtitle-${taskId}`}>字幕版本</label><select id={`subtitle-${taskId}`} disabled={!!busy || reading || !artifacts.subtitles.length} value={subtitleId} onChange={event => setSubtitleId(event.target.value)}>
          {!artifacts.subtitles.length && <option value="">请先生成字幕</option>}{artifacts.subtitles.map(item => <option key={item.subtitle_id} value={item.subtitle_id}>{dateLabel(item.created_at)} · {item.cue_count} 条 · {shortId(item.subtitle_id)}</option>)}
        </select><Checkbox checked={withSubtitles} disabled={!!busy || reading || !selectedSubtitle} onChange={event => setWithSubtitles(event.target.checked)}>导出时带字幕</Checkbox></div>
        {selectedSubtitle && <div className="export-actions"><Button href={editing.subtitleFileUrl(taskId, revision, selectedSubtitle.subtitle_id, 'captions.srt')} download>下载 SRT 字幕</Button><Button href={editing.subtitleFileUrl(taskId, revision, selectedSubtitle.subtitle_id, 'captions.ass')} download>下载 ASS 字幕</Button><span className="hint">工程字幕字体：{selectedSubtitle.font_name}</span></div>}
        <div className="export-actions export-main-actions"><Button type="primary" disabled={disabled || (withSubtitles && !selectedSubtitle)} onClick={() => void mutate('正在按保存版本渲染并核验 MP4…', async signal => { await editing.createMp4(taskId, revision, boundSubtitle, signal); return { revision }; })}>导出 MP4</Button>
          <Button disabled={disabled || (withSubtitles && !selectedSubtitle)} onClick={() => void mutate('正在生成并核验 Shotcut 工程…', async signal => { await editing.createMlt(taskId, revision, boundSubtitle, signal); return { revision }; })}>导出 Shotcut 工程</Button></div>
        <p className="hint">MP4 可以直接播放。Shotcut 工程需要同时保留源素材；换电脑后请在 Shotcut 重新定位素材，并安装上方字幕字体。</p>
        {artifacts.renders.length > 0 && <section aria-label="此版本的已保存成片"><h3>已保存成片</h3>{artifacts.renders.map((item, index) => <article key={item.render_id} className="export-artifact">
          <strong>{dateLabel(item.created_at)} · {item.subtitle_id ? '带字幕' : '无字幕'} · {shortId(item.render_id)}</strong>
          {index === 0 && <video controls preload="metadata" src={editing.renderFileUrl(taskId, revision, item.render_id, 'output.mp4')} aria-label="最新保存成片播放器" onError={() => setPreviewFailed(item.render_id)} />}
          {index === 0 && previewFailed === item.render_id && <p role="status" className="hint">网页预览暂不可用，请下载 MP4 查看。</p>}
          <div className="export-actions"><Button href={editing.renderFileUrl(taskId, revision, item.render_id, 'output.mp4')} download>下载 MP4 {index + 1}</Button><Button href={editing.renderFileUrl(taskId, revision, item.render_id, 'verification.json')} download>下载成片核验记录 {index + 1}</Button></div>
        </article>)}</section>}
        {artifacts.mlt_exports.length > 0 && <section aria-label="此版本的已保存工程"><h3>已保存 Shotcut 工程</h3>{artifacts.mlt_exports.map((item, index) => <article key={item.export_id} className="export-artifact"><strong>{dateLabel(item.created_at)} · {item.subtitle_id ? '带字幕' : '无字幕'} · {shortId(item.export_id)}</strong><div className="export-actions"><Button href={editing.mltFileUrl(taskId, revision, item.export_id, 'project.mlt')} download>下载 Shotcut 工程 {index + 1}</Button></div></article>)}</section>}
      </>}
    </section>
  </section>;
}
