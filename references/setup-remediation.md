# Setup remediation and consent

Use this reference only after a read-only prerequisite or model-discovery check
fails.

Tell the user:

1. which check failed and what evidence established the failure;
2. why it blocks the requested evaluation;
3. the exact proposed command or configuration change;
4. what it will download, write, authenticate, or expose;
5. how to verify or reverse it when applicable.

Then ask whether they want the agent to perform that exact fix. Wait for an
explicit yes. Do not install packages, run downloaded scripts, start login
flows, change permissions or shell files, create configuration, or invoke an
automatic fixer before confirmation. Never ask the user to paste a secret into
chat; use the harness's interactive login or documented secret store.

After an approved fix, rerun the original read-only check. If it still fails,
report the new evidence and request fresh confirmation for any different fix.

## Harness starting points

- Hermes: install from <https://hermes-agent.nousresearch.com/install.sh> on
  macOS/Linux/WSL or its documented PowerShell installer on Windows. Diagnose
  with `hermes doctor`; `hermes doctor --fix` is mutating and requires consent.
  Configure authentication with `hermes model`.
- Claude Code: use the official installer documented at
  <https://code.claude.com/docs/en/getting-started>. Check authentication with
  `claude auth status`; start login with `claude auth login` only after consent.
- Codex: use the official installer documented at
  <https://github.com/openai/codex>. Check authentication with
  `codex login status`; start `codex login` only after consent.
- Pi: use the official installation and provider instructions at
  <https://github.com/earendil-works/pi/tree/main/packages/coding-agent>.
  Configure a provider through Pi's `/login`; do not print API keys or bearer
  tokens into logs to test authentication.

Prefer the platform's native package manager when the user already uses one.
Do not silently select `sudo`, alter a system-wide installation, or replace an
existing binary.
