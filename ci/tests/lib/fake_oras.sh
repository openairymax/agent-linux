#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/lib/fake_oras.sh — 把制品落在本地目录的假 oras（测试夹具）
#
# 发布与预热两个 gate 的回归都用它：真实 registry 不可达，oras 也不保证
# 安装。存储布局是 FAKE_STORE 下每个 tag 一个目录，push 复制文件、pull 反向
# 复制、manifest fetch 以目录是否存在作答——判据与 gate 用到的子集一一对应。
#
# FAKE_CORRUPT=1 让 push 之后把载荷层多写一个字节，用于模拟「tag 存在但字节
# 取回后不一致」。
#
# 用法: . ci/tests/lib/fake_oras.sh; fake_oras_install DIR
# ===========================================================================
fake_oras_install()
{
    local dir="$1"
    mkdir -p "${dir}"
    cat >"${dir}/oras" <<'EOF'
#!/bin/sh
store="${FAKE_STORE:-/nonexistent}"
cmd="$1"
shift
key()
{
    printf '%s' "$1" | tr '/:' '__'
}
case "$cmd" in
login)
    cat >/dev/null
    exit 0
    ;;
manifest)
    sub="$1"
    shift
    case "$sub" in
    fetch)
        meta="${store}/.meta/$(key "$1").json"
        if [ -f "${meta}" ]; then
            cat "${meta}"
        elif [ -d "${store}/$(key "$1")" ]; then
            echo '{"layers":[]}'
        else
            echo "Error: not found" >&2
            exit 1
        fi
        ;;
    delete)
        rm -rf "${store}/$(key "$2")" "${store}/.meta/$(key "$2").json"
        ;;
    esac
    ;;
push)
    ref=""
    files=""
    anns=""
    while [ $# -gt 0 ]; do
        case "$1" in
        --artifact-type) shift; shift ;;
        --annotation)
            anns="${anns}
$2"
            shift 2
            ;;
        *)
            if [ -z "${ref}" ]; then
                ref="$1"
            else
                files="${files} $1"
            fi
            shift
            ;;
        esac
    done
    mkdir -p "${store}/$(key "${ref}")" "${store}/.meta"
    for f in ${files}; do
        src="${f%%:*}"
        [ -f "${src}" ] || src="${f}"
        cp "${src}" "${store}/$(key "${ref}")/"
    done
    if [ -n "${anns}" ]; then
        {
            printf '{"annotations":{'
            printf '%s\n' "${anns}" | {
                sep=""
                while IFS= read -r a; do
                    [ -n "${a}" ] || continue
                    printf '%s"%s":"%s"' "${sep}" "${a%%=*}" "${a#*=}"
                    sep=","
                done
            }
            printf '},"layers":[]}'
        } >"${store}/.meta/$(key "${ref}").json"
    fi
    [ -n "${FAKE_CORRUPT:-}" ] &&
        printf 'x' >>"${store}/$(key "${ref}")/payload.tar"
    ;;
pull)
    ref=""
    out=""
    while [ $# -gt 0 ]; do
        case "$1" in
        -o) shift; out="$1"; shift ;;
        *) ref="$1"; shift ;;
        esac
    done
    [ -d "${store}/$(key "${ref}")" ] || {
        echo "Error: manifest unknown" >&2
        exit 1
    }
    mkdir -p "${out}"
    cp -a "${store}/$(key "${ref}")/." "${out}"/
    ;;
esac
exit 0
EOF
    chmod 755 "${dir}/oras"
}
