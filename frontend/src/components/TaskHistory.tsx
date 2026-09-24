import { Button, Empty, Spin } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useTasks } from '../store/tasks';
import { statusLabels } from '../types/tasks';

export function TaskHistory() {
  const { tasks, selectedId, offset, loading, hasMore, selectTask, setOffset, refresh } = useTasks();
  return <section className="panel history" id="history-panel" aria-label="历史任务">
    <div className="head history-head"><h2><span className="step">02</span>我的任务</h2><Button size="small" icon={<ReloadOutlined />} onClick={refresh} aria-label="刷新任务列表">刷新</Button></div>
    <Spin spinning={loading}><div className="task-list">
      {!tasks.length && !loading && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有任务，上传一个视频开始吧。" />}
      {tasks.map(task => <button key={task.task_id} type="button" className={`task${task.task_id === selectedId ? ' active' : ''}`} aria-pressed={task.task_id === selectedId} onClick={() => selectTask(task.task_id, true)}>
        <span className="task-name">{task.source_name}</span><span className="task-meta"><span>{new Date(task.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}</span><span className="task-status" data-state={task.status}>{statusLabels[task.status]}</span></span>
      </button>)}
    </div></Spin>
    <div className="pager"><Button size="small" disabled={offset === 0 || loading} onClick={() => setOffset(offset - 10)}>上一页</Button><span>第 {offset / 10 + 1} 页</span><Button size="small" disabled={!hasMore || loading} onClick={() => setOffset(offset + 10)}>下一页</Button></div>
  </section>;
}
