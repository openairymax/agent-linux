# Security Policy

> **Security authority**: [`docs/AirymaxOS/50-engineering-standards/09-ssot-registry.md`](https://github.com/openairymax/docs/blob/main/AirymaxOS/50-engineering-standards/09-ssot-registry.md) (OS-SEC-* rules)
> **Architecture threat model**: [`docs/AirymaxOS/10-architecture/07-threat-model.md`](https://github.com/openairymax/docs/blob/main/AirymaxOS/10-architecture/07-threat-model.md)

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 1. Supported Versions

| Version | Supported | Status |
|---------|-----------|--------|
| 1.0.1 | Yes | Active development (Pilot) |
| 0.1.1 | Yes | Design baseline (Experimental) |
| < 0.1.1 | No | End of life |

## 2. Reporting a Vulnerability

### 2.1 Private Disclosure

**Do NOT file public GitHub issues for security vulnerabilities.**

Report vulnerabilities privately:

1. **Email**: `security@spharx.com`
2. **Subject**: `[SECURITY] agentrt-linux — <brief description>`
3. **PGP**: Request public key from `security@spharx.com`

Include in your report:

- Description of the vulnerability
- Affected component (kernel / services / security / memory / cognition / cloudnative / system / tests-linux)
- Steps to reproduce
- Impact assessment
- Suggested fix (if any)

### 2.2 Response Timeline

| Step | Timeline |
|------|----------|
| Acknowledgment | Within 48 hours |
| Initial assessment | Within 7 days |
| Fix or mitigation | Within 30 days (severity-dependent) |
| Public disclosure | After fix release + 14-day grace period |

### 2.3 Severity Classification

We use CVSS v3.1:

| Severity | CVSS Range | Example |
|----------|------------|---------|
| Critical | 9.0–10.0 | Kernel privilege escalation, RCE |
| High | 7.0–8.9 | Memory corruption, sandbox escape |
| Medium | 4.0–6.9 | Information leak, DoS |
| Low | 0.1–3.9 | Minor info disclosure |

## 3. Security Design Principles

### 3.1 OS-IRON-001: User-space ABI Never Breaks

The `include/uapi/airymax/` interface is permanently stable. Changes that break userspace are rejected.

### 3.2 Capability-Based Security (OS-SEC-*)

agentrt-linux adopts seL4-style capability-based security:

- Every kernel resource access requires a capability
- Capabilities are unforgeable tokens
- No ambient authority — capabilities must be explicitly passed

### 3.3 [SC] Shared Contract Layer Integrity

The 6+2 header files in `kernel/include/airymax/` are the single physical source of truth for cross-subsystem contracts. Any change requires:

1. Dual CI validation (agentrt-linux + agentrt)
2. L1 subsystem maintainer review
3. L3 top maintainer (SPHARX Engineering) final approval

### 3.4 Security as Cross-Cutting Concern

Security is not a separate data flow — it is a cross-cutting concern that permeates all 4 major data flows (IPC, scheduling, memory, cognition). Every subsystem must implement the relevant OS-SEC-* rules.

## 4. Security CI Gates

The CI pipeline enforces:

- **checkpatch.pl**: Style and common bug detection
- **sparse**: Static analysis for kernel code
- **Coccinelle**: Pattern-based bug detection
- **clang-analyzer**: Additional static analysis
- **Code coverage**: Minimum coverage thresholds per subsystem
- **SSoT validation**: Rule ID consistency

## 5. Disclosure Policy

- We follow **Coordinated Disclosure**.
- Reporters are credited (unless they prefer anonymity).
- We request a 90-day embargo before public disclosure.
- Extensions may be granted for complex fixes.
