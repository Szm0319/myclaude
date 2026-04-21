# myclaude

一个基于 Claude 模型的智能代理系统，提供完整的代理框架和丰富的功能模块。

## 项目概述

myclaude 是一个功能强大的智能代理系统，旨在提供一个完整的、可扩展的代理框架，支持多种功能和工具集成。该项目将多个核心机制组合成一个可运行的代理，包括工具调度、任务管理、团队协作、技能加载等功能。

**主要功能亮点：**
- 完整的代理循环与工具调度系统
- 待办事项管理与任务跟踪
- 子代理系统用于隔离执行任务
- 技能加载机制以获取专业知识
- 上下文压缩以优化模型性能
- 团队协作与消息传递系统
- 后台任务管理
- 自主运行与任务认领能力

## 目录结构

```
/workspace
├── README.md              # 项目说明文件
├── main.py                # 主程序文件
├── requirements.txt       # 依赖文件
├── .env.example           # 环境变量示例
├── src/                   # 源代码目录
│   ├── __init__.py
│   └── modules/           # 模块目录
│       ├── __init__.py
│       ├── persisted_output.py  # 工具结果持久化
│       ├── base_tools.py        # 基本工具操作
│       ├── todos.py             # 待办事项管理
│       ├── subagent.py          # 子代理系统
│       ├── skills.py            # 技能加载
│       ├── compression.py       # 上下文压缩
│       ├── task_manager.py      # 任务管理
│       ├── background.py        # 后台任务管理
│       ├── messaging.py         # 消息总线
│       ├── team.py              # 团队成员管理
│       └── agent_loop.py        # 代理主循环
├── .team/                 # 团队相关目录
│   ├── config.json        # 团队配置文件
│   └── inbox/             # 收件箱目录
├── .tasks/                # 任务目录
├── skills/                # 技能目录
│   └── *                  # 技能子目录，包含 SKILL.md 文件
├── .transcripts/          # 对话转录目录
└── .task_outputs/         # 任务输出目录
    └── tool-results/      # 工具结果目录
```

## 安装步骤

1. **克隆项目**
   ```bash
   git clone <项目仓库地址>
   cd myclaude
   ```

2. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

3. **配置环境变量**
   - 复制 `.env.example` 文件为 `.env`
   - 编辑 `.env` 文件，设置必要的环境变量
   ```bash
   cp .env.example .env
   # 编辑 .env 文件
   ```

## 运行方法

```bash
python main.py
```

## REPL 命令

程序支持以下 REPL 命令：

- `/compact`：手动压缩对话上下文
- `/tasks`：列出所有任务
- `/team`：列出所有团队成员
- `/inbox`：读取领导收件箱
- `q` 或 `exit`：退出程序

## 扩展与定制

### 添加新工具

要添加新工具，需要：
1. 在 `TOOL_HANDLERS` 字典中添加工具处理函数
2. 在 `TOOLS` 列表中添加工具定义

### 添加新技能

要添加新技能，需要：
1. 在 `skills` 目录下创建新的技能子目录
2. 在子目录中创建 `SKILL.md` 文件，包含技能的元数据和内容

### 定制系统提示

可以通过修改 `SYSTEM` 变量来自定义系统提示，调整代理的行为和可用工具。

## 依赖关系

- **anthropic**：Claude 模型客户端
- **python-dotenv**：加载环境变量

## 注意事项

- 确保设置了正确的 `MODEL_ID` 环境变量
- 如需使用自定义 API 端点，请设置 `ANTHROPIC_BASE_URL` 环境变量
- 对于大型工具输出，系统会自动将其持久化到磁盘
- 系统会自动压缩对话上下文，以优化模型性能
