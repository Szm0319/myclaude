# === SECTION: messaging (s09) ===
# 消息总线
import json
import time
from pathlib import Path

# 团队相关目录
TEAM_DIR = Path.cwd() / ".team"
INBOX_DIR = TEAM_DIR / "inbox"


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
