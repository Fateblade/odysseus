# Experimental Strata DS41F on Ubuntu gfx1151

This is a separate deployment on loopback port **8095**. It preserves Qwen Strata, Maya, DwarfStar and their files. The source accepts experimental gfx1151 HIP, but this exact ROCm7.1/clang21 host remains build/runtime-unverified. Keep the working ROCm stack. Stop at a compiler or kernel failure rather than replacing packages or spoofing the GPU architecture.

Read the [source contract](research/STRATA_DS41F_LINUX_INSTALL_CONTRACT.md) for immutable references and memory caveats. Strata's IST-DAS weights differ from Maya-XS; this compares deployment choices and quality, not just engines.

## 1. Isolated build gate, before downloading weights

Update the existing Odysseus checkout on branch feature/linux-ai-operations-maya-glm53. Create a **new** checkout; do not run these commands inside the existing Qwen or Maya source directories.

~~~bash
test ! -e "$HOME/strata-ds41f"
git clone --branch v0.2.9 --depth 1 https://github.com/Niko1221/Strata.git "$HOME/strata-ds41f"
cd "$HOME/strata-ds41f"
test "$(git rev-parse HEAD)" = c62d5e3a15b40ea51c96aae9e8d36928965f53ab
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt -r requirements.txt
mkdir -p third_party/llama.cpp
git -C third_party/llama.cpp init
git -C third_party/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp.git
git -C third_party/llama.cpp fetch --depth 1 origin 3cf03257f219afbe7334045ff7c6a06ac68c627d
git -C third_party/llama.cpp checkout --detach FETCH_HEAD
~~~

On retry, inspect/reuse existing directories instead of overwriting them. Python packages install into the new venv only. Reuse the complete rocWMMA7.1 headers at 27a847f610bfdb4e58dd257889734168b0ef8626, LLVM21 compiler and device bitcode already used by Maya. No apt or ROCm installation is prescribed.

Stop the router while idle, stop the other exclusive engines, and unload Qwen/Ollama before GPU tests. Ensure external direct clients are idle too.

~~~bash
# Omit this if the router has not been installed.
systemctl --user stop odysseus-inference-router.service
python3 /mnt/odysseus-fateblade/scripts/odysseus-build-strata-ds41f.py "$HOME/strata-ds41f" --jobs 1
~~~

The helper verifies source/dependency pins and effective HIP Release flags, applies the source-guarded test correction described below, builds the native engine plus three no-weight kernel tests, then runs version discovery, all tests, and an additional isolated IQ4_XS check. A successful run writes build-hip/odysseus-build-receipt.json with the binary hash and `iq_test_contract: shared-activations-v1`. Configure success alone is insufficient. Do not download weights until this gate passes. Preserve exact build/kernel errors on failure.

### IQ4_XS numerical failure on the ROCm7.1 host

The first host run built engine 0.1.55 but failed `IQ4_XS wmma vs scalar y`: 8.56e-3 in the full suite and 1.04e-2 with `--type 23`, against the unchanged 3e-3 limit. Other default IQ checks and both TQ suites passed. This does not qualify the build or establish model correctness. Keep downloads paused.

The final comparison uses independently computed intermediate activations. To distinguish accumulated gate/up differences from a down-projection kernel discrepancy, run this diagnostic with the other engines stopped:

~~~bash
set -o pipefail
python3 /mnt/odysseus-fateblade/scripts/odysseus-diagnose-strata-ds41f.py "$HOME/strata-ds41f" 2>&1 | tee "$HOME/strata-ds41f-iq-diagnostic.log"
~~~

The diagnostic helper checks the pinned revision and pristine test source, temporarily adds shared-activation comparisons, and runs both the full IQ suite and isolated IQ4_XS. It retains every original check and tolerance, restores the original source in a finally block, and rebuilds the original test binary. It does not rebuild the native engine, modify a receipt, or bypass qualification. A nonzero exit is expected with the original test. Preserve the `DIAG` lines and original failures for analysis. If interrupted by a power loss or force-kill, inspect the test source before retrying; the helper refuses local changes. This diagnostic is for the original source only, before the correction below is installed.

The host diagnostic subsequently passed both shared-input down comparisons for IQ4_XS: worst 1.44e-4 in the full suite and 1.85e-4 in isolation, against the existing 3e-3 limit. Scalar and WMMA down results also passed the CPU reference on each input. This supports propagated intermediate-activation differences as the explanation for the original end-to-end mismatch; it does not establish model quality.

The build helper now installs a test-only correction guarded by the exact upstream file SHA256. It retains the raw pipeline difference as informational, checks both down paths against each other on both shared inputs, retains the CPU checks, and adds a scalar gate/up CPU check. It additionally gates the pipeline residual `(WMMA_y - scalar_y) - (CPU_D(WMMA_h) - CPU_D(scalar_h))`, normalized by the same CPU absolute-product sum, at 3e-3. This accounts explicitly for the difference propagated through the down projection. No numerical tolerance is increased; native kernels and engine configuration remain unchanged. Nonfinite values in the added comparisons fail. The corrected source remains modified in the isolated checkout; reruns accept only the pristine file or the exact corrected file and reject unrelated edits.

After updating Odysseus, rerun the build command from step 1 with competing engines stopped. Continue to step 2 only if every corrected check and both TQ suites pass and the helper writes a receipt. The correction itself has not yet been executed on the host GPU.

## 2. Disk space and separate model installation

The chosen IST-DAS-2.00bpw / IQ2_XXS files plus Engram, drafter and metadata total **256,816,878,701 bytes**. Extra space is needed for packing, builds, partial downloads, logs and rollback. Plan at least 300 GB free, then check the installer's requirement; this margin is not a measured upper bound. Retained Qwen/Maya/GLM files are additional. These weights are streamed from SSD, not all held in RAM.

~~~bash
install -d /mnt/ai-models/strata-ds41f
df -h /mnt/ai-models/strata-ds41f
cd "$HOME/strata-ds41f"
.venv/bin/python setup.py --help
export STRATA_DSV41_EXE="$HOME/strata-ds41f/build-hip/strata-dsv41"
export STRATA_DSV41_NO_BIG_CHUNK=1
export STRATA_DSV41_ASYNC_T=8
./setup.sh --setup --yes --family deepseek41 --model IST-DAS-2.00bpw \
  --backend hip --gpu 0 --context 8192 --vision no --no-browser \
  --data-dir /mnt/ai-models/strata-ds41f --port 8095 --no-start
~~~

Use the actual GPU index printed for the 8060S. The explicit local binary avoids assuming the Linux package's gfx1200/gfx1201 coverage applies. If setup ignores gfx1151, chooses another device, replaces this binary or proposes host-package changes, stop and inspect its output. Do not point STRATA_INSTALL_DIR at Qwen. Generated files must remain isolated.

The pinned installer uses immutable revisions and size/hash validation. Our seven-file manifest provides an independent check:

~~~bash
python3 /mnt/odysseus-fateblade/scripts/odysseus-verify-strata-ds41f.py /mnt/ai-models/strata-ds41f
~~~

This reads roughly 257 GB. Duplicates, changed layouts or hash failures require inspection. Do not substitute Maya-XS packs or use packer skip-tensors/dense-override options. Retain source GGUFs.

## 3. Config and user service

Inspect the generated strata-deepseek-v4.1-flash-*.json and launcher. Copy the correct JSON to ~/strata-ds41f/odysseus-ds41f.json, retaining the original. Verify exe points to the tested local binary, model_name is deepseek-v4.1-flash, arch is deepseek41, backend is HIP, engine_ready_s is 3600, and there is one --max-context 8192 argument. Preserve pack/tokenizer paths and keep vision off.

Record experts, dspark and prefill arguments. UMA defaults may choose five experts and auto speculation; six experts is a separate full-quality trial. Drafter off still loads weights and is not equivalent to none. Avoid DS4_COPY_ALL/DS4_STORE_ZC and arbitrary RAM cache overrides. The 96GiB GPU carve-out and 30.48GiB Linux allocation must each fit their own allocations; no Maya memory patch is applied here.

~~~bash
install -d "$HOME/.config/systemd/user"
install -m 644 /mnt/odysseus-fateblade/scripts/strata-ds41f.service.example \
  "$HOME/.config/systemd/user/strata-ds41f.service"
systemctl --user daemon-reload
~~~

The unit runs the Python HTTP wrapper on loopback8095, with whole-cgroup shutdown, no native key on loopback, and restart disabled. LiteLLM remains the authenticated external entry. Do not enable at boot during qualification.

Merge into the existing management JSON, preserving its other entries:

~~~json
{
  "services": {
    "strata_ds41f": {"unit": "strata-ds41f.service", "scope": "user"}
  },
  "strata_ds41f_setup": {
    "url": "http://127.0.0.1:8095",
    "config_file": "/home/seth/strata-ds41f/odysseus-ds41f.json",
    "build_receipt": "/home/seth/strata-ds41f/build-hip/odysseus-build-receipt.json",
    "recipe": "IST-DAS-2.00bpw"
  }
}
~~~

The URL and unit must be distinct from existing engines. Rebuild/restart the Odysseus container. Management, Overview, TPS tests and scoreboard now expose **Strata DS41F (experimental)**. No new host-helper environment variable is required. Start validates the receipt against the configured binary; that proves build/kernel checks, not model fit or quality.

## 4. First load and runtime gate

Keep the router and other models stopped/unloaded initially. Start this profile in Management, then inspect its queued job and service logs. Initial readiness can take up to one hour.

~~~bash
journalctl --user -u strata-ds41f.service -f
curl -fsS http://127.0.0.1:8095/health
curl -fsS http://127.0.0.1:8095/v1/models
~~~

HTTP200 alone is insufficient: loaded must be true, model identity correct and context positive and consistent with the catalog. Monitor Linux MemAvailable, GPU allocations, SSD reads and swap-in/out. Engram is SSD-backed; some expert streaming is expected for this set. Stop on GPU faults, OOM, thrashing or wrong outputs.

~~~bash
curl -fsS http://127.0.0.1:8095/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"What is the capital of France? Reply in one sentence."}],"reasoning_effort":"none","temperature":0,"max_tokens":64}'
~~~

Verify correct text, repeated prompts, streaming, tools/JSON, cancellation/recovery and successful service stop. Do not enable automatic restart until runtime behaviour is understood.

## 5. Benchmarks, optional routing and rollback

Select backend Strata DS41F (experimental), model deepseek-v4.1-flash, a small task and 256 output tokens at8192 context. Budget checks use a conservative UTF-8 byte allowance plus template reserve; they are not exact tokenizer counts. Large tasks require a separately validated larger startup context. This initial integration deliberately does not offer an unqualified context editor for this engine.

Match task seed, prompt, output cap, context, temperature and thinking with Maya. Record quantization, experts and drafter differences. Compare quality and tool validity as well as cold load, prefill, TTFT, decode, end-to-end, caches and SSD IO. Benchmark metadata records recipe and observed experts/drafter arguments; source revision is labelled expected rather than reported by health.

Only after runtime qualification, add a distinct strata_ds41f backend at http://127.0.0.1:8095 and route in the on-demand editor. Example public router alias: local-strata-ds41f; LiteLLM model openai/local-strata-ds41f. Restart an existing router once after pulling this integration to load the new backend schema. Keep Qwen/Maya routes. Thereafter route edits use Operations as before.

The profile participates in exclusive switching and idle expiry. Qualification is checked before competitors are evicted. Release stops the entire service because an unloaded reachable server can reload on direct requests. All inference must use the router for ownership coordination. Long startup may exceed existing gateway/client defaults or the router's 900-second queue wait; begin with one caller and appropriate timeouts.

Rollback: stop this service, confirm its cgroup/endpoint stopped, remove only its optional route while idle, and restore the prior Maya/Qwen choice. Preserve logs/config/receipt. No replacement of prior model files or ROCm packages is necessary.
