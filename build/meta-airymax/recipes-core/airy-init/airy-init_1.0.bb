# SPDX-License-Identifier: GPL-2.0-only
# AirymaxOS microkernel-enhanced init system (placeholder recipe).
# See docs-closed/agentrt-linux/01-openeuler-tech-reference/12-build-and-flash-strategy.md §2.2

SUMMARY = "AirymaxOS init system (agentrt-linux microkernel-aware)"
DESCRIPTION = "Placeholder recipe for the AirymaxOS init system. Real \
implementation will be wired up alongside agentrt-linux userspace."
HOMEPAGE = "https://www.spharx.cn"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

SRC_URI = ""

S = "${WORKDIR}"

do_install() {
    # Placeholder: real init binaries ship from agentrt-linux userspace build.
    install -d ${D}${sbindir}
}
