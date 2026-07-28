#!/usr/bin/env bash
# Bootstrap an OD devserver worker for the distributed test-fix harness.
# STUB: ordered, idempotent, check-then-act steps. Run FROM master, SSHing in.
# See implementation plan Slice 9 and the design spec (Provisioning).
#
# Usage: bootstrap_worker.sh <host> <pinned_commit>
# Idempotent: re-running converges a partially-provisioned worker to target.
# On success the worker is eligible for verify_worker.sh -> hosts.txt.

set -euo pipefail

HOST="${1:?usage: bootstrap_worker.sh <host> <pinned_commit>}"
PINNED="${2:?usage: bootstrap_worker.sh <host> <pinned_commit>}"

echo "[bootstrap] $HOST -> pinned $PINNED (stub)"

# 1. EdenFS checkout present, pinned + clean.
#    ssh "$HOST" 'sl root >/dev/null 2>&1 || eden clone ...'
#    ssh "$HOST" "sl pull -r $PINNED && sl goto $PINNED --clean"
#
# 2. Claude Code present + pinned. Pre-installed on ODs; else:
#    ssh "$HOST" 'devfeature install claude_code --persist'
#    pin via META_CLAUDE_CODE_RELEASE / CLAUDE_CODE_VERSION_OVERRIDE; verify claude --version
#    Auth = per-host Meta SSO + Lowbox/TPM cert via internal proxy -- NO token to push,
#    creds are host-bound (not portable). A one-time interactive SSO may be needed per host.
#    ssh "$HOST" 'claude -p ping'    # auth smoke
#
# 3. Config synced via dotsync2 (NOT a token/rsync). Brings ~/.claude:
#    ssh "$HOST" 'dotsync2 pull'      # skills (incl. testx-debug), settings, templates
#
# 4. Warm buck: build the union of <=~200 targets across the issues (15m timeout,
#    RE cache; warm_buck=partial allowed). Write ~/.tfh/warm_buck.done.
#
# 5. Drop run_unit.sh + prompt templates + the PATH land-guard shim
#    (shadows sl/jf/arc/hg land and sl/hg push; logs + exits non-zero).
#
# 6. Emit attestation manifest (claude_version, binary sha256, skill version,
#    base_commit) for master to validate against the pinned expected_manifest.

echo "[bootstrap] TODO: implement steps 1-6"
exit 0
