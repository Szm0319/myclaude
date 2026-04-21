# === SECTION: compression (s06) ===
# 估计消息的令牌数
# 输入: messages - 消息列表
# 输出: 估计的令牌数

import json
import time
from pathlib import Path

# 对话转录目录
TRANSCRIPT_DIR = Path.cwd() / ".transcripts"
# 保留最近的工具结果数量
KEEP_RECENT = 3
# 需要保留结果的工具列表
PRESERVE_RESULT_TOOLS = {"read_file"}


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

def auto_compact(client, MODEL, messages: list, focus: str = None) -> list:
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
