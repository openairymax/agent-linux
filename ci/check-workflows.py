#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""ci/check-workflows.py —— 守住 workflow 薄化与命名约束

十九条规则：

1. 结构上限：每个 workflow 的 job 数 ≤ 预算，内联 ``run: |`` 块数 ≤ 预算
   （预算见 ci/workflow-budgets.txt，只许下调）。
2. 棘轮：内联块数不得高于 git HEAD 中的同名文件——调低预算才算迁移，
   把逻辑抄回 YAML 会让这条规则变红。
3. 已迁移文件（内联预算为 0）不得出现被 gate 收编的模式：宿主清理、
   docker load、摘要校验、qemu 安装、串口 TAP 解析。
4. 任何 workflow 都不得直接调用 apt-get：acquire 超时、重试与整步硬超时
   只有 ci/lib/apt.sh 一处实现，写回 YAML 就会重新长出「镜像不响应、
   一步烧光 240min 预算」的 run。
5. 命名约定：文件名须为 kebab-case ``<scope>[-<scope>…].yml``，且顶层
   ``name:`` 必须与文件同名词——UI 显示名与文件路径只允许一处事实源，
   否则 ``gh workflow run``、Actions 列表与文档会对不上。
6. 门禁脚本只能调用它 source 的库函数。``need_cmd`` 原在 host.sh，四个
   未 source 它的 gate 调用时只得到 "command not found"，前置校验静默
   失效（run 40478538815）；构建期间才发现等于白烧一轮。
7. 最小权限：顶层必须显式声明 ``permissions:``，且不得出现
   ``write-all`` / ``*: write``。未声明时令牌权限由仓库设置决定，
   同一份 YAML 在不同仓库里权限不同，审查时无从判断。
8. 每个 job 必须有 ``timeout-minutes:``。缺省是 360 分钟，等于没有上限；
   acquire 卡死、串口等不到终态这类故障会一路烧到配额尽头。
9. PR 触发的 workflow 必须有 ``concurrency:`` 且按 PR 取消旧轮。
   两次 push 间隔短于 job 时长时，旧 run 的结论对新 HEAD 没有意义，
   却会和它抢同一批并发额度，排障时也容易读到旧 run 的日志。
10. ``pull_request`` 的 paths 必须是 ``push`` 的 paths 子集。PR 侧多出来的
   判据意味着该文件合并后不再被校验：门禁脚本自己改了、评审阶段看不到
   结论，只能在 main 上事后发现（ssot-verify 曾如此）。
11. paths 不得落进子模块内部（``<gitlink>/**``）。父仓 diff 里子模块只
   呈现为目录名一项，内部 glob 永远不命中，写上去只是让人以为覆盖了
   （kernel-verify 的 ``kernel/**`` 就是这样一条死判据）。
12. workflow 里调用的每个 ``ci/ci.sh <gate>`` 都必须在 ``ci/gates/<gate>.sh``
   真实存在，且该脚本自带 ``# 用法:`` 头——分发器按文件名解析，名字写错
   会在 checkout 与 runner 预热之后才以 exit 2 暴露，而 ``--list`` 的自动
   登记表也会漏项。判据在本地一秒可查，没有理由留给云端。
13. 每个 ``runs-on:`` 必须钉成 ``<发行版>-<YY.MM>`` 的镜像标签，不得用
   ``-latest``。浮动标签会在不改变本仓任何一行的情况下换掉编译器、
   shellcheck 与 Python 版本，于是「门禁结论取决于跑的那一天」：0.0.3
   的 SC2034 就是这样在本地与 runner 上分别成立与不成立的。
14. 文件名分两族，语义即用法：``*-verify`` 是阻断型门禁，必须由
   ``pull_request`` 触发（否则它的结论不进入合并判断，等于没拦住）；
   其余只能是生命周期名 ``image-build`` / ``nightly`` / ``release`` /
   ``payload-seed``。读名字就知道会红，也读得出它何时跑。
   ``payload-seed`` 是唯一按周期预热制品、不产出镜像的生命周期：它的
   触发面与 ``image-build`` 无关，混进 ``nightly`` 只会让测试日志与
   预热日志互相淹没。
15. 本文件的规则号与 ``.github/workflows/README.md`` 第 4 节的表格必须
   一一对应。规则表长期写着「十二条」而脚本已有十三条：读者按错版本
   执行，新增规则也没有人知道。表格每行首列登记它对应的规则号，缺失、
   多余与重号都判红——文档因此不能再单独演化。
16. 每个 ``uses:`` 都必须钉到 40 位提交 SHA，并带 ``# <版本>`` 注释。浮动
   tag（``@v4``）是可被上游重新指向的引用：同一条 workflow 在两次运行之间
   可以换掉代码，而日志里看不出任何差别。钉 SHA 把「升级」变成一次显式
   提交，``# v4`` 只是给人读的溯源标签。本地 action（``./…``）与
   ``docker://`` 镜像不受此约束——前者随本仓检出一同变化，后者由摘要
   另行约束。
17. 每个 ``runs-on:`` 必须与 ``tools/nightly/manifest.yaml`` 的
   ``runner.label`` 逐字相等。运行器版本此前散落在两处：``ceeea8e`` 把
   workflow 全部钉到 ``ubuntu-24.04``，而 nightly 契约清单仍写浮动标签，
   靠 run 40864072696 失败之后人工对齐。同一个事实只允许一个取值，改一侧
   就必须同时改另一侧，否则本地一秒即可发现，而不是等云端预热完再报。
18. 门禁、库、工具与 workflow 都不得由名称形状推语义：不得把「标签以
   ``-latest`` 结尾」当成「托管运行器」，也不得在比较、``case`` 匹配或
   GitHub 表达式里写 ``*-latest``。托管运行器的两种合法写法（``ubuntu-24.04``
   与 ``ubuntu-latest``）都表示托管，自托管标签则任意命名——形状与语义
   之间没有蕴含关系。0.0.3 的 run 40864072696 就是这样把自托管的
   ``-latest`` 标签判成托管，随后按托管上限去等一个永远不会到来的空位。
   制品类型（``.asc``/``.rpm``/``.ko``）与包名前缀是命名契约，不在此列。
19. 调用 ``ci/ci.sh remote-sync`` 必须显式给出 ``--kind``。门禁缺省按
   ``heads`` 查 ``refs/heads/<ref>``，而 ``github.ref_name`` 把 ``refs/``
   前缀剥掉之后标签与分支无法区分：标签派发时它去查根本不存在的
   ``refs/heads/v0.0.4``，判负为「无此分支」（run 42335415156 即如此，
   代价是整轮组装在第一步就落空）。命名空间是调用方的已知事实，必须由
   调用方写明，而不是让门禁去猜。

为什么需要它：这些约束此前只存在于文档里，没有执行者。78 段内联 shell
就是这样一段段长出来的——每一次单看都合理，合起来就没有人能在本地验证。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
BUDGETS = ROOT / "ci" / "workflow-budgets.txt"

# 所有 workflow 都适用的禁令：(正则, 应改用的入口)
ALWAYS_FORBIDDEN = [
    (re.compile(r"\bapt-get\b"), "ci/lib/apt.sh apt_sync/apt_install"),
]

# 内联预算为 0 的文件里不允许再出现的模式：(正则, 应改用的 gate)
FORBIDDEN = [
    (re.compile(r"/usr/share/dotnet"), "ci/lib/host.sh free_disk"),
    (re.compile(r"docker\s+load"), "ci/ci.sh container-load"),
    (re.compile(r"sha256sum\s+-c"), "ci/ci.sh iso-checksum | image-verify"),
    (re.compile(r"AIRYMAX-TAP\|"), "ci/ci.sh tap-extract"),
]

RUN_BLOCK = re.compile(r"^(\s*)run:\s*[|>]")

# 命名约定：kebab-case 文件名，顶层 name: 与文件同名词。
NAME_FILE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.yml$")
NAME_TOP = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)

CILIB = ROOT / "ci" / "lib"
CIGATES = ROOT / "ci" / "gates"
SOURCE_LIB = re.compile(r"lib/([a-z0-9_-]+)\.sh")
FUNC_DEF = re.compile(r"^([a-z][a-z0-9_]*)\(\)", re.MULTILINE)
# 命令位：行首，或 if/then/elif/else/while/until/do 之后，或 && || ; | 之后。
# 名字后紧跟 = 的是变量赋值（`bad=0` 这类累加器），不是函数调用；
# 不排除会把「门禁自己记账」误报成「调用了没 source 的库函数」。
FUNC_CALL = re.compile(
    r"(?:^|[;&|]\s*|\b(?:if|then|elif|else|while|until|do)\s+)"
    r"([a-z][a-z0-9_]*)(?![\w=])", re.MULTILINE)

# 权限与并发：只看列 0 的顶层键，避免把 job 内同名片段当成顶层声明。
TOP_PERM = re.compile(r"^permissions:", re.MULTILINE)
PERM_WILD = re.compile(
    r"^\s*(?:permissions:\s*write-all|\*:\s*(?:read|write))\s*$", re.MULTILINE)
TOP_CONC = re.compile(r"^concurrency:", re.MULTILINE)
CANCEL_IN = re.compile(r"^  cancel-in-progress:\s*(.+?)\s*$", re.MULTILINE)
PR_TRIGGER = re.compile(r"^  pull_request(?:_target)?:", re.MULTILINE)
JOB_TIMEOUT = re.compile(r"^    timeout-minutes:\s*\S")

# 门禁引用：ci/ci.sh 后面紧跟的门禁名（``--list``、``-h`` 等选项不算）。
GATE_REF = re.compile(r"ci/ci\.sh\s+([a-z][a-z0-9-]*)")
GATE_USAGE = re.compile(r"^# 用法:", re.MULTILINE)

# 供应链：uses: 只认 40 位提交 SHA，且要带 ``# <版本>`` 溯源注释。
USES = re.compile(r"^(\s*)(?:- )?uses:\s*(\S+)\s*(#\s*\S+)?\s*$")
PINNED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*@[0-9a-f]{40}$")

# 运行器：只认钉版镜像标签（ubuntu-24.04）。浮动标签会在本仓一行未改的
# 情况下换掉编译器、shellcheck 与 Python 版本，门禁结论就取决于跑的那天。
RUNS_ON = re.compile(r"^\s*runs-on:\s*(\S+)\s*$", re.MULTILINE)
RUNNER_TAG = re.compile(r"^[a-z]+-\d{2}\.\d{2}$")

# 运行器版本的唯一事实源：nightly 契约清单的 runner.label。
NIGHTLY = ROOT / "tools" / "nightly" / "manifest.yaml"

# 多远程同 sha 门禁的调用点：命名空间（heads/tags）只能由调用方给出。
REMOTE_SYNC = "ci/ci.sh remote-sync"

# 形状推语义：把「名字长什么样」当成「它是什么」。托管与否只有清单的字段
# 能回答，标签形状与语义之间没有蕴含关系。
LATEST_SHAPE = [
    (re.compile(r"""\.endswith\(\s*['"]-latest['"]"""), "endswith('-latest')"),
    (re.compile(r"""\bin\s+['"]?\*-latest"""), "case 匹配 *-latest"),
    (re.compile(r"""[=!]=\s*['"]?\*-latest"""), "比较 *-latest"),
    (re.compile(r"""(?:contains|startsWith|endsWith)\([^)]*['"]-latest['"]"""),
     "表达式 contains/endsWith '-latest'"),
    (re.compile(r"""['"]-latest['"]\s+in\b"""), "'-latest' in"),
]

# 文件名词族：*-verify 是阻断型门禁，其余只能是生命周期名。
LIFECYCLE = ("image-build", "nightly", "release", "payload-seed")

# 文档一致性：模块 docstring 的规则号 ↔ README 第 4 节表格首列的规则号。
RULE_DOC = re.compile(r"^(\d+)\. ", re.MULTILINE)
RULE_ROW = re.compile(r"^\|\s*([\d,\s]+?)\s*\|")


def count_inline(text: str) -> int:
    """统计 ``run: |`` / ``run: >`` 块数（单行 ``run: cmd`` 不计）。"""
    lines = text.splitlines()
    n = 0
    i = 0
    while i < len(lines):
        m = RUN_BLOCK.match(lines[i])
        if not m:
            i += 1
            continue
        n += 1
        indent = len(m.group(1))
        i += 1
        while i < len(lines):
            s = lines[i]
            if s.strip() and len(s) - len(s.lstrip()) <= indent:
                break
            i += 1
    return n


def job_blocks(text: str) -> list[tuple[str, int, list[str]]]:
    """按 ``jobs:`` 解析一级 job：返回 (job 名, 行号, 该 job 的正文行)。"""
    lines = text.splitlines()
    try:
        start = next(i for i, s in enumerate(lines) if s.rstrip() == "jobs:")
    except StopIteration:
        return []
    out: list[tuple[str, int, list[str]]] = []
    cur: tuple[str, int, list[str]] | None = None
    for i in range(start + 1, len(lines)):
        s = lines[i]
        if not s.strip():
            continue
        if not s.startswith(" "):
            break
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", s)
        if m:
            if cur is not None:
                out.append(cur)
            cur = (m.group(1), i + 1, [])
        elif cur is not None:
            cur[2].append(s)
    if cur is not None:
        out.append(cur)
    return out


def count_jobs(text: str) -> int:
    """统计 ``jobs:`` 下的一级 job 名。"""
    return len(job_blocks(text))


def check_policy(name: str, text: str) -> list[str]:
    """最小权限、job 级超时、PR 并发：三条都是「缺了就静默退化」的默认值。"""
    errors: list[str] = []
    if not TOP_PERM.search(text):
        errors.append(f"{name}: 缺少顶层 permissions:（须显式声明 contents: read 起的权限集）")
    for no, s in enumerate(text.splitlines(), 1):
        if PERM_WILD.search(s):
            errors.append(f"{name}:{no}: 权限通配（{s.strip()}），须逐项列出")
    for job, no, body in job_blocks(text):
        if not any(JOB_TIMEOUT.match(s) for s in body):
            errors.append(f"{name}:{no}: job {job} 缺少 timeout-minutes（默认 360 分钟等于无上限）")
    if PR_TRIGGER.search(text):
        if not TOP_CONC.search(text):
            errors.append(f"{name}: PR 触发但缺少顶层 concurrency:")
        elif not (m := CANCEL_IN.search(text)) or m.group(1) != "true":
            errors.append(f"{name}: PR 触发的 concurrency 须 cancel-in-progress: true")
    return errors


def load_budgets() -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for raw in BUDGETS.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3:
            raise SystemExit(f"ci/workflow-budgets.txt 格式错误: {raw!r}")
        name, inline, jobs = parts
        out[name] = (int(inline), int(jobs))
    return out


def head_inline(name: str) -> int | None:
    """git HEAD 中同一文件的内联块数；不在版本控制里则返回 None。"""
    try:
        blob = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:.github/workflows/{name}"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return count_inline(blob)


def trigger_paths(text: str, key: str) -> list[str]:
    """取出某个触发器下 ``paths:`` 的条目。

    只认同级缩进的键切换，不引入 YAML 解析器：workflow 的实际形态是手写的
    四空格缩进，PyYAML 会把 ``on`` 读成布尔真值，反而多一层歧义。
    """
    lines = text.splitlines()
    try:
        start = next(i for i, s in enumerate(lines)
                     if re.match(rf"^  {key}:\s*$", s))
    except StopIteration:
        return []
    out: list[str] = []
    in_paths = False
    for s in lines[start + 1:]:
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        if not s.startswith("  ") or re.match(r"^  \S", s):
            break
        if re.match(r"^\s+paths:\s*$", s):
            in_paths = True
            continue
        if re.match(r"^\s+[\w-]+:", s):
            in_paths = False
            continue
        m = re.match(r"^\s+-\s*'?([^'\s]+)'?\s*$", s)
        if in_paths and m:
            out.append(m.group(1))
    return out


def check_triggers(name: str, text: str) -> list[str]:
    """PR 判据必须是 post-merge 判据的子集，且不得落进子模块内部。"""
    pr = trigger_paths(text, "pull_request")
    push = trigger_paths(text, "push")
    errors = [
        f"{name}: pull_request 判据 {p} 未进入 push 判据（合并后不再校验）"
        for p in pr if push and p not in push
    ]
    for key, paths in (("pull_request", pr), ("push", push)):
        for p in paths:
            for d in submodule_paths():
                # 列子模块目录名本身是对的（gitlink 指针变化就长这样），
                # 错的只有伸进内部的 glob。
                if p.startswith(d + "/"):
                    errors.append(
                        f"{name}: {key} 判据 {p} 落在子模块 {d} 内部，"
                        "父仓 diff 只呈现目录名，该 glob 永不命中")
    return errors


def submodule_paths() -> list[str]:
    """父仓子模块目录名：.gitmodules 是唯一事实源。"""
    path = ROOT / ".gitmodules"
    if not path.exists():
        return []
    return re.findall(r"^\s*path\s*=\s*(\S+)\s*$", path.read_text(), re.MULTILINE)


def check_name(name: str, text: str) -> list[str]:
    """命名约定：kebab-case 文件名，且顶层 ``name:`` 与文件同名词。"""
    if not NAME_FILE.match(name):
        return [f"{name}: 文件名须为 kebab-case 的 <scope>[-<scope>…].yml"]
    stem = name[:-4]
    m = NAME_TOP.search(text)
    if m is None:
        return [f"{name}: 缺少顶层 name:（须等于文件名 {stem}）"]
    if m.group(1) != stem:
        return [f"{name}: 顶层 name: 为 {m.group(1)}，须等于文件名 {stem}"]
    return []


def check_gate_libs() -> list[str]:
    """门禁只能调用它 source 的库函数，缺失会在构建中途才暴露。"""
    home: dict[str, str] = {}
    for path in CILIB.glob("*.sh"):
        for name in FUNC_DEF.findall(path.read_text()):
            home.setdefault(name, path.name)
    errors: list[str] = []
    scripts = sorted(CIGATES.glob("*.sh")) + [ROOT / "ci" / "ci.sh"]
    for path in scripts:
        text = path.read_text()
        body = "\n".join(
            "" if line.lstrip().startswith("#") else line
            for line in text.splitlines())
        sourced = set(SOURCE_LIB.findall(text))
        local = set(FUNC_DEF.findall(text))
        rel = path.relative_to(ROOT)
        for name in sorted(set(FUNC_CALL.findall(body))):
            lib = home.get(name)
            if lib is None or name in local or lib[:-3] in sourced:
                continue
            errors.append(f"{rel}: 调用 {name}() 但未 source ci/lib/{lib}")
    return errors


def check_gate_refs(name: str, text: str) -> list[str]:
    """引用的门禁必须存在且自带 ``# 用法:`` 头（分发器按文件名解析）。"""
    errors: list[str] = []
    for gate in sorted(set(GATE_REF.findall(text))):
        path = CIGATES / f"{gate}.sh"
        if not path.exists():
            errors.append(
                f"{name}: 调用 ci/ci.sh {gate}，但 ci/gates/{gate}.sh 不存在"
                "（分发器会以 exit 2 终止）")
            continue
        if not GATE_USAGE.search(path.read_text()):
            errors.append(
                f"{name}: 门禁 {gate} 缺少「# 用法:」头，ci/ci.sh --list 会漏项")
    return errors


def check_pins(name: str, text: str) -> list[str]:
    """每个 uses: 钉 40 位 SHA 并带版本注释；本地 action 与镜像除外。"""
    errors: list[str] = []
    for no, s in enumerate(text.splitlines(), 1):
        m = USES.match(s)
        if m is None:
            continue
        ref = m.group(2)
        if ref.startswith(("./", "docker://")):
            continue
        if not PINNED.match(ref):
            errors.append(
                f"{name}:{no}: {ref} 未钉到 40 位提交 SHA（浮动 tag 可被上游重指）")
            continue
        if m.group(3) is None:
            errors.append(
                f"{name}:{no}: {ref} 缺少 `# <版本>` 注释，升级时无从判断基线")
    return errors


def check_runner(name: str, text: str) -> list[str]:
    """运行器必须钉版：浮动标签会在两次运行之间换掉整条工具链。"""
    errors: list[str] = []
    for no, s in enumerate(text.splitlines(), 1):
        m = RUNS_ON.match(s)
        if m is None:
            continue
        tag = m.group(1)
        if not RUNNER_TAG.match(tag):
            errors.append(
                f"{name}:{no}: runs-on: {tag} 未钉版（须为 <发行版>-<YY.MM>，"
                "浮动标签会换掉编译器与 shellcheck 版本）")
    return errors


def runner_label() -> str | None:
    """nightly 契约清单里 ``runner.label`` 的取值；清单缺失或无此键返回 None。"""
    if not NIGHTLY.exists():
        return None
    in_runner = False
    for s in NIGHTLY.read_text().splitlines():
        if re.match(r"^runner:\s*$", s):
            in_runner = True
            continue
        if not in_runner:
            continue
        if s.strip() and not s.startswith(" "):
            break
        m = re.match(r"^  label:\s*(\S+)\s*$", s)
        if m is not None:
            return m.group(1)
    return None


def wf_label_rule(name: str, text: str, label: str) -> list[str]:
    """runs-on 必须与清单的 runner.label 逐字相等：同一事实只能有一个取值。"""
    errors: list[str] = []
    for no, s in enumerate(text.splitlines(), 1):
        m = RUNS_ON.match(s)
        if m is None or m.group(1) == label:
            continue
        errors.append(
            f"{name}:{no}: runs-on: {m.group(1)} 与 tools/nightly/manifest.yaml 的 "
            f"runner.label={label} 不一致（两处各自演化，改一侧不会带出另一侧）")
    return errors


def shape_sites() -> list[Path]:
    """形状判定的扫描面：门禁、库、工具与 workflow 本体（不含自测）。"""
    out = sorted(CIGATES.glob("*.sh")) + sorted(CILIB.glob("*.sh"))
    out += [p for p in sorted(ROOT.glob("tools/**/*.py"))
            if "tests" not in p.parts and p.name != "check-workflows.py"]
    return out + sorted(WORKFLOWS.glob("*.yml"))


def check_shape(name: str, text: str) -> list[str]:
    """禁止由名称形状推语义：托管与否只能读清单字段。

    按整段文本匹配而不是整行：``case "$label" in`` 的匹配式在下一行，
    逐行扫描会漏掉这种最常见的 shell 写法。
    """
    hits: set[tuple[int, str]] = set()
    for pat, what in LATEST_SHAPE:
        for m in pat.finditer(text):
            hits.add((text.count("\n", 0, m.start()) + 1, what))
    return [f"{name}:{no}: 由标签形状推语义（{what}），"
            "托管性须读 tools/nightly/manifest.yaml 的字段"
            for no, what in sorted(hits)]


def check_family(name: str, text: str) -> list[str]:
    """命名即语义：*-verify 必须阻断合并，其余只能是生命周期名。"""
    stem = name[:-4]
    if stem.endswith("-verify"):
        if not PR_TRIGGER.search(text):
            return [f"{name}: 名为 *-verify 却不由 pull_request 触发，"
                    "其结论不进入合并判断"]
        return []
    if stem not in LIFECYCLE:
        return [f"{name}: 非阻断型流水线须取生命周期名 "
                f"{' / '.join(LIFECYCLE)}，否则读名字判断不出它何时跑"]
    return []


def check_ref_kind(name: str, text: str) -> list[str]:
    """remote-sync 的 ref 命名空间必须由调用方写明。

    门禁缺省查 ``refs/heads/<ref>``；``github.ref_name`` 已把 ``refs/`` 前缀
    剥掉，标签派发时它会去查不存在的同名分支，判负成「无此分支」。
    """
    errors: list[str] = []
    for no, s in enumerate(text.splitlines(), 1):
        if REMOTE_SYNC not in s or s.lstrip().startswith("#"):
            continue
        if "--kind" in s:
            continue
        errors.append(
            f"{name}:{no}: remote-sync 未声明 --kind（缺省查 refs/heads/，"
            "标签派发必然判「无此分支」）")
    return errors


def rule_numbers() -> list[int]:
    """本文件 docstring 登记的规则号。"""
    return sorted({int(n) for n in RULE_DOC.findall(__doc__ or "")})


def check_doc(name: str = "README.md") -> list[str]:
    """README 第 4 节的规则号必须覆盖脚本全部规则，且不重号。"""
    path = WORKFLOWS / name
    if not path.exists():
        return [f"{name}: 文件不存在，规则表无从对账"]
    declared = rule_numbers()
    lines = path.read_text().splitlines()
    try:
        start = next(i for i, s in enumerate(lines) if s.startswith("## 4."))
    except StopIteration:
        return [f"{name}: 缺少第 4 节规则表"]
    seen: list[int] = []
    for s in lines[start + 1:]:
        if s.startswith("## "):
            break
        m = RULE_ROW.match(s)
        if m is not None:
            seen.extend(int(x) for x in re.findall(r"\d+", m.group(1)))
    errors = [f"{name}: 规则 {n} 未出现在第 4 节表格（新增规则须同步登记）"
              for n in declared if n not in seen]
    errors += [f"{name}: 第 4 节表格登记了不存在的规则 {n}"
               for n in seen if n not in declared]
    dup = sorted({n for n in seen if seen.count(n) > 1})
    if dup:
        errors.append(f"{name}: 第 4 节表格重号 {', '.join(map(str, dup))}")
    return errors


def main() -> int:
    budgets = load_budgets()
    files = sorted(p.name for p in WORKFLOWS.glob("*.yml"))
    errors: list[str] = check_gate_libs()
    errors.extend(check_doc())
    label = runner_label()
    if label is None:
        errors.append(
            "tools/nightly/manifest.yaml: 未登记 runner.label，runs-on 无对照基准")

    for path in shape_sites():
        errors.extend(
            check_shape(str(path.relative_to(ROOT)), path.read_text()))

    for name in files:
        if name not in budgets:
            errors.append(f"{name}: 未登记预算（请写入 ci/workflow-budgets.txt）")
    for name in budgets:
        if name not in files:
            errors.append(f"{name}: 预算已登记但文件不存在")

    total = 0
    for name in files:
        text = (WORKFLOWS / name).read_text()
        errors.extend(check_name(name, text))
        errors.extend(check_family(name, text))
        errors.extend(check_runner(name, text))
        if label is not None:
            errors.extend(wf_label_rule(name, text, label))
        errors.extend(check_policy(name, text))
        errors.extend(check_triggers(name, text))
        errors.extend(check_gate_refs(name, text))
        errors.extend(check_ref_kind(name, text))
        errors.extend(check_pins(name, text))
        inline = count_inline(text)
        jobs = count_jobs(text)
        total += inline
        if name not in budgets:
            continue
        max_inline, max_jobs = budgets[name]

        if inline > max_inline:
            errors.append(f"{name}: 内联 run 块 {inline} > 预算 {max_inline}")
        if jobs > max_jobs:
            errors.append(f"{name}: job 数 {jobs} > 预算 {max_jobs}")
        if jobs == 0:
            errors.append(f"{name}: 没有解析到任何 job")

        base = head_inline(name)
        if base is not None and inline > base and inline > max_inline:
            errors.append(f"{name}: 内联块由 HEAD 的 {base} 增至 {inline}（棘轮回退）")

        if max_inline == 0:
            for pat, gate in FORBIDDEN:
                for no, s in enumerate(text.splitlines(), 1):
                    if pat.search(s):
                        errors.append(
                            f"{name}:{no}: 已收编逻辑回流（{pat.pattern}），应走 {gate}")

        for pat, entry in ALWAYS_FORBIDDEN:
            for no, s in enumerate(text.splitlines(), 1):
                if pat.search(s):
                    errors.append(
                        f"{name}:{no}: 直接调用 apt-get，应走 {entry}")

    if errors:
        print("workflow 薄化与命名检查未通过:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"workflow 薄化与命名检查通过: {len(files)} 个文件, 内联 run 块合计 {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
