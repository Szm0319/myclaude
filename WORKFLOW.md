# myclaude 工作流程详解

## 简单理解：这个项目是啥？

想象你有个超级聪明的助手，它能帮你写代码、查资料、甚至帮你安排任务。这个项目就是这样一个智能助手系统！

## 状态枚举：系统会经历哪些状态？

```python
# 系统状态枚举
class AgentState(Enum):
    IDLE = "idle"                    # 空闲状态，等待用户输入
    PROCESSING = "processing"        # 正在处理用户请求
    TOOL_CALLING = "tool_calling"    # 正在调用工具
    AWAITING_RESULT = "awaiting_result"  # 等待工具执行结果
    COMPRESSING = "compressing"      # 正在压缩对话上下文
    RESPONDING = "responding"        # 正在生成回复
    ERROR = "error"                  # 发生错误
```

## 实际例子：让我们看看系统是怎么工作的！

假设你运行程序后输入："帮我创建一个简单的 hello.py 文件"

让我们跟着系统一步步走：

### 第 1 步：用户输入

**状态变化**：`IDLE` → `PROCESSING`

- 你在终端输入："帮我创建一个简单的 hello.py 文件"
- 系统接收到你的输入，进入处理状态

### 第 2 步：代理主循环启动

系统进入主循环，开始处理你的请求。先检查一下：
- 对话上下文太长吗？需要压缩吗？
- 有后台任务完成吗？
- 有新消息吗？

### 第 3 步：调用 Claude 模型

**状态变化**：`PROCESSING` → `TOOL_CALLING`

系统把你的问题发给 Claude 模型：
- 你说："帮我创建一个简单的 hello.py 文件"
- 系统提示：你是个编程助手，有这些工具可用（写文件、读文件、运行命令等）

Claude 模型思考了一下，决定：我应该用 `write_file` 工具来创建文件。

### 第 4 步：工具调用

**状态变化**：`TOOL_CALLING` → `AWAITING_RESULT`

系统执行 Claude 的决定，调用 `write_file` 工具：
```python
tool_input = {
    "path": "hello.py",
    "content": "print('Hello, World!')"
}
result = write_file(**tool_input)  # 执行写文件操作
```

### 第 5 步：获取工具结果

**状态变化**：`AWAITING_RESULT` → `PROCESSING`

工具执行完毕，返回结果：
```
Wrote 21 bytes to hello.py
```

### 第 6 步：继续处理

系统把结果发给 Claude，让它继续：
- Claude 看了看结果：文件创建成功了！
- 决定：不需要再用工具了，直接回复用户

### 第 7 步：生成回复

**状态变化**：`PROCESSING` → `RESPONDING` → `IDLE`

Claude 生成回复：
```
好的！我已经帮你创建了 hello.py 文件，内容是：
print('Hello, World!')
```

然后系统回到空闲状态，等待你的下一个指令。

## 更复杂一点的例子：多个工具调用

假设你输入："先读一下 hello.py，然后把它改成输出 'Hello, myclaude!'"

**状态流转**：
1. `IDLE` → `PROCESSING`：收到请求
2. `PROCESSING` → `TOOL_CALLING`：决定先读文件
3. `TOOL_CALLING` → `AWAITING_RESULT`：调用 `read_file`
4. `AWAITING_RESULT` → `PROCESSING`：获取文件内容
5. `PROCESSING` → `TOOL_CALLING`：决定修改文件
6. `TOOL_CALLING` → `AWAITING_RESULT`：调用 `edit_file`
7. `AWAITING_RESULT` → `PROCESSING`：确认修改成功
8. `PROCESSING` → `RESPONDING`：生成完成回复
9. `RESPONDING` → `IDLE`：回到空闲状态

## 团队协作的例子

假设你输入："生成一个团队成员帮我写测试代码"

**工作流程**：
1. 你说："生成一个团队成员帮我写测试代码"
2. Claude 决定：用 `spawn_teammate` 工具
3. 系统创建一个新的团队成员线程
4. 团队成员有自己的状态机：`working` → `idle` → `shutdown`
5. 团队成员可以独立工作、认领任务、和你及其他成员通信

## 后台任务的例子

假设你运行一个长时间的命令："后台运行 pytest 测试"

**工作流程**：
1. Claude 决定：用 `background_run` 工具
2. 系统在后台启动线程运行 pytest
3. 主线程继续，可以处理其他问题
4. 后台任务完成后，会通知系统
5. 下次循环时，系统会把结果告诉你

## 核心模块职责（大白话版）

### 1. [persisted_output.py](file:///workspace/src/modules/persisted_output.py)
**干啥的**：帮你存大文件
- 工具输出太长了？别担心，这个模块会把它存到磁盘
- 下次需要看，还能找到

### 2. [base_tools.py](file:///workspace/src/modules/base_tools.py)
**干啥的**：基本工具集合
- 运行命令：想在终端执行个命令？找它
- 读文件：想看看文件里有啥？找它
- 写文件：想创建或修改文件？找它
- 安全第一：所有路径都在工作目录内，不会让你破坏外面的东西

### 3. [todos.py](file:///workspace/src/modules/todos.py)
**干啥的**：待办事项管理器
- 帮你列任务清单
- 标记哪些完成了，哪些正在做
- 提醒你别忘了更新进度

### 4. [subagent.py](file:///workspace/src/modules/subagent.py)
**干啥的**：子代理系统
- 需要帮你探索一下代码库？派个子代理去
- 子代理有自己的工具，完成任务后向你报告

### 5. [skills.py](file:///workspace/src/modules/skills.py)
**干啥的**：技能加载器
- 需要写 React 组件？加载前端开发技能
- 需要优化数据库查询？加载数据库专家技能
- 技能就像插件，想用就用

### 6. [compression.py](file:///workspace/src/modules/compression.py)
**干啥的**：上下文压缩
- 聊了很久，对话太长？这个模块帮你总结一下
- 保留重要信息，扔掉没用的
- 让模型能继续和你聊天，不会忘事

### 7. [task_manager.py](file:///workspace/src/modules/task_manager.py)
**干啥的**：任务看板
- 把大任务拆成小任务
- 可以认领任务、设置状态
- 还能设置任务依赖关系

### 8. [background.py](file:///workspace/src/modules/background.py)
**干啥的**：后台任务管理器
- 有些命令运行时间很长，别等了！放后台跑
- 后台任务完成了会通知你
- 多个后台任务可以同时跑

### 9. [messaging.py](file:///workspace/src/modules/messaging.py)
**干啥的**：团队通信系统
- 团队成员之间可以发消息
- 每个成员有自己的收件箱
- 还能广播消息给所有人

### 10. [team.py](file:///workspace/src/modules/team.py)
**干啥的**：团队成员管理
- 生成新的团队成员
- 管理成员状态（工作中、空闲、关闭）
- 成员可以独立工作、自动认领任务

### 11. [agent_loop.py](file:///workspace/src/modules/agent_loop.py)
**干啥的**：代理主循环
- 整个系统的指挥中心
- 把所有模块串起来工作
- 处理用户输入、调用模型、执行工具

## 完整状态流转图

```
用户输入
   ↓
[IDLE] → [PROCESSING] → [TOOL_CALLING] ←┐
                        ↓                │
                  [AWAITING_RESULT] ────┘
                        ↓
            [需要更多工具吗？] → 是 → 回到[TOOL_CALLING]
                        ↓ 否
                  [RESPONDING]
                        ↓
                  [IDLE]
                        ↓
                  等待下次输入
```

## 出错处理

万一出错了怎么办？
- 系统会变成 `ERROR` 状态
- 但不用担心，它会尽量恢复
- 把错误信息发给 Claude，让它帮忙解决

## 总结

这个系统就像一个智能助手团队：
- 有主助手直接和你对话
- 有后台助手帮你跑长时间任务
- 有团队助手帮你分担工作
- 还有各种工具帮你完成具体操作

整个系统通过状态机来管理，清晰有序，不会混乱！
