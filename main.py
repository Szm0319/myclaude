#!/usr/bin/env python3
# Harness: all mechanisms combined -- the complete cockpit for the model.
"""
main.py - 综合智能代理系统

该文件是一个综合智能代理系统，整合了s01-s19的核心机制，包括：
- 代理循环
- 工具调度
- 待办事项管理
- 子代理运行
- 技能加载
- 上下文压缩
- 权限管理
- 钩子系统
- 内存管理
- 系统提示
- 错误恢复
- 任务系统
- 后台任务
- 定时调度
- 代理团队
- 团队协议
- 自主运行
- 工作树隔离
- MCP插件系统

章节 -> 类/函数映射：
  s01 代理循环     -> agent_loop()
  s02 工具调度     -> TOOL_HANDLERS
  s03 待办事项     -> TodoManager
  s04 子代理       -> run_subagent()
  s05 技能加载     -> SkillLoader
  s06 上下文压缩   -> auto_compact()
  s07 权限管理     -> CapabilityPermissionGate
  s08 钩子系统     -> 集成在各个模块中
  s09 内存管理     -> 集成在各个模块中
  s10 系统提示     -> SYSTEM 变量
  s11 错误恢复     -> agent_loop() 中的恢复逻辑
  s12 任务系统     -> TaskManager
  s13 后台任务     -> BackgroundManager
  s14 定时调度     -> 集成在后台任务中
  s15 代理团队     -> TeammateManager, MessageBus
  s16 团队协议     -> shutdown_requests, plan_requests 字典
  s17 自主运行     -> 集成在团队模块中
  s18 工作树隔离   -> WorktreeManager
  s19 MCP插件系统  -> MCPToolRouter

REPL 命令：/compact /tasks /team /inbox /worktree /mcp
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
from src.modules.worktree_task_isolation import EventBus, TaskManager as WorktreeTaskManager, WorktreeManager, detect_repo_root, get_worktree_tools
from src.modules.mcp_plugin import CapabilityPermissionGate, MCPClient, PluginLoader, MCPToolRouter, normalize_tool_result, get_mcp_tools

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

# s18: Worktree Task Isolation
REPO_ROOT = detect_repo_root(WORKDIR) or WORKDIR
TASKS = WorktreeTaskManager(REPO_ROOT / ".tasks")
EVENTS = EventBus(REPO_ROOT / ".worktrees" / "events.jsonl")
WORKTREES = WorktreeManager(REPO_ROOT, TASKS, EVENTS)

# s19: MCP Plugin System
permission_gate = CapabilityPermissionGate()
mcp_router = MCPToolRouter()
plugin_loader = PluginLoader()

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
    
    # s18: Worktree Task Isolation tools
    "worktree_task_create":      lambda **kw: TASKS.create(kw["subject"], kw.get("description", "")),          # 创建工作树任务
    "worktree_task_list":        lambda **kw: TASKS.list_all(),                                                   # 列出所有工作树任务
    "worktree_task_get":         lambda **kw: TASKS.get(kw["task_id"]),                                          # 获取工作树任务
    "worktree_task_update":      lambda **kw: TASKS.update(kw["task_id"], kw.get("status"), kw.get("owner")),  # 更新工作树任务
    "worktree_task_bind":        lambda **kw: TASKS.bind_worktree(kw["task_id"], kw["worktree"], kw.get("owner", "")), # 绑定工作树
    "worktree_create":           lambda **kw: WORKTREES.create(kw["name"], kw.get("task_id"), kw.get("base_ref", "HEAD")), # 创建工作树
    "worktree_list":             lambda **kw: WORKTREES.list_all(),                                               # 列出所有工作树
    "worktree_enter":            lambda **kw: WORKTREES.enter(kw["name"]),                                      # 进入工作树
    "worktree_status":           lambda **kw: WORKTREES.status(kw["name"]),                                     # 检查工作树状态
    "worktree_run":              lambda **kw: WORKTREES.run(kw["name"], kw["command"]),                         # 在工作树中运行命令
    "worktree_closeout":         lambda **kw: WORKTREES.closeout(kw["name"], kw["action"], kw.get("reason", ""), kw.get("force", False), kw.get("complete_task", False)), # 关闭工作树
    "worktree_remove":           lambda **kw: WORKTREES.remove(kw["name"], kw.get("force", False), kw.get("complete_task", False), kw.get("reason", "")), # 删除工作树
    "worktree_keep":             lambda **kw: WORKTREES.keep(kw["name"]),                                        # 保留工作树
    "worktree_events":           lambda **kw: EVENTS.list_recent(kw.get("limit", 20)),                             # 列出工作树事件
    
    # s19: MCP Plugin System tools
    "mcp_call":                  lambda **kw: mcp_router.call(kw["tool_name"], kw["arguments"]),                 # 调用MCP工具
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
    
    # s18: Worktree Task Isolation tools
    {"name": "worktree_task_create", "description": "Create a new task for worktree isolation.",
     "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}}, "required": ["subject"]}},
    {"name": "worktree_task_list", "description": "List all worktree tasks with status and binding.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "worktree_task_get", "description": "Get worktree task details by ID.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
    {"name": "worktree_task_update", "description": "Update worktree task status or owner.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]}, "owner": {"type": "string"}}, "required": ["task_id"]}},
    {"name": "worktree_task_bind", "description": "Bind a worktree task to a worktree name.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}, "worktree": {"type": "string"}, "owner": {"type": "string"}}, "required": ["task_id", "worktree"]}},
    {"name": "worktree_create", "description": "Create a git worktree for isolated execution.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "task_id": {"type": "integer"}, "base_ref": {"type": "string"}}, "required": ["name"]}},
    {"name": "worktree_list", "description": "List all worktrees tracked in .worktrees/index.json.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "worktree_enter", "description": "Enter or reopen a worktree lane.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "worktree_status", "description": "Show git status for one worktree.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "worktree_run", "description": "Run a shell command in a named worktree directory.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "command": {"type": "string"}}, "required": ["name", "command"]}},
    {"name": "worktree_closeout", "description": "Close out a worktree by keeping or removing it.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "action": {"type": "string", "enum": ["keep", "remove"]}, "reason": {"type": "string"}, "force": {"type": "boolean"}, "complete_task": {"type": "boolean"}}, "required": ["name", "action"]}},
    {"name": "worktree_remove", "description": "Remove a worktree and optionally mark its task completed.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "force": {"type": "boolean"}, "complete_task": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["name"]}},
    {"name": "worktree_keep", "description": "Mark a worktree as kept without removing it.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "worktree_events", "description": "List recent worktree lifecycle events.",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    
    # s19: MCP Plugin System tools
    {"name": "mcp_call", "description": "Call an MCP tool from an external server.",
     "input_schema": {"type": "object", "properties": {"tool_name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["tool_name", "arguments"]}},
]


# === SECTION: repl ===
# 主程序入口
if __name__ == "__main__":
    # s19: 扫描插件并连接MCP服务器
    found_plugins = plugin_loader.scan()
    if found_plugins:
        print(f"[Plugins loaded: {', '.join(found_plugins)}]")
        for server_name, config in plugin_loader.get_mcp_servers().items():
            mcp_client = MCPClient(server_name, config.get("command", ""), config.get("args", []))
            if mcp_client.connect():
                mcp_client.list_tools()
                mcp_router.register_client(mcp_client)
                print(f"[MCP] Connected to {server_name}")
    
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
        # 处理 /worktree 命令：列出所有工作树
        if query.strip() == "/worktree":
            print(WORKTREES.list_all())
            continue
        # 处理 /mcp 命令：列出所有MCP服务器和工具
        if query.strip() == "/mcp":
            if mcp_router.clients:
                for name, c in mcp_router.clients.items():
                    tools = c.get_agent_tools()
                    print(f"  {name}: {len(tools)} tools")
            else:
                print("  (no MCP servers connected)")
            continue
        # 添加用户输入到历史消息
        history.append({"role": "user", "content": query})
        # 运行代理循环
        agent_loop(client, MODEL, history, TODO, BG, BUS, TOOL_HANDLERS, TOOLS, SYSTEM)
        # 打印空行
        print()
    
    # 清理MCP连接
    mcp_router.disconnect_all()
