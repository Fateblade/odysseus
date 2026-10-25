# Fix GLM Maya's small expert pool with a 96 GiB Linux GPU reservation

Updated 2026-10-25. Branch `feature/linux-ai-operations-maya-glm53`.

## Diagnosis

The first device test succeeded at 8192 context with native MTP off, but measured about 3.0 decode tokens/s, 57.8 prefill tokens/s and 18.23 s TTFT. The optimized kernel flags were correct. Startup reported 96 GiB GPU memory, 7.55 GiB already used, 80.17 GiB expert weights, but only an 8.28 GiB expert pool and a further 9.27 GiB pinned RAM tier. Expert-cache hits were approximately 42–46%.

The pinned Linux APU planner calculated `25.28 GiB MemAvailable - 16 GiB headroom - 1 GiB reserve`. That suits allocations from ordinary shared Linux RAM but fails to use this machine's large firmware GPU reservation excluded from Linux's 30.48 GiB MemTotal. Raising `POOL_GB` cannot fix that calculation; it is a cap. Do not lower the system headroom, change BIOS settings or replace ROCm for this trial.

The source patch adds **opt-in** `MAYA_GLM_RESERVED_VRAM=1` (native engine spelling `STRATA_GLM_RESERVED_VRAM`). Defaults are unchanged. At startup it identifies the active HIP GPU by PCI address and reads that device's sysfs VRAM total/used. It requires a reservation of at least 64 GiB, larger than Linux MemTotal, and sufficient available system RAM to retain the configured headroom plus reserve. Missing/inconsistent counters or failed checks abort startup. The expert budget is bounded by both HIP free memory and sysfs free VRAM, minus GPU reserve and any configured MMQ scratch reserve, and capped at the expert bytes. GTT is never added. Existing pool/slot caps still apply. The APU flag stays enabled, retaining its full-pool warmup and staging-only RAM behavior when all experts fit.

This is a local patch for pinned Maya v1.1.22, not an upstream setting. Its source hashes, opt-in behavior and CPU budget tests are checked locally; the complete HIP build and device runtime still need validation. Do not enable this mode for a normal shared-memory APU without this verified reservation.

## 1. Stop GLM and pull the fix

Run as the existing service owner. Finish active host jobs first. Stop **Maya GLM** in Management; leave the other large models stopped. Run one command at a time and stop at the first error.

```bash
cd /mnt/odysseus-fateblade
git status --short
git pull --ff-only
test "$(git branch --show-current)" = feature/linux-ai-operations-maya-glm53
test "$(systemctl --user is-active maya-glm53.service)" = inactive
```

No model downloads or repacking are required. Preserve local edits instead of resetting the checkout.

## 2. Back up the profile and run the standalone budget checks

```bash
umask 077
GLM_FIX_BACKUP="$HOME/.local/share/odysseus-maya-glm53/reserved-vram-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$GLM_FIX_BACKUP"
cp -p "$HOME/project-maya-glm53/maya-maya-s-v2-iq2_xxs-hip.json" "$GLM_FIX_BACKUP/config.json"
cp -p "$HOME/project-maya-glm53/build-hip/maya" "$GLM_FIX_BACKUP/maya-before-vram-fix"
g++ -std=c++17 -Wall -Wextra -Werror -I scripts tests/maya_reserved_vram_test.cpp -o "$GLM_FIX_BACKUP/test-budget"
"$GLM_FIX_BACKUP/test-budget"
```

Success prints nothing. Keep the backup directory. No system packages are installed. The patch helper also retains the original `src/core/glm_fast_path.cu` and refuses an unrecognized upstream source or a conflicting prior patch/header.

## 3. Apply the isolated source patch and rebuild

```bash
python3 scripts/odysseus-patch-maya-glm-vram.py "$HOME/project-maya-glm53"
python3 scripts/odysseus-build-maya-glm.py "$HOME/project-maya-glm53" --jobs 1
```

The existing Ubuntu layout and optimized HIP flags remain in place. This rebuilds only GLM target `maya`; it does not modify the DS41F source/service or ROCm. Keep the first compiler error if the device build fails. Do not enable the setting unless the rebuild completes successfully.

## 4. Enable the mode in this GLM profile only

Keep context 8192, MTP off and the existing 16 GiB system headroom for this comparison. The following changes only the new opt-in variable and removes its equivalent legacy alias so they cannot conflict:

```bash
test "$(systemctl --user is-active maya-glm53.service)" = inactive
"$HOME/project-maya-glm53/.venv/bin/python" - <<'PYCONFIG'
import json
import os
from pathlib import Path
path = Path.home() / 'project-maya-glm53/maya-maya-s-v2-iq2_xxs-hip.json'
config = json.loads(path.read_text(encoding='utf-8'))
env = config.setdefault('env', {})
env.pop('STRATA_GLM_RESERVED_VRAM', None)
env['MAYA_GLM_RESERVED_VRAM'] = '1'
temporary = path.with_name(path.name + '.reserved-vram.tmp')
with temporary.open('x', encoding='utf-8') as stream:
    stream.write(json.dumps(config, indent=2) + '\n')
temporary.chmod(path.stat().st_mode & 0o777)
os.replace(temporary, path)
PYCONFIG
```

Do not rerun the upstream installer for this step; it could regenerate the config. No dashboard container rebuild is needed for this engine-only change.

## 5. Start and verify allocations before benchmarking

Start **Maya GLM** in Management after confirming it was stopped. Replacing a binary or editing its config does not change an already-running engine. If it remained running during the rebuild, explicitly restart this service before checking allocations:

```bash
systemctl --user restart maya-glm53.service
```

An old engine can appear as `build-hip/maya (deleted)` in `/proc/PID/exe`, with its old environment still active. The first device retry encountered exactly this: the patched binary existed on disk and the setting was saved, but the original process still served requests. Restarting loaded the new binary and setting. Check the new engine log:

```bash
cd "$HOME/project-maya-glm53"
GLM_LOG=$(.venv/bin/python -c 'import json; print(json.load(open("maya-maya-s-v2-iq2_xxs-hip.json"))["log"])')
grep -Ei 'reserved VRAM|unified memory|expert pool|slots/layer|RAM tier|all experts|error|failed' "$GLM_LOG" | tail -n 60
free -h
```

Read the most recent startup, because the log appends prior attempts. It must include `reserved VRAM mode` for the expected PCI device and approximately 96 GiB total VRAM / 30.5 GiB Linux RAM. The pool should grow toward the 80.17 GiB expert set if available capacity and existing caps permit it. Look for full expert residency and a staging-only RAM tier. GPU use should grow substantially; RAM-tier usage should fall. These are acceptance targets, not guaranteed allocations or speed.

If the mode verification fails, preserve the exact error; do not disable its checks or force a larger memory cap. If it still reports an 8.28 GiB pool, verify the rebuilt binary is the one in the config and that the new setting reached it. If another pool cap remains configured, inspect it rather than clearing unrelated settings blindly.

After readiness, repeat the original three-run Small/mixed/distinct-task benchmark at 8192 context and 256 output tokens, MTP still off. For the matched workload use task-set seed **1494806378**, temperature 0 and sampling seed 42. Mark the new scoreboard notes `reserved VRAM fix; MTP-off`; retain the historical 3 TPS baseline. Monitor `vmstat 1 10` while generating, GPU allocations, available RAM and logs. Compare native decode, prefill, TTFT and cache hit rate. Only after allocation and stability are satisfactory proceed to the separate MTP-on comparison in the main guide. No throughput improvement is promised before this device test.

## Verified device result, 2026-10-25

After the actual restart, the host built and ran the patch on the existing HIP 7.1 / clang 21 stack. The active PCI device was 0000:bd:00.0, with 96 GiB VRAM, 88.24 GiB free before the pool and 30.48 GiB Linux RAM. The expert pool reached 80.17 GiB, 288 slots/layer and 12,096 total slots. The engine confirmed all experts fit; the pinned RAM tier fell from 9.27 GiB to 0.22 GiB, staging only. The dashboard reported about 87 GiB GPU usage.

Three Small/mixed/distinct runs at 8192 context, 256 output tokens, temperature 0, sampling seed 42 and MTP off measured native decode median **18.4 t/s**, prefill **177 t/s**, TTFT **6.204 s**, end-to-end **12.723 t/s**, elapsed **20.121 s**. Decode samples were 18.4, 18.4 and 18.3 t/s; warmup was 18.3. Prompt-cache counts and draft counts were zero. The post-run Linux memory snapshot reported 5.61 GiB used / 24.87 GiB available. Swap used was about 0.99 GiB, unchanged from the prior sample; this does not establish whether swapping occurred during requests.

Relative to the first 3.0 t/s baseline this is about 6.1x native decode, 3.1x prefill and 5.2x end-to-end throughput. However, this successful export used task-set seed **1893222011**, not the original **1494806378**: it is a workload-class comparison, not identical prompts. Reuse **1893222011** for the next MTP-on comparison against this fixed baseline, with the same other settings. All answers reached the output cap, so task quality and longer-context stability remain separate acceptance work. These data do not establish the maximum context or MTP benefit.

### First MTP-on trial

Subsequent MTP-on smoke result: native decode median 26.9 t/s (22.4–28.3 across measured tasks), median per-sample draft acceptance 79.02%, prefill 174 t/s and TTFT 6.310 s at 8192 context. This establishes active native drafting, not a controlled speedup measurement: that export used task-set seed 1521369663 and 647–1444 output tokens, unlike the MTP-off seed 1893222011 / 256 tokens. Compare both modes with an identical explicit task-set seed and output cap before attributing the full rate change to MTP. The fuller result and remaining quality/context acceptance work are recorded in the GLM plan.

## Roll back

Stop GLM first. In its isolated JSON config set `MAYA_GLM_RESERVED_VRAM` to the string `0`, preserving everything else, then start it again. That selects the original MemAvailable budget even with the patched binary. Alternatively, restore the saved config and binary from the printed backup directory while the service is stopped. Leave the source backup and model files intact. Existing ROCm and DS41F require no rollback.
