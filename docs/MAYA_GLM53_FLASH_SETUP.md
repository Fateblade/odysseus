# Set up GLM 5.3 Flash with Maya on Strix Halo

Updated 2026-10-24. Branch: `feature/linux-ai-operations-maya-glm53`, based on the tested DS41F integration at `47d2f34f`. This adds an isolated GLM profile; it preserves Maya DS41F, DwarfStar, Strata, Ollama and their model files.

The target is Ubuntu resolute, Ryzen AI Max+ 395 / Radeon 8060S (`gfx1151`), the working Ubuntu HIP 7.1 and clang 21 stack. DS41F compiled and ran after the Ubuntu layout, complete rocWMMA, device-bitcode and explicit HIP Release fixes below. GLM has different kernels and memory planning: these steps carry forward the verified build configuration, but GLM GPU execution and memory fit still require validation on your machine. Do not replace ROCm or change BIOS/kernel memory settings to make the first test proceed.

## Pins and files

- Maya v1.1.22: `ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197`.
- llama.cpp build/packing dependency: `3cf03257f219afbe7334045ff7c6a06ac68c627d`.
- Ordinary GLM Maya-S-v2 repository: `peasantsmith/GLM-5.3-Flash-Maya-GGUF`, revision `f16ee4ba50d8fd2bb726ea1862ca6ca14ba188da`.
- Recipe: `Maya-S-v2-IQ2_XXS`; three GGUF shards, **96,468,592,256 bytes / about 89.84 GiB**. Sizes and publisher SHA-256 values are pinned in `scripts/maya-glm53-artifacts.json`.
- GLM architecture: `glm5-next`. The GLM binary is `build-hip/maya`, not `maya-ds4`. Its tokenizer and native MTP tensors are embedded/exported from the GLM shards. DeepSeek DSpark, Engram and XS files do not apply.

The pinned installer normally downloads from mutable `main`. This guide downloads immutable URLs first and passes `--gguf-dir`. Sources and the complete contract are in [the research note](research/GLM53_MAYA_INSTALL_CONTRACT.md). Optional vision and NVIDIA-only DFlash2 are excluded.

## 1. Preserve the working installation

Run Bash commands on Ubuntu as the existing service owner, normally `seth`. Run one section at a time and stop on the first failed command. Finish host jobs and active inference first. Adjust paths if your working checkouts differ.

```bash
cd /mnt/odysseus-fateblade
git status --short
hipcc --version
command -v python3 git curl make g++
free -h
df -h /mnt/ai-models "$HOME"
ss -ltnp '( sport = :8093 )'
systemctl --user status maya-xs.service --no-pager
```

Use another port consistently if 8093 is occupied. Keep at least **150 GiB free** on the new model filesystem, in addition to the existing models, plus build room under your home directory. This is an initial margin for weights/pack/index/cache, not a measured peak storage requirement. Record the active DS41F unit, source/config paths and model allocation. The guide uses `~/project-maya-glm53`, `/mnt/ai-models/maya-glm53` and `maya-glm53.service`.

```bash
umask 077
GLM_BACKUP="$HOME/.local/share/odysseus-maya-glm53/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$GLM_BACKUP"
cp -p .env "$GLM_BACKUP/odysseus.env"
cp -p "$HOME/.config/odysseus-operations/management.json" "$GLM_BACKUP/management.json"
git rev-parse HEAD > "$GLM_BACKUP/odysseus-base.txt"
printf '%s\n' "$GLM_BACKUP"
```

Keep the printed backup directory for rollback. Backups contain credentials; do not paste them into chat. Preserve existing database/data backups. No service needs to be stopped merely to download or compile with `--no-start`; perform the work outside host benchmarks so compilation, hashing and disk I/O do not distort measurements.

## 2. Pull the GLM integration branch

Preserve local edits; do not reset the host checkout.

```bash
git fetch origin
if git show-ref --verify --quiet refs/heads/feature/linux-ai-operations-maya-glm53; then
  git switch feature/linux-ai-operations-maya-glm53
else
  git switch --track -c feature/linux-ai-operations-maya-glm53 origin/feature/linux-ai-operations-maya-glm53
fi
git pull --ff-only
test -f scripts/maya-glm53-artifacts.json
test -f scripts/odysseus-build-maya-glm.py
```

## 3. Create the isolated pinned source and build environment

```bash
test ! -e "$HOME/project-maya-glm53"
git clone --branch v1.1.22 --depth 1 https://github.com/mw00/project-maya.git "$HOME/project-maya-glm53"
cd "$HOME/project-maya-glm53"
test "$(git rev-parse HEAD)" = ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197
python3 -m venv .venv
```

For a repeated attempt, reuse the same pinned checkout after checking its revision and existing local patch; do not remove or overwrite it. Install build tools into this new venv using the versions that already built DS41F:

```bash
"$HOME/project-maya-xs/.venv/bin/python" - <<'PYTOOLS' > "$GLM_BACKUP/build-tools.txt"
import importlib.metadata as metadata
for package in ('cmake', 'ninja'):
    print(f'{package}=={metadata.version(package)}')
PYTOOLS
.venv/bin/python -m pip install -r "$GLM_BACKUP/build-tools.txt"
python3 /mnt/odysseus-fateblade/scripts/odysseus-patch-maya-ubuntu.py "$PWD"
export ROCM_PATH=/usr
./maya.sh --backend hip --gpu 0 --model Maya-S-v2-IQ2_XXS --check
```

If the DS41F environment is elsewhere, change its path. If a required package cannot be read, inspect the working build tools rather than replacing host packages. Confirm the Radeon 8060S index in `--check`; substitute that index consistently below. The Ubuntu patch is source-hash guarded, saves the original `maya.py`, adds LLVM-21 and multiarch hipBLAS lookup, and sets HIP Release `-O3 -DNDEBUG`. It does not modify `/usr` or the DS41F checkout.

Fetch the pinned llama.cpp dependency in this checkout. On a retry, skip initialization only after verifying the existing dependency revision.

```bash
if ! test -e third_party/llama.cpp; then
  git init third_party/llama.cpp
  git -C third_party/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp.git
  git -C third_party/llama.cpp fetch --depth 1 origin 3cf03257f219afbe7334045ff7c6a06ac68c627d
  git -C third_party/llama.cpp checkout --detach FETCH_HEAD
fi
test "$(git -C third_party/llama.cpp rev-parse HEAD)" = 3cf03257f219afbe7334045ff7c6a06ac68c627d
git -C third_party/llama.cpp diff --quiet HEAD
```

Reuse the complete rocWMMA 7.1 headers from the DS41F build. If they are missing, create a new matching checkout, preserving any existing one:

```bash
if ! test -e "$HOME/rocwmma-7.1.0"; then
  git clone --depth 1 --branch rocm-7.1.0 https://github.com/ROCm/rocWMMA.git "$HOME/rocwmma-7.1.0"
fi
test "$(git -C "$HOME/rocwmma-7.1.0" rev-parse HEAD)" = 27a847f610bfdb4e58dd257889734168b0ef8626
test -f "$HOME/rocwmma-7.1.0/library/include/rocwmma/internal/accessors.hpp"
git -C "$HOME/rocwmma-7.1.0" diff --quiet HEAD -- library/include
dpkg-query -L rocm-device-libs-21 | grep '/ocml\.bc$'
```

The working DS41F bitcode path was `/usr/lib/llvm-21/lib/clang/21/amdgcn/bitcode`. Confirm `ocml.bc` there. If the installed package reports another location, pass that directory to the helper; do not use `-nogpulib` or replace ROCm.

```bash
python3 /mnt/odysseus-fateblade/scripts/odysseus-build-maya-glm.py "$HOME/project-maya-glm53" --jobs 1
test -x "$HOME/project-maya-glm53/build-hip/maya"
```

The helper checks the Maya, llama.cpp and rocWMMA pins, sets the validated compiler/bitcode/header options, verifies actual generated `strata_kernels` HIP flags contain effective `-O3 -DNDEBUG`, then builds target **maya**. It accepts `--rocwmma-include` and `--device-lib-dir` if the checked locations differ. Stop at the first configuration/compiler error and retain it. A GLM build has not yet been validated on this machine merely because DS41F built.

## 4. Download and verify the three pinned GLM shards

```bash
export GLM_MODEL_ROOT=/mnt/ai-models/maya-glm53
mkdir -p "$GLM_MODEL_ROOT/Maya-S-v2-IQ2_XXS"
while IFS= read -r relative; do
  curl --fail --location --retry 3 --continue-at - \
    --output "$GLM_MODEL_ROOT/$relative" \
    "https://huggingface.co/peasantsmith/GLM-5.3-Flash-Maya-GGUF/resolve/f16ee4ba50d8fd2bb726ea1862ca6ca14ba188da/$relative" || break
done < <(python3 - <<'PYFILES'
import json
from pathlib import Path
manifest = json.loads(Path('/mnt/odysseus-fateblade/scripts/maya-glm53-artifacts.json').read_text())
for name in manifest['files']:
    print(name)
PYFILES
)
python3 /mnt/odysseus-fateblade/scripts/odysseus-verify-maya-glm.py "$GLM_MODEL_ROOT"
```

All three size and SHA-256 checks must pass before packing. Verification reads approximately 96.5 GB from storage. A partial download or a failed hash is not a verified model. Do not substitute Derisked, S24, EXL3, DeepSeek or other revisions. The source shard directory must be writable because the pack/index goes beside the weights.

## 5. Pack and write a no-start GLM profile

```bash
cd "$HOME/project-maya-glm53"
export ROCM_PATH=/usr
./setup.sh --setup --backend hip --gpu 0 --model Maya-S-v2-IQ2_XXS \
  --draft mtp --drafting off --context 8192 --no-vision --yes --no-start \
  --llama-dir "$PWD/third_party/llama.cpp" \
  --gguf-dir "$GLM_MODEL_ROOT/Maya-S-v2-IQ2_XXS" \
  --env MAYA_GLM_MTP_DECODE=0 --host 127.0.0.1 --port 8093
```

This uses the GLM packer and its BF16 compatibility conversion. The default pack is `$GLM_MODEL_ROOT/Maya-S-v2-IQ2_XXS/pack`; if the installer reports a different pack path, inspect that path in the generated config. Do not move or remove indexed shards after packing. The tokenizer is exported from the GGUF; no DSpark download or separate tokenizer model is needed.

Inspect `maya-maya-s-v2-iq2_xxs-hip.json` locally. It must point to `build-hip/maya`, the GLM `--glm-pack`, its tokenizer, `--max-context 8192`, `--prefill auto`, `--expert-cache auto`, `--kv-streaming on`, HIP, loopback port 8093 and model name `glm-5.3-flash`. Keep the generated memory defaults; do not copy discrete-GPU `RAM_GB`, force a pool size, or force a 256-token prefill chunk.

`--drafting off` skips external-drafter calibration; **it does not reliably disable native GLM MTP**. The explicit `MAYA_GLM_MTP_DECODE=0` establishes this baseline. DSpark is DeepSeek-specific. Save this configuration before further edits:

```bash
cp -p maya-maya-s-v2-iq2_xxs-hip.json "$GLM_BACKUP/glm-baseline.json"
grep -E '^CMAKE_HIP_FLAGS_RELEASE:|^CMAKE_HIP_ARCHITECTURES:' build-hip/CMakeCache.txt
grep '^HIP_FLAGS =' build-hip/CMakeFiles/strata_kernels.dir/flags.make
```

Stop if optimization flags are missing or a later `-O0` overrides `-O3`. Initial context is 8192, not a claim that 128K or the model's training ceiling fits. GLM's APU planner differs from DeepSeek's: it sizes a pool from reported available memory after allocations/headroom. The prior DS41F snapshot had only 30.48 GiB Linux-visible RAM and a separately reported large GPU pool; this needs actual GLM allocation-log validation. Do not add RAM, GPU and GTT counters as independent capacity or count swap as weight capacity.

## 6. Register the independent user service and dashboard endpoint

Copy the reviewed example, adjusting only paths/port if your locations differ:

```bash
mkdir -p "$HOME/.config/systemd/user"
cp /mnt/odysseus-fateblade/scripts/maya-glm53.service.example "$HOME/.config/systemd/user/maya-glm53.service"
systemctl --user daemon-reload
```

Do not enable this experimental model at boot alongside DS41F. `Restart=no` avoids repeated model-load failures. The pre-start RAM margin is an early check, not a full GPU/residency guarantee.

Merge these entries into `~/.config/odysseus-operations/management.json`, preserving every existing entry:

```json
{
  "services": {
    "maya_glm": {"unit": "maya-glm53.service", "scope": "user"}
  },
  "maya_glm_setup": {"maximum_context": 128000}
}
```

`maximum_context` is the reviewed upper selection bound, not the allocation at startup and not a memory-fit guarantee. The GLM service retains its separate generated run config. The dashboard discovers the default isolated GLM config at `~/project-maya-glm53/maya-maya-s-v2-iq2_xxs-hip.json`. For another path, set `maya_glm_setup.config_file` to its absolute filename (and `unit_file` if the user unit is outside its normal location). Managed context and native MTP changes use Review changes / Apply changes: a running service restarts and verifies its context and the actual child engine switch; a stopped service stays stopped. Reserved-VRAM and other unrelated settings are preserved. If no managed config is available, the context-only native reload remains available while GLM is ready. DeepSeek DSpark settings never edit GLM settings.

Add this to the Odysseus `.env`, keeping the working `ODYSSEUS_OPERATIONS_MAYA_URL` unchanged:

```dotenv
ODYSSEUS_OPERATIONS_MAYA_GLM_URL=http://127.0.0.1:8093
```

The URL is interpreted on the Linux host helper, not inside the dashboard container. Pulling updates the helper if its configured path points into this checkout; otherwise update its installed copy too. Rebuild the existing application without recreating unrelated services:

```bash
cd /mnt/odysseus-fateblade
docker compose build odysseus
docker compose up -d --no-deps odysseus
```

If the optional residency monitor is installed, add `--maya-glm-url http://127.0.0.1:8093` to its existing allowlisted invocation and restart that monitor after preserving its unit. Do not assume gateway full-content logging was installed simply because its code exists; use the existing request-journal installation guide if needed.

## 7. Stop competing models and prove small-context inference

If Operations reports “The host operation failed”, this is a helper exception, not evidence of a model memory failure. First validate the host management file without printing its contents:

```bash
python3 -m json.tool "$HOME/.config/odysseus-operations/management.json" >/dev/null
```

A successful check prints nothing. A JSON error reports its line/column; merge the GLM entries into the existing object rather than appending a second JSON object. Current helpers report malformed configuration directly and include the exception type for unexpected failures. If the old generic wording persists after pulling and rebuilding, verify that the configured host helper actually points to the updated checkout. Do not paste `.env`, the full management file or credential-bearing logs when reporting the error.

Use Management to unload Strata and all Ollama models, stop DwarfStar and stop Maya DS41F. Confirm their processes and allocations have actually fallen before loading GLM. Do not stop LiteLLM. The profile controls refuse a known competing Maya model and never stop it for you; direct manual systemd commands bypass that application guard.

Start **Maya GLM** through Management. Inspect startup:

```bash
journalctl --user -u maya-glm53.service -n 160 --no-pager
curl --fail http://127.0.0.1:8093/health
curl --fail http://127.0.0.1:8093/v1/models
free -h
vmstat 1 10
```

Read the complete allocation/tier/pool messages. Native GLM must be ready at 8192, not another model accidentally listening on this port. Monitor available host RAM, GPU allocations, swap-in/out and SSD activity during startup, prefill and decode. Retain the first allocation error; do not force a pool cap from another machine. A one-time swap-used number does not establish active swapping.

Start with a short native smoke request:

```bash
curl --fail-with-body http://127.0.0.1:8093/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"Reply with exactly: ready"}],"max_tokens":32,"temperature":0,"reasoning_effort":"none"}'
```

Check returned text, token usage and native timings. In Operations, select backend **Maya GLM**, model `glm-5.3-flash`, Small task, 256–512 output tokens, three measured runs and a separate warmup. Check task limits first. Exact limits use this profile's native token counter and the largest selected prompt, plus engine reserve; 262144 is the application cap, not remaining context. Increase prompt/context depth only after the small case succeeds and memory stays acceptable.

For comparison, record artifact size about 89.84 GiB, quantization `Maya-S-v2-IQ2_XXS`, engine `1.1.22 + Ubuntu HIP Release patch`, context and MTP mode in benchmark notes. Reuse a task-set seed for a matched workload across models. Inspect full responses in the journal and check review/code correctness; throughput is not a quality score. Prior DS41F large-review medians (25.8 native decode, 504.8 prefill, 35.69 s TTFT, 19.67 end-to-end t/s) used roughly 18K-token prompts, 128000 context and substantial draft acceptance. They are not a GLM baseline or a matched MTP-off comparison.

## 8. Optional native MTP and tool checks

**Known Linux reserved-VRAM case:** the first host test with 96 GiB firmware GPU reservation / 30.48 GiB Linux RAM produced an 8.28 GiB expert pool, 9.27 GiB pinned RAM tier and about 3 decode t/s. If startup shows this pattern, follow the [reserved-VRAM fix and rebuild guide](MAYA_GLM53_RESERVED_VRAM_FIX.md) before MTP trials. The fix is opt-in, source-pinned and verifies the active GPU reservation at runtime; it is not needed for normal shared-memory layouts. Do not reduce system headroom to work around this planner mismatch.

After the baseline, open **Management → Exclusive engines → Maya GLM**. Set **Native MTP speculative decoding**, choose **Review changes**, inspect the context/MTP diff and choose **Apply changes**. Confirm the restart. A running service restarts automatically; a stopped service saves the setting without starting. The host job backs up the isolated config and attempts recovery if startup verification fails. The switch changes only MAYA_GLM_MTP_DECODE (and its equivalent legacy alias); the reserved-VRAM setting and system headroom remain intact. No engine rebuild is needed to switch modes.

All three exclusive engines—Maya DS41F, Maya GLM and DwarfStar—share Start, Stop, Refresh status, Logs and reviewed settings controls in one desktop row. They stack on smaller screens. A separator places Ollama and Strata in **Shared engines**. Dashboard service starts and shared-engine model loads refuse known conflicts rather than stopping other models automatically. DwarfStar service changes now use the same durable host-job queue. Direct host commands and external callers remain outside these dashboard controls.

Repeat the same task-set seed, task sizes, output cap, context and sampling, recording the MTP mode in notes. Check native draft counts/acceptance where reported and inspect output quality. Variables MAYA_GLM_NO_MTP / MAYA_GLM_NO_SPEC (or STRATA_ equivalents) disable by presence, even the text `0`; enabling MTP refuses conflicting profile settings rather than silently removing them. Parallel sequences, an external drafter or zero draft length can also prevent native MTP. If the saved mode differs from the actual running child engine, the settings card asks for a restart instead of reporting a misleading switch state. Inspect unusual service environment settings if startup verification fails; do not unset the global user-manager environment blindly.

Run the shared native text and structured tool-call acceptance check while GLM is ready:

```bash
cd /mnt/odysseus-fateblade
"$HOME/.local/share/odysseus-operations/venv/bin/python" scripts/odysseus-evaluate-model.py \
  --backend maya_glm --base-url http://127.0.0.1:8093 --model glm-5.3-flash \
  --management-config "$HOME/.config/odysseus-operations/management.json" \
  --label 'GLM Maya-S-v2; v1.1.22 Ubuntu HIP Release; context8192; MTP-on' \
  --output "$GLM_BACKUP/glm-mtp-on-acceptance.json"
```

Use a new output filename and the correct MTP label each time. This performs repeated text fixtures plus a fixed `add(a,b)` tool exchange; it does not execute arbitrary model-generated commands. Inspect `arguments_correct`, the final answer (it should use the tool result 5), text/code correctness and native timings. It does not establish agent capability by itself. Run a small project/build/test trial before treating the model as a coding backend.

In Models & routing, add an explicit alias such as `pa-glm-flash` with LiteLLM model `openai/glm-5.3-flash` and base URL `http://127.0.0.1:8093/v1`, using the existing Review, Probe, Apply flow. Keep it out of automatic fallbacks until validated. This loopback route assumes the existing host-network LiteLLM deployment; a bridge-network gateway needs a host-reachable address instead. A probe may query the model, so only run it after GLM is ready. Confirm gateway caller/model attribution in the request journal separately from native Operations benchmark attribution.

## 9. Roll back without removing existing models

Stop GLM through Management and confirm `maya-glm53.service` and its child engine exited. Leave its source/models available for inspection; no deletion is needed. Restart DS41F or the previous control explicitly and verify its original endpoint. Remove only the `maya_glm` service/setup and endpoint additions from configuration if disabling this integration, preserving all other entries; remove the optional gateway route and monitor argument through their reviewed procedures.

If reverting the dashboard branch/image, preserve newer application data and the saved private configuration first. The GLM service is not boot-enabled by this guide. Its unit, checkout and artifacts do not replace the working ROCm stack or DS41F files. Local fixture tests for this integration are not a substitute for the device's startup, sustained memory, tool correctness and task-quality checks.
