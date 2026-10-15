# ===========================================================================
# airymaxos-kernel.spec —— agent-linux（AirymaxOS）内核包 spec 文件
#
# 用途：由 .github/workflows/release.yml 以 rpmbuild 直调方式为 4 架构
#       （x86_64 / aarch64 / riscv64 / loongarch64）构建内核 RPM。源码树
#       就地构建（rpmbuild --build-in-place --noprep），默认配置取自
#       arch/<SRCARCH>/configs/airy_defconfig。
#
# 由 workflow 以 --define 传入的宏（缺省值仅用于本地手工构建自洽）：
#   ARCH            内核 ARCH 名（x86_64 / arm64 / riscv / loongarch）
#   SRCARCH         源码树 arch/ 子目录名（x86 / arm64 / riscv / loongarch）
#   KERNELRELEASE   `make -s kernelrelease` 输出（如 6.6.157）
#   airy_version    RPM Version（发布 tag 去前导 v，如 0.0.3）
#   airy_release    RPM Release（默认 1）
#   cross_compile   交叉工具链前缀（如 aarch64-linux-gnu-；x86_64 传空走原生）
#
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
# ===========================================================================

# ---- workflow 提供的宏（--define 覆盖；缺省值保证本地手工构建自洽）---------
%{!?airy_version: %global airy_version 0.0.3}
%{!?airy_release: %global airy_release 1}
%{!?ARCH: %global ARCH %(uname -m)}
%{!?SRCARCH: %global SRCARCH %{ARCH}}
%{!?KERNELRELEASE: %global KERNELRELEASE 6.6.157}

# ---- 就地构建所需的最小宏（对齐上游 scripts/package/kernel.spec）---------
%{!?_arch: %define _arch dummy}
%{!?make: %define make make}
%define makeflags %{?_smp_mflags} ARCH=%{ARCH} CROSS_COMPILE=%{?cross_compile}

# 对齐上游内核 spec 的既定做法：禁用 RPM 自动 debuginfo 抽取，仅保留
# brp-compress。原因有二：(1) 就地构建下 %{buildsubdir} 不存在，自动抽取
# 会产出退化（空）debuginfo 包；(2) 部分构建平台（Debian/Ubuntu 的 rpm
# 包）未提供 %{__find_debuginfo} 脚本。debuginfo 独立交付列入发行待办。
%define __spec_install_post /usr/lib/rpm/brp-compress || :
%global debug_package %{nil}

Name:           airymaxos-kernel
Version:        %{airy_version}
Release:        %{airy_release}%{?dist}
Summary:        agent-linux (AirymaxOS) Linux 6.6 kernel
License:        GPL-2.0-only
Group:          System Environment/Kernel
Vendor:         SPHARX Ltd.
URL:            https://atomgit.com/openairymax/agent-linux
Provides:       kernel-%{KERNELRELEASE} = %{version}-%{release}
Requires:       %{name}-modules = %{version}-%{release}
BuildRequires:  bc binutils bison dwarves
BuildRequires:  (elfutils-libelf-devel or libelf-devel) flex
BuildRequires:  gcc make openssl openssl-devel perl python3 rsync
ExclusiveArch:  x86_64 aarch64 riscv64 loongarch64

%description
agent-linux（AirymaxOS）内核，基于 Linux 6.6 LTS 微内核化改造，集成
airy_defconfig 默认配置与 Airy LSM 安全穹顶，面向智能体工作负载。

%package modules
Summary:        Modules for the agent-linux (AirymaxOS) kernel

%description modules
本包提供 %{KERNELRELEASE} 内核的全部可加载模块（/lib/modules 树）。

%package headers
Summary:        Header files for the agent-linux (AirymaxOS) kernel

%description headers
本包提供内核 UAPI 头文件，供 glibc 与用户态程序编译使用。

%package devel
Summary:        Development package for building modules against the agent-linux kernel
AutoReqProv:    no
Requires:       %{name}-headers = %{version}-%{release}

%description devel
本包提供编译外部内核模块所需的头文件与构建脚本
（/usr/src/kernels/%{KERNELRELEASE}）。

%prep
# 就地构建（--build-in-place --noprep）时本段被 rpmbuild 跳过；
# 保留树完整性自检，防止误将本 spec 用于源码包构建路径。
test -f Makefile
test -f arch/%{SRCARCH}/configs/airy_defconfig

%build
%{make} %{makeflags} KERNELRELEASE=%{KERNELRELEASE} KBUILD_BUILD_VERSION=%{airy_release}

%install
# 内核镜像与启动态文件
mkdir -p %{buildroot}/boot
install -m 0644 $(%{make} %{makeflags} -s image_name) %{buildroot}/boot/vmlinuz-%{KERNELRELEASE}
install -m 0644 System.map %{buildroot}/boot/System.map-%{KERNELRELEASE}
install -m 0644 .config %{buildroot}/boot/config-%{KERNELRELEASE}
install -m 0644 arch/%{SRCARCH}/configs/airy_defconfig \
	%{buildroot}/boot/airy_defconfig-%{KERNELRELEASE}

# 内核模块（剥离调试段以控制体积）
%{make} %{makeflags} INSTALL_MOD_PATH=%{buildroot} INSTALL_MOD_STRIP=1 modules_install
rm -f %{buildroot}/lib/modules/%{KERNELRELEASE}/build
rm -f %{buildroot}/lib/modules/%{KERNELRELEASE}/source

# UAPI 头文件
%{make} %{makeflags} INSTALL_HDR_PATH=%{buildroot}/usr headers_install

# 外部模块构建树与 build/source 符号链接
%{make} %{makeflags} run-command KBUILD_RUN_COMMAND='${srctree}/scripts/package/install-extmod-build %{buildroot}/usr/src/kernels/%{KERNELRELEASE}'
ln -fns /usr/src/kernels/%{KERNELRELEASE} %{buildroot}/lib/modules/%{KERNELRELEASE}/build
ln -fns /usr/src/kernels/%{KERNELRELEASE} %{buildroot}/lib/modules/%{KERNELRELEASE}/source

%post
# 模块依赖索引（modules 子包由 Requires 先行安装）
if [ -x /sbin/depmod ]; then
	/sbin/depmod -a %{KERNELRELEASE} || :
fi
# 生成初始内存盘（目标系统提供 dracut 时）
if [ -x /usr/bin/dracut ]; then
	/usr/bin/dracut -f /boot/initramfs-%{KERNELRELEASE}.img %{KERNELRELEASE} || :
fi
# 注册引导项（目标系统提供 kernel-install 时）
if [ -x /usr/bin/kernel-install ]; then
	/usr/bin/kernel-install add %{KERNELRELEASE} /boot/vmlinuz-%{KERNELRELEASE} || :
fi

%preun
if [ -x /usr/bin/kernel-install ]; then
	/usr/bin/kernel-install remove %{KERNELRELEASE} || :
fi

%files
%defattr(-, root, root, -)
/boot/vmlinuz-%{KERNELRELEASE}
/boot/System.map-%{KERNELRELEASE}
/boot/config-%{KERNELRELEASE}
/boot/airy_defconfig-%{KERNELRELEASE}

%files modules
%defattr(-, root, root, -)
/lib/modules/%{KERNELRELEASE}
%exclude /lib/modules/%{KERNELRELEASE}/build
%exclude /lib/modules/%{KERNELRELEASE}/source

%files headers
%defattr(-, root, root, -)
/usr/include

%files devel
%defattr(-, root, root, -)
/usr/src/kernels/%{KERNELRELEASE}
/lib/modules/%{KERNELRELEASE}/build
/lib/modules/%{KERNELRELEASE}/source

%changelog
* Thu Oct 15 2026 SPHARX <dev@spharx.com> - 0.0.3-1
- agent-linux (AirymaxOS) 0.0.3 内核包：vmlinuz + 模块 + 头文件 + 开发包
- 默认配置对齐 arch/<SRCARCH>/configs/airy_defconfig（含 Airy LSM 与 BTF）
