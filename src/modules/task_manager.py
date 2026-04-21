# === SECTION: file_tasks (s07) ===
# 任务管理器
import json
from pathlib import Path

# 任务目录
TASKS_DIR = Path.cwd() / ".tasks"


class TaskManager:
    def __init__(self):
        # 创建任务目录（如果不存在）
        TASKS_DIR.mkdir(exist_ok=True)

    # 获取下一个任务 ID
    # 输出: 下一个任务 ID
    def _next_id(self) -> int:
        # 收集所有任务 ID
        ids = [int(f.stem.split("_")[1]) for f in TASKS_DIR.glob("task_*.json")]
        # 返回最大 ID + 1，默认为 1
        return max(ids, default=0) + 1

    # 加载任务
    # 输入: tid - 任务 ID
    # 输出: 任务字典
    def _load(self, tid: int) -> dict:
        # 构建任务文件路径
        p = TASKS_DIR / f"task_{tid}.json"
        # 检查文件是否存在
        if not p.exists(): raise ValueError(f"Task {tid} not found")
        # 读取并解析任务文件
        return json.loads(p.read_text())

    # 保存任务
    # 输入: task - 任务字典
    def _save(self, task: dict):
        # 构建任务文件路径并写入
        (TASKS_DIR / f"task_{task['id']}.json").write_text(json.dumps(task, indent=2))

    # 创建任务
    # 输入: subject - 任务主题, description - 任务描述
    # 输出: 任务 JSON 字符串
    def create(self, subject: str, description: str = "") -> str:
        # 创建任务字典
        task = {"id": self._next_id(), "subject": subject, "description": description,
                "status": "pending", "owner": None, "blockedBy": [], "blocks": []}
        # 保存任务
        self._save(task)
        # 返回任务 JSON 字符串
        return json.dumps(task, indent=2)

    # 获取任务
    # 输入: tid - 任务 ID
    # 输出: 任务 JSON 字符串
    def get(self, tid: int) -> str:
        return json.dumps(self._load(tid), indent=2)

    # 更新任务
    # 输入: tid - 任务 ID, status - 任务状态, add_blocked_by - 被阻塞的任务 ID 列表, add_blocks - 阻塞的任务 ID 列表
    # 输出: 任务 JSON 字符串或删除确认
    def update(self, tid: int, status: str = None,
               add_blocked_by: list = None, add_blocks: list = None) -> str:
        # 加载任务
        task = self._load(tid)
        # 更新状态
        if status:
            task["status"] = status
            # 如果任务完成，更新依赖它的任务
            if status == "completed":
                for f in TASKS_DIR.glob("task_*.json"):
                    t = json.loads(f.read_text())
                    if tid in t.get("blockedBy", []):
                        t["blockedBy"].remove(tid)
                        self._save(t)
            # 如果任务删除，删除文件
            if status == "deleted":
                (TASKS_DIR / f"task_{tid}.json").unlink(missing_ok=True)
                return f"Task {tid} deleted"
        # 添加被阻塞的任务
        if add_blocked_by:
            task["blockedBy"] = list(set(task["blockedBy"] + add_blocked_by))
        # 添加阻塞的任务
        if add_blocks:
            task["blocks"] = list(set(task["blocks"] + add_blocks))
        # 保存任务
        self._save(task)
        # 返回任务 JSON 字符串
        return json.dumps(task, indent=2)

    # 列出所有任务
    # 输出: 任务列表字符串
    def list_all(self) -> str:
        # 加载所有任务
        tasks = [json.loads(f.read_text()) for f in sorted(TASKS_DIR.glob("task_*.json"))]
        # 如果没有任务，返回提示
        if not tasks: return "No tasks."
        lines = []
        # 遍历任务
        for t in tasks:
            # 根据状态生成标记
            m = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(t["status"], "[?]")
            # 添加所有者信息
            owner = f" @{t['owner']}" if t.get("owner") else ""
            # 添加阻塞信息
            blocked = f" (blocked by: {t['blockedBy']})" if t.get("blockedBy") else ""
            # 添加到行列表
            lines.append(f"{m} #{t['id']}: {t['subject']}{owner}{blocked}")
        # 返回任务列表字符串
        return "\n".join(lines)

    # 认领任务
    # 输入: tid - 任务 ID, owner - 所有者
    # 输出: 认领确认
    def claim(self, tid: int, owner: str) -> str:
        # 加载任务
        task = self._load(tid)
        # 设置所有者和状态
        task["owner"] = owner
        task["status"] = "in_progress"
        # 保存任务
        self._save(task)
        # 返回确认信息
        return f"Claimed task #{tid} for {owner}"
