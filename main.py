#!/usr/bin/env python3
# Harness: all mechanisms combined -- the complete cockpit for the model.
"""
s_full.py - Capstone Teaching Agent

Capstone file that combines the core local mechanisms taught across
`s01-s18` into one runnable agent.

`s19` (MCP / plugin integration) is still taught as a separate chapter,
because external tool connectivity is easier to understand after the local
core is already stable.

Chapter -> Class/Function mapping:
  s01 Agent Loop     -> agent_loop()
  s02 Tool Dispatch  -> TOOL_HANDLERS, normalize_messages()
  s03 TodoWrite      -> TodoManager
  s04 Subagent       -> run_subagent()
  s05 Skill Loading  -> SkillLoader
  s06 Context Compact-> maybe_persist_output(), micro_compact(), auto_compact()
  s07 Permissions    -> PermissionManager
  s08 Hooks          -> HookManager
  s09 Memory         -> MemoryManager
  s10 System Prompt  -> build_system_prompt()
  s11 Error Recovery -> recovery logic inside agent_loop()
  s12 Task System    -> TaskManager
  s13 Background     -> BackgroundManager
  s14 Cron Scheduler -> CronScheduler
  s15 Agent Teams    -> TeammateManager, MessageBus
  s16 Team Protocols -> shutdown_requests, plan_requests dicts
  s17 Autonomous     -> _idle_poll(), scan_unclaimed_tasks()
  s18 Worktree       -> WorktreeManager

REPL commands: /compact /tasks /team /inbox
"""

# 导入必要的库
import json
import os
import uuid
from pathlib import Path

# 导入 Anthropic 客户端和环境变量加载器
from anthropic import Anthropic
from dotenv import load_dotenv

# 导入模块
from src.modules.persisted_output import maybe_persist_output
from src.modules.base_tools import run_bash, run_read, run_write, run_edit
from src.modules.todos import TodoManager
from src.modules.subagent import run_subagent
from src.modules.skills import SkillLoader
from src.modules.compression import auto_compact
from src.modules.task_manager import TaskManager
from src.modules.background import BackgroundManager
from src.modules.messaging import MessageBus
from src.modules.team import TeammateManager
from src.modules.agent_loop import agent_loop

# 加载环境变量，覆盖已有的环境变量
load_dotenv(override=True)
# 如果设置了 ANTHROPIC_BASE_URL，则移除 ANTHROPIC_AUTH_TOKEN
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 工作目录设置
WORKDIR = Path.cwd()
# 创建 Anthropic 客户端实例
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
# 从环境变量获取模型 ID
MODEL = os.environ["MODEL_ID"]

# 技能目录
SKILLS_DIR = WORKDIR / "skills"

# 有效的消息类型
VALID_MSG_TYPES = {"message", "broadcast", "shutdown_request",
                   "shutdown_response", "plan_approval_response"}


# === SECTION: shutdown + plan tracking (s10) ===
# 关闭请求字典，用于跟踪关闭请求状态
shutdown_requests = {}
# 计划请求字典，用于跟踪计划审批状态
plan_requests = {}


# === SECTION: global_instances ===
# 全局待办事项管理器实例
TODO = TodoManager()
# 全局技能加载器实例
SKILLS = SkillLoader(SKILLS_DIR)
# 全局任务管理器实例
TASK_MGR = TaskManager()
# 全局后台任务管理器实例
BG = BackgroundManager()
# 全局消息总线实例
BUS = MessageBus()
# 全局团队成员管理器实例
TEAM = TeammateManager(BUS, TASK_MGR, client, MODEL)

# === SECTION: system_prompt ===
# 系统提示，定义代理的行为和可用工具
SYSTEM = f"""You are a coding agent at {WORKDIR}. Use tools to solve tasks.
Prefer task_create/task_update/task_list for multi-step work. Use TodoWrite for short checklists.
Use task for subagent delegation. Use load_skill for specialized knowledge.
Skills: {SKILLS.descriptions()}"""


# === SECTION: shutdown_protocol (s10) ===
# 处理关闭请求
# 输入: teammate - 团队成员名称
# 输出: 关闭请求发送确认

def handle_shutdown_request(teammate: str) -> str:
    # 生成请求 ID
    req_id = str(uuid.uuid4())[:8]
    # 记录关闭请求
    shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
    # 发送关闭请求消息
    BUS.send("lead", teammate, "Please shut down.", "shutdown_request", {"request_id": req_id})
    # 返回发送确认
    return f"Shutdown request {req_id} sent to '{teammate}'"

# === SECTION: plan_approval (s10) ===
# 处理计划审批
# 输入: request_id - 请求 ID, approve - 是否批准, feedback - 反馈信息
# 输出: 审批结果确认

def handle_plan_review(request_id: str, approve: bool, feedback: str = "") -> str:
    # 获取计划请求
    req = plan_requests.get(request_id)
    if not req: return f"Error: Unknown plan request_id '{request_id}'"
    # 更新请求状态
    req["status"] = "approved" if approve else "rejected"
    # 发送审批响应
    BUS.send("lead", req["from"], feedback, "plan_approval_response",
             {"request_id": request_id, "approve": approve, "feedback": feedback})
    # 返回审批结果
    return f"Plan {req['status']} for '{req['from']}'"


# === SECTION: tool_dispatch (s02) ===
# 工具处理函数映射
TOOL_HANDLERS = {
    "bash":             lambda **kw: run_bash(kw["command"], kw.get("tool_use_id", "")),             # 运行 bash 命令
    "read_file":        lambda **kw: run_read(kw["path"], kw.get("tool_use_id", ""), kw.get("limit")), # 读取文件
    "write_file":       lambda **kw: run_write(kw["path"], kw["content"]),                             # 写入文件
    "edit_file":        lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),           # 编辑文件
    "TodoWrite":        lambda **kw: TODO.update(kw["items"]),                                          # 更新待办事项
    "task":             lambda **kw: run_subagent(client, MODEL, kw["prompt"], kw.get("agent_type", "Explore")),    # 运行子代理
    "load_skill":       lambda **kw: SKILLS.load(kw["name"]),                                           # 加载技能
    "compress":         lambda **kw: "Compressing...",                                                   # 压缩上下文
    "background_run":   lambda **kw: BG.run(kw["command"], kw.get("timeout", 120)),                   # 后台运行命令
    "check_background": lambda **kw: BG.check(kw.get("task_id")),                                        # 检查后台任务
    "task_create":      lambda **kw: TASK_MGR.create(kw["subject"], kw.get("description", "")),       # 创建任务
    "task_get":         lambda **kw: TASK_MGR.get(kw["task_id"]),                                       # 获取任务
    "task_update":      lambda **kw: TASK_MGR.update(kw["task_id"], kw.get("status"), kw.get("add_blocked_by"), kw.get("add_blocks")), # 更新任务
    "task_list":        lambda **kw: TASK_MGR.list_all(),                                                # 列出所有任务
    "spawn_teammate":   lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),              # 生成团队成员
    "list_teammates":   lambda **kw: TEAM.list_all(),                                                   # 列出所有团队成员
    "send_message":     lambda **kw: BUS.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")), # 发送消息
    "read_inbox":       lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2),                      # 读取收件箱
    "broadcast":        lambda **kw: BUS.broadcast("lead", kw["content"], TEAM.member_names()),       # 广播消息
    "shutdown_request": lambda **kw: handle_shutdown_request(kw["teammate"]),                          # 发送关闭请求
    "plan_approval":    lambda **kw: handle_plan_review(kw["request_id"], kw["approve"], kw.get("feedback", "")), # 处理计划审批
    "idle":             lambda **kw: "Lead does not idle.",                                             # 空闲状态（领导不空闲）
    "claim_task":       lambda **kw: TASK_MGR.claim(kw["task_id"], "lead"),                            # 认领任务
}

# 工具定义列表
TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "TodoWrite", "description": "Update task tracking list.",
     "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}, "activeForm": {"type": "string"}}, "required": ["content", "status", "activeForm"]}}}, "required": ["items"]}},
    {"name": "task", "description": "Spawn a subagent for isolated exploration or work.",
     "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}, "agent_type": {"type": "string", "enum": ["Explore", "general-purpose"]}}, "required": ["prompt"]}},
    {"name": "load_skill", "description": "Load specialized knowledge by name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "compress", "description": "Manually compress conversation context.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "background_run", "description": "Run command in background thread.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]}},
    {"name": "check_background", "description": "Check background task status.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}}},
    {"name": "task_create", "description": "Create a persistent file task.",
     "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}}, "required": ["subject"]}},
    {"name": "task_get", "description": "Get task details by ID.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
    {"name": "task_update", "description": "Update task status or dependencies.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]}, "add_blocked_by": {"type": "array", "items": {"type": "integer"}}, "add_blocks": {"type": "array", "items": {"type": "integer"}}}, "required": ["task_id"]}},
    {"name": "task_list", "description": "List all tasks.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "spawn_teammate", "description": "Spawn a persistent autonomous teammate.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates", "description": "List all teammates.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_message", "description": "Send a message to a teammate.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}}, "required": ["to", "content"]}},
    {"name": "read_inbox", "description": "Read and drain the lead's inbox.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "broadcast", "description": "Send message to all teammates.",
     "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "shutdown_request", "description": "Request a teammate to shut down.",
     "input_schema": {"type": "object", "properties": {"teammate": {"type": "string"}}, "required": ["teammate"]}},
    {"name": "plan_approval", "description": "Approve or reject a teammate's plan.",
     "input_schema": {"type": "object", "properties": {"request_id": {"type": "string"}, "approve": {"type": "boolean"}, "feedback": {"type": "string"}}, "required": ["request_id", "approve"]}},
    {"name": "idle", "description": "Enter idle state.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "claim_task", "description": "Claim a task from the board.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
]


# === SECTION: repl ===
# 主程序入口
if __name__ == "__main__":
    # 初始化历史消息列表
    history = []
    while True:
        try:
            # 读取用户输入
            query = input("\033[36ms_full >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            # 处理 EOF 或键盘中断
            break
        # 处理退出命令
        if query.strip().lower() in ("q", "exit", ""):
            break
        # 处理 /compact 命令：手动压缩上下文
        if query.strip() == "/compact":
            if history:
                print("[manual compact via /compact]")
                history[:] = auto_compact(client, MODEL, history)
            continue
        # 处理 /tasks 命令：列出所有任务
        if query.strip() == "/tasks":
            print(TASK_MGR.list_all())
            continue
        # 处理 /team 命令：列出所有团队成员
        if query.strip() == "/team":
            print(TEAM.list_all())
            continue
        # 处理 /inbox 命令：读取领导收件箱
        if query.strip() == "/inbox":
            print(json.dumps(BUS.read_inbox("lead"), indent=2))
            continue
        # 添加用户输入到历史消息
        history.append({"role": "user", "content": query})
        # 运行代理循环
        agent_loop(client, MODEL, history, TODO, BG, BUS, TOOL_HANDLERS, TOOLS, SYSTEM)
        # 打印空行
        print()
