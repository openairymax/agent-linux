# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_runner — 实验执行器主循环与断点续跑。

执行器把"准备、执行、取证"三步串成单一主循环：每步产物由步骤函数返回，
经证据库原子落盘后立即登记摘要；判定步只读已登记证据，输出写入
``step-04-verdict.json``，最后以当前链头封口。任何步骤都不允许旁路
读写证据目录，落盘与封口全部经由 `Store`。

复核步以 `Review` 回调注入：裁决之后、封口之前，回调拿到已判定结果
并返回 `Ruling`；执行器只校验结论不丢原有证据、复核记录只落在
``step-05-review.json``，随后按复核结论封口。只含准备步的计划用于
实机缺失的诚实降级，此时不落判定文件，由复核步直接给出 BLOCKED。

断点续跑以证据文件是否存在为准：已封口包直接复用结论；未封口包按
已有证据跳过对应步骤；采集步若只落下部分产物则拒绝续跑，避免两次
运行的数据混入同一证据包。证据包目录名即实验编号，跨日期全局唯一。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §3.1 六步规程、§3.2 判定状态机、§3.4 证据包规范
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9.1 模块 V（TV-01-06 实验执行器主循环与断点续跑）

Example:
    >>> plan = ExpPlan("exp-001", (th,), (
    ...     (Step.PREPARE, prepare), (Step.RUN, run), (Step.CAPTURE, cap),
    ... ))
    >>> Runner(store, PackJudge(), "qemu-x86_64", "b" * 64).run(plan).verdict
    <Verdict.PASS: 'PASS'>

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Final

from osaccrt_evidence import STEP_LAYOUT, Manifest, check_date
from osaccrt_judge import CAPTURE_ROOT, missing_keys
from osaccrt_model import (
    SHA256_RE,
    Evidence,
    ExpResult,
    Judge,
    Step,
    Threshold,
    Verdict,
    need_relpath,
    need_text,
)
from osaccrt_store import Store

__all__ = ["ExpPlan", "Outcome", "Review", "Ruling", "Runner", "StepFn"]

StepFn = Callable[[Path], Mapping[str, bytes]]

_ORDER: Final[tuple[Step, ...]] = (Step.PREPARE, Step.RUN, Step.CAPTURE)
_CAPTURE: Final[str] = CAPTURE_ROOT + "/"
_REVIEW: Final[str] = STEP_LAYOUT[Step.REVIEW]
_VERDICT: Final[str] = STEP_LAYOUT[Step.VERDICT]
_V_KEYS: Final[frozenset[str]] = frozenset(
    {"exp_id", "keys", "missing", "verdict"}
)


@dataclass(frozen=True)
class ExpPlan:
    """单实验执行计划：实验编号、阈值表与有序步骤函数。"""

    exp_id: str
    thresholds: tuple[Threshold, ...]
    steps: tuple[tuple[Step, StepFn], ...]

    def __post_init__(self) -> None:
        ExpResult(self.exp_id, self.thresholds)
        _need_steps(self.steps)


@dataclass(frozen=True)
class Ruling:
    """复核结论：更新后的结果与需要落盘的复核记录。"""

    result: ExpResult
    records: Mapping[str, bytes] = field(default_factory=dict)


Review = Callable[[ExpResult], Ruling]


@dataclass(frozen=True)
class Outcome:
    """执行结果：判定、封口链头，以及是否复用了既有证据包。"""

    exp_id: str
    verdict: Verdict
    chain: str
    resumed: bool


class Runner:
    """按计划执行实验并在证据库中封口。"""

    def __init__(self, store: Store, judge: Judge, host: str,
                 image_sha256: str, day: str | None = None) -> None:
        need_text(host, "host")
        if not SHA256_RE.fullmatch(image_sha256):
            raise ValueError("image_sha256 must be 64 lowercase hex")
        self.store = store
        self.judge = judge
        self.host = host
        self.image_sha256 = image_sha256
        self.day = day or date.today().isoformat()
        check_date(self.day)

    def run(self, plan: ExpPlan,
            review: Review | None = None) -> Outcome:
        """执行单个实验；已封口则复用结论，未封口则续跑缺口步骤。"""
        if not isinstance(plan, ExpPlan):
            raise TypeError("plan must be ExpPlan")
        if not _complete(plan) and review is None:
            raise ValueError("partial plan requires a review")
        sealed = self.store.find(plan.exp_id)
        if sealed is not None:
            return self._reuse(plan, sealed)
        result = ExpResult(plan.exp_id, plan.thresholds)
        pack, resumed = self._pack_dir(plan.exp_id)
        for step, fn in plan.steps:
            self._step(plan, step, fn, result, pack)
        if _complete(plan):
            self._decide(result, pack)
        if review is not None:
            result = self._review(plan, result, review, pack)
        man = Manifest(self.day, self.host, self.image_sha256, result,
                       prev_chain_head=self.store.head())
        chain = self.store.seal(man).read_text(encoding="ascii").strip()
        return Outcome(plan.exp_id, _ruled(result), chain, resumed)

    def run_all(self, plans: Sequence[ExpPlan],
                review: Review | None = None) -> tuple[Outcome, ...]:
        """按序执行一组实验；编号重复时在任何取证前拒绝。"""
        items = tuple(plans)
        _need_unique(items)
        return tuple(self.run(plan, review) for plan in items)

    def _review(self, plan: ExpPlan, result: ExpResult, review: Review,
                pack: Path) -> ExpResult:
        """执行复核步：校验结论不丢证据，再把复核记录落盘。"""
        if not callable(review):
            raise TypeError("review must be callable")
        ruling = review(result)
        if not isinstance(ruling, Ruling):
            raise TypeError("review must return Ruling")
        _need_ruling(plan, result, ruling.result)
        return self._records(plan, ruling, pack)

    def _records(self, plan: ExpPlan, ruling: Ruling,
                 pack: Path) -> ExpResult:
        """复核记录只能写在复核步位置；已存在则要求内容一致。"""
        if not isinstance(ruling.records, Mapping):
            raise TypeError("review records must be a mapping")
        result = ruling.result
        for path, data in ruling.records.items():
            if path != _REVIEW:
                raise ValueError(f"review record must be {_REVIEW}: {path!r}")
            if not isinstance(data, bytes):
                raise TypeError("review record data must be bytes")
            digest = _same(self.store, self.day, plan.exp_id, pack, path, data)
            result.evidences.append(Evidence(path, digest, Step.REVIEW))
        return result

    def _pack_dir(self, exp_id: str) -> tuple[Path, bool]:
        """定位或新建证据包，返回目录与调用前是否已存在。"""
        pack = self.store.pack(self.day, exp_id)
        if pack.exists() and not pack.is_dir():
            raise ValueError(f"evidence pack is not a directory: {pack}")
        resumed = pack.is_dir()
        try:
            self.store.create(self.day, exp_id)
        except FileExistsError:
            pass
        return self.store.pack(self.day, exp_id), resumed

    def _step(self, plan: ExpPlan, step: Step, fn: StepFn,
              result: ExpResult, pack: Path) -> None:
        """执行一步：已有证据则跳过，否则落盘并登记摘要。"""
        found = self._existing(plan, step, pack)
        if found:
            result.evidences.extend(found)
            return
        data = fn(pack)
        for path in self._paths(step, data):
            digest = self.store.write(self.day, plan.exp_id, path, data[path])
            result.evidences.append(Evidence(path, digest, step))

    def _existing(self, plan: ExpPlan, step: Step,
                  pack: Path) -> list[Evidence]:
        """返回磁盘上已落盘的该步证据；采集步要求产物完整。"""
        if step is Step.CAPTURE:
            return self._capture(plan, pack)
        path = STEP_LAYOUT[step]
        target = pack / path
        if not target.exists():
            return []
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"{step.value} evidence is not a regular file")
        return [Evidence(path, self.store.digest(self.day, plan.exp_id, path),
                         step)]

    def _capture(self, plan: ExpPlan, pack: Path) -> list[Evidence]:
        """收集采集产物；部分产物不可续跑，避免两次运行混入。"""
        root = pack / CAPTURE_ROOT
        if not root.is_dir():
            return []
        files = _files(root)
        if not files:
            return []
        evidences = [
            Evidence(item.relative_to(pack).as_posix(),
                     self.store.digest(self.day, plan.exp_id,
                                       item.relative_to(pack).as_posix()),
                     Step.CAPTURE)
            for item in files
        ]
        gaps = missing_keys(
            ExpResult(plan.exp_id, plan.thresholds, list(evidences))
        )
        if gaps:
            raise ValueError(
                f"partial capture is not resumable, missing: {', '.join(gaps)}"
            )
        return evidences

    def _paths(self, step: Step,
               data: Mapping[str, bytes]) -> tuple[str, ...]:
        """校验步骤产物：类型、非空且落在该步约定位置。"""
        if not isinstance(data, Mapping):
            raise TypeError("step function must return a mapping")
        paths = tuple(data)
        if not paths:
            raise ValueError(f"{step.value} step produced no evidence")
        for path in paths:
            if not isinstance(path, str):
                raise TypeError("evidence path must be a string")
            if not isinstance(data[path], bytes):
                raise TypeError("evidence data must be bytes")
            _slot(step, path)
        return paths

    def _decide(self, result: ExpResult, pack: Path) -> Verdict:
        """判定并落盘；若已存在判定文件则原样接续，不重复裁决。"""
        found = self._recorded(result.exp_id, pack)
        if found is not None:
            evidence, verdict = found
            if set(result.steps) != set(_ORDER):
                raise ValueError(
                    "verdict recorded before evidence is complete"
                )
            result.verdict = verdict
            result.evidences.append(evidence)
            return verdict
        verdict = self.judge.decide(result)
        result.verdict = verdict
        payload = _verdict_json(result)
        digest = self.store.write(self.day, result.exp_id, _VERDICT, payload)
        result.evidences.append(Evidence(_VERDICT, digest, Step.VERDICT))
        return verdict

    def _recorded(self, exp_id: str,
                  pack: Path) -> tuple[Evidence, Verdict] | None:
        """读取上次中断前落盘的判定文件，严格校验其归属与字段。"""
        path = pack / _VERDICT
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise ValueError("verdict evidence is not a regular file")
        raw = _load_verdict(path.read_text(encoding="utf-8"))
        if raw["exp_id"] != exp_id:
            raise ValueError("verdict does not belong to this experiment")
        digest = self.store.digest(self.day, exp_id, _VERDICT)
        return Evidence(_VERDICT, digest, Step.VERDICT), _to_verdict(
            raw["verdict"]
        )

    def _reuse(self, plan: ExpPlan, man: Manifest) -> Outcome:
        """复用已封口结论；阈值或环境不一致时拒绝，避免张冠李戴。"""
        if man.result.thresholds != plan.thresholds:
            raise ValueError(
                f"sealed pack has a different threshold table: {plan.exp_id}"
            )
        if man.host != self.host or man.image_sha256 != self.image_sha256:
            raise ValueError(
                f"sealed pack was produced elsewhere: {plan.exp_id}"
            )
        verdict = man.result.verdict
        if verdict is None:
            raise ValueError(f"sealed pack has no verdict: {plan.exp_id}")
        return Outcome(plan.exp_id, verdict, man.chain_head, True)


def _same(store: Store, day: str, exp_id: str, pack: Path,
          path: str, data: bytes) -> str:
    """复核记录已存在时要求内容一致，否则原子落盘并返回摘要。"""
    if (pack / path).exists():
        digest = store.digest(day, exp_id, path)
        if digest != _sha(data):
            raise ValueError(f"review record mismatch: {path}")
        return digest
    return store.write(day, exp_id, path, data)


def _sha(data: bytes) -> str:
    """复核记录的规范摘要，用于判断重跑是否产出同一份记录。"""
    return hashlib.sha256(data).hexdigest()


def _files(root: Path) -> list[Path]:
    """列出采集目录下的普通文件；链接与特殊文件一律拒绝。"""
    files: list[Path] = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"evidence must not be a symlink: {item}")
        if item.is_file():
            files.append(item)
        elif not item.is_dir():
            raise ValueError(f"special file is not allowed: {item}")
    return files


def _slot(step: Step, path: str) -> None:
    """新建证据只能落在该步约定的文件或采集目录内。"""
    need_relpath(path)
    if step is Step.CAPTURE:
        if not path.startswith(_CAPTURE):
            raise ValueError(
                f"capture evidence must live under {CAPTURE_ROOT}/: {path!r}"
            )
        return
    base = STEP_LAYOUT[step]
    if path != base:
        raise ValueError(
            f"{step.value} evidence must live at {base}: {path!r}"
        )


def _verdict_json(result: ExpResult) -> bytes:
    """判定文件只记录结论与对账缺口，供复核与复现脚本引用。"""
    data = {
        "exp_id": result.exp_id,
        "keys": list(result.keys),
        "missing": list(missing_keys(result)),
        "verdict": result.verdict.value if result.verdict else "",
    }
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _load_verdict(text: str) -> dict[str, object]:
    """解析判定文件，拒绝语法错误、未知字段与类型不符。"""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"verdict is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("verdict must be a JSON object")
    if set(raw) != _V_KEYS:
        raise ValueError("verdict must have exp_id/keys/missing/verdict")
    if not isinstance(raw["exp_id"], str):
        raise ValueError("verdict exp_id must be a string")
    _need_strs(raw["keys"], "keys")
    _need_strs(raw["missing"], "missing")
    if not isinstance(raw["verdict"], str):
        raise ValueError("verdict value must be a string")
    return raw


def _need_strs(value: object, name: str) -> None:
    """列表字段必须全部为字符串。"""
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"verdict {name} must be a list of strings")


def _to_verdict(value: object) -> Verdict:
    """把判定文件中的字符串还原为四态枚举。"""
    try:
        return Verdict(value)
    except ValueError as exc:
        raise ValueError(f"invalid verdict: {value!r}") from exc


def _need_steps(items: tuple[tuple[Step, StepFn], ...]) -> None:
    """执行步骤必须是准备、执行、取证的有序前缀，且至少含准备步。"""
    if not isinstance(items, tuple) or not 0 < len(items) <= len(_ORDER):
        raise ValueError("steps must run PREPARE, RUN, CAPTURE in order")
    for (step, fn), want in zip(items, _ORDER):
        if step is not want or not callable(fn):
            raise ValueError("steps must run PREPARE, RUN, CAPTURE in order")


def _complete(plan: ExpPlan) -> bool:
    """三步是否齐全；只含准备步的计划留给复核步裁定 BLOCKED。"""
    return len(plan.steps) == len(_ORDER)


def _need_ruling(plan: ExpPlan, before: ExpResult,
                 after: ExpResult) -> None:
    """复核只能追加证据，不得改编号、改阈值或丢弃已取证条目。"""
    if not isinstance(after, ExpResult):
        raise TypeError("ruling result must be ExpResult")
    if after.exp_id != plan.exp_id:
        raise ValueError(f"ruling changed exp_id: {after.exp_id}")
    if after.thresholds != plan.thresholds:
        raise ValueError("ruling changed the threshold table")
    if after.verdict is None:
        raise ValueError("ruling must decide a verdict")
    kept = tuple(after.evidences[:len(before.evidences)])
    if kept != tuple(before.evidences):
        raise ValueError("ruling must keep collected evidence")


def _ruled(result: ExpResult) -> Verdict:
    """返回最终判定；未判定说明流程缺陷，直接拒绝封口。"""
    if result.verdict is None:
        raise ValueError(f"experiment is not ruled: {result.exp_id}")
    return result.verdict


def _need_unique(plans: tuple[ExpPlan, ...]) -> None:
    """一组计划内实验编号不得重复，避免互相覆盖证据包。"""
    for plan in plans:
        if not isinstance(plan, ExpPlan):
            raise TypeError("plans must contain ExpPlan items")
    ids = [plan.exp_id for plan in plans]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate exp_id in plans: {ids}")
