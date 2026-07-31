# SPDX-License-Identifier: GPL-2.0-only
#
# airy-kernel.spec — AirymaxOS kernel RPM spec
#
# Adapted from openEuler kernel.spec. Reuses openEuler's 5-package split and
# %post scripts. Brand/package name overridden; build toolchain not forked.
#
# Design reference:
#   docs-closed/agentrt-linux/01-openeuler-tech-reference/12-build-and-flash-strategy.md §1.2
# Base template:
#   .engineer-reference/kernel-OLK-6.6/scripts/package/kernel.spec
#
# LAYER strategy: openEuler toolchain (rpmbuild / OBS) reused as-is; only the
# brand, package name and patch list are overridden for AirymaxOS.

%define KERNELRELEASE 6.6.0-airy.1
%define pkg_release airy.1

# _arch is undefined if /usr/lib/rpm/platform/*/macros was not included.
%{!?_arch: %define _arch dummy}
%{!?make: %define make make}
%define makeflags %{?_smp_mflags} ARCH=%{ARCH}
%define __spec_install_post /usr/lib/rpm/brp-compress || :
%define debug_package %{nil}

Name: airy-kernel
Summary: AirymaxOS kernel (agentrt-linux microkernel-enhanced)
Version: 6.6.0
Release: %{pkg_release}
License: GPL-2.0-only
Group: System Environment/Kernel
Vendor: SPHARX Ltd
URL: https://www.spharx.cn
Source0: airy-kernel-6.6.0.tar.gz
Source1: config
Patch0: 0001-airy-microkernel-enhancements.patch
# Patch0: placeholder for agentrt-linux microkernel enhancement series
# (sched_tac / airy_lsm / AgentsIPC / [SC] UAPI). Applied via %patch0 below.
Provides: airy-kernel = %{version}-%{release}
Obsoletes: kernel < %{version}-%{release}
BuildRequires: bc binutils bison dwarves
BuildRequires: (elfutils-libelf-devel or libelf-devel) flex
BuildRequires: gcc make openssl openssl-devel perl python3 rsync

%description
AirymaxOS kernel based on vanilla Linux 6.6 LTS + openEuler hardware
adaptation layer + agentrt-linux microkernel enhancements. Built via LAYER
strategy, no fork of openEuler toolchain.

%package devel
Summary: Development package for building kernel modules to match the %{version} kernel
Group: System Environment/Kernel
AutoReqProv: no
Provides: airy-kernel-devel = %{version}-%{release}
Obsoletes: kernel-devel < %{version}-%{release}

%description devel
This package provides kernel headers and makefiles sufficient to build modules
against the %{version} airy-kernel package.

%package headers
Summary: Header files for the AirymaxOS kernel for use by glibc
Group: Development/System
Provides: airy-kernel-headers = %{version}-%{release}
Obsoletes: kernel-headers < %{version}-%{release}

%description headers
airy-kernel-headers includes the C header files that specify the interface
between the AirymaxOS kernel and userspace libraries and programs, including
the UAPI headers under include/uapi/linux/airymax/. The header files define
structures and constants that are needed for building most standard programs
and are also needed for rebuilding the glibc package.

%package modules
Summary: AirymaxOS kernel modules (.ko) for %{version}-%{release}
Group: System Environment/Kernel
Provides: airy-kernel-modules = %{version}-%{release}
Obsoletes: kernel-modules < %{version}-%{release}

%description modules
This package provides the loadable kernel modules (.ko) for the AirymaxOS
%{version}-%{release} kernel.

%package debuginfo
Summary: Debug information for the AirymaxOS kernel package
Group: Development/Debug
AutoReqProv: no
Provides: airy-kernel-debuginfo = %{version}-%{release}
Obsoletes: kernel-debuginfo < %{version}-%{release}

%description debuginfo
This package provides debug information for the AirymaxOS airy-kernel package,
useful for diagnosing kernel issues with crash, perf, gdb and SystemTap.

%prep
%setup -q -n linux
cp %{SOURCE1} .config
%patch0 -p1

%build
%{make} %{makeflags} KERNELRELEASE=%{KERNELRELEASE} KBUILD_BUILD_VERSION=%{release}

%install
mkdir -p %{buildroot}/boot
%ifarch ia64
mkdir -p %{buildroot}/boot/efi
cp $(%{make} %{makeflags} -s image_name) %{buildroot}/boot/efi/vmlinuz-%{KERNELRELEASE}
ln -s efi/vmlinuz-%{KERNELRELEASE} %{buildroot}/boot/
%else
cp $(%{make} %{makeflags} -s image_name) %{buildroot}/boot/vmlinuz-%{KERNELRELEASE}
%endif
%{make} %{makeflags} INSTALL_MOD_PATH=%{buildroot} modules_install
%{make} %{makeflags} INSTALL_HDR_PATH=%{buildroot}/usr headers_install
cp System.map %{buildroot}/boot/System.map-%{KERNELRELEASE}
cp .config %{buildroot}/boot/config-%{KERNELRELEASE}
ln -fns /usr/src/kernels/%{KERNELRELEASE} %{buildroot}/lib/modules/%{KERNELRELEASE}/build
%{make} %{makeflags} run-command KBUILD_RUN_COMMAND='${srctree}/scripts/package/install-extmod-build %{buildroot}/usr/src/kernels/%{KERNELRELEASE}'

%clean
rm -rf %{buildroot}

%post
if [ -x /sbin/installkernel -a -r /boot/vmlinuz-%{KERNELRELEASE} -a -r /boot/System.map-%{KERNELRELEASE} ]; then
cp /boot/vmlinuz-%{KERNELRELEASE} /boot/.vmlinuz-%{KERNELRELEASE}-rpm
cp /boot/System.map-%{KERNELRELEASE} /boot/.System.map-%{KERNELRELEASE}-rpm
rm -f /boot/vmlinuz-%{KERNELRELEASE} /boot/System.map-%{KERNELRELEASE}
/sbin/installkernel %{KERNELRELEASE} /boot/.vmlinuz-%{KERNELRELEASE}-rpm /boot/.System.map-%{KERNELRELEASE}-rpm
rm -f /boot/.vmlinuz-%{KERNELRELEASE}-rpm /boot/.System.map-%{KERNELRELEASE}-rpm
fi

%preun
if [ -x /sbin/new-kernel-pkg ]; then
new-kernel-pkg --remove %{KERNELRELEASE} --rminitrd --initrdfile=/boot/initramfs-%{KERNELRELEASE}.img
elif [ -x /usr/bin/kernel-install ]; then
kernel-install remove %{KERNELRELEASE}
fi

%postun
if [ -x /sbin/update-bootloader ]; then
/sbin/update-bootloader --remove %{KERNELRELEASE}
fi

%files
%defattr (-, root, root)
/lib/modules/%{KERNELRELEASE}
%exclude /lib/modules/%{KERNELRELEASE}/build
/boot/*

%files devel
%defattr (-, root, root)
/usr/src/kernels/%{KERNELRELEASE}
/lib/modules/%{KERNELRELEASE}/build

%files headers
%defattr (-, root, root)
/usr/include

%files modules
%defattr (-, root, root)
/lib/modules/%{KERNELRELEASE}/*

%files debuginfo
%defattr (-, root, root)
/usr/lib/debug/lib/modules/%{KERNELRELEASE}
