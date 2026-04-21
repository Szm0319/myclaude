# === SECTION: todos (s03) ===
# 待办事项管理器
class TodoManager:
    def __init__(self):
        # 初始化待办事项列表
        self.items = []

    # 更新待办事项列表
    # 输入: items - 待办事项列表
    # 输出: 渲染后的待办事项字符串
    def update(self, items: list) -> str:
        validated, ip = [], 0
        # 验证每个待办事项
        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            af = str(item.get("activeForm", "")).strip()
            # 验证内容
            if not content: raise ValueError(f"Item {i}: content required")
            # 验证状态
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")
            # 验证活动表单
            if not af: raise ValueError(f"Item {i}: activeForm required")
            # 统计进行中的任务数
            if status == "in_progress": ip += 1
            # 添加到验证列表
            validated.append({"content": content, "status": status, "activeForm": af})
        # 检查待办事项数量限制
        if len(validated) > 20: raise ValueError("Max 20 todos")
        # 检查进行中任务数量限制
        if ip > 1: raise ValueError("Only one in_progress allowed")
        # 更新待办事项列表
        self.items = validated
        # 渲染并返回
        return self.render()

    # 渲染待办事项列表
    # 输出: 格式化的待办事项字符串
    def render(self) -> str:
        if not self.items: return "No todos."
        lines = []
        # 遍历每个待办事项
        for item in self.items:
            # 根据状态生成标记
            m = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(item["status"], "[?]")
            # 为进行中的任务添加活动表单标记
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            # 添加到行列表
            lines.append(f"{m} {item['content']}{suffix}")
        # 计算已完成的任务数
        done = sum(1 for t in self.items if t["status"] == "completed")
        # 添加完成情况
        lines.append(f"\n({done}/{len(self.items)} completed)")
        # 连接并返回
        return "\n".join(lines)

    # 检查是否有未完成的任务
    # 输出: 布尔值，表示是否有未完成的任务
    def has_open_items(self) -> bool:
        return any(item.get("status") != "completed" for item in self.items)
