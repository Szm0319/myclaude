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
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from queue import Queue

# 导入 Anthropic 客户端和环境变量加载器
from anthropic import Anthropic
from dotenv import load_dotenv

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

# 团队相关目录
TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"
# 任务目录
TASKS_DIR = WORKDIR / ".tasks"
# 技能目录
SKILLS_DIR = WORKDIR / "skills"
# 对话转录目录
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
# 令牌阈值，超过此值会触发自动压缩
TOKEN_THRESHOLD = 100000
# 轮询间隔（秒）
POLL_INTERVAL = 5
# 空闲超时（秒）
IDLE_TIMEOUT = 60

# 持久化输出相关配置
# 大型工具输出会被写入磁盘，并用预览标记替换
TASK_OUTPUT_DIR = WORKDIR / ".task_outputs"
TOOL_RESULTS_DIR = TASK_OUTPUT_DIR / "tool-results"
# 默认触发持久化的字符数
PERSIST_OUTPUT_TRIGGER_CHARS_DEFAULT = 50000
# Bash 命令输出触发持久化的字符数
PERSIST_OUTPUT_TRIGGER_CHARS_BASH = 30000
# 上下文截断字符数
CONTEXT_TRUNCATE_CHARS = 50000
# 持久化输出标记
PERSISTED_OPEN = "<persisted-output>"
PERSISTED_CLOSE = "</persisted-output>"
# 预览字符数
PERSISTED_PREVIEW_CHARS = 2000
# 保留最近的工具结果数量
KEEP_RECENT = 3
# 需要保留结果的工具列表
PRESERVE_RESULT_TOOLS = {"read_file"}

# 有效的消息类型
VALID_MSG_TYPES = {"message", "broadcast", "shutdown_request",
                   "shutdown_response", "plan_approval_response"}


# === SECTION: persisted_output (s06) ===
# 持久化工具结果到文件
# 输入: tool_use_id - 工具使用 ID, content - 工具输出内容
# 输出: 存储文件的相对路径

def _persist_tool_result(tool_use_id: str, content: str) -> Path:
    # 创建工具结果目录（如果不存在）
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    # 生成安全的文件名
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", tool_use_id or "unknown")
    # 构建文件路径
    path = TOOL_RESULTS_DIR / f"{safe_id}.txt"
    # 如果文件不存在，写入内容
    if not path.exists():
        path.write_text(content)
    # 返回相对于工作目录的路径
    return path.relative_to(WORKDIR)

# 格式化文件大小
# 输入: size - 字节数
# 输出: 格式化后的大小字符串

def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"

# 生成文本预览
# 输入: text - 原始文本, limit - 预览字符数
# 输出: (预览文本, 是否有更多内容)

def _preview_slice(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    # 尝试在预览限制内找到最后一个换行符
    idx = text[:limit].rfind("\n")
    # 如果找到合适的换行符，就截断到那里，否则直接截断到限制
    cut = idx if idx > (limit * 0.5) else limit
    return text[:cut], True

# 构建持久化标记
# 输入: stored_path - 存储路径, content - 原始内容
# 输出: 包含预览和存储信息的标记字符串

def _build_persisted_marker(stored_path: Path, content: str) -> str:
    # 生成预览和判断是否有更多内容
    preview, has_more = _preview_slice(content, PERSISTED_PREVIEW_CHARS)
    # 构建标记内容
    marker = (
        f"{PERSISTED_OPEN}\n"
        f"Output too large ({_format_size(len(content))}). "
        f"Full output saved to: {stored_path}\n\n"
        f"Preview (first {_format_size(PERSISTED_PREVIEW_CHARS)}):\n"
        f"{preview}"
    )
    # 如果有更多内容，添加省略号
    if has_more:
        marker += "\n..."
    # 闭合标记
    marker += f"\n{PERSISTED_CLOSE}"
    return marker

# 可能持久化输出
# 输入: tool_use_id - 工具使用 ID, output - 输出内容, trigger_chars - 触发持久化的字符数
# 输出: 原始输出或持久化标记

def maybe_persist_output(tool_use_id: str, output: str, trigger_chars: int = None) -> str:
    # 如果输出不是字符串，转换为字符串
    if not isinstance(output, str):
        return str(output)
    # 确定触发持久化的字符数
    trigger = PERSIST_OUTPUT_TRIGGER_CHARS_DEFAULT if trigger_chars is None else int(trigger_chars)
    # 如果输出长度未超过触发阈值，直接返回
    if len(output) <= trigger:
        return output
    # 持久化输出并返回标记
    stored_path = _persist_tool_result(tool_use_id, output)
    return _build_persisted_marker(stored_path, output)


# === SECTION: base_tools ===
# 安全路径处理
# 输入: p - 路径字符串
# 输出: 解析后的安全路径对象

def safe_path(p: str) -> Path:
    # 解析路径并确保其在工作目录内
    path = (WORKDIR / p).resolve()
    # 检查路径是否在工作目录内，防止路径遍历攻击
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

# 运行 bash 命令
# 输入: command - 要执行的命令, tool_use_id - 工具使用 ID
# 输出: 命令执行结果

def run_bash(command: str, tool_use_id: str = "") -> str:
    # 危险命令列表，禁止执行
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    # 检查命令是否包含危险操作
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        # 执行命令，捕获输出
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120)
        # 合并标准输出和标准错误
        out = (r.stdout + r.stderr).strip()
        # 如果没有输出，返回提示
        if not out:
            return "(no output)"
        # 可能持久化输出
        out = maybe_persist_output(tool_use_id, out, trigger_chars=PERSIST_OUTPUT_TRIGGER_CHARS_BASH)
        # 截断输出到上下文限制
        return out[:CONTEXT_TRUNCATE_CHARS] if isinstance(out, str) else str(out)[:CONTEXT_TRUNCATE_CHARS]
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

# 读取文件内容
# 输入: path - 文件路径, tool_use_id - 工具使用 ID, limit - 行数限制
# 输出: 文件内容

def run_read(path: str, tool_use_id: str = "", limit: int = None) -> str:
    try:
        # 安全解析路径并读取文件内容
        lines = safe_path(path).read_text().splitlines()
        # 如果设置了行数限制，并且文件行数超过限制
        if limit and limit < len(lines):
            # 截断到限制行数，并添加省略信息
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        # 重新组合行
        out = "\n".join(lines)
        # 可能持久化输出
        out = maybe_persist_output(tool_use_id, out)
        # 截断输出到上下文限制
        return out[:CONTEXT_TRUNCATE_CHARS] if isinstance(out, str) else str(out)[:CONTEXT_TRUNCATE_CHARS]
    except Exception as e:
        return f"Error: {e}"

# 写入文件
# 输入: path - 文件路径, content - 要写入的内容
# 输出: 操作结果

def run_write(path: str, content: str) -> str:
    try:
        # 安全解析路径
        fp = safe_path(path)
        # 创建父目录（如果不存在）
        fp.parent.mkdir(parents=True, exist_ok=True)
        # 写入内容
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

# 编辑文件
# 输入: path - 文件路径, old_text - 要替换的旧文本, new_text - 新文本
# 输出: 操作结果

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        # 安全解析路径
        fp = safe_path(path)
        # 读取文件内容
        c = fp.read_text()
        # 检查旧文本是否存在
        if old_text not in c:
            return f"Error: Text not found in {path}"
        # 替换文本（只替换第一次出现）
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# === SECTION: todos (s03) ===
# 待办事项管理器
class TodoManager:
    def __init__(self):
        # 初始化待办事项列表
        self.items = []

    # 更新待办事项列表
    # 输入: items - 待办事项列表
    # 输出: 渲染后的待办事项字符串
    def update(self, items: list) -> str:
        validated, ip = [], 0
        # 验证每个待办事项
        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            af = str(item.get("activeForm", "")).strip()
            # 验证内容
            if not content: raise ValueError(f"Item {i}: content required")
            # 验证状态
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")
            # 验证活动表单
            if not af: raise ValueError(f"Item {i}: activeForm required")
            # 统计进行中的任务数
            if status == "in_progress": ip += 1
            # 添加到验证列表
            validated.append({"content": content, "status": status, "activeForm": af})
        # 检查待办事项数量限制
        if len(validated) > 20: raise ValueError("Max 20 todos")
        # 检查进行中任务数量限制
        if ip > 1: raise ValueError("Only one in_progress allowed")
        # 更新待办事项列表
        self.items = validated
        # 渲染并返回
        return self.render()

    # 渲染待办事项列表
    # 输出: 格式化的待办事项字符串
    def render(self) -> str:
        if not self.items: return "No todos."
        lines = []
        # 遍历每个待办事项
        for item in self.items:
            # 根据状态生成标记
            m = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(item["status"], "[?]")
            # 为进行中的任务添加活动表单标记
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            # 添加到行列表
            lines.append(f"{m} {item['content']}{suffix}")
        # 计算已完成的任务数
        done = sum(1 for t in self.items if t["status"] == "completed")
        # 添加完成情况
        lines.append(f"\n({done}/{len(self.items)} completed)")
        # 连接并返回
        return "\n".join(lines)

    # 检查是否有未完成的任务
    # 输出: 布尔值，表示是否有未完成的任务
    def has_open_items(self) -> bool:
        return any(item.get("status") != "completed" for item in self.items)


# === SECTION: subagent (s04) ===
# 运行子代理
# 输入: prompt - 子代理的提示, agent_type - 代理类型（默认为 "Explore"）
# 输出: 子代理的执行结果

def run_subagent(prompt: str, agent_type: str = "Explore") -> str:
    # 基础工具列表
    sub_tools = [
        {"name": "bash", "description": "Run command.",
         "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
        {"name": "read_file", "description": "Read file.",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    ]
    # 如果不是 Explore 类型，添加写入和编辑文件的工具
    if agent_type != "Explore":
        sub_tools += [
            {"name": "write_file", "description": "Write file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Edit file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
        ]
    # 工具处理函数映射
    sub_handlers = {
        "bash": lambda **kw: run_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"]),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    }
    # 初始化消息列表
    sub_msgs = [{"role": "user", "content": prompt}]
    resp = None
    # 最多执行 30 轮
    for _ in range(30):
        # 调用模型
        resp = client.messages.create(model=MODEL, messages=sub_msgs, tools=sub_tools, max_tokens=8000)
        # 添加模型响应到消息列表
        sub_msgs.append({"role": "assistant", "content": resp.content})
        # 如果不是工具使用，退出循环
        if resp.stop_reason != "tool_use":
            break
        # 处理工具调用
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                # 获取工具处理函数
                h = sub_handlers.get(b.name, lambda **kw: "Unknown tool")
                # 执行工具并添加结果
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": str(h(**b.input))[:50000]})
        # 添加工具结果到消息列表
        sub_msgs.append({"role": "user", "content": results})
    # 如果有响应，返回文本内容
    if resp:
        return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no summary)"
    # 失败情况
    return "(subagent failed)"


# === SECTION: skills (s05) ===
# 技能加载器
class SkillLoader:
    def __init__(self, skills_dir: Path):
        # 初始化技能字典
        self.skills = {}
        # 如果技能目录存在
        if skills_dir.exists():
            # 遍历所有 SKILL.md 文件
            for f in sorted(skills_dir.rglob("SKILL.md")):
                # 读取文件内容
                text = f.read_text()
                # 解析文件头部的元数据
                match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
                meta, body = {}, text
                if match:
                    # 解析元数据
                    for line in match.group(1).strip().splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            meta[k.strip()] = v.strip()
                    # 获取正文
                    body = match.group(2).strip()
                # 技能名称，优先使用元数据中的名称，否则使用父目录名称
                name = meta.get("name", f.parent.name)
                # 存储技能信息
                self.skills[name] = {"meta": meta, "body": body}

    # 获取所有技能的描述
    # 输出: 技能描述列表
    def descriptions(self) -> str:
        if not self.skills: return "(no skills)"
        return "\n".join(f"  - {n}: {s['meta'].get('description', '-')}" for n, s in self.skills.items())

    # 加载指定技能
    # 输入: name - 技能名称
    # 输出: 技能内容或错误信息
    def load(self, name: str) -> str:
        s = self.skills.get(name)
        if not s: return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill name=\"{name}\">\n{s['body']}\n</skill>"


# === SECTION: compression (s06) ===
# 估计消息的令牌数
# 输入: messages - 消息列表
# 输出: 估计的令牌数

def estimate_tokens(messages: list) -> int:
    # 简单估计：将消息序列化为 JSON 后长度除以 4
    return len(json.dumps(messages, default=str)) // 4

# 微压缩：压缩旧的工具结果
# 输入: messages - 消息列表

def microcompact(messages: list):
    # 收集所有工具结果
    tool_results = []
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_results.append(part)
    # 如果工具结果数量不超过保留数量，直接返回
    if len(tool_results) <= KEEP_RECENT:
        return
    # 构建工具 ID 到工具名称的映射
    tool_name_map = {}
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_name_map[block.id] = block.name
    # 压缩旧的工具结果
    for part in tool_results[:-KEEP_RECENT]:
        # 跳过短内容或非字符串内容
        if not isinstance(part.get("content"), str) or len(part["content"]) <= 100:
            continue
        # 获取工具 ID 和名称
        tool_id = part.get("tool_use_id", "")
        tool_name = tool_name_map.get(tool_id, "unknown")
        # 跳过需要保留结果的工具
        if tool_name in PRESERVE_RESULT_TOOLS:
            continue
        # 压缩为简短描述
        part["content"] = f"[Previous: used {tool_name}]"

# 自动压缩：使用模型总结对话
# 输入: messages - 消息列表, focus - 关注重点
# 输出: 压缩后的消息列表

def auto_compact(messages: list, focus: str = None) -> list:
    # 创建转录目录
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    # 保存转录文件
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    # 准备对话文本（限制长度）
    conv_text = json.dumps(messages, default=str)[:80000]
    # 构建总结提示
    prompt = (
        "Summarize this conversation for continuity. Structure your summary:\n"
        "1) Task overview: core request, success criteria, constraints\n"
        "2) Current state: completed work, files touched, artifacts created\n"
        "3) Key decisions and discoveries: constraints, errors, failed approaches\n"
        "4) Next steps: remaining actions, blockers, priority order\n"
        "5) Context to preserve: user preferences, domain details, commitments\n"
        "Be concise but preserve critical details.\n"
    )
    # 如果有关注重点，添加到提示中
    if focus:
        prompt += f"\nPay special attention to: {focus}\n"
    # 调用模型生成总结
    resp = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt + "\n" + conv_text}],
        max_tokens=4000,
    )
    # 获取总结内容
    summary = resp.content[0].text
    # 构建继续对话的提示
    continuation = (
        "This session is being continued from a previous conversation that ran out "
        "of context. The summary below covers the earlier portion of the conversation.\n\n"
        f"{summary}\n\n"
        "Please continue the conversation from where we left it off without asking "
        "the user any further questions."
    )
    # 返回压缩后的消息列表
    return [
        {"role": "user", "content": continuation},
    ]


# === SECTION: file_tasks (s07) ===
# 任务管理器
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


# === SECTION: background (s08) ===
# 后台任务管理器
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


# === SECTION: messaging (s09) ===
# 消息总线
class MessageBus:
    def __init__(self):
        # 创建收件箱目录（如果不存在）
        INBOX_DIR.mkdir(parents=True, exist_ok=True)

    # 发送消息
    # 输入: sender - 发送者, to - 接收者, content - 消息内容, msg_type - 消息类型, extra - 额外信息
    # 输出: 发送确认
    def send(self, sender: str, to: str, content: str,
             msg_type: str = "message", extra: dict = None) -> str:
        # 创建消息字典
        msg = {"type": msg_type, "from": sender, "content": content,
               "timestamp": time.time()}
        # 添加额外信息
        if extra: msg.update(extra)
        # 写入收件箱文件
        with open(INBOX_DIR / f"{to}.jsonl", "a") as f:
            f.write(json.dumps(msg) + "\n")
        # 返回发送确认
        return f"Sent {msg_type} to {to}"

    # 读取收件箱
    # 输入: name - 收件人名称
    # 输出: 消息列表
    def read_inbox(self, name: str) -> list:
        # 构建收件箱文件路径
        path = INBOX_DIR / f"{name}.jsonl"
        # 如果文件不存在，返回空列表
        if not path.exists(): return []
        # 读取并解析消息
        msgs = [json.loads(l) for l in path.read_text().strip().splitlines() if l]
        # 清空收件箱
        path.write_text("")
        # 返回消息列表
        return msgs

    # 广播消息
    # 输入: sender - 发送者, content - 消息内容, names - 接收者列表
    # 输出: 广播确认
    def broadcast(self, sender: str, content: str, names: list) -> str:
        count = 0
        # 遍历接收者列表
        for n in names:
            # 跳过发送者自己
            if n != sender:
                # 发送广播消息
                self.send(sender, n, content, "broadcast")
                count += 1
        # 返回广播确认
        return f"Broadcast to {count} teammates"


# === SECTION: shutdown + plan tracking (s10) ===
# 关闭请求字典，用于跟踪关闭请求状态
shutdown_requests = {}
# 计划请求字典，用于跟踪计划审批状态
plan_requests = {}


# === SECTION: team (s09/s11) ===
# 团队成员管理器
class TeammateManager:
    def __init__(self, bus: MessageBus, task_mgr: TaskManager):
        # 创建团队目录（如果不存在）
        TEAM_DIR.mkdir(exist_ok=True)
        # 消息总线
        self.bus = bus
        # 任务管理器
        self.task_mgr = task_mgr
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
        threading.Thread(target=self._loop, args=(name, role, prompt), daemon=True).start()
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
    def _loop(self, name: str, role: str, prompt: str):
        team_name = self.config["team_name"]
        # 系统提示
        sys_prompt = (f"You are '{name}', role: {role}, team: {team_name}, at {WORKDIR}. "
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
TEAM = TeammateManager(BUS, TASK_MGR)

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
    "task":             lambda **kw: run_subagent(kw["prompt"], kw.get("agent_type", "Explore")),    # 运行子代理
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


# === SECTION: agent_loop ===
# 代理主循环
# 输入: messages - 消息列表

def agent_loop(messages: list):
    # 记录没有使用待办事项的轮数
    rounds_without_todo = 0
    while True:
        # s06: 压缩管道
        # 微压缩消息
        microcompact(messages)
        # 如果令牌数超过阈值，自动压缩
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            print("[auto-compact triggered]")
            messages[:] = auto_compact(messages)
        # s08: 处理后台通知
        notifs = BG.drain()
        if notifs:
            # 格式化通知内容
            txt = "\n".join(f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs)
            # 添加通知到消息列表
            messages.append({"role": "user", "content": f"<background-results>\n{txt}\n</background-results>"})
            messages.append({"role": "assistant", "content": "Noted background results."})
        # s10: 检查领导收件箱
        inbox = BUS.read_inbox("lead")
        if inbox:
            # 添加收件箱消息到消息列表
            messages.append({"role": "user", "content": f"<inbox>{json.dumps(inbox, indent=2)}</inbox>"})
            messages.append({"role": "assistant", "content": "Noted inbox messages."})
        # 调用 LLM
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        # 添加模型响应到消息列表
        messages.append({"role": "assistant", "content": response.content})
        # 如果不是工具使用，退出循环
        if response.stop_reason != "tool_use":
            return
        # 工具执行
        results = []
        used_todo = False
        manual_compress = False
        compact_focus = None
        for block in response.content:
            if block.type == "tool_use":
                # 处理压缩请求
                if block.name == "compress":
                    manual_compress = True
                    compact_focus = (block.input or {}).get("focus")
                # 获取工具处理函数
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    # 准备工具输入
                    tool_input = dict(block.input or {})
                    tool_input["tool_use_id"] = block.id
                    # 执行工具
                    output = handler(**tool_input) if handler else f"Unknown tool: {block.name}"
                except Exception as e:
                    # 处理异常
                    output = f"Error: {e}"
                # 打印工具执行信息
                print(f"> {block.name}: {str(output)[:200]}")
                # 添加工具结果
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
                # 记录是否使用了待办事项
                if block.name == "TodoWrite":
                    used_todo = True
        # s03: 提醒更新待办事项（仅当待办事项工作流激活时）
        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if TODO.has_open_items() and rounds_without_todo >= 3:
            results.insert(0, {"type": "text", "text": "<reminder>Update your todos.</reminder>"})
        # 添加工具结果到消息列表
        messages.append({"role": "user", "content": results})
        # s06: 手动压缩
        if manual_compress:
            print("[manual compact]")
            messages[:] = auto_compact(messages, focus=compact_focus)


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
                history[:] = auto_compact(history)
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
        agent_loop(history)
        # 打印空行
        print()
