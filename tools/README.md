# agentrt-linux Tools

Cross-submodule tool aggregation for the agentrt-linux (AirymaxOS) management repository.

## Tools

| Tool | Description |
|------|-------------|
| `validate-ssot.py` | SSoT rule ID consistency validator — checks that all `OS-*-NNN` and agentrt rule IDs used in documentation are registered in `ssot-registry.yaml` |

## Usage

### SSoT Validation

```bash
# Install dependency
pip install pyyaml

# Run validation (from agentrt-linux management repo root)
python3 tools/validate-ssot.py docs/AirymaxOS ssot-registry.yaml
```

Exit codes:
- `0` — all rule IDs registered (PASS)
- `1` — unregistered rule IDs found (FAIL)
- `2` — file/YAML error

## Adding New Tools

New cross-submodule tools should be placed in this directory. Each tool must:
1. Include a `--help` flag
2. Return proper exit codes
3. Be referenced in this README
