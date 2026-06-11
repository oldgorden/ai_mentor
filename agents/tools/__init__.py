"""
agents/tools/ — AI 可调用的工具集

每个工具继承 Tool 基类，定义 permission（权限）和 confidence_required（置信度阈值）。
Member（导师 + 研究生）通过 LLM 决策选择工具，Group 检查权限和置信度后执行。

工具分组:
    basic.py       基本能力（读文件、搜代码、浏览目录、编辑文件、执行命令）
      ReadFile               读文件                    perm=research:read       conf=0.0
      SearchCode             搜索代码（grep）          perm=research:read       conf=0.0
      ListFiles              列出目录/搜索文件         perm=research:read       conf=0.0
      EditFile               局部编辑文件              perm=research:write      conf=0.3
      RunShell               执行 shell 命令           perm=research:write      conf=0.5
      WebFetch               抓取网页内容              perm=research:read       conf=0.0

    experiment.py  实验管理
      WriteExperimentCode   写实验代码               perm=experiment:write    conf=0.3
      RunExperiment         执行实验代码              perm=experiment:write    conf=0.5
      ValidateResults       审查实验结果              perm=experiment:read     conf=0.0
      ReadExperimentOutput  读取实验日志              perm=experiment:read     conf=0.0

    research.py    研究工具
      SearchPapers          搜索学术论文              perm=research:read       conf=0.0
      RunCode               执行 Python 代码          perm=research:write      conf=0.5

    literature.py  文献工具
      SearchLiterature      搜索论文（详细版）        perm=literature:read     conf=0.0
      GetPaperDetails       获取论文引用格式           perm=literature:read     conf=0.0

    writing.py     写作工具
      WritePaper            生成 LaTeX 论文           perm=writing:write       conf=0.7
      GeneratePlots         生成聚合图表              perm=writing:write       conf=0.5

    review.py      审稿工具
      ReviewPaper           LLM 文本审稿              perm=review:write        conf=0.3
      VisualReview          VLM 图表审查              perm=review:write        conf=0.3
      AnalyzeImages         VLM 读图分析              perm=research:read       conf=0.0

    journal.py     Journal 工具
      ReadJournal           读取实验 journal          perm=journal:read        conf=0.0
      SummarizeLogs         压缩实验日志              perm=journal:write       conf=0.3

    knowledge.py   知识库
      ReadKB                读取知识库                perm=knowledge:read       conf=0.0
      WriteKB               写入知识库                perm=knowledge:write      conf=0.3

    project.py     项目管理
      CreateProject         创建研究项目              perm=project:write        conf=0.7
      ListProjects          列出项目及状态            perm=project:read         conf=0.0
      UpdateProject         更新项目信息              perm=project:write        conf=0.5
      ScanExperiments       扫描并关联实验            perm=project:write        conf=0.3

权限体系:
    experiment:*     实验控制
    research:*       研究工具
    literature:*     文献搜索
    writing:*        论文写作
    review:*         审稿
    journal:*        实验记录
    knowledge:*      知识库
    project:*        项目管理
"""
from agents.tools.basic import ReadFile, SearchCode, ListFiles, EditFile, RunShell, WebFetch, AssignTask, CheckTaskResults
from agents.tools.experiment import (
    WriteExperimentCode, RunExperiment, ValidateResults, ReadExperimentOutput,
)
from agents.tools.research import SearchPapers, RunCode
from agents.tools.literature import SearchLiterature, GetPaperDetails
from agents.tools.writing import WritePaper, GeneratePlots
from agents.tools.review import ReviewPaper, VisualReview, AnalyzeImages
from agents.tools.journal import ReadJournal, SummarizeLogs
from agents.tools.knowledge import ReadKB, WriteKB
from agents.tools.project import CreateProject, ListProjects, UpdateProject, ScanExperiments
from agents.tools.improve import CritiquePaper

ALL_TOOLS = [
    ReadFile(),
    SearchCode(),
    ListFiles(),
    EditFile(),
    RunShell(),
    WebFetch(),
    AssignTask(),
    CheckTaskResults(),
    WriteExperimentCode(),
    RunExperiment(),
    ValidateResults(),
    ReadExperimentOutput(),
    SearchPapers(),
    RunCode(),
    SearchLiterature(),
    GetPaperDetails(),
    WritePaper(),
    GeneratePlots(),
    ReviewPaper(),
    VisualReview(),
    AnalyzeImages(),
    ReadJournal(),
    SummarizeLogs(),
    ReadKB(),
    WriteKB(),
    CreateProject(),
    ListProjects(),
    UpdateProject(),
    ScanExperiments(),
    CritiquePaper(),
]
