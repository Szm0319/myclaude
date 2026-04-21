# === SECTION: agent_loop ===
# 代理主循环
# 输入: messages - 消息列表

import json
from src.modules.compression import microcompact, estimate_tokens, auto_compact
from src.modules.background import BackgroundManager
from src.modules.messaging import MessageBus
from src.modules.todos import TodoManager

# 令牌阈值，超过此值会触发自动压缩
TOKEN_THRESHOLD = 100000


def agent_loop(client, MODEL, messages: list, TODO: TodoManager, BG: BackgroundManager, BUS: MessageBus, TOOL_HANDLERS, TOOLS, SYSTEM):
    # 记录没有使用待办事项的轮数
    rounds_without_todo = 0
    while True:
        # s06: 压缩管道
        # 微压缩消息
        microcompact(messages)
        # 如果令牌数超过阈值，自动压缩
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            print("[auto-compact triggered]")
            messages[:] = auto_compact(client, MODEL, messages)
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
            messages[:] = auto_compact(client, MODEL, messages, focus=compact_focus)
