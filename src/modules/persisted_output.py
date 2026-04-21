# === SECTION: persisted_output (s06) ===
# 持久化工具结果到文件
# 输入: tool_use_id - 工具使用 ID, content - 工具输出内容
# 输出: 存储文件的相对路径

import re
from pathlib import Path

# 持久化输出相关配置
# 大型工具输出会被写入磁盘，并用预览标记替换
TASK_OUTPUT_DIR = Path.cwd() / ".task_outputs"
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
    return path.relative_to(Path.cwd())

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
