# myclaude 代码结构白话总结

## 从内到外看代码结构

### 1. 核心：主循环

**主循环是什么？**
主循环是整个系统的心脏，它不断运行，处理用户输入，调用工具，直到用户结束对话。主循环位于 [agent_loop.py](file:///workspace/src/modules/agent_loop.py) 文件中。

**主循环的工作流程**：
1. **压缩上下文**：如果对话太长（超过 TOKEN_THRESHOLD=100000），自动压缩，保持系统高效
2. **检查后台任务**：看看有没有后台任务完成了，获取通知
3. **检查信箱**：读取领导的信箱（路径：`.team/inbox/lead.jsonl`），看看有没有团队成员发消息
4. **调用 Claude**：把当前情况告诉 Claude，让它决定下一步
5. **执行工具**：如果 Claude 决定用工具，就执行对应的工具函数
6. **等待结果**：工具执行完，把结果告诉 Claude
7. **循环继续**：只有当 Claude 直接回答用户，不调用工具时，才退出当前循环

**主循环的能力**：
- 调用所有工具（读写文件、运行命令、管理任务等）
- 管理待办事项，提醒你更新（如果 3 轮没有更新待办事项）
- 压缩对话，优化性能（使用 microcompact 和 auto_compact 函数）
- 处理后台任务通知（从 BackgroundManager 获取）
- 读取和处理团队消息（从 MessageBus 获取）

**示例**：
```python
# 主循环调用示例
history = [{"role": "user", "content": "帮我创建一个文件"}]
agent_loop(client, MODEL, history, TODO, BG, BUS, TOOL_HANDLERS, TOOLS, SYSTEM)

# 主循环内部流程：
# 1. 压缩上下文
# 2. 检查后台任务 → 无
# 3. 检查信箱 → 无
# 4. 调用 Claude → Claude 决定使用 write_file 工具
# 5. 执行 write_file 工具 → 创建文件
# 6. 等待结果 → 文件创建成功
# 7. 循环继续 → Claude 生成回复，退出循环
```

### 2. 工具层：系统的手脚

**工具是什么？**
工具是系统的手脚，让 Claude 能够与外部世界交互。工具定义和处理函数位于 [main.py](file:///workspace/main.py) 文件中的 `TOOLS` 列表和 `TOOL_HANDLERS` 字典。

**主要工具**：
- **文件工具**：读文件、写文件、编辑文件（实现：[base_tools.py](file:///workspace/src/modules/base_tools.py)）
- **命令工具**：运行 bash 命令（实现：[base_tools.py](file:///workspace/src/modules/base_tools.py)）
- **任务工具**：创建任务、更新任务、列出任务（实现：[task_manager.py](file:///workspace/src/modules/task_manager.py)）
- **团队工具**：生成团队成员、发送消息、广播消息（实现：[team.py](file:///workspace/src/modules/team.py) 和 [messaging.py](file:///workspace/src/modules/messaging.py)）
- **后台工具**：后台运行命令、检查后台任务（实现：[background.py](file:///workspace/src/modules/background.py)）
- **待办工具**：更新待办事项列表（实现：[todos.py](file:///workspace/src/modules/todos.py)）
- **子代理工具**：生成子代理执行特定任务（实现：[subagent.py](file:///workspace/src/modules/subagent.py)）
- **技能工具**：加载专业技能（实现：[skills.py](file:///workspace/src/modules/skills.py)）

**工具的工作方式**：
1. Claude 决定使用哪个工具
2. 系统调用 `TOOL_HANDLERS` 中对应的工具处理函数
3. 工具执行操作（比如读文件、运行命令）
4. 工具返回结果给系统
5. 系统把结果告诉 Claude

**示例**：
```python
# 工具处理函数示例（来自 TOOL_HANDLERS 字典）
TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"], kw.get("tool_use_id", "")),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("tool_use_id", ""), kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "TodoWrite": lambda **kw: TODO.update(kw["items"]),
    "task": lambda **kw: run_subagent(client, MODEL, kw["prompt"], kw.get("agent_type", "Explore")),
    "spawn_teammate": lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),
    # 更多工具...
}

# 工具使用示例
# Claude 生成工具调用：
# {
#   "type": "tool_use",
#   "id": "tool_1",
#   "name": "write_file",
#   "input": {
#     "path": "hello.py",
#     "content": "print('Hello, World!')"
#   }
# }

# 系统执行工具并返回结果：
# {
#   "type": "tool_result",
#   "tool_use_id": "tool_1",
#   "content": "Wrote 21 bytes to hello.py"
# }
```

### 3. 管理层：系统的大脑

**管理层有哪些？**
管理层是系统的大脑，负责管理各种资源和状态。

**主要管理器**：
- **任务管理器**：管理任务的创建、更新、状态变化（实现：[task_manager.py](file:///workspace/src/modules/task_manager.py)）
- **团队管理器**：管理团队成员的生成、状态、通信（实现：[team.py](file:///workspace/src/modules/team.py)）
- **后台管理器**：管理后台任务的运行和结果（实现：[background.py](file:///workspace/src/modules/background.py)）
- **消息总线**：处理团队成员之间的通信（实现：[messaging.py](file:///workspace/src/modules/messaging.py)）
- **待办管理器**：管理待办事项的状态和更新（实现：[todos.py](file:///workspace/src/modules/todos.py)）
- **技能加载器**：加载和管理技能（实现：[skills.py](file:///workspace/src/modules/skills.py)）

**任务的详细字段**：
每个任务文件（路径：`.tasks/task_{id}.json`）包含以下字段：
- `id`：任务唯一标识符
- `subject`：任务主题
- `description`：任务描述
- `status`：任务状态（`pending`/`in_progress`/`completed`/`deleted`）
- `owner`：任务所有者（团队成员名称）
- `blockedBy`：被哪些任务阻塞（任务 ID 列表）
- `blocks`：阻塞哪些任务（任务 ID 列表）

**示例任务文件**：
```json
{
  "id": 1,
  "subject": "创建前端页面",
  "description": "创建一个包含登录和注册功能的前端页面",
  "status": "in_progress",
  "owner": "frontend_dev",
  "blockedBy": [],
  "blocks": [2]
}
```

**信箱的消息示例**：
团队成员的信箱文件（路径：`.team/inbox/{name}.jsonl`）包含以下格式的消息：
```json
{"type": "message", "from": "lead", "content": "请创建一个前端页面", "timestamp": 1620000000}
{"type": "shutdown_request", "from": "lead", "content": "请关闭", "timestamp": 1620000000, "request_id": "abc123"}
```

**如何处理有消息的情况**：
1. **主代理处理消息**：
   - 主循环定期检查领导信箱（`.team/inbox/lead.jsonl`）
   - 读取所有消息并清空信箱
   - 将消息添加到对话历史中
   - Claude 会分析消息并决定如何响应

2. **团队成员处理消息**：
   - 团队成员在工作阶段开始时读取自己的信箱
   - 处理不同类型的消息：
     - `message`：普通消息，添加到对话历史
     - `shutdown_request`：关闭请求，处理后关闭
     - `plan_approval_response`：计划审批响应，处理审批结果
   - 对消息做出相应的操作

**示例**：
```python
# 主代理检查信箱
inbox = BUS.read_inbox("lead")
if inbox:
    # 添加到对话历史
    messages.append({"role": "user", "content": f"<inbox>{json.dumps(inbox, indent=2)}</inbox>"})
    messages.append({"role": "assistant", "content": "Noted inbox messages."})

# 团队成员处理消息
inbox = self.bus.read_inbox(name)
for msg in inbox:
    if msg.get("type") == "shutdown_request":
        self._set_status(name, "shutdown")
        return
    messages.append({"role": "user", "content": json.dumps(msg)})
```

**管理器的工作方式**：
1. 工具调用管理器的方法
2. 管理器执行相应的操作
3. 管理器更新内部状态
4. 管理器返回操作结果

### 4. 团队成员：系统的帮手

**团队成员是什么？**
团队成员是系统的帮手，每个成员都在自己的线程里工作，可以独立完成任务。每个成员有自己的状态：`working`（工作中）、`idle`（空闲）、`shutdown`（关闭）。

**团队成员的具体能力**：
- **独立工作**：在自己的线程中运行，不影响主代理
- **自动认领任务**：空闲时会自动检查并认领未分配的任务
- **通信能力**：可以通过消息总线与其他成员和主代理通信
- **工具使用**：可以使用大部分工具（读写文件、运行命令、发送消息等）
- **状态管理**：根据工作情况自动切换状态
- **错误处理**：遇到错误时会自动关闭

**团队成员的工作流程**：
1. **工作阶段**：
   - 读取自己的信箱（路径：`.team/inbox/{name}.jsonl`），看看有没有新消息
   - 处理消息（比如关闭请求）
   - 调用 Claude 决定下一步
   - 执行工具操作
   - 直到完成当前工作或请求空闲

2. **空闲阶段**：
   - 状态变为 `idle`
   - 定期检查信箱
   - 检查有没有未认领的任务（路径：`.tasks/` 目录下的任务文件）
   - 如果有新消息或任务，就恢复工作
   - 如果长时间空闲，就关闭

**示例**：
```python
# 创建一个前端开发者团队成员
TEAM.spawn("frontend_dev", "前端开发者", "你是一名专业的前端开发者，擅长 React 开发")

# 团队成员状态变化：
# 1. 初始状态：working（开始工作）
# 2. 完成任务后：working → idle（进入空闲）
# 3. 收到新消息或发现新任务：idle → working（恢复工作）
# 4. 长时间无工作：idle → shutdown（关闭）
```

### 5. 状态管理：系统的秩序

**状态是什么？**
状态是系统的秩序，让每个组件都知道自己当前在做什么，确保系统有序运行。

**主要状态**：
- **团队成员状态**：`working`（工作中）、`idle`（空闲）、`shutdown`（关闭）
- **任务状态**：`pending`（待处理）、`in_progress`（进行中）、`completed`（已完成）、`deleted`（已删除）
- **待办事项状态**：`pending`（待处理）、`in_progress`（进行中）、`completed`（已完成）
- **后台任务状态**：`running`（运行中）、`completed`（已完成）、`error`（错误）

**为什么要追踪 shutdown 请求？**
- **状态同步**：确保关闭请求被正确处理，避免团队成员状态不一致
- **请求跟踪**：通过 `shutdown_requests` 字典（路径：内存中）跟踪每个关闭请求的状态
- **可靠性**：确保关闭请求不会丢失，即使系统重启
- **响应反馈**：让主代理知道关闭请求的执行情况

**状态的作用**：
- 跟踪每个组件的当前状态
- 决定组件的下一步行为
- 确保系统有序运行
- 提供清晰的状态流转

**示例**：
```python
# 发送关闭请求给团队成员
result = handle_shutdown_request("frontend_dev")
# 结果："Shutdown request abc123 sent to 'frontend_dev'"

# 关闭请求状态跟踪（内存中的字典）
# shutdown_requests = {
#     "abc123": {"target": "frontend_dev", "status": "pending"}
# }

# 团队成员收到关闭请求后，状态变为 shutdown
# TEAM.list_all() 会显示：frontend_dev (前端开发者): shutdown
```

### 6. 外部接口：用户的入口

**外部接口是什么？**
外部接口是用户与系统交互的入口，让用户能够控制和使用系统。

**主要接口**：
- **REPL 命令行**：用户输入命令和请求的地方
- **特殊命令**：
  - `/compact`：手动压缩对话
  - `/tasks`：列出所有任务
  - `/team`：列出所有团队成员
  - `/inbox`：读取领导信箱
  - `q` 或 `exit`：退出系统

**接口的工作方式**：
1. 用户在命令行输入请求
2. 系统读取并处理请求
3. 如果是特殊命令，直接执行
4. 如果是普通请求，交给主循环处理
5. 系统生成回复，显示给用户

## 整体工作流程示例

**场景**：用户请求创建一个项目并部署

1. **用户输入**："帮我创建一个 Web 项目并部署到服务器"

2. **主循环处理**：
   - 分析请求，决定生成团队成员
   - 创建任务列表

3. **团队成员生成**：
   - 前端开发者（状态：`working`）
   - 后端开发者（状态：`working`）
   - DevOps 工程师（状态：`idle`）

4. **任务分配**：
   - 任务 1（前端）：创建 React 前端
   - 任务 2（后端）：创建 FastAPI 后端
   - 任务 3（DevOps）：部署到服务器

5. **前端开发者工作**：
   - 读取信箱，发现任务分配
   - 开始工作（状态：`working`）
   - 调用工具创建前端代码
   - 完成后，状态变为 `idle`

6. **后端开发者工作**：
   - 读取信箱，发现任务分配
   - 开始工作（状态：`working`）
   - 调用工具创建后端 API
   - 完成后，状态变为 `idle`

7. **DevOps 工程师工作**：
   - 空闲状态下检查任务
   - 发现任务 3 待处理，自动认领
   - 状态变为 `working`
   - 调用后台工具部署应用
   - 完成后，状态变为 `idle`

8. **主循环汇总**：
   - 检查任务状态，发现全部完成
   - 生成回复给用户："Web 项目已创建并部署成功！"

## 代码结构的优势

1. **模块化**：每个功能都有独立的模块，便于理解和维护
2. **状态清晰**：每个组件都有明确的状态，流程可控
3. **扩展性**：可以轻松添加新工具、新技能、新团队成员
4. **并行处理**：团队成员在不同线程工作，提高效率
5. **容错性**：某个模块出错不影响整个系统
6. **自动化**：团队成员可以自动认领任务，减少人工干预

## 总结

myclaude 系统就像一个智能办公室：
- **主代理**是办公室主任，负责协调所有工作
- **工具**是各种办公设备，帮助完成具体任务
- **管理器**是部门主管，管理各自领域的工作
- **团队成员**是各个员工，在自己的岗位上工作
- **状态**是工作状态牌，显示每个人的当前状态
- **接口**是办公室门口，用户通过它进来提需求

整个系统通过主循环驱动，各模块协作，状态管理有序，能够高效完成各种复杂任务。无论是简单的文件操作，还是复杂的团队协作，系统都能轻松应对！
