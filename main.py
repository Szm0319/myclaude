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

# Import necessary libraries
import json
import os
import uuid
from pathlib import Path

# Import Anthropic client and environment variable loader
from anthropic import Anthropic
from dotenv import load_dotenv

# Import modules
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

# Load environment variables, override existing ones
load_dotenv(override=True)
# If ANTHROPIC_BASE_URL is set, remove ANTHROPIC_AUTH_TOKEN
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# Working directory setup
WORKDIR = Path.cwd()
# Create Anthropic client instance
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
# Get model ID from environment variables
MODEL = os.environ["MODEL_ID"]

# Skills directory
SKILLS_DIR = WORKDIR / "skills"

# Valid message types
VALID_MSG_TYPES = {"message", "broadcast", "shutdown_request",
                   "shutdown_response", "plan_approval_response"}


# === SECTION: shutdown + plan tracking (s10) ===
# Shutdown request dictionary, for tracking shutdown request status
shutdown_requests = {}
# Plan request dictionary, for tracking plan approval status
plan_requests = {}


# === SECTION: global_instances ===
# Global todo manager instance
TODO = TodoManager()
# Global skill loader instance
SKILLS = SkillLoader(SKILLS_DIR)
# Global task manager instance
TASK_MGR = TaskManager()
# Global background task manager instance
BG = BackgroundManager()
# Global message bus instance
BUS = MessageBus()
# Global teammate manager instance
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
# System prompt, defines agent behavior and available tools
SYSTEM = f"""You are a coding agent at {WORKDIR}. Use tools to solve tasks.
Prefer task_create/task_update/task_list for multi-step work. Use TodoWrite for short checklists.
Use task for subagent delegation. Use load_skill for specialized knowledge.
Skills: {SKILLS.descriptions()}"""


# === SECTION: shutdown_protocol (s10) ===
# Handle shutdown requests
# Input: teammate - team member name
# Output: shutdown request sent confirmation

def handle_shutdown_request(teammate: str) -> str:
    # Generate request ID
    req_id = str(uuid.uuid4())[:8]
    # Record shutdown request
    shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
    # Send shutdown request message
    BUS.send("lead", teammate, "Please shut down.", "shutdown_request", {"request_id": req_id})
    # Return send confirmation
    return f"Shutdown request {req_id} sent to '{teammate}'"

# === SECTION: plan_approval (s10) ===
# Handle plan approvals
# Input: request_id - request ID, approve - whether to approve, feedback - feedback information
# Output: approval result confirmation

def handle_plan_review(request_id: str, approve: bool, feedback: str = "") -> str:
    # Get plan request
    req = plan_requests.get(request_id)
    if not req: return f"Error: Unknown plan request_id '{request_id}'"
    # Update request status
    req["status"] = "approved" if approve else "rejected"
    # Send approval response
    BUS.send("lead", req["from"], feedback, "plan_approval_response",
             {"request_id": request_id, "approve": approve, "feedback": feedback})
    # Return approval result
    return f"Plan {req['status']} for '{req['from']}'"


# === SECTION: tool_dispatch (s02) ===
# Tool handler function mapping
TOOL_HANDLERS = {
    "bash":             lambda **kw: run_bash(kw["command"], kw.get("tool_use_id", "")),             # Run bash command
    "read_file":        lambda **kw: run_read(kw["path"], kw.get("tool_use_id", ""), kw.get("limit")), # Read file
    "write_file":       lambda **kw: run_write(kw["path"], kw["content"]),                             # Write file
    "edit_file":        lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),           # Edit file
    "TodoWrite":        lambda **kw: TODO.update(kw["items"]),                                          # Update todo list
    "task":             lambda **kw: run_subagent(client, MODEL, kw["prompt"], kw.get("agent_type", "Explore")),    # Run subagent
    "load_skill":       lambda **kw: SKILLS.load(kw["name"]),                                           # Load skill
    "compress":         lambda **kw: "Compressing...",                                                   # Compress context
    "background_run":   lambda **kw: BG.run(kw["command"], kw.get("timeout", 120)),                   # Run command in background
    "check_background": lambda **kw: BG.check(kw.get("task_id")),                                        # Check background task
    "task_create":      lambda **kw: TASK_MGR.create(kw["subject"], kw.get("description", "")),       # Create task
    "task_get":         lambda **kw: TASK_MGR.get(kw["task_id"]),                                       # Get task
    "task_update":      lambda **kw: TASK_MGR.update(kw["task_id"], kw.get("status"), kw.get("add_blocked_by"), kw.get("add_blocks")), # Update task
    "task_list":        lambda **kw: TASK_MGR.list_all(),                                                # List all tasks
    "spawn_teammate":   lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),              # Spawn teammate
    "list_teammates":   lambda **kw: TEAM.list_all(),                                                   # List all teammates
    "send_message":     lambda **kw: BUS.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")), # Send message
    "read_inbox":       lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2),                      # Read inbox
    "broadcast":        lambda **kw: BUS.broadcast("lead", kw["content"], TEAM.member_names()),       # Broadcast message
    "shutdown_request": lambda **kw: handle_shutdown_request(kw["teammate"]),                          # Send shutdown request
    "plan_approval":    lambda **kw: handle_plan_review(kw["request_id"], kw["approve"], kw.get("feedback", "")), # Handle plan approval
    "idle":             lambda **kw: "Lead does not idle.",                                             # Idle state (lead does not idle)
    "claim_task":       lambda **kw: TASK_MGR.claim(kw["task_id"], "lead"),                            # Claim task
    
    # s18: Worktree Task Isolation tools
    "worktree_task_create":      lambda **kw: TASKS.create(kw["subject"], kw.get("description", "")),          # Create worktree task
    "worktree_task_list":        lambda **kw: TASKS.list_all(),                                                   # List all worktree tasks
    "worktree_task_get":         lambda **kw: TASKS.get(kw["task_id"]),                                          # Get worktree task
    "worktree_task_update":      lambda **kw: TASKS.update(kw["task_id"], kw.get("status"), kw.get("owner")),  # Update worktree task
    "worktree_task_bind":        lambda **kw: TASKS.bind_worktree(kw["task_id"], kw["worktree"], kw.get("owner", "")), # Bind worktree
    "worktree_create":           lambda **kw: WORKTREES.create(kw["name"], kw.get("task_id"), kw.get("base_ref", "HEAD")), # Create worktree
    "worktree_list":             lambda **kw: WORKTREES.list_all(),                                               # List all worktrees
    "worktree_enter":            lambda **kw: WORKTREES.enter(kw["name"]),                                      # Enter worktree
    "worktree_status":           lambda **kw: WORKTREES.status(kw["name"]),                                     # Check worktree status
    "worktree_run":              lambda **kw: WORKTREES.run(kw["name"], kw["command"]),                         # Run command in worktree
    "worktree_closeout":         lambda **kw: WORKTREES.closeout(kw["name"], kw["action"], kw.get("reason", ""), kw.get("force", False), kw.get("complete_task", False)), # Close out worktree
    "worktree_remove":           lambda **kw: WORKTREES.remove(kw["name"], kw.get("force", False), kw.get("complete_task", False), kw.get("reason", "")), # Remove worktree
    "worktree_keep":             lambda **kw: WORKTREES.keep(kw["name"]),                                        # Keep worktree
    "worktree_events":           lambda **kw: EVENTS.list_recent(kw.get("limit", 20)),                             # List worktree events
    
    # s19: MCP Plugin System tools
    "mcp_call":                  lambda **kw: mcp_router.call(kw["tool_name"], kw["arguments"]),                 # Call MCP tool
}

# Tool definition list
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
# Main program entry point
if __name__ == "__main__":
    # s19: Scan for plugins and connect to MCP servers
    found_plugins = plugin_loader.scan()
    if found_plugins:
        print(f"[Plugins loaded: {', '.join(found_plugins)}]")
        for server_name, config in plugin_loader.get_mcp_servers().items():
            mcp_client = MCPClient(server_name, config.get("command", ""), config.get("args", []))
            if mcp_client.connect():
                mcp_client.list_tools()
                mcp_router.register_client(mcp_client)
                print(f"[MCP] Connected to {server_name}")
    
    # Initialize history message list
    history = []
    while True:
        try:
            # Read user input
            query = input("\033[36ms_full >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            # Handle EOF or keyboard interrupt
            break
        # Handle exit command
        if query.strip().lower() in ("q", "exit", ""):
            break
        # Handle /compact command: manually compress context
        if query.strip() == "/compact":
            if history:
                print("[manual compact via /compact]")
                history[:] = auto_compact(client, MODEL, history)
            continue
        # Handle /tasks command: list all tasks
        if query.strip() == "/tasks":
            print(TASK_MGR.list_all())
            continue
        # Handle /team command: list all teammates
        if query.strip() == "/team":
            print(TEAM.list_all())
            continue
        # Handle /inbox command: read lead's inbox
        if query.strip() == "/inbox":
            print(json.dumps(BUS.read_inbox("lead"), indent=2))
            continue
        # Handle /worktree command: list all worktrees
        if query.strip() == "/worktree":
            print(WORKTREES.list_all())
            continue
        # Handle /mcp command: list all MCP servers and tools
        if query.strip() == "/mcp":
            if mcp_router.clients:
                for name, c in mcp_router.clients.items():
                    tools = c.get_agent_tools()
                    print(f"  {name}: {len(tools)} tools")
            else:
                print("  (no MCP servers connected)")
            continue
        # Add user input to history messages
        history.append({"role": "user", "content": query})
        # Run agent loop
        agent_loop(client, MODEL, history, TODO, BG, BUS, TOOL_HANDLERS, TOOLS, SYSTEM)
        # Print empty line
        print()
    
    # Clean up MCP connections
    mcp_router.disconnect_all()
