# === SECTION: background (s08) ===
# 后台任务管理器
import subprocess
import threading
import uuid
from queue import Queue

# 工作目录设置
import os
from pathlib import Path
WORKDIR = Path.cwd()


class BackgroundManager:
    def __init__(self):
        # 任务字典
        self.tasks = {}
        # 通知队列
        self.notifications = Queue()

    # 运行后台任务
    # 输入: command - 要执行的命令, timeout - 超时时间（秒）
    # 输出: 任务启动信息
    def run(self, command: str, timeout: int = 120) -> str:
        # 生成任务 ID
        tid = str(uuid.uuid4())[:8]
        # 初始化任务信息
        self.tasks[tid] = {"status": "running", "command": command, "result": None}
        # 启动后台线程执行任务
        threading.Thread(target=self._exec, args=(tid, command, timeout), daemon=True).start()
        # 返回启动信息
        return f"Background task {tid} started: {command[:80]}"

    # 执行后台任务
    # 输入: tid - 任务 ID, command - 要执行的命令, timeout - 超时时间
    def _exec(self, tid: str, command: str, timeout: int):
        try:
            # 执行命令
            r = subprocess.run(command, shell=True, cwd=WORKDIR,
                               capture_output=True, text=True, timeout=timeout)
            # 合并输出并限制长度
            output = (r.stdout + r.stderr).strip()[:50000]
            # 更新任务状态为完成
            self.tasks[tid].update({"status": "completed", "result": output or "(no output)"})
        except Exception as e:
            # 更新任务状态为错误
            self.tasks[tid].update({"status": "error", "result": str(e)})
        # 发送通知
        self.notifications.put({"task_id": tid, "status": self.tasks[tid]["status"],
                                "result": self.tasks[tid]["result"][:500]})

    # 检查后台任务状态
    # 输入: tid - 任务 ID（可选）
    # 输出: 任务状态信息
    def check(self, tid: str = None) -> str:
        if tid:
            # 检查指定任务
            t = self.tasks.get(tid)
            return f"[{t['status']}] {t.get('result', '(running)')}" if t else f"Unknown: {tid}"
        # 检查所有任务
        return "\n".join(f"{k}: [{v['status']}] {v['command'][:60]}" for k, v in self.tasks.items()) or "No bg tasks."

    # 清空通知队列
    # 输出: 通知列表
    def drain(self) -> list:
        notifs = []
        # 从队列中获取所有通知
        while not self.notifications.empty():
            notifs.append(self.notifications.get_nowait())
        return notifs
