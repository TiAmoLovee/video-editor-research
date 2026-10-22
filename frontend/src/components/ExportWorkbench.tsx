import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Empty, Tag } from 'antd';
import * as editing from '../api/editing';
import type { ArtifactHistory, EditedClip, EditEffort, EdlPage, EdlSummary, SavedEdl } from '../types/editing';
import type { Candidate } from '../types/tasks';
import { useActor } from '../hooks/useActor';
import { frameToTimecode, timecodeToFrame } from '../utils/timecode';
import { BatchExportPanel } from './BatchExportPanel';

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
interface ClipDraft { candidate_id: string; source_in_frame: string; source_out_frame: string; source_in_time?: string; source_out_time?: string }
function parseEdits(draft: ClipDraft[]): EditedClip[] | null {
  const clips = draft.map(clip => ({ candidate_id: clip.candidate_id,
    source_in_frame: /^\d+$/.test(clip.source_in_frame) ? Number(clip.source_in_frame) : NaN,
    source_out_frame: /^\d+$/.test(clip.source_out_frame) ? Number(clip.source_out_frame) : NaN }));
  return clips.length > 0 && clips.length <= 100 && new Set(clips.map(clip => clip.candidate_id)).size === clips.length
    && clips.every(clip => Number.isSafeInteger(clip.source_in_frame) && clip.source_in_frame >= 0
      && Number.isSafeInteger(clip.source_out_frame) && clip.source_out_frame > clip.source_in_frame) ? clips : null;
}
function EffortSummary({ label, report }: { label: string; report: EditEffort }) {
  return <div aria-label={label}><strong>{label}</strong><p>切点调整 {report.net_boundary_adjustment_seconds.toFixed(2)} 秒 · 调整 {report.adjusted_clip_count} 条 · 新增 {report.added_clip_count} 条 · 删除 {report.removed_clip_count} 条 · 最少移动 {report.minimum_moved_clip_count} 条</p>
    {report.added_clip_boundary_adjustment_seconds !== undefined && <p>新增片段切点调整 {report.added_clip_boundary_adjustment_seconds.toFixed(2)} 秒（相对候选快照）。</p>}</div>;
}
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
const framingLabel = (kind?: string) => kind === 'fit_pad_9_16' ? '完整画面 9:16' : kind === 'center_crop_9_16' ? '居中裁切 9:16' : '';

export function ExportWorkbench({ taskId, selected, onRemove, onMove, onBusyChange }: Props) {
  const [actor, setActor] = useActor();
  const [page, setPage] = useState<EdlPage>(noPage);
  const [saved, setSaved] = useState<SavedEdl | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactHistory>(noArtifacts);
  const [subtitleId, setSubtitleId] = useState('');
  const [withSubtitles, setWithSubtitles] = useState(false);
  const [reading, setReading] = useState(true);
  const [busy, setBusy] = useState('');
  const [batchBusy, setBatchBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [unknown, setUnknown] = useState(() => !!readRecovery(taskId));
  const [reviewReady, setReviewReady] = useState(false);
  const [previewFailed, setPreviewFailed] = useState('');
  const [editDraft, setEditDraft] = useState<ClipDraft[] | null>(null);
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
    if (!sameRevision) { setWithSubtitles(false); setEditDraft(null); }
    setError('');
  }

  async function refreshRecords() {
    if (operation.current || batchBusy) return;
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
    setSubtitleId(''); setWithSubtitles(false); setEditDraft(null); setBusy(''); setBatchBusy(false); setError(''); setNotice('');
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
    if (operation.current || batchBusy || reading || editDraft || !revision) return;
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
    if (operation.current || batchBusy || reading || !page.has_more) return;
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
    if (operation.current || batchBusy || reading || readRecovery(taskId)) return;
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
      window.dispatchEvent(new CustomEvent('clipforge:audit-changed', { detail: { taskId: task } }));
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
  const editingDisabled = !!busy || batchBusy || reading || unknown;
  const disabled = editingDisabled || editDraft !== null;
  const editClips = editDraft ? parseEdits(editDraft) : null;
  const canEditSaved = !!saved && saved.edl.clips.length <= 100
    && saved.edl.clips.every(clip => !!clip.provenance?.candidate_id?.trim() && clip.provenance.candidate_id.length <= 200)
    && new Set(saved.edl.clips.map(clip => clip.provenance?.candidate_id)).size === saved.edl.clips.length;
  const snapshotClips = new Map(saved?.available_clips?.map(clip => [clip.candidate_id, clip]) ?? []);
  const draftIds = new Set(editDraft?.map(clip => clip.candidate_id));
  const unselectedDraftClips = [...new Map(selected.filter(candidate => !draftIds.has(candidate.id)).map(candidate => [candidate.id, candidate])).values()];
  const addableClips = unselectedDraftClips.flatMap(candidate => {
    const clip = snapshotClips.get(candidate.id); return clip ? [clip] : [];
  });
  const unavailableSelectedCount = unselectedDraftClips.length - addableClips.length;
  const editsChanged = !!saved && !!editClips && (editClips.length !== saved.edl.clips.length || editClips.some((clip, index) => {
    const original = saved.edl.clips[index];
    return clip.candidate_id !== original.provenance?.candidate_id || clip.source_in_frame !== original.source_in_frame || clip.source_out_frame !== original.source_out_frame;
  }));
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
      <Button disabled={!!busy || batchBusy || reading || (!!editDraft && !unknown)} onClick={() => void refreshRecords()}>刷新已保存记录</Button></div>
    <label>操作记录姓名 <input value={actor} maxLength={80} disabled={editingDisabled} onChange={event => setActor(event.target.value)} /></label>
    <section aria-label="待保存的片段顺序">
      <h3>待保存的片段 · {selected.length} 条</h3>
      {!selected.length ? <p className="hint">先在候选列表中选择片段。已有保存版本仍可直接导出。</p> : <ol className="export-draft-list">{selected.map((candidate, index) => <li key={candidate.id}>
        <div><strong>片段 {index + 1} · 候选 #{candidate.rank}</strong><p>{candidate.start.toFixed(2)}–{candidate.end.toFixed(2)} 秒</p><p className="export-draft-text">{candidate.text}</p></div>
        <div className="export-actions"><Button size="small" disabled={!!busy || index === 0} aria-label={`上移片段 ${index + 1}`} onClick={() => onMove(candidate.id, -1)}>上移</Button>
          <Button size="small" disabled={!!busy || index === selected.length - 1} aria-label={`下移片段 ${index + 1}`} onClick={() => onMove(candidate.id, 1)}>下移</Button>
          <Button size="small" disabled={!!busy} aria-label={`移除片段 ${index + 1}`} onClick={() => onRemove(candidate.id)}>移除</Button></div>
      </li>)}</ol>}
      <Button type="primary" disabled={disabled || !selected.length || !actor.trim() || actor.trim().length > 80} onClick={() => {
        const ids = selected.map(item => item.id);
        void mutate('正在核验媒体并保存剪辑版本…', async signal => ({ revision: (await editing.createEdl(taskId, ids, signal, actor.trim())).revision_id }), true);
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
      {versions.length > 0 && <div className="export-version-picker"><label htmlFor={`edl-${taskId}`}>导出版本</label><select id={`edl-${taskId}`} value={revision ?? ''} disabled={!!busy || batchBusy || reading || !!editDraft} onChange={event => void chooseRevision(event.target.value)}>
        {!revision && <option value="">请选择版本</option>}{versions.map(item => <option key={item.revision_id} value={item.revision_id}>{dateLabel(item.created_at)} · {item.clip_count} 条 · {item.profile.width}×{item.profile.height}{framingLabel(item.derivation?.kind) && ` · ${framingLabel(item.derivation?.kind)}`} · {shortId(item.revision_id)}</option>)}
      </select>{page.has_more && <Button size="small" disabled={!!busy || reading} onClick={() => void loadOlder()}>载入更早版本</Button>}</div>}
      {(page.unavailable_count > 0 || artifacts.unavailable_count > 0) && <p className="hint">部分旧记录暂时无法读取，未放入可下载列表。</p>}
      {!saved && !reading ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未保存剪辑版本" /> : saved && profile && revision && <>
        <p className="export-current"><Tag color="purple">当前导出版本 {shortId(revision)}</Tag>{saved.edl.clips.length} 条 · {profile.width}×{profile.height} · {seconds} 秒</p>
        {saved.edl.framing && <p className="hint">{saved.edl.framing.mode === 'fit_pad' ? '完整画面 9:16 · 等比缩放，黑色留边，保留画面内容。' : '居中裁切 9:16 · 会裁掉原画面两侧或上下边缘。'}</p>}
        {saved.revision.derivation && <p className="hint">此版本由 {shortId(saved.revision.derivation.parent_revision_id)} 生成。</p>}
        <ol className="export-saved-list" aria-label="已保存片段顺序">{saved.edl.clips.map((clip, index) => <li key={clip.id}>片段 {index + 1} · 成片内 {(clip.timeline_in_frame * profile.fps.den / profile.fps.num).toFixed(2)}–{(clip.timeline_out_frame * profile.fps.den / profile.fps.num).toFixed(2)} 秒</li>)}</ol>
        <section aria-label="调整保存版本的切点与顺序"><h3>调整切点与顺序</h3>
          {editDraft === null ? <><Button disabled={disabled || !canEditSaved} onClick={() => {
            setEditDraft(saved.edl.clips.map(clip => ({ candidate_id: clip.provenance!.candidate_id,
              source_in_frame: String(clip.source_in_frame), source_out_frame: String(clip.source_out_frame) })));
            setError(''); setNotice('');
          }}>编辑这个版本</Button>{!canEditSaved && <p className="hint">此旧版本缺少唯一候选编号或片段超过 100 条，暂时不能在网页中调整。</p>}</> : <>
            <p className="hint">可输入时间（时:分:秒或秒数），自动对齐最近的视频帧；也可用帧号精调。起点包含，止点不包含。保存会生成新版本，导出使用已保存版本。</p>
            <label>编辑者姓名 <input value={actor} maxLength={80} disabled={editingDisabled} onChange={event => setActor(event.target.value)} /></label>
            <p className="hint">请填写用于操作记录的姓名。</p>
            <div className="export-actions"><Button disabled={editingDisabled || !addableClips.length || editDraft.length + addableClips.length > 100} onClick={() => setEditDraft(previous => {
              if (!previous) return null;
              const existing = new Set(previous.map(clip => clip.candidate_id));
              const added = addableClips.filter(clip => !existing.has(clip.candidate_id));
              if (previous.length + added.length > 100) return previous;
              return [...previous, ...added.map(clip => ({ candidate_id: clip.candidate_id,
                source_in_frame: String(clip.source_in_frame), source_out_frame: String(clip.source_out_frame) }))];
            })}>加入当前所选的新片段</Button><span className="hint">可加入 {addableClips.length} 条，添加到末尾后可调整顺序。</span></div>
            {unavailableSelectedCount > 0 && <p className="hint">当前所选中有 {unavailableSelectedCount} 条不在此版本的候选快照内，不能加入。</p>}
            {editDraft.length + addableClips.length > 100 && <p className="hint">加入后会超过 100 条，请减少候选选择或先删除草稿中的片段。</p>}
            <ol className="export-draft-list" aria-label="切点编辑草稿">{editDraft.map((clip, index) => <li key={clip.candidate_id}>
              <div><strong>片段 {index + 1}</strong><div><label>片段 {index + 1} 开始时间 <input value={clip.source_in_time ?? frameToTimecode(clip.source_in_frame, profile.fps)} disabled={editingDisabled} onChange={event => {
                const value = event.target.value; const frame = timecodeToFrame(value, profile.fps);
                setEditDraft(previous => previous?.map((item, position) => position === index ? { ...item, source_in_time: value, source_in_frame: frame === null ? '' : String(frame) } : item) ?? null);
              }} /></label></div><div><label>片段 {index + 1} 结束时间 <input value={clip.source_out_time ?? frameToTimecode(clip.source_out_frame, profile.fps)} disabled={editingDisabled} onChange={event => {
                const value = event.target.value; const frame = timecodeToFrame(value, profile.fps);
                setEditDraft(previous => previous?.map((item, position) => position === index ? { ...item, source_out_time: value, source_out_frame: frame === null ? '' : String(frame) } : item) ?? null);
              }} /></label></div><div><label>片段 {index + 1} 源起帧 <input type="number" min="0" step="1" value={clip.source_in_frame} disabled={editingDisabled} onChange={event => {
                const value = event.target.value; setEditDraft(previous => previous?.map((item, position) => position === index ? { ...item, source_in_frame: value, source_in_time: undefined } : item) ?? null);
              }} /></label></div><div><label>片段 {index + 1} 源止帧 <input type="number" min="1" step="1" value={clip.source_out_frame} disabled={editingDisabled} onChange={event => {
                const value = event.target.value; setEditDraft(previous => previous?.map((item, position) => position === index ? { ...item, source_out_frame: value, source_out_time: undefined } : item) ?? null);
              }} /></label></div></div>
              <div className="export-actions">{([-1, 1] as const).map(direction => <Button key={direction} size="small" disabled={editingDisabled || (direction === -1 ? index === 0 : index === editDraft.length - 1)} aria-label={`${direction === -1 ? '上移' : '下移'}编辑片段 ${index + 1}`} onClick={() => setEditDraft(previous => {
                if (!previous) return null; const next = [...previous]; const destination = index + direction;
                if (destination < 0 || destination >= next.length) return previous;
                [next[index], next[destination]] = [next[destination], next[index]]; return next;
              })}>{direction === -1 ? '上移' : '下移'}</Button>)}<Button size="small" disabled={editingDisabled || editDraft.length === 1} aria-label={`删除编辑片段 ${index + 1}`} onClick={() => setEditDraft(previous => previous && previous.length > 1 ? previous.filter((_, position) => position !== index) : previous)}>删除</Button></div>
            </li>)}</ol>
            {!editClips && <p role="status" className="hint">请输入非负整数起帧，止帧必须大于起帧。</p>}
            <div className="export-actions"><Button type="primary" disabled={editingDisabled || !editClips || !editsChanged || !actor.trim() || actor.trim().length > 80} onClick={() => {
              if (!editClips) return;
              void mutate('正在核验切点并保存新版本…', async signal => ({ revision: (await editing.saveEdits(taskId, revision, editClips, signal, actor.trim())).revision_id }), true);
            }}>保存修改为新版本</Button><Button disabled={editingDisabled} onClick={() => { setEditDraft(null); setError(''); }}>放弃这次修改</Button></div>
          </>}
        </section>
        {saved.edit_record && <section aria-label="人工修改量"><h3>人工修改量</h3>
          <p>编辑者：{saved.edit_record.actor ?? '未记录'}{saved.edit_record.created_at ? ` · ${dateLabel(saved.edit_record.created_at)}` : ''}</p>
          <p className="hint">基于源帧计算切点移动量。净修改相对最初采纳版本 {shortId(saved.edit_record.baseline_revision_id)}，本次修改相对父版本 {shortId(saved.edit_record.parent_revision_id)}；版本数量不代表劳动量。</p>
          <EffortSummary label="最终净修改" report={saved.edit_record.effort} /><EffortSummary label="本次修改" report={saved.edit_record.step_effort} />
          <p className="hint">{saved.edit_record.editing_elapsed_ms === null ? '未记录编辑耗时。' : `本次编辑经过 ${(saved.edit_record.editing_elapsed_ms / 1000).toFixed(1)} 秒（包含等待和停留）。`}这些记录不等于节省的时间，也不表示片段质量已达标。</p>
        </section>}
        <div className="export-actions"><Button href={editing.edlFileUrl(taskId, revision, 'project.edl.json')} download>下载 EDL</Button><Button href={editing.edlFileUrl(taskId, revision, 'media-profile.json')} download>下载媒体描述</Button><Button href={editing.sourceUrl(taskId, revision)} download>下载工程所需源素材</Button>
          {saved.revision.files?.['edit-record.json'] && <Button href={editing.edlFileUrl(taskId, revision, 'edit-record.json')} download>下载人工修改记录</Button>}
          {profile.width > profile.height && <><Button type="primary" disabled={disabled} onClick={() => void mutate('正在保存完整画面 9:16 版本…', async signal => ({ revision: (await editing.createFittedPortrait(taskId, revision, signal)).revision_id }), true)}>完整画面 9:16</Button>
            <Button disabled={disabled} onClick={() => void mutate('正在保存居中裁切 9:16 版本…', async signal => ({ revision: (await editing.createPortrait(taskId, revision, signal)).revision_id }), true)}>居中裁切 9:16</Button></>}
          <Button disabled={disabled} onClick={() => void mutate('正在生成并保存字幕…', async signal => { const item = await editing.createSubtitles(taskId, revision, signal); return { revision, subtitle: item.subtitle_id }; })}>生成字幕</Button></div>
        <p className="hint">完整画面竖屏会等比缩放并以黑色留边补足 9:16；居中裁切会裁掉边缘。竖屏会保存为独立版本，请在新版本中生成字幕。字幕来自原转写，可能有错字，下载后可以修改。</p>
        <div className="export-subtitle-picker"><label htmlFor={`subtitle-${taskId}`}>字幕版本</label><select id={`subtitle-${taskId}`} disabled={!!busy || reading || !artifacts.subtitles.length} value={subtitleId} onChange={event => setSubtitleId(event.target.value)}>
          {!artifacts.subtitles.length && <option value="">请先生成字幕</option>}{artifacts.subtitles.map(item => <option key={item.subtitle_id} value={item.subtitle_id}>{dateLabel(item.created_at)} · {item.cue_count} 条 · {shortId(item.subtitle_id)}</option>)}
        </select><Checkbox checked={withSubtitles} disabled={!!busy || reading || !selectedSubtitle} onChange={event => setWithSubtitles(event.target.checked)}>导出时带字幕</Checkbox></div>
        {selectedSubtitle && <div className="export-actions"><Button href={editing.subtitleFileUrl(taskId, revision, selectedSubtitle.subtitle_id, 'captions.srt')} download>下载 SRT 字幕</Button><Button href={editing.subtitleFileUrl(taskId, revision, selectedSubtitle.subtitle_id, 'captions.ass')} download>下载 ASS 字幕</Button><span className="hint">工程字幕字体：{selectedSubtitle.font_name}</span></div>}
        <div className="export-actions export-main-actions"><Button type="primary" disabled={disabled || (withSubtitles && !selectedSubtitle)} onClick={() => void mutate('正在按保存版本渲染并核验 MP4…', async signal => { await editing.createMp4(taskId, revision, boundSubtitle, signal); return { revision }; })}>导出 MP4</Button>
          <Button disabled={disabled || (withSubtitles && !selectedSubtitle)} onClick={() => void mutate('正在生成并核验 Shotcut 工程…', async signal => { await editing.createMlt(taskId, revision, boundSubtitle, signal); return { revision }; })}>导出 Shotcut 工程</Button></div>
        <p className="hint">MP4 可以直接播放。Shotcut 工程需要同时保留源素材；换电脑后请在 Shotcut 重新定位素材，并安装上方字幕字体。</p>
        <BatchExportPanel key={`${taskId}:${revision}`} taskId={taskId} revisionId={revision} disabled={!!busy || reading || unknown || editDraft !== null} onBusyChange={value => { setBatchBusy(value); busyCallback.current?.(value); }} />
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
