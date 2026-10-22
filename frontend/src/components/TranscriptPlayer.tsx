import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Skeleton } from 'antd';
import { getTranscript, taskSourceUrl, type Transcript, type TranscriptSentence } from '../api/transcript';

export function TranscriptPlayer({ taskId, download }: { taskId: string; download: string }) {
  return <TranscriptForTask key={`${taskId}:${download}`} taskId={taskId} download={download} />;
}

function TranscriptForTask({ taskId, download }: { taskId: string; download: string }) {
  const video = useRef<HTMLVideoElement>(null);
  const mounted = useRef(false);
  const pendingSeek = useRef<TranscriptSentence | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [mediaError, setMediaError] = useState(false);
  const [active, setActive] = useState<string>();
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    mounted.current = true;
    const element = video.current;
    return () => {
      mounted.current = false; pendingSeek.current = null;
      if (element) { element.pause(); element.removeAttribute('src'); element.load(); }
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    getTranscript(taskId, download, controller.signal).then(result => {
      if (!controller.signal.aborted) setTranscript(result);
    }).catch(error => {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : '文字时间轴暂时无法读取。');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [taskId, download, retry]);
  function applySeek(element: HTMLVideoElement) {
    const sentence = pendingSeek.current;
    if (!mounted.current || element !== video.current || !sentence || element.readyState < 1) return;
    try {
      element.currentTime = sentence.start;
      pendingSeek.current = null; setActive(sentence.id);
      setNotice(`已跳到 ${sentence.start.toFixed(2)} 秒，点击视频播放即可试听。`);
    } catch { setNotice('视频仍在加载，请稍后再点击这句话。'); }
  }
  function seek(sentence: TranscriptSentence) {
    const element = video.current;
    if (!element || !mounted.current || mediaError) return;
    pendingSeek.current = sentence; setActive(sentence.id);
    setNotice(`正在定位到 ${sentence.start.toFixed(2)} 秒…`);
    applySeek(element);
  }
  function updateActive(element: HTMLVideoElement) {
    if (!mounted.current || element !== video.current || pendingSeek.current) return;
    setActive(transcript?.sentences.find(sentence => sentence.start <= element.currentTime && element.currentTime < sentence.end)?.id);
  }
  let source: string;
  try { source = taskSourceUrl(taskId); }
  catch { return <Alert type="error" message="原视频地址异常，请重新选择任务。" />; }
  return <section aria-label="原视频与文字联动">
    <h2>原视频与文字联动</h2>
    <p className="hint">点击一句转写，视频跳到对应时刻。自动转写可能有误，请试听核对。</p>
    <video ref={video} controls playsInline preload="metadata" src={source} aria-label="原视频播放器"
      style={{ width: '100%', maxHeight: 420 }} onLoadedMetadata={event => applySeek(event.currentTarget)}
      onCanPlay={event => applySeek(event.currentTarget)} onTimeUpdate={event => updateActive(event.currentTarget)}
      onError={event => { if (mounted.current && event.currentTarget === video.current) { setMediaError(true); setNotice(''); } }} />
    {mediaError && <Alert type="warning" message="原视频暂时无法播放，请检查后台服务后刷新任务。" />}
    <p role="status" aria-live="polite" className="hint">{notice}</p>
    {loading ? <Skeleton active title={false} paragraph={{ rows: 3 }} aria-label="正在加载转写" />
      : error ? <Alert type="warning" message={error} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>刷新文字时间轴</Button>} />
      : transcript && (transcript.sentences.length ? <ol aria-label="可点击的转写句子" style={{ maxHeight: 320, overflow: 'auto', paddingLeft: 28 }}>
        {transcript.sentences.map((sentence, index) => <li key={sentence.id} style={{ marginBottom: 8 }}>
          <Button type={active === sentence.id ? 'primary' : 'default'} disabled={mediaError} aria-label={`跳转第 ${index + 1} 句，${sentence.start.toFixed(2)} 秒：${sentence.text}`}
            aria-current={active === sentence.id ? 'true' : undefined} onClick={() => seek(sentence)}
            style={{ height: 'auto', whiteSpace: 'normal', textAlign: 'left', width: '100%' }}>
            <span>{sentence.start.toFixed(2)}–{sentence.end.toFixed(2)} 秒 · {sentence.text}</span>
          </Button>
        </li>)}
      </ol> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这段视频没有可用转写，仍可播放视频。" />)}
  </section>;
}
