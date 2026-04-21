# === SECTION: subagent (s04) ===
# 运行子代理
# 输入: prompt - 子代理的提示, agent_type - 代理类型（默认为 "Explore"）
# 输出: 子代理的执行结果

from src.modules.base_tools import run_bash, run_read, run_write, run_edit


def run_subagent(client, MODEL, prompt: str, agent_type: str = "Explore") -> str:
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
