# SPDX-License-Identifier: GPL-2.0-only
# AirymaxOS cockpit-airymax plugin (placeholder recipe).
# See docs-closed/agentrt-linux/01-openeuler-tech-reference/12-build-and-flash-strategy.md §5.1

SUMMARY = "Cockpit plugin for AirymaxOS (agentrt-linux / airy_lsm / [SC])"
DESCRIPTION = "Placeholder recipe for cockpit-airymax plugin panel exposing \
agentrt-linux microkernel status, [SC] contract view and airy_lsm policy."
HOMEPAGE = "https://www.spharx.cn"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

RDEPENDS:${PN} = "cockpit"

SRC_URI = ""

S = "${WORKDIR}"

do_install() {
    # Placeholder: real plugin assets ship from the cockpit-airymax frontend build.
    install -d ${D}${datadir}/cockpit/airymax
}
