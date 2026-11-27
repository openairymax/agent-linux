#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/unit/test_header_lint.sh — ci/checks/header_lint.py 的分支回归
#
# 真实树在本次提交后才合规，判定逻辑本身只能靠负例驱动：缺许可证、缺版权、
# 声明写在字符串里、tests/ 与 vendor/ 的口径差异，逐条断言退出码与判据名。
#
# 用法: bash ci/tests/unit/test_header_lint.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/checks" "${FX}/ci/lib" "${FX}/tools/pkg/tests" "${FX}/tools/pkg/vendor"
cp "${ROOT}/ci/checks/header_lint.py" "${FX}/ci/checks/header_lint.py"
# 夹具整目录复制判定器的依赖包，而不是点名几个模块：点名的那份清单与
# 判定器的 import 图是两处事实，判定器多引一个模块（0.0.5 引入 lint_drive
# 时就是如此），夹具立刻以 ImportError 报错，而报错看起来像判据缺陷。
cp "${ROOT}/ci/lib/"*.py "${FX}/ci/lib/"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

run()
{
    ( cd "${FX}" && env -u LD_LIBRARY_PATH python3 ci/checks/header_lint.py "$@" 2>&1 )
}

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

tree()
{
    rm -rf "${FX}/tools/pkg"
    mkdir -p "${FX}/tools/pkg/tests" "${FX}/tools/pkg/vendor"
}

tree
cat >"${FX}/tools/pkg/good.sh" <<'EOF'
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
:
EOF
out="$(run tools)"
check "两行齐全判通过" 0 "$?" "${out}" "0 issue(s)"

tree
cat >"${FX}/tools/pkg/nolic.sh" <<'EOF'
#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
:
EOF
out="$(run tools)"
check "缺许可证判拒绝" 1 "$?" "${out}" "H001"

tree
cat >"${FX}/tools/pkg/nocpy.sh" <<'EOF'
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
:
EOF
out="$(run tools)"
check "缺版权行判拒绝" 1 "$?" "${out}" "H002"

tree
cat >"${FX}/tools/pkg/copy.sh" <<'EOF'
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
:
EOF
out="$(run tools)"
check "传统版权行等同 SPDX 版权" 0 "$?" "${out}" "0 issue(s)"

tree
cat >"${FX}/tools/pkg/rust.rs" <<'EOF'
// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
fn main() {}
EOF
out="$(run tools)"
check "双斜线注释同样识别" 0 "$?" "${out}" "0 issue(s)"

tree
cat >"${FX}/tools/pkg/str.sh" <<'EOF'
#!/usr/bin/env bash
TEXT="# SPDX-License-Identifier: MIT"
OTHER="# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd."
EOF
out="$(run tools)"
check "字符串里的声明不构成文件头" 1 "$?" "${out}" "H001"

tree
cat >"${FX}/tools/pkg/tests/case.sh" <<'EOF'
#!/usr/bin/env bash
:
EOF
out="$(run tools)"
check "tests 目录同样要求文件头" 1 "$?" "${out}" "H002"

tree
cat >"${FX}/tools/pkg/vendor/lib.sh" <<'EOF'
#!/usr/bin/env bash
:
EOF
out="$(run tools)"
check "vendor 目录不要求文件头" 0 "$?" "${out}" "0 issue(s)"

tree
out="$(run)"
check "缺省根缺少时不假绿" 0 "$?" "${out}" "0 issue(s)"

tree
cat >"${FX}/ci/Makefile.py" <<'EOF'
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
EOF
out="$(run)"
rc=$?
# 缺省根必须真的扫到文件，否则「0 issue(s)」只是空扫描的假绿。夹具里
# ci/lib 的模块数会随判定器的依赖增长，钉死总数等于每加一个模块就要改
# 一次断言；这里只要求扫到的文件数为正。
n="$(printf '%s\n' "${out}" | awk '/^header: / { print $2 + 0; exit }')"
if [ "${rc}" -eq 0 ] && [ "${n:-0}" -gt 0 ]; then
    ok "缺省根扫描 ci"
else
    bad "缺省根扫描 ci（rc=${rc}，扫到 ${n:-0} 个文件）"
    printf '%s\n' "${out}" >&2
fi

if [ "${fails}" -ne 0 ]; then
    printf 'test_header_lint: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_header_lint: 全部通过\n'
