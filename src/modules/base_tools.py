# === SECTION: base_tools ===
# 安全路径处理
# 输入: p - 路径字符串
# 输出: 解析后的安全路径对象

import subprocess
from pathlib import Path
from src.modules.persisted_output import maybe_persist_output, PERSIST_OUTPUT_TRIGGER_CHARS_BASH, CONTEXT_TRUNCATE_CHARS

# 工作目录设置
WORKDIR = Path.cwd()


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
