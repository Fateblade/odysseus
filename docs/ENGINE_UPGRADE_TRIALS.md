# Strix Halo engine upgrade trials

Reviewed 2026-11-01. Findings and immutable upstream references are in [the research report](research/STRIX_HALO_ENGINE_UPDATES_20261101.md). No host upgrade has been performed. Keep the tested ROCm 7.1 / clang21 stack and existing model bytes. This change adds a supported **candidate build path**, not a new production default.

## What to try

| Deployment | Decision | Expected benefit and qualification |
| --- | --- | --- |
| Maya DS41F | Trial v1.1.48, `465a981579ff5e91b19ff023721c6661f3c6030b` | gfx1151 prefill, cache and unattended-start fixes. Published prefill gains are workload-specific; decode gains are unproven here. |
| Maya GLM53 | Keep v1.1.22 and the verified reserved-VRAM patch | v1.1.48 has additional budget/planner paths. Porting only our old insertion or changing its hash would not qualify full-pool residency. |
| DwarfStar | Keep `5379c1c9ac315f1540371b06a3db88491d4059ff` for performance | Latest inspected `b4e3faf5262b21db94ec309cf321640ff1eed460` has server fixes but no changed ROCm files. DSpark is a separate experiment with extra memory and sampling implications. |
| Strata | Inventory before upgrade | mw00 fork v0.1.38 and Niko v0.2.9 are different provenance. Windows gfx1151 and Linux gfx12 improvements do not establish a Linux gfx1151 gain. |
| Ollama | Inventory, then compare stable v0.40.13 if newer than installed | Device support is documented; no measured 395 speedup is established. Test flash attention and q8 KV independently, preserving f16 as control. |

## Read-only inventory

Run from the updated host checkout. Replace paths with existing source directories; an absent checkout reports unknown instead of guessing a version.

```bash
cd /mnt/odysseus-fateblade
python3 scripts/odysseus-engine-inventory.py   --source strata="$HOME/strata"   --source maya_ds41f="$HOME/project-maya-xs"   --source maya_glm="$HOME/project-maya-glm53"   --source dwarfstar="$HOME/dwarfstar"   > "$HOME/engine-inventory-before.json"
```

For actual process settings and replaced-binary detection, add `--pid NUMBER` for each native engine PID, including a Maya child engine rather than only its Python launcher. This reads only an explicit tuning-variable allowlist, never full environment or command lines. It does not start/stop services, load models, update repositories or install packages. Origin URL credentials/query strings are omitted. Check the report before sharing it: local paths and model/source identities remain visible.

Also record the actual Ollama service executable/container digest and startup backend logs. A CLI version can differ from the server; the tool uses `/api/version`. Native Ollama benchmark exports now record this server version when available; existing scoreboard records stay historical and unknown versions stay unknown. Keep the inventory JSON alongside each benchmark export.

## Isolated Maya DS41F candidate

Keep `~/project-maya-xs` intact. Create a new candidate checkout; never run these commands inside production source/build directories:

```bash
test ! -e "$HOME/project-maya-xs-1.1.48"
git clone --branch v1.1.48 --depth 1 https://github.com/mw00/project-maya.git "$HOME/project-maya-xs-1.1.48"
cd "$HOME/project-maya-xs-1.1.48"
test "$(git rev-parse HEAD)" = 465a981579ff5e91b19ff023721c6661f3c6030b
```

Follow the existing [DS41F setup guide](MAYA_DEEPSEEK41_XS_SETUP.md) for the isolated venv and reuse its known build-tool versions. Populate this checkout's `third_party/llama.cpp` at the **unchanged** revision `3cf03257f219afbe7334045ff7c6a06ac68c627d`. Keep the complete rocWMMA7.1 headers at `27a847f610bfdb4e58dd257889734168b0ef8626` and the existing LLVM21 bitcode path. Do not use an installer update or mutable latest dependency in place of those pins.

The Ubuntu patch now explicitly recognizes both reviewed pristine installer hashes. Its LLVM21 compiler lookup, multiarch hipBLAS lookup and explicit optimized HIP Release behavior have been checked against the actual v1.1.48 source. Backups must match the same version; unknown edits still fail closed.

```bash
python3 /mnt/odysseus-fateblade/scripts/odysseus-patch-maya-ubuntu.py "$PWD"
python3 /mnt/odysseus-fateblade/scripts/odysseus-build-maya-glm.py "$PWD"   --candidate-release v1.1.48 --jobs 1
```

Despite its historical filename, the build helper selects **maya-ds4 only** for this explicit candidate option. Without the option it retains the v1.1.22 GLM build contract. It verifies source, llama.cpp and rocWMMA revisions, installed prerequisites and effective `strata_kernels` HIP `-O3 -DNDEBUG` flags. This has source/command verification here, not a completed HIP build on the device.

Do not run a candidate packer over production data. Preserve the existing Maya-XS artifacts, Engram, tokenizer, pack and hashes; new Maya-XS-v2 artifacts are a separate model/quality trial. Preserve the existing generated run config and create a candidate copy only after checking its native CLI/pack compatibility. Point its executable to the candidate binary, retain text-only/no-vision, context and MTP settings, and run it on the same port **only after stopping the production service**. Never leave the existing auto-starting router able to start the production service during a candidate test: stop it while idle, confirm no other inference is active, then start the candidate manually with the preserved launch parameters. Resume the router only after the test and baseline restoration. There is no automatic production service/config rewrite in this helper.

Start at small context and inspect RAM/GPU allocations before restoring the 128K/18K baseline workload. The new HIP mmap/copy behavior must fit the 30.48GiB Linux allocation as well as the 96GiB GPU allocation. A new binary's successful startup does not establish long-context fit or model correctness.

## Controlled performance trials

Use identical model bytes, task seed, native sampler values, runtime capacity, requested output cap and MTP settings. Alternate baseline/candidate runs. Separate cold load, uncached prefill, cache reuse, TTFT, native decode and client end-to-end TPS. Compare actual output lengths and cache counts; prompt hashes alone do not guarantee a cold cache. Keep source version, tuning flags and backend logs with each export.

- DS41F: include the existing 18K code-review/128K capacity workload, streaming, tools, JSON, thinking-budget continuation and repeated prompt reuse. Prefer representative medians/spread to a best-case TPS.
- GLM: retain the measured full 80.17GiB pool. Candidate source remains blocked pending a complete planner rebase; do not bypass the old guarded patch. Paired MTP off/on trials on the working version can still be performed using Management.
- DwarfStar: its existing optimized Q8 activation decode remains the control. DSpark requires matching V4 Flash weights (~5.6GiB extra plus state), adequate headroom and acceptance tracking. Nonzero-temperature opportunistic acceptance is a sampling change; exact-sampling mode needs its own trial. Do not turn it on globally based on a peak upstream result.
- Ollama: prove which runner/backend is active from startup logs. For the observed stable version flash attention may already be automatic; `OLLAMA_FLASH_ATTENTION=1` is a forced trial, not proof that the baseline was off. Compare `OLLAMA_KV_CACHE_TYPE=q8_0` against f16 separately with flash attention supported. Approximate half-memory applies to KV only; precision and long-context quality can change. Keep concurrency fixed at one and existing native context explicit. Vulkan versus ROCm is a separate service trial, not an assumed faster default. Do not copy flags from current docs into an older installed build without checking its version.

Restore the old executable/config/pack paths, environment and normal service/router ownership on build failure, missing residency, OOM/swap activity, correctness regression or meaningful paired latency regression. Keep failed logs for diagnosis. Never replace the working ROCm stack merely to match an upstream benchmark's library version.
