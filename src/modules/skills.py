# === SECTION: skills (s05) ===
# 技能加载器
import re
from pathlib import Path


class SkillLoader:
    def __init__(self, skills_dir: Path):
        # 初始化技能字典
        self.skills = {}
        # 如果技能目录存在
        if skills_dir.exists():
            # 遍历所有 SKILL.md 文件
            for f in sorted(skills_dir.rglob("SKILL.md")):
                # 读取文件内容
                text = f.read_text()
                # 解析文件头部的元数据
                match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
                meta, body = {}, text
                if match:
                    # 解析元数据
                    for line in match.group(1).strip().splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            meta[k.strip()] = v.strip()
                    # 获取正文
                    body = match.group(2).strip()
                # 技能名称，优先使用元数据中的名称，否则使用父目录名称
                name = meta.get("name", f.parent.name)
                # 存储技能信息
                self.skills[name] = {"meta": meta, "body": body}

    # 获取所有技能的描述
    # 输出: 技能描述列表
    def descriptions(self) -> str:
        if not self.skills: return "(no skills)"
        return "\n".join(f"  - {n}: {s['meta'].get('description', '-')}" for n, s in self.skills.items())

    # 加载指定技能
    # 输入: name - 技能名称
    # 输出: 技能内容或错误信息
    def load(self, name: str) -> str:
        s = self.skills.get(name)
        if not s: return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill name=\"{name}\">\n{s['body']}\n</skill>"
