import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty } from 'antd';
import * as api from '../api/batchExport';
import { validId } from '../api/tasks';
import type { BatchExportHistory } from '../types/batchExport';

interface Props { taskId: string; revisionId: string; disabled?: boolean; onBusyChange?: (busy: boolean) => void }
interface Recovery { task_id: string; revision_id: string; request_id: string }
const fallback = new Map<string, Recovery>();
const storageBlocked = new Set<string>();
const key = (task: string, revision: string) => `clipforge:batch-recovery:v1:${task}:${revision}`;
function read(task: string, revision: string): Recovery | undefined {
  const storageKey = key(task, revision);
  if (storageBlocked.has(storageKey)) return fallback.get(storageKey);
  try {
    const raw = sessionStorage.getItem(storageKey);
    if (raw) {
      const value = JSON.parse(raw) as Recovery;
      if (value.task_id === task && value.revision_id === revision && validId(value.request_id)) return value;
    } else fallback.delete(storageKey);
  } catch { /* A blocked storage surface still has process-local recovery. */ }
  return fallback.get(storageKey);
}
function remember(task: string, revision: string, value?: Recovery) {
  const storageKey = key(task, revision);
  if (value) fallback.set(storageKey, value); else fallback.delete(storageKey);
  try { if (value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else sessionStorage.removeItem(storageKey); }
  catch { storageBlocked.add(storageKey); }
}
const empty: BatchExportHistory = { items: [], unavailable_count: 0 };

export function BatchExportPanel({ taskId, revisionId, disabled = false, onBusyChange }: Props) {
  const [history, setHistory] = useState<BatchExportHistory>(empty);
  const [pending, setPending] = useState<Recovery>();
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const generation = useRef(0);
  const request = useRef<AbortController>();
  const historyRequest = useRef<AbortController>();
  const callback = useRef(onBusyChange); callback.current = onBusyChange;
  const token = `${taskId}:${revisionId}`;
  const current = useRef(token); current.current = token;
  // Hide old revision downloads immediately, before the new effect runs.
  const [loadedFor, setLoadedFor] = useState(token);
  const shown = loadedFor === token ? history : empty;
  const recovery = loadedFor === token ? pending : read(taskId, revisionId);

  async function refresh() {
    const version = generation.current;
    const target = token;
    historyRequest.current?.abort();
    const controller = new AbortController(); historyRequest.current = controller;
    setLoading(true); setLoadError('');
    try {
      const loaded = await api.listBatchExports(taskId, revisionId, controller.signal);
      if (current.current !== target || generation.current !== version || controller.signal.aborted) return;
      setHistory(loaded); setLoadedFor(target);
      const stored = read(taskId, revisionId);
      if (stored && loaded.items.some(item => item.request_id === stored.request_id)) {
        remember(taskId, revisionId); setPending(undefined); setError('');
      }
    } catch (failure) {
      if (current.current === target && generation.current === version && !controller.signal.aborted) setLoadError(failure instanceof Error ? failure.message : '批量导出历史读取失败。');
    } finally {
      if (current.current === target && generation.current === version) setLoading(false);
    }
  }

  useEffect(() => {
    generation.current += 1;
    request.current?.abort(); historyRequest.current?.abort();
    setHistory(empty); setLoadedFor(token); setPending(read(taskId, revisionId));
    setBusy(false); callback.current?.(false); setError(''); setLoadError('');
    void refresh();
    return () => { generation.current += 1; request.current?.abort(); historyRequest.current?.abort(); callback.current?.(false); };
  }, [taskId, revisionId]); // Every history and recovery record belongs to this pair.

  async function generate() {
    if (busy || disabled || loading) return;
    const target = token;
    const version = generation.current;
    let recoveryRecord: Recovery;
    try {
      recoveryRecord = read(taskId, revisionId) ?? { task_id: taskId, revision_id: revisionId, request_id: api.newBatchExportRequestId() };
    } catch {
      setError('无法创建批量请求编号，请使用支持随机数生成的浏览器。');
      return;
    }
    remember(taskId, revisionId, recoveryRecord); setPending(recoveryRecord);
    setBusy(true); callback.current?.(true); setError('');
    const controller = new AbortController(); request.current = controller;
    try {
      const saved = await api.createBatchExport(taskId, revisionId, recoveryRecord.request_id, controller.signal);
      remember(taskId, revisionId);
      if (current.current !== target || generation.current !== version) return;
      setPending(undefined); setHistory(previous => ({ ...previous, items: [saved, ...previous.items.filter(item => item.export_id !== saved.export_id)] }));
    } catch (failure) {
      if (failure instanceof api.ApiError) remember(taskId, revisionId);
      if (current.current !== target || generation.current !== version) return;
      if (failure instanceof api.ApiError) setPending(undefined);
      setError(failure instanceof Error ? failure.message : '批量导出失败，请刷新本版本记录。');
    } finally {
      if (current.current === target && generation.current === version) { setBusy(false); callback.current?.(false); }
    }
  }

  return <section aria-label="保存版本批量导出" className="batch-export-panel">
    <h4>按最终切点批量导出</h4>
    <p>将当前保存版本的每段剪辑分别生成 MP4，ZIP 内含 EDL 和记录原范围、最终切点、帧数与校验值的清单。修改后请先保存，再生成对应版本的 ZIP。</p>
    <p>单次最多 100 段、总计 18000 帧、512 MiB；处理和核验限时 90 秒。</p>
    {recovery && !busy && <Alert type="warning" showIcon message="这次批量请求的结果尚未确认。请先刷新记录；继续同一请求会沿用原请求编号，不会生成第二份。" />}
    {error && <Alert type="warning" showIcon message={error} />}
    {loadError && <Alert type="error" showIcon message={loadError} />}
    <Button aria-label={recovery ? '继续同一批量请求' : '生成批量片段 ZIP'} loading={busy} disabled={disabled || loading || busy} onClick={() => void generate()}>{recovery ? '继续同一批量请求' : '生成批量片段 ZIP'}</Button>
    <Button aria-label="刷新批量导出记录" loading={loading} disabled={busy} onClick={() => void refresh()}>刷新批量导出记录</Button>
    {shown.unavailable_count > 0 && <p>有 {shown.unavailable_count} 条记录未通过校验，已隐藏下载。</p>}
    {!loading && shown.items.length === 0 ? <Empty description="此保存版本还没有已核验的批量 ZIP" /> : <ul aria-label="当前版本批量导出历史">
      {shown.items.map(item => <li key={item.export_id}>
        <p>{new Date(item.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })} · {item.clip_count} 段 · {item.total_frames} 帧 · {(item.output_bytes / 1024 / 1024).toFixed(2)} MiB</p>
        <a href={api.batchExportFileUrl(taskId, revisionId, item.export_id, 'clips.zip')} download>下载此版本片段 ZIP</a>{' · '}
        <a href={api.batchExportFileUrl(taskId, revisionId, item.export_id, 'manifest.json')} download>下载批量清单</a>{' · '}
        <a href={api.batchExportFileUrl(taskId, revisionId, item.export_id, 'source.edl.json')} download>下载批量 EDL</a>
      </li>)}
    </ul>}
  </section>;
}
