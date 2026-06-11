"""
Project — 项目管理

每个 Project 对应一个研究方向，包含多个实验 (attempt)。
项目数据存储在 projects/<name>/project.json。

Project 状态机:
    draft → active → completed / archived

用法:
    from lib.project import ProjectManager
    pm = ProjectManager(root=Path("/home/lk/ai_mentor"))
    proj = pm.create("semantic_slam", idea_file="data/ideas/semantic_filtering_slam.json")
    proj.add_attempt("experiments/2026-06-08_01-31-13_semantic_filtering_slam_attempt_0")
    proj.set_status("active")
    pm.list_projects()
"""
import json
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Attempt:
    path: str = ""
    timestamp: float = 0.0
    status: str = "running"
    notes: str = ""
    best_metric: float = 0.0
    stage_completed: int = 0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class PaperReview:
    timestamp: float = 0.0
    reviewer: str = ""
    severity: str = ""
    issues: list = field(default_factory=list)
    summary: str = ""


@dataclass
class Project:
    name: str = ""
    title: str = ""
    idea_file: str = ""
    status: str = "draft"
    created_at: float = 0.0
    updated_at: float = 0.0
    attempts: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    notes: str = ""
    goal: str = ""
    target_venue: str = ""
    page_limit: int = 4
    paper_reviews: list = field(default_factory=list)
    config: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = self.created_at
        self.attempts = [
            Attempt(**a) if isinstance(a, dict) else a for a in self.attempts
        ]
        self.paper_reviews = [
            PaperReview(**r) if isinstance(r, dict) else r
            for r in self.paper_reviews
        ]

    def add_attempt(self, path: str, notes: str = "") -> Attempt:
        a = Attempt(path=path, notes=notes)
        self.attempts.append(a)
        self.updated_at = time.time()
        return a

    def get_latest_attempt(self) -> Optional[Attempt]:
        return self.attempts[-1] if self.attempts else None

    def get_best_attempt(self) -> Optional[Attempt]:
        if not self.attempts:
            return None
        return max(self.attempts, key=lambda a: a.best_metric)

    def set_status(self, status: str):
        valid = ("draft", "active", "completed", "archived")
        if status not in valid:
            raise ValueError(f"Invalid status '{status}', must be one of {valid}")
        self.status = status
        self.updated_at = time.time()

    def add_review(self, reviewer: str, severity: str, issues: list, summary: str = "") -> PaperReview:
        r = PaperReview(
            timestamp=time.time(),
            reviewer=reviewer,
            severity=severity,
            issues=issues,
            summary=summary,
        )
        self.paper_reviews.append(r)
        self.updated_at = time.time()
        return r

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(**d)


class ProjectManager:
    def __init__(self, root: Path):
        self.root = root
        self.projects_dir = root / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def _project_path(self, name: str) -> Path:
        safe = name.replace("/", "_").replace(" ", "_")
        return self.projects_dir / safe / "project.json"

    def create(self, name: str, title: str = "", idea_file: str = "",
               goal: str = "", target_venue: str = "", page_limit: int = 4,
               tags: list = None, config: dict = None) -> Project:
        p = self._project_path(name)
        if p.exists():
            raise FileExistsError(f"Project '{name}' already exists at {p}")
        proj = Project(
            name=name,
            title=title or name,
            idea_file=idea_file,
            goal=goal,
            target_venue=target_venue,
            page_limit=page_limit,
            tags=tags or [],
            config=config or {},
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        self._save(proj)
        return proj

    def load(self, name: str) -> Project:
        p = self._project_path(name)
        if not p.exists():
            raise FileNotFoundError(f"Project '{name}' not found at {p}")
        with open(p) as f:
            return Project.from_dict(json.load(f))

    def _save(self, proj: Project):
        p = self._project_path(proj.name)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(proj.to_dict(), f, indent=2, ensure_ascii=False)

    def save(self, proj: Project):
        self._save(proj)

    def delete(self, name: str):
        p = self._project_path(name)
        if p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.rmtree(p.parent)

    def list_projects(self, status: str = None) -> list[Project]:
        projects = []
        if not self.projects_dir.exists():
            return projects
        for d in sorted(self.projects_dir.iterdir()):
            pf = d / "project.json"
            if pf.exists():
                with open(pf) as f:
                    proj = Project.from_dict(json.load(f))
                if status is None or proj.status == status:
                    projects.append(proj)
        return projects

    def get_or_create(self, name: str, **kwargs) -> Project:
        try:
            return self.load(name)
        except FileNotFoundError:
            return self.create(name, **kwargs)

    def link_experiment(self, project_name: str, experiment_path: str, notes: str = "") -> Project:
        proj = self.load(project_name)
        proj.add_attempt(experiment_path, notes=notes)
        self._save(proj)
        return proj

    def scan_experiments(self, project_name: str) -> Project:
        proj = self.load(project_name)
        exp_dir = self.root / "experiments"
        if not exp_dir.exists():
            return proj
        existing = {a.path for a in proj.attempts}
        for d in sorted(exp_dir.iterdir()):
            if not d.is_dir():
                continue
            rel = str(d)
            if rel not in existing and project_name.replace("_", "") in d.name.replace("_", ""):
                proj.add_attempt(rel)
        self._save(proj)
        return proj


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parent.parent
    pm = ProjectManager(root)

    if len(sys.argv) < 2:
        print("Usage: python project.py <command> [args]")
        print("Commands: list, create <name>, show <name>, scan <name>, link <name> <exp_path>")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "list":
        for p in pm.list_projects():
            attempts = len(p.attempts)
            latest = p.get_latest_attempt()
            latest_status = latest.status if latest else "-"
            print(f"  [{p.status:>9}] {p.name:<30} attempts={attempts} latest={latest_status}")
    elif cmd == "create":
        name = sys.argv[2] if len(sys.argv) > 2 else "untitled"
        proj = pm.create(name, title=name)
        print(f"Created project: {proj.name} at projects/{name}/")
    elif cmd == "show":
        name = sys.argv[2]
        proj = pm.load(name)
        print(json.dumps(proj.to_dict(), indent=2, ensure_ascii=False))
    elif cmd == "scan":
        name = sys.argv[2]
        proj = pm.scan_experiments(name)
        pm.save(proj)
        print(f"Scanned and linked experiments for '{name}': {len(proj.attempts)} attempts")
    elif cmd == "link":
        name = sys.argv[2]
        exp = sys.argv[3] if len(sys.argv) > 3 else ""
        if exp:
            proj = pm.link_experiment(name, exp)
            print(f"Linked {exp} to project '{name}'")
        else:
            print("Usage: link <project_name> <experiment_path>")
    else:
        print(f"Unknown command: {cmd}")
