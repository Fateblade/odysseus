# Set up Maya DeepSeek V4.1 Flash XS

Updated 2026-10-24 for the Ubuntu Ryzen AI Max+ 395 / Radeon 8060S (`gfx1151`), 128 GB class host. Odysseus branch: `feature/linux-ai-operations-maya-xs`, based on DwarfStar commit `dafdb8f2`, including its measurement scoreboard. Fetch this branch on Ubuntu before running its helper scripts; the Windows checkout does not update the server automatically.

This is an isolated experiment alongside the working V4 Flash 0731 Q2 control. The user reports a successful host build with Ubuntu HIP 7.1 and clang 21 after the three layout/header corrections below. Model files subsequently verified and inference ran, but the first measurements used unoptimized HIP kernels: CMAKE_HIP_FLAGS_RELEASE was empty. The corrected HIP Release build subsequently reached 17.2 native decode tokens/s in a short probe (1.599 seconds total for 16 output tokens); apply the explicit optimization below before further measurements. Do not replace the working ROCm, Strata, Ollama or DwarfStar installations to make the experiment fit.

## Verified pins and capacity

- Maya release `v1.1.22`, commit `ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197`.
- Model repository `peasantsmith/DeepSeek-V4.1-Flash-1017-Maya-GGUF`, revision `c273fcbdda76a1db074c2f414cb70edbfca2f2ef`.
- Five Maya-XS GGUF shards plus `dspark-xs2.bin` and `dspark-xs2.json`: **280,878,132,974 bytes**. Sizes and publisher SHA-256 values are pinned in `scripts/maya-xs-artifacts.json` and were checked against HF metadata; the small drafter JSON was also hashed. Downloaded weight bytes must still be verified locally.
- A bounded remote read of the pinned first shard confirmed GGUF v3 and `general.architecture=deepseek41`. Maya's packer checks that architecture again locally.
- Publisher planning figures: 115.3 decimal GB main tensors (about 107.4 GiB), 4.3 GB drafter (about 4.0 GiB), 161.3 GB SSD Engram. Context, runtime buffers, page cache and the OS are additional. This does not establish full residency within the existing GPU allocation pool.

XS keeps every routed expert using IQ1_S; it is a different quantization and checkpoint from the working control. Publisher next-token agreement/NLL diagnostics are not task accuracy. The DwarfStar V4.1 Q2 main-weight size must not be applied to XS. Local throughput, quality and residency remain unmeasured.

## 1. Inspect and preserve the host

Run Bash commands on Ubuntu as the existing service owner (`seth`). Finish all host jobs and active requests first. These examples assume `/mnt/odysseus-fateblade` is the server checkout. Keep actual paths if they differ.

```bash
cd /mnt/odysseus-fateblade
git status --short
git rev-parse HEAD
cat /etc/os-release
hipcc --version
command -v cmake ninja g++ python3
rocminfo > "$HOME/maya-xs-rocminfo.txt"
grep -E 'gfx1151|GLOBAL|Allocatable|Size:' "$HOME/maya-xs-rocminfo.txt"
free -h
df -h /mnt/ai-models "$HOME"
ss -ltnp '( sport = :8092 )'
systemctl --user status dwarfstar.service --no-pager
```

Read the complete GPU allocation report and confirm `/dev/kfd`/render permissions. Port 8092 must be free; substitute another port consistently if needed. Plan at least 350 GiB free on the model filesystem for this experiment, plus build/log room elsewhere. This is an initial storage margin, not a measured cache bound. Do not count swap as model capacity or alter boot/kernel memory settings.

Back up private configuration and the running application image:

```bash
umask 077
export MAYA_BACKUP="$HOME/.local/share/odysseus-maya-xs/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$MAYA_BACKUP"
git rev-parse HEAD > "$MAYA_BACKUP/odysseus-base.txt"
cp -p .env "$MAYA_BACKUP/odysseus.env"
cp -p "$HOME/.config/odysseus-operations/management.json" "$MAYA_BACKUP/management.json"
if test -f docker-compose.override.yml; then
  cp -p docker-compose.override.yml "$MAYA_BACKUP/docker-compose.override.yml"
fi
MAYA_OLD_CONTAINER=$(docker compose ps -q odysseus)
test -n "$MAYA_OLD_CONTAINER"
MAYA_OLD_IMAGE=$(docker inspect --format '{{.Image}}' "$MAYA_OLD_CONTAINER")
docker image tag "$MAYA_OLD_IMAGE" odysseus-fateblade:before-maya-xs
printf '%s\n' "$MAYA_BACKUP"
```

Stop if backup/image tagging fails. Preserve the printed backup path between sessions. Backups contain credentials; do not paste their contents into chat. Preserve your existing database/data backups.

## 2. Install the reviewed Odysseus branch

Fetch and switch to the Maya branch in a clean server checkout. Preserve local edits; do not reset them. The commands below create the local tracking branch on first use and update it on later runs.

```bash
git fetch origin
if git show-ref --verify --quiet refs/heads/feature/linux-ai-operations-maya-xs; then
  git switch feature/linux-ai-operations-maya-xs
else
  git switch --track -c feature/linux-ai-operations-maya-xs origin/feature/linux-ai-operations-maya-xs
fi
git pull --ff-only
test -f scripts/odysseus-verify-maya-xs.py
test -f scripts/maya-xs-artifacts.json
```

Both the verifier and its adjacent artifact manifest are versioned on this branch. A missing verifier at `/mnt/odysseus-fateblade/scripts/odysseus-verify-maya-xs.py` means the server checkout has not received this integration, or its path differs. Update the server checkout before continuing; no separate SCP transfer is needed.

## 3. Check Maya without downloading weights

Use a new directory. If it already exists, inspect its origin, revision and edits instead of overwriting it.

```bash
git clone --branch v1.1.22 --depth 1 https://github.com/mw00/project-maya.git "$HOME/project-maya-xs"
cd "$HOME/project-maya-xs"
test "$(git rev-parse HEAD)" = ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197
git checkout --detach ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197
./maya.sh --backend hip --model DeepSeek-V4.1-Flash-1017 --ds4-quant maya-xs --gpu 0 --check
```

Inspect GPU numbering; use the actual gfx1151 device. The launcher creates its private `.venv`; setup uses private Python dependencies and pinned auxiliary source. It does not install system packages. Prerequisites include Python 3.10+ with venv/pip, CMake 3.24+, a C++20 compiler, ROCm 7 HIP and matching hipBLAS/header dependencies. The pinned installer recognises some distribution layouts, but misses Ubuntu clang 21 under `/usr/lib/llvm-21/bin` and hipBLAS under `/usr/lib/x86_64-linux-gnu`. On the observed HIP 7.1 / Ubuntu clang 21 host, use the guarded correction below; setting `ROCM_PATH=/usr` alone does not fix these two checks.


### Correct Ubuntu LLVM 21 detection

Observed: `hipcc --version` reports HIP 7.1.52801 and Ubuntu clang 21.1.8; the compiler is `/usr/lib/llvm-21/bin/clang++`, and hipBLAS is `/usr/lib/x86_64-linux-gnu/libhipblas.so`. The installer can therefore reject existing prerequisites. No package replacement or system symlink is needed for this detection error.

From the updated Odysseus source on Ubuntu, apply the guarded patch to the isolated Maya checkout:

```bash
python3 /mnt/odysseus-fateblade/scripts/odysseus-patch-maya-ubuntu.py "$HOME/project-maya-xs"
cd "$HOME/project-maya-xs"
ROCM_PATH=/usr ./maya.sh --backend hip --model DeepSeek-V4.1-Flash-1017 --ds4-quant maya-xs --gpu 0 --check
```

The helper checks the installed paths and the pinned `maya.py` source hash before adding the two Ubuntu search locations and explicit `-O3 -DNDEBUG` HIP Release flags. It saves `maya.py.before-ubuntu-layout`, refuses unrelated source edits, and permits a repeat invocation. Keep `ROCM_PATH=/usr` in this terminal for subsequent setup (`export ROCM_PATH=/usr`). Record this local source patch with the engine revision. Passing discovery does not establish that CMake, device libraries, GPU kernels or model inference work on this host; keep the first subsequent build error if one occurs.


On-host result after this patch: HIP prerequisites pass with ROCm root `/usr`, gfx1151, HIP 7.1 and Ubuntu clang 21.1.8. The precheck reports roughly 30 GiB CPU-visible RAM, 25 GiB available, and a 96 GiB GPU pool. The RAM figures come from `/proc/meminfo`; this split is consistent with a large reserved GPU allocation and does not by itself establish missing physical memory. The generic APU message is not a complete description of the DeepSeek engine: `maya-ds4` also budgets GPU allocations from HIP memory queries and sizes its host tier separately. Do not assume the two reports are independent additional capacity, or conclude that all experts fit from either one. Build/download with `--no-start` can proceed; inspect actual allocation/tier logs and both memory pools before declaring residency. This discovery result alone does not prove residency; the later successful build is recorded below.

To undo this patch, stop Maya and restore the saved original `maya.py`. Do not update the isolated upstream checkout while it contains this deliberate local edit without preserving the patch.

If a prerequisite/header/compiler check fails, keep the first error and stop. The old DwarfStar rocWMMA workaround is not proof that Maya builds. Resolve dependencies without replacing the control's working packages. This is the host-specific ROCm 7.1 compatibility gate.


### Locate ROCm device bitcode for the compiler test

Observed next build failure: CMake finds `/usr/lib/llvm-21/bin/clang++`, but clang reports that it cannot find the ROCm device library. This is a separate device-bitcode search problem; the hipBLAS shared library is not the missing component. Locate the installed package's `ocml.bc` and pass its containing directory explicitly to the existing isolated build cache:

```bash
cd "$HOME/project-maya-xs"
MAYA_DEVICE_LIB_FILE=$(dpkg-query -L rocm-device-libs-21 2>/dev/null | grep '/ocml\.bc$' | head -n 1)
if test -n "$MAYA_DEVICE_LIB_FILE" && test -f "$MAYA_DEVICE_LIB_FILE"; then
  MAYA_DEVICE_LIB_DIR=$(dirname "$MAYA_DEVICE_LIB_FILE")
  printf 'Device bitcode: %s\n' "$MAYA_DEVICE_LIB_DIR"
  ROCM_PATH=/usr .venv/bin/cmake -S . -B build-hip \
    -DCMAKE_HIP_FLAGS_RELEASE:STRING="-O3 -DNDEBUG" \
    -DCMAKE_HIP_FLAGS:STRING="--rocm-path=/usr --rocm-device-lib-path=$MAYA_DEVICE_LIB_DIR"
else
  printf 'No installed rocm-device-libs-21/ocml.bc found. Inspect package inventory before installing anything.\n'
  dpkg-query -W '*rocm*device*' 2>/dev/null
  find /usr/lib -name ocml.bc -print 2>/dev/null
fi
```

If configuration succeeds, rerun the same setup command below with `export ROCM_PATH=/usr` still set. The explicit flags persist in `build-hip/CMakeCache.txt`; Maya's pinned Linux build command does not override them. If the build directory is recreated, supply those flags again. Setting `HIPFLAGS` alone after a failed configure may be ignored because `CMAKE_HIP_FLAGS` is already cached ([CMake HIPFLAGS documentation](https://cmake.org/cmake/help/latest/envvar/HIPFLAGS.html)). Do not use `-nogpulib` to bypass device-library validation. If no matching installed bitcode is found, retain the inventory output before deciding on package changes. The user subsequently passed configuration and completed the engine build after also applying the rocWMMA correction below.


### Use complete matching rocWMMA headers

Observed after device-bitcode configuration: compilation reaches `glm_batch.cu`, then `/usr/include/rocwmma/rocwmma.hpp` fails to include `internal/accessors.hpp`. This matches the incomplete Ubuntu rocWMMA 7.1.0 package encountered in the DwarfStar setup. Maya includes rocWMMA in both `glm_batch.cu` and `ds4_iq.cu`; both are compiled as HIP by the pinned CMake profile. Supply the complete matching upstream headers through HIP compiler flags, preserving the established bitcode flags and system installation.

```bash
(
  set -e
  if ! test -e "$HOME/rocwmma-7.1.0"; then
    git clone --depth 1 --branch rocm-7.1.0 \
      https://github.com/ROCm/rocWMMA.git "$HOME/rocwmma-7.1.0"
  fi
  test "$(git -C "$HOME/rocwmma-7.1.0" rev-parse HEAD)" = 27a847f610bfdb4e58dd257889734168b0ef8626
  test -f "$HOME/rocwmma-7.1.0/library/include/rocwmma/internal/accessors.hpp"
  git -C "$HOME/rocwmma-7.1.0" diff --quiet HEAD -- library/include
  cd "$HOME/project-maya-xs"
  export ROCM_PATH=/usr
  .venv/bin/cmake -S . -B build-hip \
    -DCMAKE_HIP_FLAGS_RELEASE:STRING="-O3 -DNDEBUG" \
    -DCMAKE_HIP_FLAGS:STRING="--rocm-path=/usr --rocm-device-lib-path=/usr/lib/llvm-21/lib/clang/21/amdgcn/bitcode -I$HOME/rocwmma-7.1.0/library/include"
  .venv/bin/cmake --build build-hip --target maya-ds4 -j 1
)
```

The bitcode path above was found on this host in Ubuntu package `rocm-device-libs-21` version `7.1.1+dfsg-0ubuntu1`. The subshell stops on failed checkout/header/configuration checks. Reuse an existing clean matching rocWMMA checkout; do not replace an existing directory. No separate rocWMMA build or change under `/usr/include` is required. On successful engine build, rerun the full Maya setup command with `--no-start` to finish its pack/model/configuration stages. Keep the header checkout for subsequent builds. The user subsequently reported a successful engine build with these flags. GPU inference still requires on-host validation.


### Verify HIP Release optimization before measuring

Confirmed from the first slow deployment: `CMAKE_BUILD_TYPE=Release` and C++ compilation used `-O3`, but `CMAKE_HIP_FLAGS_RELEASE` was empty and actual `HIP_FLAGS` contained no optimization option. Those GPU kernels were compiled with clang's default optimization level rather than an optimized Release configuration. Three 256-token runs spent approximately 1183 seconds in native decode, with about 99.8% expert-cache hits. This establishes a build misconfiguration and invalidates those runs as an optimized performance baseline. The subsequent optimized 16-token smoke test measured 17.2 native decode tokens/s and 1.599 seconds total; repeated matched benchmarks and agent quality still need validation.

Set the language-specific Release flags explicitly. Existing builds keep their bitcode/header search flags in `CMAKE_HIP_FLAGS`; the following command adds configuration flags without replacing them:

```bash
cd "$HOME/project-maya-xs"
export ROCM_PATH=/usr
.venv/bin/cmake -S . -B build-hip \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_HIP_FLAGS_RELEASE:STRING="-O3 -DNDEBUG"
for target in maya-ds4 strata_kernels; do
  grep -E '^HIP_FLAGS = .* -O3( |$)' "build-hip/CMakeFiles/$target.dir/flags.make"
done
```

Both targets must show `-O3` in `HIP_FLAGS`. Inspect actual generated flags, not just the Release build-type label or CXX_FLAGS. Stop if either is missing optimization or a later conflicting optimization flag appears. The updated Ubuntu helper also passes these flags during future setup builds, but applying it to an already stamped build alone does not guarantee recompilation: explicitly configure and rebuild the current cache.

Finish host jobs and requests, stop an existing running `maya-xs.service`, and keep a copy of `build-hip/maya-ds4` before rebuilding. Then run `.venv/bin/cmake --build build-hip --target maya-ds4 -j 2`. Changed compiler flags cause affected HIP objects to rebuild; no model download or cache deletion is required. Restart the service only if it was previously running, verify readiness, and begin with one short 16-token request with thinking disabled. Record native decode timings, output correctness and the new startup expert-calibration timings before a full benchmark. Preserve the original binary for rollback and keep speculation off while isolating this change.

References: [Clang optimization options](https://clang.llvm.org/docs/CommandGuide/clang.html#code-generation-options), [CMake language configuration flags](https://cmake.org/cmake/help/latest/variable/CMAKE_LANG_FLAGS_CONFIG.html).


### Optimized smoke test result

After rebuilding with explicit HIP `-O3 -DNDEBUG`, the user supplied a successful HTTP 200 response for `Count from 1 to 8.` with 12 uncached prompt tokens, 16 output tokens, thinking disabled and no speculative drafts. Native prefill took 665.4 ms; native decode took 929.2 ms (17.2 tokens/s); client total time was 1.599148 seconds (about 10.0 end-to-end tokens/s). The output was the correct count prefix through 6 and stopped at the intentional token cap (`finish_reason=length`).

Latest startup calibration changed GPU expert cost from 8.942 ms in the prior build to 0.025 ms, and enabled some CPU-lane expert streaming on 40 layers. The initial 20-minute runs used unoptimized HIP kernels and are unsuitable as a hardware capability conclusion. The approximately 80-fold difference in reported native decode rates is between different short/long probes, not a matched throughput comparison. Preserve [the smoke result](benchmarks/maya-deepseek41-xs-optimized-smoke.json), then repeat the original three-run 256-token benchmark with identical prompt, context, sampling and no-drafter settings. Keep longer prompt depths, sustained memory headroom and tool correctness as separate acceptance checks.


### Repeated optimized 256 token benchmark

The user then repeated three measured runs after one excluded warmup: each produced 256 tokens from a 15-token prompt at 4096 context, temperature 0, seed 42 and thinking disabled, with zero speculative drafts. Median native decode was **16.9 tokens/s**, median end-to-end rate **16.78 tokens/s**, and median elapsed time **15.258 seconds**. Individual native decode rates were 16.9, 16.9 and 16.8 tokens/s. This confirms stable recovery across these short requests; the previous approximately 1187-second runs were unoptimized.

Measured TTFT was approximately 66 ms with 14 of 15 prompt tokens reused; it is a warm-prefix result. The uncached warmup had approximately 672 ms TTFT, 664 ms native prefill and 16.6 native decode tokens/s. The measured `prefill_tps` around 16.2 concerns only one fresh token and must not be extrapolated to long prompts. The post-sample CPU-visible memory snapshot reported approximately 20.5 GiB available and 2.1 MiB swap occupied; it does not establish peak GPU or host memory use. Preserve [the full user-supplied benchmark export](benchmarks/maya-deepseek41-xs-optimized-benchmark.json). Tool correctness, fresh 2–4K prompts and deeper contexts remain separate acceptance gates; keep drafting disabled until those are established.

## 4. Build and download the explicitly selected XS profile

Build/download can run without inference, but schedule it outside host benchmark jobs. Setup builds `maya-ds4` before its model download step. `--no-start` and `--drafting off` avoid generation/calibration during installation.

```bash
./maya.sh --setup --backend hip --model DeepSeek-V4.1-Flash-1017 \
  --ds4-quant maya-xs --gpu 0 --context 4096 --no-vision --drafting off \
  --model-dir /mnt/ai-models/maya-xs/Maya-XS \
  --host 127.0.0.1 --port 8092 --yes --download-model --no-start
test -x build-hip/maya-ds4
test -f maya-ds4-hip.json
python3 /mnt/odysseus-fateblade/scripts/odysseus-verify-maya-xs.py /mnt/ai-models/maya-xs
```

All seven files must pass size and SHA-256 validation; hashing reads about 281 GB from storage. Keep the five GGUFs under `Maya-XS/` and the matching XS drafter pair under adjacent `dspark/`. Do not substitute Maya-S or the FP4 drafter. The upstream packer indexes the files in place and checks `deepseek41`; do not remove or relocate indexed shards afterwards without rebuilding the pack.

Inspect `maya-ds4-hip.json` locally: backend HIP, `build-hip/maya-ds4`, correct pack path, `--max-context 4096`, vision off, DSpark disabled, model name `deepseek-v4.1-flash`, loopback host and port 8092. Keep a private copy in `$MAYA_BACKUP`. Retain the install/build output and source hash. Do not run calibration yet.

## 5. Stop competing models and prove small-context startup

Use existing Management controls to unload Strata and every Ollama model, then stop DwarfStar. Confirm no other coding client is generating. Do not stop or replace the LiteLLM gateway. Keep the control's model files and service unit intact.

```bash
systemctl --user stop dwarfstar.service
curl --fail http://127.0.0.1:8081/health
curl --fail http://127.0.0.1:11434/api/ps
free -h
ps -eo pid,comm,rss,args --sort=-rss | head -20
```

Strata should report unloaded and Ollama's `models` should be empty. Verify the DwarfStar process has exited and GPU/physical memory has fallen. Inspect unavailable endpoints separately; an unreachable daemon alone does not prove that every competing GPU process exited.

Install the isolated user service from the updated Odysseus checkout:

```bash
cd /mnt/odysseus-fateblade
install -d -m 700 "$HOME/.config/systemd/user"
install -m 600 scripts/maya-xs.service.example "$HOME/.config/systemd/user/maya-xs.service"
systemctl --user daemon-reload
systemctl --user start maya-xs.service
journalctl --user -u maya-xs.service -n 100 --no-pager
```

The unit runs Maya's Python server directly with its HIP JSON; it does not invoke setup, download, update or auto-restart. It is deliberately not enabled at boot. The 16 GiB **pre-load** MemAvailable check only catches gross competition; it cannot predict post-load residency. Existing user-session/linger behavior applies; inspect it rather than changing login policy automatically.

In another terminal watch `free -h`, `vmstat 1`, available GPU telemetry and Maya logs throughout loading and inference. Preserve an operational margin of at least 8 GiB physical MemAvailable for this initial experiment. Stop Maya if headroom falls below that margin, swap-in/out grows, or the host becomes unresponsive. This margin is a conservative experiment stop condition, not evidence the model fits. Do not force-fit with swap or driver/kernel changes. Engram SSD page-cache reads and unified GPU accounting can overlap; do not add them as independent memory allocations.

After loading, prove readiness and a real answer:

```bash
curl --fail http://127.0.0.1:8092/health
curl --fail http://127.0.0.1:8092/v1/models
curl --fail http://127.0.0.1:8092/api/context
curl --fail http://127.0.0.1:8092/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"Reply with exactly: ready"}],"max_tokens":32,"temperature":0,"reasoning_effort":"none"}'
```

Expect health `status=ok`, `unloaded=false`, the correct model, catalog `status.value=loaded`, `meta.n_ctx=4096`, and an actual answer. An HTTP listener or catalog alone is insufficient. Inspect logs for expert tiers, Engram behavior and allocations; do not describe the main experts as resident until observed under real prompt depths.

## 6. Connect Operations and optional gateway routing

Add this variable to the existing deployment `.env`, preserving all other values:

```dotenv
ODYSSEUS_OPERATIONS_MAYA_URL=http://127.0.0.1:8092
```

The URL is interpreted on the SSH helper host, without `/v1`. It is independent of `ODYSSEUS_OPERATIONS_DWARFSTAR_URL`. This profile uses a loopback-only native endpoint without native API authentication, matching the local helper contract. Operations does not accept a Maya API key from the browser; a native key-protected deployment needs an explicit host-side authentication extension. Keep existing dashboard, SSH and gateway authentication intact; do not expose 8092 on the network.

Merge these entries into `~/.config/odysseus-operations/management.json`; do not replace the file:

```json
{
  "services": {
    "maya": {"unit": "maya-xs.service", "scope": "user"}
  },
  "maya_setup": {
    "config_file": "/home/seth/project-maya-xs/maya-ds4-hip.json",
    "maximum_context": 4096
  }
}
```

Keep all existing services, jobs, scripts and audit settings. Monitoring configuration alone does not grant service permissions. Substitute your actual home/config path. The standard `~/project-maya-xs/maya-ds4-hip.json` location is detected automatically, so existing installations at that location need no management-file edit. A custom user-unit location can be supplied as `maya_setup.unit_file`; the default is `~/.config/systemd/user/maya-xs.service`. Settings control requires the direct `serve.server --config ...` user unit above, without drop-ins or pending daemon reloads.

```bash
cd /mnt/odysseus-fateblade
ODYSSEUS_IMAGE=odysseus-fateblade:local docker compose build odysseus
ODYSSEUS_IMAGE=odysseus-fateblade:local docker compose up -d --no-deps --pull never odysseus
```

Check Operations overview and Management → Maya residency. Start/stop/restart and context applies use the existing one-active-host-job queue. Startup rejects known loaded Strata/Ollama/DwarfStar models, but does not discover or stop every external GPU application. Readiness and live context are distinct from physical residency.

**Maya residency** sits beside **DwarfStar residency** on desktop and stacks on narrow screens. Its settings load automatically. Set **Maximum context (tokens)** and **DSpark speculative decoding → Off / On**, then choose **Review settings → Apply settings**. Editing either value invalidates the previous review. A running Maya service restarts, interrupting requests; a stopped service saves its settings without starting. Apply only when clients are idle. Settings use the same one-active-host-job queue as benchmarks and service controls; watch progress in **Scripts & TPS tests**.

Context can be raised from Operations beyond the original 4,096-token setting. A running service supplies the native context limit; stopped editing uses Maya's conservative 262,144-token fallback. This limit does not establish that a context fits in memory. The approved `maya_setup.maximum_context` value is raised when necessary and saved with the run config. Increase context in measured steps and check memory with sustained, uncached prompts.

DSpark **On** saves `--dspark on` and requires MTP tensors in the selected pack's `tensors.txt`. It does not download or modify model artifacts. **Off** saves `--dspark none`, releasing drafter memory at the next managed restart. The existing non-speculative baseline is approximately 16.9 decode tokens/s; DSpark may improve or reduce throughput, so compare identical fixtures. After restarting, Operations verifies the model, context and drafter GPU residency; actual draft acceptance is measured by a benchmark.

The worker checks the reviewed files again, backs up both JSON files under the host job's `maya-backup` directory, preserves other settings and attempts to restore the previous files and service state on failure or cancellation. Configuration edited externally during a job is never overwritten by automatic recovery. Inspect the job result and service logs if recovery is unverified. Keep Maya's native UI/config edits idle during an Operations change. Configs outside the managed local profile retain the older ready-only native context reload control; DSpark stays unavailable there, and cancelling that native reload job does not cancel Maya's background reload.

For optional routing, append a deployment through **Models & routing**:

- Public name: `maya-deepseek41-xs`
- Backend model: `openai/deepseek-v4.1-flash`
- Endpoint: `http://127.0.0.1:8092/v1` (only if LiteLLM uses host networking; otherwise use its established host address).

Test the endpoint, review the diff and apply. Keep existing aliases/fallbacks and the default model unchanged. Do not add automatic fallback to a service normally stopped. Test a real streamed answer and tool cycle through the gateway; catalog probing does not establish these capabilities.

## 7. Compare the control and XS sequentially

Do not run the two models together. Finish all dashboard jobs before using the standalone evaluator; it does not own the dashboard job lock. Run no other host jobs or agent requests until it finishes. Alternatively save it under the configured scripts root and invoke it through a saved Operations Python-script job with explicit arguments to use that queue.

First run the working DwarfStar 0731 control at 4096 context with Maya stopped. Save its engine SHA, checkpoint/quantization, active context, ROCm packages, memory observations, concurrency and cache conditions. Then stop the control, confirm memory release, and repeat with Maya at 4096. The evaluator uses identical fixed arithmetic, C# and WPF prompts, 256 output tokens, temperature 0, seed 42 and three measured runs after one warmup per fixture. It disables thinking using each engine's own parameter. Engines need not implement seeds identically; this is not an engine-only comparison because weights and quantization differ.

```bash
cd /mnt/odysseus-fateblade
install -d -m 700 "$HOME/.local/share/odysseus-maya-xs/results"
PYTHON="$HOME/.local/share/odysseus-operations/venv/bin/python"
# Run this command only while the control alone is ready:
"$PYTHON" scripts/odysseus-evaluate-deepseek.py \
  --backend dwarfstar --base-url http://127.0.0.1:8000 --model deepseek-v4-flash \
  --management-config "$HOME/.config/odysseus-operations/management.json" \
  --label 'V4 Flash 0731 Q2; record actual engine SHA; ctx4096; no drafting' \
  --output "$HOME/.local/share/odysseus-maya-xs/results/control.json"
# Stop the control, verify released memory, start Maya and verify readiness before this command:
"$PYTHON" scripts/odysseus-evaluate-deepseek.py \
  --backend maya --base-url http://127.0.0.1:8092 --model deepseek-v4.1-flash \
  --management-config "$HOME/.config/odysseus-operations/management.json" \
  --label 'V4.1 Flash1017 Maya-XS; ffe2e5e5; ctx4096; DSpark off' \
  --output "$HOME/.local/share/odysseus-maya-xs/results/maya-xs-no-draft.json"
```

Use unique output names; the evaluator refuses to overwrite an existing result. Results are private JSON. It runs a fixed `add(2,3)` tool cycle only, checks exact integer arguments, preserves the returned tool-call ID, and submits `5`. It never executes arbitrary model-generated code. Inspect the final answer and transcripts for correct tool-result use; malformed responses are failures. Text prompts/responses are recorded by the existing benchmark journal when `audit_capture_content=true`. C# and WPF answers require human review for edge cases, complexity, UI threading and cancellation; no automatic task-quality percentage is claimed. The arithmetic expected answer is 3973. The 256-token cap can truncate code; mark truncation explicitly or repeat both backends with an identical larger cap.

Record TTFT, end-to-end TPS, native prefill/decode rates when supplied, output usage and errors. No rate is inferred by counting SSE chunks. Maya `timings.draft_n` and `draft_n_accepted` yield speculative acceptance only when actually supplied; otherwise it stays null. DwarfStar's missing native timing stays null. Warmup does not clear prefix, disk or OS caches.

Save Maya `/metrics` and service logs before/after each isolated test, with `free`, `vmstat`, GPU telemetry and SSD device counters (`iostat -dx 1` if already available). `/metrics` contains `engine`, `live`, `tiers`, `hardware`, `requests` and `reload`; tier `disk_mb` and `engram_ms` are engine-reported when present. Do not treat missing fields as zero. Separate host SSD reads/Engram cost from expert-cache traffic and record actual tokenized prompt depth. Do not attribute concurrent external traffic to this request.

Only after a stable no-drafter baseline, stop Maya, back up its JSON and change its `args` pair `--dspark none` to `--dspark on` (the pinned engine's supported drafting mode). Confirm the packed drafter is the XS pair and available physical/GPU headroom remains sufficient. Restart, repeat with a new result name and record the exact args. If the pinned engine rejects that mode, stop and inspect its `--help` rather than borrowing DwarfStar flags. Do not run auto-calibration while other models or host jobs are active.

For deeper prompts, first prove 4K, then raise **Maximum context** in Operations deliberately and test in steps using identical fixtures and measured prompt-token counts on both engines. Check post-load and sustained-prompt memory at each step. The existing 1792738674 benchmark rejection was a host-operation rejection; this integration makes no claim about its cause or a 256K allocation failure.

## 8. Journal and rollback

Maya Operations jobs and dashboard benchmarks use the existing request journal. For continuous residency observation add `--maya-url http://127.0.0.1:8092` to the installed `odysseus-audit-monitor.service` ExecStart, preserving its current arguments, then daemon-reload and restart that observer while no host job is active. Disabled Maya is not probed. Explicit native unloaded state records an empty model list; unavailable/reloading states remain unknown rather than invented unloads.

Gateway content capture requires the separate installation in `INFERENCE_REQUEST_JOURNAL.md`; this change does not install it. Native arbitrary client requests and the evaluator's fixed tool calls bypass gateway capture, although the evaluator saves tool transcripts privately. Continue using the authenticated audited gateway for external coding clients.

Normal recovery:

```bash
systemctl --user stop maya-xs.service
systemctl --user is-active maya-xs.service
ss -ltnp '( sport = :8092 )'
free -h
```

Confirm Maya's engine and server processes exited and memory fell before restarting DwarfStar or loading Strata/Ollama. Never start competing models just because a stop was requested. Retain model files for diagnosis. Restore Maya's backed-up JSON only with its service stopped if a context/drafting experiment fails.

For application rollback, wait for host jobs/native reloads to settle, restore `.env` and management JSON from the recorded private backup (reconcile any newer changes first), and recreate only Odysseus with the preserved image:

```bash
cd /mnt/odysseus-fateblade
cp -p "$MAYA_BACKUP/odysseus.env" .env
cp -p "$MAYA_BACKUP/management.json" "$HOME/.config/odysseus-operations/management.json"
ODYSSEUS_IMAGE=odysseus-fateblade:before-maya-xs docker compose up -d --no-deps --pull never odysseus
```

Restore the observer's previous arguments if changed, daemon-reload/restart it, and remove only the new gateway route if added. To roll back host helper source too, switch to the recorded Odysseus revision after preserving later edits. Do not remove volumes or downgrade host packages.

## Evidence and remaining gates

Pinned primary sources: [Maya release source](https://github.com/mw00/project-maya/tree/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197), [installer](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/maya.py), [server/API](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/serve/server.py), [artifact manifest](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/tools/ds4/download_gguf.json), [HIP guidance](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/docs/AMD_MAYA.md), [pinned model files](https://huggingface.co/peasantsmith/DeepSeek-V4.1-Flash-1017-Maya-GGUF/tree/c273fcbdda76a1db074c2f414cb70edbfca2f2ef).

Validation: 112 Operations and Ubuntu layout patch tests pass, plus desktop/mobile browser fixtures without page errors. Local fixture tests cover the documented API shapes, helper/worker endpoint propagation, native timings/token usage/acceptance, journal capture, context review/state drift and known competing-model rejection. These fixtures alone do not establish host GPU behavior. The user-supplied optimized smoke and repeated 256-token benchmark above now establish working inference and short-request decode recovery on this host. Peak residency, deeper-prompt latency and real-model tool correctness remain unvalidated; keep the control available during those checks.
