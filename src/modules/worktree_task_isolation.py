#!/usr/bin/env python3
"""
worktree_task_isolation.py - 工作树任务隔离模块

该模块提供了目录级别的隔离功能，支持并行任务执行。
任务作为控制平面，工作树作为执行平面。

核心功能：
- EventBus: 事件总线，用于跟踪工作树生命周期事件
- TaskManager: 任务管理器，支持任务的创建、更新、绑定工作树等操作
- WorktreeManager: 工作树管理器，支持创建、列出、运行、删除git工作树

使用场景：
- 并行执行多个任务，避免相互干扰
- 隔离不同任务的工作环境
- 管理任务与工作树的绑定关系
"""


import json
import os
import re
import subprocess
import time
from pathlib import Path


class EventBus:
    """
    事件总线类，用于跟踪工作树生命周期事件
    
    功能：
    - 记录工作树相关的事件，如创建、删除、运行等
    - 支持事件的追加写入
    - 提供最近事件的查询功能
    """
    def __init__(self, event_log_path: Path):
        """
        初始化事件总线
        
        参数：
        - event_log_path: 事件日志文件路径
        """
        self.path = event_log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("")

    def emit(self, event: str, task_id=None, wt_name=None, error=None, **extra):
        """
        发送事件
        
        参数：
        - event: 事件名称
        - task_id: 任务ID（可选）
        - wt_name: 工作树名称（可选）
        - error: 错误信息（可选）
        - **extra: 额外的事件数据
        """
        payload = {"event": event, "ts": time.time()}
        if task_id is not None:
            payload["task_id"] = task_id
        if wt_name:
            payload["worktree"] = wt_name
        if error:
            payload["error"] = error
        payload.update(extra)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    def list_recent(self, limit: int = 20) -> str:
        """
        列出最近的事件
        
        参数：
        - limit: 返回事件的最大数量
        
        返回：
        - JSON格式的事件列表
        """
        n = max(1, min(int(limit or 20), 200))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        items = []
        for line in lines[-n:]:
            try:
                items.append(json.loads(line))
            except Exception:
                items.append({"event": "parse_error", "raw": line})
        return json.dumps(items, indent=2)


class TaskManager:
    """
    任务管理器类，用于管理任务和工作树的绑定关系
    
    功能：
    - 创建、获取、更新任务
    - 绑定/解绑工作树
    - 记录任务关闭信息
    - 列出所有任务
    """
    def __init__(self, tasks_dir: Path):
        """
        初始化任务管理器
        
        参数：
        - tasks_dir: 任务存储目录
        """
        self.dir = tasks_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._next_id = self._max_id() + 1

    def _max_id(self) -> int:
        """
        获取当前最大的任务ID
        
        返回：
        - 最大的任务ID
        """
        ids = []
        for f in self.dir.glob("task_*.json"):
            try:
                ids.append(int(f.stem.split("_")[1]))
            except Exception:
                pass
        return max(ids) if ids else 0

    def _path(self, task_id: int) -> Path:
        """
        获取任务文件路径
        
        参数：
        - task_id: 任务ID
        
        返回：
        - 任务文件路径
        """
        return self.dir / f"task_{task_id}.json"

    def _load(self, task_id: int) -> dict:
        """
        加载任务
        
        参数：
        - task_id: 任务ID
        
        返回：
        - 任务字典
        """
        path = self._path(task_id)
        if not path.exists():
            raise ValueError(f"Task {task_id} not found")
        return json.loads(path.read_text())

    def _save(self, task: dict):
        """
        保存任务
        
        参数：
        - task: 任务字典
        """
        self._path(task["id"]).write_text(json.dumps(task, indent=2))

    def create(self, subject: str, description: str = "") -> str:
        """
        创建任务
        
        参数：
        - subject: 任务主题
        - description: 任务描述（可选）
        
        返回：
        - JSON格式的任务信息
        """
        task = {
            "id": self._next_id, "subject": subject, "description": description,
            "status": "pending", "owner": "", "worktree": "",
            "worktree_state": "unbound", "last_worktree": "",
            "closeout": None, "blockedBy": [],
            "created_at": time.time(), "updated_at": time.time(),
        }
        self._save(task)
        self._next_id += 1
        return json.dumps(task, indent=2)

    def get(self, task_id: int) -> str:
        """
        获取任务详情
        
        参数：
        - task_id: 任务ID
        
        返回：
        - JSON格式的任务详情
        """
        return json.dumps(self._load(task_id), indent=2)

    def exists(self, task_id: int) -> bool:
        """
        检查任务是否存在
        
        参数：
        - task_id: 任务ID
        
        返回：
        - 任务是否存在
        """
        return self._path(task_id).exists()

    def update(self, task_id: int, status: str = None, owner: str = None) -> str:
        """
        更新任务状态或所有者
        
        参数：
        - task_id: 任务ID
        - status: 任务状态（可选）
        - owner: 任务所有者（可选）
        
        返回：
        - JSON格式的更新后的任务信息
        """
        task = self._load(task_id)
        if status:
            if status not in ("pending", "in_progress", "completed", "deleted"):
                raise ValueError(f"Invalid status: {status}")
            task["status"] = status
        if owner is not None:
            task["owner"] = owner
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2)

    def bind_worktree(self, task_id: int, worktree: str, owner: str = "") -> str:
        """
        绑定工作树到任务
        
        参数：
        - task_id: 任务ID
        - worktree: 工作树名称
        - owner: 任务所有者（可选）
        
        返回：
        - JSON格式的更新后的任务信息
        """
        task = self._load(task_id)
        task["worktree"] = worktree
        task["last_worktree"] = worktree
        task["worktree_state"] = "active"
        if owner:
            task["owner"] = owner
        if task["status"] == "pending":
            task["status"] = "in_progress"
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2)

    def unbind_worktree(self, task_id: int) -> str:
        """
        解绑任务的工作树
        
        参数：
        - task_id: 任务ID
        
        返回：
        - JSON格式的更新后的任务信息
        """
        task = self._load(task_id)
        task["worktree"] = ""
        task["worktree_state"] = "unbound"
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2)

    def record_closeout(self, task_id: int, action: str, reason: str = "", keep_binding: bool = False) -> str:
        """
        记录任务关闭信息
        
        参数：
        - task_id: 任务ID
        - action: 关闭动作（如"kept"或"removed"）
        - reason: 关闭原因（可选）
        - keep_binding: 是否保持工作树绑定（可选）
        
        返回：
        - JSON格式的更新后的任务信息
        """
        task = self._load(task_id)
        task["closeout"] = {
            "action": action,
            "reason": reason,
            "at": time.time(),
        }
        task["worktree_state"] = action
        if not keep_binding:
            task["worktree"] = ""
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2)

    def list_all(self) -> str:
        """
        列出所有任务
        
        返回：
        - 任务列表字符串
        """
        tasks = []
        for f in sorted(self.dir.glob("task_*.json")):
            tasks.append(json.loads(f.read_text()))
        if not tasks:
            return "No tasks."
        lines = []
        for t in tasks:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]", "deleted": "[-]"}.get(t["status"], "[?]")
            owner = f" owner={t['owner']}" if t.get("owner") else ""
            wt = f" wt={t['worktree']}" if t.get("worktree") else ""
            lines.append(f"{marker} #{t['id']}: {t['subject']}{owner}{wt}")
        return "\n".join(lines)


class WorktreeManager:
    """
    工作树管理器类，用于管理git工作树
    
    功能：
    - 创建、列出、运行、删除git工作树
    - 绑定工作树到任务
    - 记录工作树生命周期事件
    - 检查工作树状态
    """
    def __init__(self, repo_root: Path, tasks: TaskManager, events: EventBus):
        """
        初始化工作树管理器
        
        参数：
        - repo_root: 仓库根目录
        - tasks: 任务管理器实例
        - events: 事件总线实例
        """
        self.repo_root = repo_root
        self.tasks = tasks
        self.events = events
        self.dir = repo_root / ".worktrees"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"worktrees": []}, indent=2))
        self.git_available = self._check_git()

    def _check_git(self) -> bool:
        """
        检查是否在git仓库中
        
        返回：
        - 是否在git仓库中
        """
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.repo_root, capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    def _run_git(self, args: list[str]) -> str:
        """
        运行git命令
        
        参数：
        - args: git命令参数
        
        返回：
        - 命令输出
        """
        if not self.git_available:
            raise RuntimeError("Not in a git repository.")
        r = subprocess.run(
            ["git", *args], cwd=self.repo_root,
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise RuntimeError((r.stdout + r.stderr).strip() or f"git {' '.join(args)} failed")
        return (r.stdout + r.stderr).strip() or "(no output)"

    def _load_index(self) -> dict:
        """
        加载工作树索引
        
        返回：
        - 工作树索引字典
        """
        return json.loads(self.index_path.read_text())

    def _save_index(self, data: dict):
        """
        保存工作树索引
        
        参数：
        - data: 工作树索引字典
        """
        self.index_path.write_text(json.dumps(data, indent=2))

    def _find(self, name: str) -> dict | None:
        """
        查找工作树
        
        参数：
        - name: 工作树名称
        
        返回：
        - 工作树信息字典，找不到返回None
        """
        for wt in self._load_index().get("worktrees", []):
            if wt.get("name") == name:
                return wt
        return None

    def _update_entry(self, name: str, **changes) -> dict:
        """
        更新工作树条目
        
        参数：
        - name: 工作树名称
        - **changes: 要更新的字段
        
        返回：
        - 更新后的工作树信息字典
        """
        idx = self._load_index()
        updated = None
        for item in idx.get("worktrees", []):
            if item.get("name") == name:
                item.update(changes)
                updated = item
                break
        self._save_index(idx)
        if not updated:
            raise ValueError(f"Worktree '{name}' not found in index")
        return updated

    def _validate_name(self, name: str):
        """
        验证工作树名称
        
        参数：
        - name: 工作树名称
        """
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name or ""):
            raise ValueError("Invalid worktree name. Use 1-40 chars: letters, digits, ., _, -")

    def create(self, name: str, task_id: int = None, base_ref: str = "HEAD") -> str:
        """
        创建工作树
        
        参数：
        - name: 工作树名称
        - task_id: 任务ID（可选）
        - base_ref: 基础引用（可选，默认为HEAD）
        
        返回：
        - JSON格式的工作树信息
        """
        self._validate_name(name)
        if self._find(name):
            raise ValueError(f"Worktree '{name}' already exists")
        if task_id is not None and not self.tasks.exists(task_id):
            raise ValueError(f"Task {task_id} not found")

        path = self.dir / name
        branch = f"wt/{name}"
        self.events.emit("worktree.create.before", task_id=task_id, wt_name=name)
        try:
            self._run_git(["worktree", "add", "-b", branch, str(path), base_ref])
            entry = {
                "name": name, "path": str(path), "branch": branch,
                "task_id": task_id, "status": "active", "created_at": time.time(),
            }
            idx = self._load_index()
            idx["worktrees"].append(entry)
            self._save_index(idx)
            if task_id is not None:
                self.tasks.bind_worktree(task_id, name)
            self.events.emit("worktree.create.after", task_id=task_id, wt_name=name)
            return json.dumps(entry, indent=2)
        except Exception as e:
            self.events.emit("worktree.create.failed", task_id=task_id, wt_name=name, error=str(e))
            raise

    def list_all(self) -> str:
        """
        列出所有工作树
        
        返回：
        - 工作树列表字符串
        """
        wts = self._load_index().get("worktrees", [])
        if not wts:
            return "No worktrees in index."
        lines = []
        for wt in wts:
            suffix = f" task={wt['task_id']}" if wt.get("task_id") else ""
            lines.append(f"[{wt.get('status', '?')}] {wt['name']} -> {wt['path']} ({wt.get('branch', '-')}){suffix}")
        return "\n".join(lines)

    def status(self, name: str) -> str:
        """
        检查工作树状态
        
        参数：
        - name: 工作树名称
        
        返回：
        - 工作树状态字符串
        """
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"
        path = Path(wt["path"])
        if not path.exists():
            return f"Error: Worktree path missing: {path}"
        r = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=path, capture_output=True, text=True, timeout=60,
        )
        return (r.stdout + r.stderr).strip() or "Clean worktree"

    def enter(self, name: str) -> str:
        """
        进入工作树
        
        参数：
        - name: 工作树名称
        
        返回：
        - JSON格式的更新后的工作树信息
        """
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"
        path = Path(wt["path"])
        if not path.exists():
            return f"Error: Worktree path missing: {path}"
        updated = self._update_entry(name, last_entered_at=time.time())
        self.events.emit("worktree.enter", task_id=wt.get("task_id"), wt_name=name, path=str(path))
        return json.dumps(updated, indent=2)

    def run(self, name: str, command: str) -> str:
        """
        在工作树中运行命令
        
        参数：
        - name: 工作树名称
        - command: 要运行的命令
        
        返回：
        - 命令输出
        """
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
        if any(d in command for d in dangerous):
            return "Error: Dangerous command blocked"
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"
        path = Path(wt["path"])
        if not path.exists():
            return f"Error: Worktree path missing: {path}"
        try:
            self._update_entry(
                name,
                last_entered_at=time.time(),
                last_command_at=time.time(),
                last_command_preview=command[:120],
            )
            self.events.emit("worktree.run.before", task_id=wt.get("task_id"), wt_name=name, command=command[:120])
            r = subprocess.run(command, shell=True, cwd=path,
                               capture_output=True, text=True, timeout=300)
            out = (r.stdout + r.stderr).strip()
            self.events.emit("worktree.run.after", task_id=wt.get("task_id"), wt_name=name)
            return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            self.events.emit("worktree.run.timeout", task_id=wt.get("task_id"), wt_name=name)
            return "Error: Timeout (300s)"

    def remove(
        self,
        name: str,
        force: bool = False,
        complete_task: bool = False,
        reason: str = "",
    ) -> str:
        """
        删除工作树
        
        参数：
        - name: 工作树名称
        - force: 是否强制删除
        - complete_task: 是否标记绑定的任务为完成
        - reason: 删除原因
        
        返回：
        - 删除结果信息
        """
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"
        task_id = wt.get("task_id")
        self.events.emit("worktree.remove.before", task_id=task_id, wt_name=name)
        try:
            args = ["worktree", "remove"]
            if force:
                args.append("--force")
            args.append(wt["path"])
            self._run_git(args)
            if complete_task and task_id is not None:
                self.tasks.update(task_id, status="completed")
                self.events.emit("task.completed", task_id=task_id, wt_name=name)
            if task_id is not None:
                self.tasks.record_closeout(task_id, "removed", reason, keep_binding=False)
            self._update_entry(
                name,
                status="removed",
                removed_at=time.time(),
                closeout={"action": "remove", "reason": reason, "at": time.time()},
            )
            self.events.emit("worktree.remove.after", task_id=task_id, wt_name=name)
            return f"Removed worktree '{name}'"
        except Exception as e:
            self.events.emit("worktree.remove.failed", task_id=task_id, wt_name=name, error=str(e))
            raise

    def keep(self, name: str) -> str:
        """
        保留工作树
        
        参数：
        - name: 工作树名称
        
        返回：
        - JSON格式的更新后的工作树信息
        """
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"
        if wt.get("task_id") is not None:
            self.tasks.record_closeout(wt["task_id"], "kept", "", keep_binding=True)
        self._update_entry(
            name,
            status="kept",
            kept_at=time.time(),
            closeout={"action": "keep", "reason": "", "at": time.time()},
        )
        self.events.emit("worktree.keep", task_id=wt.get("task_id"), wt_name=name)
        return json.dumps(self._find(name), indent=2)

    def closeout(
        self,
        name: str,
        action: str,
        reason: str = "",
        force: bool = False,
        complete_task: bool = False,
    ) -> str:
        """
        关闭工作树
        
        参数：
        - name: 工作树名称
        - action: 关闭动作（"keep"或"remove"）
        - reason: 关闭原因
        - force: 是否强制删除
        - complete_task: 是否标记绑定的任务为完成
        
        返回：
        - 关闭结果信息
        """
        if action == "keep":
            wt = self._find(name)
            if not wt:
                return f"Error: Unknown worktree '{name}'"
            if wt.get("task_id") is not None:
                self.tasks.record_closeout(
                    wt["task_id"], "kept", reason, keep_binding=True
                )
                if complete_task:
                    self.tasks.update(wt["task_id"], status="completed")
            self._update_entry(
                name,
                status="kept",
                kept_at=time.time(),
                closeout={"action": "keep", "reason": reason, "at": time.time()},
            )
            self.events.emit(
                "worktree.closeout.keep",
                task_id=wt.get("task_id"),
                wt_name=name,
                reason=reason,
            )
            return json.dumps(self._find(name), indent=2)
        if action == "remove":
            self.events.emit("worktree.closeout.remove", wt_name=name, reason=reason)
            return self.remove(
                name,
                force=force,
                complete_task=complete_task,
                reason=reason,
            )
        raise ValueError("action must be 'keep' or 'remove'")


def detect_repo_root(cwd: Path) -> Path | None:
    """
    检测git仓库根目录
    
    参数：
    - cwd: 当前工作目录
    
    返回：
    - 仓库根目录路径，如果不是git仓库则返回None
    """
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        root = Path(r.stdout.strip())
        return root if r.returncode == 0 and root.exists() else None
    except Exception:
        return None


def get_worktree_tools():
    """
    获取工作树相关工具
    
    返回：
    - 工作树相关工具列表
    """
    return [
        {"name": "task_create", "description": "Create a new task on the shared task board.",
         "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}}, "required": ["subject"]}},
        {"name": "task_list", "description": "List all tasks with status, owner, and worktree binding.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "task_get", "description": "Get task details by ID.",
         "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
        {"name": "task_update", "description": "Update task status or owner.",
         "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]}, "owner": {"type": "string"}}, "required": ["task_id"]}},
        {"name": "task_bind_worktree", "description": "Bind a task to a worktree name.",
         "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}, "worktree": {"type": "string"}, "owner": {"type": "string"}}, "required": ["task_id", "worktree"]}},
        {"name": "worktree_create", "description": "Create a git worktree and optionally bind it to a task.",
         "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "task_id": {"type": "integer"}, "base_ref": {"type": "string"}}, "required": ["name"]}},
        {"name": "worktree_list", "description": "List worktrees tracked in .worktrees/index.json.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "worktree_enter", "description": "Enter or reopen a worktree lane before working in it.",
         "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
        {"name": "worktree_status", "description": "Show git status for one worktree.",
         "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
        {"name": "worktree_run", "description": "Run a shell command in a named worktree directory.",
         "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "command": {"type": "string"}}, "required": ["name", "command"]}},
        {"name": "worktree_closeout", "description": "Close out a lane by keeping it for follow-up or removing it.",
         "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "action": {"type": "string", "enum": ["keep", "remove"]}, "reason": {"type": "string"}, "force": {"type": "boolean"}, "complete_task": {"type": "boolean"}}, "required": ["name", "action"]}},
        {"name": "worktree_remove", "description": "Remove a worktree and optionally mark its bound task completed.",
         "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "force": {"type": "boolean"}, "complete_task": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["name"]}},
        {"name": "worktree_keep", "description": "Mark a worktree as kept without removing it.",
         "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
        {"name": "worktree_events", "description": "List recent lifecycle events.",
         "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    ]
