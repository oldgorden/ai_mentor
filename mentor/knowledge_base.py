"""知识库：永久知识库 + 临时知识库"""
import json
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict


@dataclass
class KnowledgeEntry:
    id: str = ""
    category: str = ""
    content: str = ""
    summary: str = ""
    timestamp: float = 0.0
    importance: int = 1
    tags: list = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.id:
            self.id = f"{self.category}_{int(self.timestamp)}"


class KnowledgeBase:
    """知识库"""

    def __init__(self, path: Path):
        self.path = path
        self.entries: list[KnowledgeEntry] = []
        self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path) as f:
                data = json.load(f)
            self.entries = [KnowledgeEntry(**d) for d in data]

    def save(self):
        with open(self.path, "w") as f:
            json.dump([asdict(e) for e in self.entries], f, indent=2, ensure_ascii=False)

    def add(self, category: str, content: str, summary: str = "", importance: int = 1, tags: list = None):
        entry = KnowledgeEntry(
            category=category,
            content=content,
            summary=summary or content[:200],
            importance=importance,
            tags=tags or [],
        )
        self.entries.append(entry)
        self.save()
        return entry

    def query(self, category: str = None, tags: list = None, limit: int = 10) -> list:
        results = self.entries
        if category:
            results = [e for e in results if e.category == category]
        if tags:
            results = [e for e in results if any(t in e.tags for t in tags)]
        return sorted(results, key=lambda e: -e.importance)[:limit]


class PermanentKnowledge:
    """永久知识库：导师手册，不变"""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict = {}
        self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path) as f:
                self.entries = json.load(f)

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.entries, f, indent=2, ensure_ascii=False)

    def set(self, key: str, value: str):
        self.entries[key] = value
        self.save()

    def get(self, key: str) -> str:
        return self.entries.get(key, "")

    def get_mentor_guide(self) -> str:
        """获取导师操作指南"""
        return self.entries.get("mentor_guide", "")

    def get_student_guide(self) -> str:
        """获取学生操作指南"""
        return self.entries.get("student_guide", "")

    def get_domain_knowledge(self) -> str:
        """获取领域知识"""
        return self.entries.get("domain_knowledge", "")


class TemporaryKnowledge:
    """临时知识库：目录+索引结构"""

    def __init__(self, path: Path):
        self.path = path
        self.index_path = path.parent / f"{path.stem}_index.json"
        self.entries: list[KnowledgeEntry] = []
        self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path) as f:
                data = json.load(f)
            self.entries = [KnowledgeEntry(**d) for d in data]

    def save(self):
        # 保存完整条目
        with open(self.path, "w") as f:
            json.dump([asdict(e) for e in self.entries], f, indent=2, ensure_ascii=False)
        # 保存索引（摘要+位置）
        index = []
        for i, e in enumerate(self.entries):
            index.append({
                "id": e.id,
                "category": e.category,
                "summary": e.summary[:200],
                "importance": e.importance,
                "tags": e.tags,
                "index": i,  # 指向完整条目的索引
            })
        with open(self.index_path, "w") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

    def add(self, category: str, content: str, summary: str = "", importance: int = 1, tags: list = None):
        entry = KnowledgeEntry(
            category=category,
            content=content,
            summary=summary or content[:200],
            importance=importance,
            tags=tags or [],
        )
        self.entries.append(entry)
        self.save()
        return entry

    def query(self, category: str = None, tags: list = None, limit: int = 10) -> list:
        """查询：先查索引，再取完整内容"""
        results = self.entries
        if category:
            results = [e for e in results if e.category == category]
        if tags:
            results = [e for e in results if any(t in e.tags for t in tags)]
        return sorted(results, key=lambda e: -e.importance)[:limit]

    def query_index(self, category: str = None, tags: list = None, limit: int = 10) -> list:
        """只查索引（不加载完整内容，更快）"""
        if not self.index_path.exists():
            return []
        with open(self.index_path) as f:
            index = json.load(f)
        results = index
        if category:
            results = [e for e in results if e.get("category") == category]
        if tags:
            results = [e for e in results if any(t in e.get("tags", []) for t in tags)]
        return sorted(results, key=lambda e: -e.get("importance", 0))[:limit]

    def get_summary(self, max_tokens: int = 2000) -> str:
        """获取摘要（从索引，不加载完整内容）"""
        if not self.index_path.exists():
            return ""
        with open(self.index_path) as f:
            index = json.load(f)
        sorted_entries = sorted(index, key=lambda e: -e.get("importance", 0))
        lines = []
        total_chars = 0
        for entry in sorted_entries:
            line = f"[{entry.get('category', '')}] {entry.get('summary', '')}\n"
            if total_chars + len(line) > max_tokens * 3:
                break
            lines.append(line)
            total_chars += len(line)
        return "".join(lines)

    def compress(self, keep_recent: int = 20) -> str:
        """压缩：只保留最近的条目，更早的归档到索引"""
        if len(self.entries) <= keep_recent:
            return ""

        sorted_entries = sorted(self.entries, key=lambda e: -e.timestamp)
        recent = sorted_entries[:keep_recent]
        old = sorted_entries[keep_recent:]

        summary_parts = []
        for e in old:
            summary_parts.append(f"[{e.category}] {e.summary}")

        self.entries = recent
        self.save()

        return "\n".join(summary_parts)

    def get_size(self) -> int:
        return sum(len(e.content) for e in self.entries)
