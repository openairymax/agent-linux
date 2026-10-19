# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_manifest — QEMU 测试农场清单的严格加载器。

读取 tools/airy-qemu/manifest.yaml，按固定 schema 构造 FarmManifest。
清单是「架构 × QEMU 机器 × kselftest 套件 × 并行预算」的唯一事实源
（SSoT），本模块只做严格解析与校验，不重复实现执行逻辑，也不对任何
非法输入做静默修正：未知字段、缺失字段、重复条目、非法取值一律以
FarmConfigError 拒绝，避免拼写错误或漂移在运行期才暴露。

权威依据：
  docs-linux/70-build-system/03-ci-cd-pipeline.md §5.3
    OS-TEST-013~022（kselftest 默认在 QEMU 运行、TAP 解析、flaky 登记）
  docs-linux/80-testing/02-kselftest.md
    OS-STD-009（测试超时默认 60 秒）
  .github/workflows/ci-kernel.yml（kselftest 子集基线）

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

from pathlib import Path
from typing import Final

try:  # pragma: no cover - 环境缺失时在 load_manifest 统一报错
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# 2. 本工具模块导入
from farm_types import (
    DEFAULT_SUITE_TIMEOUT_S,
    FARM_VERSION,
    Arch,
    FarmConfigError,
    FarmLimits,
    FarmManifest,
    Machine,
    Suite,
)

# 3. 公开导出
__all__ = ["load_manifest", "parse_manifest"]

# 4. 清单 schema（未知字段一律拒绝）
MANIFEST_KEYS: Final[frozenset[str]] = frozenset(
    {"version", "machines", "suites", "limits"}
)
MACHINE_KEYS: Final[frozenset[str]] = frozenset(
    {"arch", "machine", "cpu", "console", "binary"}
)
MACHINE_REQUIRED: Final[frozenset[str]] = frozenset(
    {"arch", "machine", "cpu", "console"}
)
SUITE_KEYS: Final[frozenset[str]] = frozenset({"name", "timeout_s"})
SUITE_REQUIRED: Final[frozenset[str]] = frozenset({"name"})
LIMIT_KEYS: Final[frozenset[str]] = frozenset({"max_jobs", "mem_mb", "cpus"})

# 5. 清单 schema 主版本（与农场契约主版本保持一致）
_MANIFEST_MAJOR: Final[str] = FARM_VERSION.split(".", 1)[0]


def _cfg(message: str) -> FarmConfigError:
    """构造统一错误码的配置异常。"""
    return FarmConfigError(message)


def load_manifest(path: str | Path) -> FarmManifest:
    """从 YAML 文件加载农场清单。

    Args:
        path: 清单文件路径。

    Returns:
        校验通过的 FarmManifest。

    Raises:
        FarmConfigError: 文件不可读、YAML 非法或 schema 不合法。
    """
    manifest_path = Path(path)
    if yaml is None:
        raise _cfg("PyYAML is required to load the farm manifest")
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _cfg(f"cannot read manifest {manifest_path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _cfg(f"{manifest_path}: invalid YAML: {exc}") from exc
    return parse_manifest(data, str(manifest_path))


def parse_manifest(data: object, source: str = "<memory>") -> FarmManifest:
    """校验已解析的 YAML 数据并构造清单。

    Args:
        data: YAML safe_load 的结果。
        source: 诊断信息中的来源标识（通常是文件路径）。

    Returns:
        校验通过的 FarmManifest。

    Raises:
        FarmConfigError: schema 不合法。
    """
    try:
        return _parse(data)
    except FarmConfigError as exc:
        raise _cfg(f"{source}: {exc.message}") from exc
    except ValueError as exc:
        raise _cfg(f"{source}: {exc}") from exc


def _parse(data: object) -> FarmManifest:
    """按 schema 解析清单主体。"""
    root = _mapping(data, "manifest")
    _reject_unknown(root, MANIFEST_KEYS, "manifest")
    _need_keys(root, {"version", "machines", "suites"}, "manifest")
    version = _text(root, "version", "manifest")
    _check_version(version)
    machines = tuple(
        _machine(item, index)
        for index, item in enumerate(_items(root, "machines"))
    )
    suites = tuple(
        _suite(item, index) for index, item in enumerate(_items(root, "suites"))
    )
    limits = _limits(root.get("limits"), "manifest.limits")
    return FarmManifest(version, machines, suites, limits)


def _check_version(version: str) -> None:
    """拒绝与农场契约主版本不一致的清单。"""
    if version.split(".", 1)[0] != _MANIFEST_MAJOR:
        raise _cfg(
            f"manifest version {version!r} is not supported "
            f"by farm {FARM_VERSION}"
        )


def _machine(item: object, index: int) -> Machine:
    """解析单条机器定义。"""
    where = f"machines[{index}]"
    data = _mapping(item, where)
    _reject_unknown(data, MACHINE_KEYS, where)
    _need_keys(data, MACHINE_REQUIRED, where)
    arch_name = _text(data, "arch", where)
    try:
        arch = Arch(arch_name)
    except ValueError as exc:
        raise _cfg(f"{where}.arch: unsupported arch {arch_name!r}") from exc
    binary = data.get("binary", "")
    if not isinstance(binary, str):
        raise _cfg(f"{where}.binary must be a string")
    return Machine(
        arch,
        _text(data, "machine", where),
        _text(data, "cpu", where),
        _text(data, "console", where),
        binary,
    )


def _suite(item: object, index: int) -> Suite:
    """解析单条套件定义。"""
    where = f"suites[{index}]"
    data = _mapping(item, where)
    _reject_unknown(data, SUITE_KEYS, where)
    _need_keys(data, SUITE_REQUIRED, where)
    timeout_s = _positive(data, "timeout_s", where, DEFAULT_SUITE_TIMEOUT_S)
    return Suite(_text(data, "name", where), timeout_s)


def _limits(value: object, where: str) -> FarmLimits:
    """解析可选的并行预算，缺省时取类型默认值。"""
    if value is None:
        return FarmLimits()
    data = _mapping(value, where)
    _reject_unknown(data, LIMIT_KEYS, where)
    defaults = FarmLimits()
    return FarmLimits(
        _positive(data, "max_jobs", where, defaults.max_jobs),
        _positive(data, "mem_mb", where, defaults.mem_mb),
        _positive(data, "cpus", where, defaults.cpus),
    )


def _mapping(value: object, where: str) -> dict[str, object]:
    """要求取值为映射。"""
    if not isinstance(value, dict):
        raise _cfg(f"{where} must be a mapping")
    return value


def _items(mapping: dict[str, object], key: str) -> list[object]:
    """要求取值为非空列表。"""
    value = mapping.get(key)
    if not isinstance(value, list) or not value:
        raise _cfg(f"manifest.{key} must be a non-empty list")
    return value


def _reject_unknown(
    mapping: dict[str, object], allowed: frozenset[str], where: str
) -> None:
    """拒绝 schema 之外的字段。"""
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise _cfg(f"{where}: unknown keys {unknown}")


def _need_keys(
    mapping: dict[str, object], required: frozenset[str], where: str
) -> None:
    """要求必填字段齐备。"""
    missing = sorted(required - set(mapping))
    if missing:
        raise _cfg(f"{where}: missing keys {missing}")


def _text(mapping: dict[str, object], key: str, where: str) -> str:
    """要求字段为非空字符串。"""
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _cfg(f"{where}.{key} must be a non-empty string")
    return value


def _positive(
    mapping: dict[str, object], key: str, where: str, default: int
) -> int:
    """读取可选正整数字段（显式拒绝 bool）。"""
    if key not in mapping:
        return default
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _cfg(f"{where}.{key} must be a positive integer")
    return value
