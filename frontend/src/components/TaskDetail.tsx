import { Alert, Empty, Progress, Skeleton, Steps, Tag } from 'antd';
import { useTasks } from '../store/tasks';
import { stageLabels, statusLabels, type Stage } from '../types/tasks';
import { ClipResults } from './ClipResults';

const steps: Stage[] = ['probing', 'normalizing', 'splitting', 'packaging', 'done'];
export function TaskDetail() {
  const { task, detailLoading, detailError } = useTasks();
  const failure = task?.status === 'FAILED' || task?.status === 'SUBMISSION_UNKNOWN';
  const note = task?.error || (task?.status === 'QUEUED' ? '任务已进入队列，等待后台开始处理。' : task?.status === 'SUBMISSION_UNKNOWN' ? '提交结果待确认，请先等待状态更新，避免重复上传。' : task?.status === 'RUNNING' ? '后台正在处理，页面每 3 秒自动更新。' : '');
  return <section className="panel pad details-panel" id="task-detail" aria-label="任务详情">
    <div className="head"><h2><span className="step">03</span>处理与下载</h2>{task && <Tag color={failure ? 'error' : task.status === 'SUCCEEDED' ? 'success' : 'purple'}>{statusLabels[task.status]}</Tag>}</div>
    {detailError ? <Alert type="warning" message={detailError} className="upload-notice" /> : detailLoading && !task ? <Skeleton active className="upload-notice" /> : !task ? <div className="empty"><Empty description={<><strong>你的下一段精彩，从这里开始</strong><p>上传一个视频，或选择历史任务。<br />我们会在这里为你整理好每一个片段。</p></>} /></div> : <>
      <h3 className="detail-name">{task.source_name}</h3><div className="task-id">任务编号 {task.task_id}</div>
      <div className="track"><div className="progress-label"><strong>{stageLabels[task.stage]}</strong><span>{task.progress}%</span></div><Progress percent={task.progress} showInfo={false} status={failure ? 'exception' : task.status === 'SUCCEEDED' ? 'success' : 'normal'} /><Steps size="small" responsive={false} progressDot current={steps.indexOf(task.stage)} status={failure ? 'error' : task.status === 'SUCCEEDED' ? 'finish' : 'process'} items={['读取', '转码', '切片', '打包', '完成'].map(title => ({ title }))} /><p className="hint">显示的是阶段进度，转码或切片时可能暂时保持不变。</p></div>
      <div role="status" aria-live="polite">{note && <Alert type={failure ? 'error' : 'info'} message={note} showIcon />}</div>
      {task.status === 'SUCCEEDED' && task.result && <ClipResults key={task.task_id} task={task} result={task.result} />}
    </>}
  </section>;
}
