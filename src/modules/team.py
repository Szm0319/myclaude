# === SECTION: team (s09/s11) ===
# 团队成员管理器
import json
import threading
import time
from pathlib import Path
from src.modules.base_tools import run_bash, run_read, run_write, run_edit
from src.modules.task_manager import TaskManager
from src.modules.messaging import MessageBus

# 团队相关目录
TEAM_DIR = Path.cwd() / ".team"
# 任务目录
TASKS_DIR = Path.cwd() / ".tasks"
# 轮询间隔（秒）
POLL_INTERVAL = 5
# 空闲超时（秒）
IDLE_TIMEOUT = 60


class TeammateManager:
    def __init__(self, bus: MessageBus, task_mgr: TaskManager, client, MODEL):
        # 创建团队目录（如果不存在）
        TEAM_DIR.mkdir(exist_ok=True)
        # 消息总线
        self.bus = bus
        # 任务管理器
        self.task_mgr = task_mgr
        # 客户端和模型
        self.client = client
        self.MODEL = MODEL
        # 配置文件路径
        self.config_path = TEAM_DIR / "config.json"
        # 加载配置
        self.config = self._load()
        # 线程字典
        self.threads = {}

    # 加载配置
    # 输出: 配置字典
    def _load(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        # 默认配置
        return {"team_name": "default", "members": []}

    # 保存配置
    def _save(self):
        self.config_path.write_text(json.dumps(self.config, indent=2))

    # 查找成员
    # 输入: name - 成员名称
    # 输出: 成员字典或 None
    def _find(self, name: str) -> dict:
        for m in self.config["members"]:
            if m["name"] == name: return m
        return None

    # 生成团队成员
    # 输入: name - 成员名称, role - 成员角色, prompt - 初始提示
    # 输出: 生成确认
    def spawn(self, name: str, role: str, prompt: str) -> str:
        # 查找成员
        member = self._find(name)
        if member:
            # 检查成员状态
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' is currently {member['status']}"
            # 更新状态和角色
            member["status"] = "working"
            member["role"] = role
        else:
            # 创建新成员
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)
        # 保存配置
        self._save()
        # 启动成员线程
        threading.Thread(target=self._loop, args=(name, role, prompt, self.client, self.MODEL), daemon=True).start()
        # 返回生成确认
        return f"Spawned '{name}' (role: {role})"

    # 设置成员状态
    # 输入: name - 成员名称, status - 状态
    def _set_status(self, name: str, status: str):
        member = self._find(name)
        if member:
            member["status"] = status
            self._save()

    # 成员主循环
    # 输入: name - 成员名称, role - 成员角色, prompt - 初始提示
    def _loop(self, name: str, role: str, prompt: str, client, MODEL):
        team_name = self.config["team_name"]
        # 系统提示
        sys_prompt = (f"You are '{name}', role: {role}, team: {team_name}, at {Path.cwd()}. "
                      f"Use idle when done with current work. You may auto-claim tasks.")
        # 初始化消息列表
        messages = [{"role": "user", "content": prompt}]
        # 工具列表
        tools = [
            {"name": "bash", "description": "Run command.", "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
            {"name": "read_file", "description": "Read file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
            {"name": "write_file", "description": "Write file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Edit file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
            {"name": "send_message", "description": "Send message.", "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}}, "required": ["to", "content"]}},
            {"name": "idle", "description": "Signal no more work.", "input_schema": {"type": "object", "properties": {}}},
            {"name": "claim_task", "description": "Claim task by ID.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
        ]
        while True:
            # -- 工作阶段 --
            for _ in range(50):
                # 读取收件箱
                inbox = self.bus.read_inbox(name)
                for msg in inbox:
                    # 处理关闭请求
                    if msg.get("type") == "shutdown_request":
                        self._set_status(name, "shutdown")
                        return
                    # 添加消息到消息列表
                    messages.append({"role": "user", "content": json.dumps(msg)})
                try:
                    # 调用模型
                    response = client.messages.create(
                        model=MODEL, system=sys_prompt, messages=messages,
                        tools=tools, max_tokens=8000)
                except Exception:
                    # 出错时关闭
                    self._set_status(name, "shutdown")
                    return
                # 添加模型响应到消息列表
                messages.append({"role": "assistant", "content": response.content})
                # 如果不是工具使用，退出循环
                if response.stop_reason != "tool_use":
                    break
                # 处理工具调用
                results = []
                idle_requested = False
                for block in response.content:
                    if block.type == "tool_use":
                        if block.name == "idle":
                            # 处理空闲请求
                            idle_requested = True
                            output = "Entering idle phase."
                        elif block.name == "claim_task":
                            # 处理任务认领
                            output = self.task_mgr.claim(block.input["task_id"], name)
                        elif block.name == "send_message":
                            # 处理消息发送
                            output = self.bus.send(name, block.input["to"], block.input["content"])
                        else:
                            # 处理其他工具
                            dispatch = {"bash": lambda **kw: run_bash(kw["command"]),
                                        "read_file": lambda **kw: run_read(kw["path"]),
                                        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
                                        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"])}
                            output = dispatch.get(block.name, lambda **kw: "Unknown")(**block.input)
                        # 打印工具执行信息
                        print(f"  [{name}] {block.name}: {str(output)[:120]}")
                        # 添加工具结果
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
                # 添加工具结果到消息列表
                messages.append({"role": "user", "content": results})
                # 如果请求空闲，退出循环
                if idle_requested:
                    break
            # -- 空闲阶段：轮询消息和未认领任务 --
            self._set_status(name, "idle")
            resume = False
            # 轮询 IDLE_TIMEOUT 秒
            for _ in range(IDLE_TIMEOUT // max(POLL_INTERVAL, 1)):
                time.sleep(POLL_INTERVAL)
                # 读取收件箱
                inbox = self.bus.read_inbox(name)
                if inbox:
                    for msg in inbox:
                        # 处理关闭请求
                        if msg.get("type") == "shutdown_request":
                            self._set_status(name, "shutdown")
                            return
                        # 添加消息到消息列表
                        messages.append({"role": "user", "content": json.dumps(msg)})
                    # 有消息，恢复工作
                    resume = True
                    break
                # 查找未认领的任务
                unclaimed = []
                for f in sorted(TASKS_DIR.glob("task_*.json")):
                    t = json.loads(f.read_text())
                    if t.get("status") == "pending" and not t.get("owner") and not t.get("blockedBy"):
                        unclaimed.append(t)
                # 如果有未认领的任务
                if unclaimed:
                    task = unclaimed[0]
                    # 认领任务
                    self.task_mgr.claim(task["id"], name)
                    # 为压缩的上下文重新注入身份信息
                    if len(messages) <= 3:
                        messages.insert(0, {"role": "user", "content":
                            f"<identity>You are '{name}', role: {role}, team: {team_name}.</identity>"})
                        messages.insert(1, {"role": "assistant", "content": f"I am {name}. Continuing."})
                    # 添加自动认领任务信息
                    messages.append({"role": "user", "content":
                        f"<auto-claimed>Task #{task['id']}: {task['subject']}\n{task.get('description', '')}</auto-claimed>"})
                    messages.append({"role": "assistant", "content": f"Claimed task #{task['id']}. Working on it."})
                    # 恢复工作
                    resume = True
                    break
            # 如果没有恢复工作，关闭
            if not resume:
                self._set_status(name, "shutdown")
                return
            # 设置状态为工作中
            self._set_status(name, "working")

    # 列出所有成员
    # 输出: 成员列表字符串
    def list_all(self) -> str:
        if not self.config["members"]: return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    # 获取成员名称列表
    # 输出: 成员名称列表
    def member_names(self) -> list:
        return [m["name"] for m in self.config["members"]]
