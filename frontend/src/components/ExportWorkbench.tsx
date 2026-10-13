import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Empty, Tag } from 'antd';
import * as editing from '../api/editing';
import type { ArtifactHistory, EditedClip, EditEffort, EdlPage, EdlSummary, SavedEdl } from '../types/editing';
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
interface ClipDraft { candidate_id: string; source_in_frame: string; source_out_frame: string }
function parseEdits(draft: ClipDraft[]): EditedClip[] | null {
  const clips = draft.map(clip => ({ candidate_id: clip.candidate_id,
    source_in_frame: /^\d+$/.test(clip.source_in_frame) ? Number(clip.source_in_frame) : NaN,
    source_out_frame: /^\d+$/.test(clip.source_out_frame) ? Number(clip.source_out_frame) : NaN }));
  return clips.length > 0 && clips.length <= 100 && new Set(clips.map(clip => clip.candidate_id)).size === clips.length
    && clips.every(clip => Number.isSafeInteger(clip.source_in_frame) && clip.source_in_frame >= 0
      && Number.isSafeInteger(clip.source_out_frame) && clip.source_out_frame > clip.source_in_frame) ? clips : null;
}
function EffortSummary({ label, report }: { label: string; report: EditEffort }) {
  return <div aria-label={label}><strong>{label}</strong><p>�е���� {report.net_boundary_adjustment_seconds.toFixed(2)} �� �� ���� {report.adjusted_clip_count} �� �� ���� {report.added_clip_count} �� �� ɾ�� {report.removed_clip_count} �� �� �����ƶ� {report.minimum_moved_clip_count} ��</p>
    {report.added_clip_boundary_adjustment_seconds !== undefined && <p>����Ƭ���е���� {report.added_clip_boundary_adjustment_seconds.toFixed(2)} �루��Ժ�ѡ���գ���</p>}</div>;
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
const messageOf = (error: unknown) => error instanceof Error ? error.message : '��ʱ�޷���ȡ�����Ժ�ˢ���ѱ����¼��';

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
        setNotice('��¼��ˢ�£���˶������汾���ļ��������');
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
    setSubtitleId(''); setWithSubtitles(false); setEditDraft(null); setBusy(''); setError(''); setNotice('');
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
    if (operation.current || reading || editDraft || !revision) return;
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
      setNotice('�ѱ��棬�������ػ����������');
    } catch (caught) {
      if (!stillCurrent(task, token) || active.controller.signal.aborted) return;
      if (caught instanceof editing.MutationUnknownError || created) {
        setUnknown(true);
        setError(created ? '��������ɣ�����¼��δ���롣����ˢ���ѱ����¼��ȷ�Ͻ�����ټ�����' : messageOf(caught));
      } else { clearRecovery(task, active.id); setError(messageOf(caught)); }
    } finally {
      if (operation.current === active) {
        operation.current = undefined; setBusy(''); busyCallback.current?.(false);
      }
    }
  }

  const revision = saved?.revision.revision_id;
  const profile = saved?.edl.profile;
  const editingDisabled = !!busy || reading || unknown;
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

  return <section className="export-workbench" aria-label="�����뵼������̨" aria-busy={!!busy || reading}>
    <div className="result-head"><div><h2>�����뵼��</h2><p className="hint">��˳�򱣴���ѡƬ�Σ��ٴ�ͬһ�汾������Ƭ����Ļ�� Shotcut ���̡�</p></div>
      <Button disabled={!!busy || reading || (!!editDraft && !unknown)} onClick={() => void refreshRecords()}>ˢ���ѱ����¼</Button></div>
    <section aria-label="�������Ƭ��˳��">
      <h3>�������Ƭ�� �� {selected.length} ��</h3>
      {!selected.length ? <p className="hint">���ں�ѡ�б���ѡ��Ƭ�Ρ����б���汾�Կ�ֱ�ӵ�����</p> : <ol className="export-draft-list">{selected.map((candidate, index) => <li key={candidate.id}>
        <div><strong>Ƭ�� {index + 1} �� ��ѡ #{candidate.rank}</strong><p>{candidate.start.toFixed(2)}�C{candidate.end.toFixed(2)} ��</p><p className="export-draft-text">{candidate.text}</p></div>
        <div className="export-actions"><Button size="small" disabled={!!busy || index === 0} aria-label={`����Ƭ�� ${index + 1}`} onClick={() => onMove(candidate.id, -1)}>����</Button>
          <Button size="small" disabled={!!busy || index === selected.length - 1} aria-label={`����Ƭ�� ${index + 1}`} onClick={() => onMove(candidate.id, 1)}>����</Button>
          <Button size="small" disabled={!!busy} aria-label={`�Ƴ�Ƭ�� ${index + 1}`} onClick={() => onRemove(candidate.id)}>�Ƴ�</Button></div>
      </li>)}</ol>}
      <Button type="primary" disabled={disabled || !selected.length} onClick={() => {
        const ids = selected.map(item => item.id);
        void mutate('���ں���ý�岢��������汾��', async signal => ({ revision: (await editing.createEdl(taskId, ids, signal)).revision_id }), true);
      }}>����ѡ��Ϊ�����汾</Button>
    </section>
    {unknown && <Alert type="warning" message="�ϴβ����Ľ����δȷ��" description={<>����ͣ�µĵ������ϴδ����������ڼ�������ȴ���ˢ���ѱ����¼���˶������汾���ļ��󣬵���·���ť������<div className="export-actions"><Button disabled={!reviewReady || reading || !!busy} onClick={() => {
      if (!reviewReady || reading || operation.current) return;
      const recovery = readRecovery(taskId);
      if (recovery) clearRecovery(taskId, recovery.operation_id);
      setUnknown(false); setReviewReady(false); setNotice('�Ѻ˶Լ�¼�����Լ���������');
    }}>�Ѻ˶Լ�¼����������</Button></div></>} />}
    {error && <Alert type="error" message={error} />}
    {!!busy && <p role="status" className="export-progress">{busy} ����������Ҫһ�������ӣ���ȴ����ν����</p>}
    {reading && <p role="status">���ڶ�ȡ�ѱ���İ汾�뵼����¼��</p>}
    {notice && <p role="status" className="export-success">{notice}</p>}
    {dirty && <Alert type="info" message="��ǰ�ݸ����ѱ���汾��ͬ" description="�����Ϸ�ѡ�񲻻��д�ѱ���汾���·�����ʼ��ʹ����ȷ��ʾ�ı���汾����Ҫ������˳��ʱ�����ȱ��档" />}
    <section aria-label="�ѱ���ļ����汾">
      <h3>�ѱ���ļ����汾</h3>
      {versions.length > 0 && <div className="export-version-picker"><label htmlFor={`edl-${taskId}`}>�����汾</label><select id={`edl-${taskId}`} value={revision ?? ''} disabled={!!busy || reading || !!editDraft} onChange={event => void chooseRevision(event.target.value)}>
        {!revision && <option value="">��ѡ��汾</option>}{versions.map(item => <option key={item.revision_id} value={item.revision_id}>{dateLabel(item.created_at)} �� {item.clip_count} �� �� {item.profile.width}��{item.profile.height} �� {shortId(item.revision_id)}</option>)}
      </select>{page.has_more && <Button size="small" disabled={!!busy || reading} onClick={() => void loadOlder()}>�������汾</Button>}</div>}
      {(page.unavailable_count > 0 || artifacts.unavailable_count > 0) && <p className="hint">���־ɼ�¼��ʱ�޷���ȡ��δ����������б���</p>}
      {!saved && !reading ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="��δ��������汾" /> : saved && profile && revision && <>
        <p className="export-current"><Tag color="purple">��ǰ�����汾 {shortId(revision)}</Tag>{saved.edl.clips.length} �� �� {profile.width}��{profile.height} �� {seconds} ��</p>
        {saved.revision.derivation && <p className="hint">�˰汾�� {shortId(saved.revision.derivation.parent_revision_id)} ���ɡ�</p>}
        <ol className="export-saved-list" aria-label="�ѱ���Ƭ��˳��">{saved.edl.clips.map((clip, index) => <li key={clip.id}>Ƭ�� {index + 1} �� ��Ƭ�� {(clip.timeline_in_frame * profile.fps.den / profile.fps.num).toFixed(2)}�C{(clip.timeline_out_frame * profile.fps.den / profile.fps.num).toFixed(2)} ��</li>)}</ol>
        <section aria-label="��������汾���е���˳��"><h3>�����е���˳��</h3>
          {editDraft === null ? <><Button disabled={disabled || !canEditSaved} onClick={() => {
            setEditDraft(saved.edl.clips.map(clip => ({ candidate_id: clip.provenance!.candidate_id,
              source_in_frame: String(clip.source_in_frame), source_out_frame: String(clip.source_out_frame) })));
            setError(''); setNotice('');
          }}>�༭����汾</Button>{!canEditSaved && <p className="hint">�˾ɰ汾ȱ��Ψһ��ѡ��Ż�Ƭ�γ��� 100 ������ʱ��������ҳ�е�����</p>}</> : <>
            <p className="hint">��ԭ�ز�֡�ŵ�������֡������Ƭ���ڣ�ֹ֡������������������°汾���·�������ʹ�õ�ǰ�ѱ���汾���л��汾ǰ�뱣�������޸ġ�</p>
            <div className="export-actions"><Button disabled={editingDisabled || !addableClips.length || editDraft.length + addableClips.length > 100} onClick={() => setEditDraft(previous => {
              if (!previous) return null;
              const existing = new Set(previous.map(clip => clip.candidate_id));
              const added = addableClips.filter(clip => !existing.has(clip.candidate_id));
              if (previous.length + added.length > 100) return previous;
              return [...previous, ...added.map(clip => ({ candidate_id: clip.candidate_id,
                source_in_frame: String(clip.source_in_frame), source_out_frame: String(clip.source_out_frame) }))];
            })}>���뵱ǰ��ѡ����Ƭ��</Button><span className="hint">�ɼ��� {addableClips.length} �������ӵ�ĩβ��ɵ���˳��</span></div>
            {unavailableSelectedCount > 0 && <p className="hint">��ǰ��ѡ���� {unavailableSelectedCount} �����ڴ˰汾�ĺ�ѡ�����ڣ����ܼ��롣</p>}
            {editDraft.length + addableClips.length > 100 && <p className="hint">�����ᳬ�� 100 ��������ٺ�ѡѡ�����ɾ���ݸ��е�Ƭ�Ρ�</p>}
            <ol className="export-draft-list" aria-label="�е�༭�ݸ�">{editDraft.map((clip, index) => <li key={clip.candidate_id}>
              <div><strong>Ƭ�� {index + 1}</strong><div><label>Ƭ�� {index + 1} Դ��֡ <input type="number" min="0" step="1" value={clip.source_in_frame} disabled={editingDisabled} onChange={event => {
                const value = event.target.value; setEditDraft(previous => previous?.map((item, position) => position === index ? { ...item, source_in_frame: value } : item) ?? null);
              }} /></label></div><div><label>Ƭ�� {index + 1} Դֹ֡ <input type="number" min="1" step="1" value={clip.source_out_frame} disabled={editingDisabled} onChange={event => {
                const value = event.target.value; setEditDraft(previous => previous?.map((item, position) => position === index ? { ...item, source_out_frame: value } : item) ?? null);
              }} /></label></div></div>
              <div className="export-actions">{([-1, 1] as const).map(direction => <Button key={direction} size="small" disabled={editingDisabled || (direction === -1 ? index === 0 : index === editDraft.length - 1)} aria-label={`${direction === -1 ? '����' : '����'}�༭Ƭ�� ${index + 1}`} onClick={() => setEditDraft(previous => {
                if (!previous) return null; const next = [...previous]; const destination = index + direction;
                if (destination < 0 || destination >= next.length) return previous;
                [next[index], next[destination]] = [next[destination], next[index]]; return next;
              })}>{direction === -1 ? '����' : '����'}</Button>)}<Button size="small" disabled={editingDisabled || editDraft.length === 1} aria-label={`ɾ���༭Ƭ�� ${index + 1}`} onClick={() => setEditDraft(previous => previous && previous.length > 1 ? previous.filter((_, position) => position !== index) : previous)}>ɾ��</Button></div>
            </li>)}</ol>
            {!editClips && <p role="status" className="hint">������Ǹ�������֡��ֹ֡���������֡��</p>}
            <div className="export-actions"><Button type="primary" disabled={editingDisabled || !editClips || !editsChanged} onClick={() => {
              if (!editClips) return;
              void mutate('���ں����е㲢�����°汾��', async signal => ({ revision: (await editing.saveEdits(taskId, revision, editClips, signal)).revision_id }), true);
            }}>�����޸�Ϊ�°汾</Button><Button disabled={editingDisabled} onClick={() => { setEditDraft(null); setError(''); }}>��������޸�</Button></div>
          </>}
        </section>
        {saved.edit_record && <section aria-label="�˹��޸���"><h3>�˹��޸���</h3>
          <p className="hint">����Դ֡�����е��ƶ��������޸����������ɰ汾 {shortId(saved.edit_record.baseline_revision_id)}�������޸���Ը��汾 {shortId(saved.edit_record.parent_revision_id)}���汾�����������Ͷ�����</p>
          <EffortSummary label="���վ��޸�" report={saved.edit_record.effort} /><EffortSummary label="�����޸�" report={saved.edit_record.step_effort} />
          <p className="hint">{saved.edit_record.editing_elapsed_ms === null ? 'δ��¼�༭��ʱ��' : `���α༭���� ${(saved.edit_record.editing_elapsed_ms / 1000).toFixed(1)} �루�����ȴ���ͣ������`}��Щ��¼�����ڽ�ʡ��ʱ�䣬Ҳ����ʾƬ�������Ѵ�ꡣ</p>
        </section>}
        <div className="export-actions"><Button href={editing.edlFileUrl(taskId, revision, 'project.edl.json')} download>���� EDL</Button><Button href={editing.edlFileUrl(taskId, revision, 'media-profile.json')} download>����ý������</Button><Button href={editing.sourceUrl(taskId, revision)} download>���ع�������Դ�ز�</Button>
          {saved.revision.files?.['edit-record.json'] && <Button href={editing.edlFileUrl(taskId, revision, 'edit-record.json')} download>�����˹��޸ļ�¼</Button>}
          {profile.width > profile.height && <Button disabled={disabled} onClick={() => void mutate('���ڱ��� 9:16 �����汾��', async signal => ({ revision: (await editing.createPortrait(taskId, revision, signal)).revision_id }), true)}>���� 9:16 �汾</Button>}
          <Button disabled={disabled} onClick={() => void mutate('�������ɲ�������Ļ��', async signal => { const item = await editing.createSubtitles(taskId, revision, signal); return { revision, subtitle: item.subtitle_id }; })}>������Ļ</Button></div>
        <p className="hint">����ʹ�þ��в��С���Ļ����ԭתд�������д��֣����غ�����޸ġ�</p>
        <div className="export-subtitle-picker"><label htmlFor={`subtitle-${taskId}`}>��Ļ�汾</label><select id={`subtitle-${taskId}`} disabled={!!busy || reading || !artifacts.subtitles.length} value={subtitleId} onChange={event => setSubtitleId(event.target.value)}>
          {!artifacts.subtitles.length && <option value="">����������Ļ</option>}{artifacts.subtitles.map(item => <option key={item.subtitle_id} value={item.subtitle_id}>{dateLabel(item.created_at)} �� {item.cue_count} �� �� {shortId(item.subtitle_id)}</option>)}
        </select><Checkbox checked={withSubtitles} disabled={!!busy || reading || !selectedSubtitle} onChange={event => setWithSubtitles(event.target.checked)}>����ʱ����Ļ</Checkbox></div>
        {selectedSubtitle && <div className="export-actions"><Button href={editing.subtitleFileUrl(taskId, revision, selectedSubtitle.subtitle_id, 'captions.srt')} download>���� SRT ��Ļ</Button><Button href={editing.subtitleFileUrl(taskId, revision, selectedSubtitle.subtitle_id, 'captions.ass')} download>���� ASS ��Ļ</Button><span className="hint">������Ļ���壺{selectedSubtitle.font_name}</span></div>}
        <div className="export-actions export-main-actions"><Button type="primary" disabled={disabled || (withSubtitles && !selectedSubtitle)} onClick={() => void mutate('���ڰ�����汾��Ⱦ������ MP4��', async signal => { await editing.createMp4(taskId, revision, boundSubtitle, signal); return { revision }; })}>���� MP4</Button>
          <Button disabled={disabled || (withSubtitles && !selectedSubtitle)} onClick={() => void mutate('�������ɲ����� Shotcut ���̡�', async signal => { await editing.createMlt(taskId, revision, boundSubtitle, signal); return { revision }; })}>���� Shotcut ����</Button></div>
        <p className="hint">MP4 ����ֱ�Ӳ��š�Shotcut ������Ҫͬʱ����Դ�زģ������Ժ����� Shotcut ���¶�λ�زģ�����װ�Ϸ���Ļ���塣</p>
        {artifacts.renders.length > 0 && <section aria-label="�˰汾���ѱ����Ƭ"><h3>�ѱ����Ƭ</h3>{artifacts.renders.map((item, index) => <article key={item.render_id} className="export-artifact">
          <strong>{dateLabel(item.created_at)} �� {item.subtitle_id ? '����Ļ' : '����Ļ'} �� {shortId(item.render_id)}</strong>
          {index === 0 && <video controls preload="metadata" src={editing.renderFileUrl(taskId, revision, item.render_id, 'output.mp4')} aria-label="���±����Ƭ������" onError={() => setPreviewFailed(item.render_id)} />}
          {index === 0 && previewFailed === item.render_id && <p role="status" className="hint">��ҳԤ���ݲ����ã������� MP4 �鿴��</p>}
          <div className="export-actions"><Button href={editing.renderFileUrl(taskId, revision, item.render_id, 'output.mp4')} download>���� MP4 {index + 1}</Button><Button href={editing.renderFileUrl(taskId, revision, item.render_id, 'verification.json')} download>���س�Ƭ�����¼ {index + 1}</Button></div>
        </article>)}</section>}
        {artifacts.mlt_exports.length > 0 && <section aria-label="�˰汾���ѱ��湤��"><h3>�ѱ��� Shotcut ����</h3>{artifacts.mlt_exports.map((item, index) => <article key={item.export_id} className="export-artifact"><strong>{dateLabel(item.created_at)} �� {item.subtitle_id ? '����Ļ' : '����Ļ'} �� {shortId(item.export_id)}</strong><div className="export-actions"><Button href={editing.mltFileUrl(taskId, revision, item.export_id, 'project.mlt')} download>���� Shotcut ���� {index + 1}</Button></div></article>)}</section>}
      </>}
    </section>
  </section>;
}
