# SPDX-License-Identifier: GPL-2.0-only
# AirymaxOS kernel recipe for Yocto / openEuler Embedded (Kirkstone baseline).
# Reuses the standard Yocto kernel class; only SRC_URI + defconfig are custom.
# See docs-closed/agentrt-linux/01-openeuler-tech-reference/12-build-and-flash-strategy.md §2.2

SUMMARY = "AirymaxOS kernel (agentrt-linux microkernel-enhanced)"
DESCRIPTION = "AirymaxOS kernel based on vanilla Linux 6.6 LTS + openEuler \
hardware adaptation layer + agentrt-linux microkernel enhancements. \
Built via LAYER strategy, no fork of openEuler toolchain."
HOMEPAGE = "https://www.spharx.cn"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://COPYING;md5=kernel"

KERNEL_RELEASE = "6.6.0-airy.1"

SRC_URI = "file://airy-kernel-6.6.0.tar.gz \
           file://defconfig-embedded \
           file://0001-airy-microkernel-enhancements.patch \
          "
SRCREV = "AUTOINC"
S = "${WORKDIR}/linux"

inherit kernel

KERNEL_DEFCONFIG = "defconfig-embedded"
COMPATIBLE_MACHINE = "(airymax-x86-64|airymax-arm64|airymax-sw_64)"

do_configure:prepend() {
    # Copy the embedded defconfig fragment into .config so the kernel
    # class' do_configure can merge it via merge_config.sh (see design §1.1).
    install -m 0644 ${WORKDIR}/defconfig-embedded ${B}/.config
}
