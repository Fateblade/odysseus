# GLM 5.3 Flash Maya implementation plan

Updated 2026-10-24 after the successful DeepSeek V4.1 Flash Maya-XS setup. The user now authorizes implementing the GLM integration on a separate branch and publishing completed project changes. Host GPU validation remains an on-device acceptance step.

## Findings carried forward from DS41F

- Maya v1.1.22, commit ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197, compiled and ran on Ubuntu HIP 7.1 / clang 21 after the guarded Ubuntu layout patch, complete matching rocWMMA headers and LLVM-21 bitcode path corrections. Explicit HIP Release flags `-O3 -DNDEBUG` are essential: a successful build with empty CMAKE_HIP_FLAGS_RELEASE was initially very slow.
- Keep separate source trees, virtual environments, model packs, configuration and user services. Preserve DS41F on its configured port 8092; verify that proposed GLM loopback port 8093 is unused. Starting GLM must not silently stop or replace an existing backend.
- DS41F's successful 128000-context benchmark used three distinct large code-review prompts of roughly 18000 input tokens. Medians: native decode 25.8 t/s, prefill 504.8 t/s, TTFT 35.69 s, end-to-end 19.67 t/s, draft acceptance about 85.1%, reported cached prompt tokens zero. These are DS41F results, not predictions for GLM throughput or quality.
- That sample reported Linux MemTotal 32727912448 bytes (30.48 GiB), available 22445768704 bytes and swap used 1006309376 bytes. Linux-visible RAM is not installed unified-memory capacity; GTT is not extra RAM. A snapshot neither establishes active swapping nor a complete model memory footprint.
- The tested base is feature/linux-ai-operations-maya-xs at 47d2f34f. It includes Maya discovery, service/context/drafting controls, caller attribution with prompts/responses, header resource readings, task presets and historical benchmark comparisons.
- Context budgeting is input plus output plus engine reserve. A 262144 output cap cannot fit into 128000 context. Maya's exact `/api/tokens` check includes every measured prompt and warmup plus an eight-token reserve. Preserve checked task seeds and revalidate before queueing; never silently truncate or resize context.
- The previous DwarfStar rejection was input validation, not OOM; 256K allocation subsequently ran at about 15.41 client t/s on a short prompt. The overview identifier collision and stopped-service warning were fixed. These resolved incidents are not GLM blockers.

## Updated implementation sequence

1. Reverify ordinary GLM Maya-S-v2 against primary model metadata and the pinned Maya HIP contract; capture exact file names, revision, sizes and SHA-256 values.
2. Create feature/linux-ai-operations-maya-glm53 from the tested Maya branch, preserving unrelated staged work.
3. Add an independently managed GLM endpoint/service profile while reusing native Maya API handling and the one-active-host-job queue. Include discovery, settings, benchmarks, limits, request attribution and scoreboard compatibility.
4. Write a fresh step-by-step guide with the validated Ubuntu build fixes, pinned downloads, verification, modest initial context, memory checks, chat/tools/benchmark acceptance and rollback.
5. Run local transport/fixture and responsive UI checks; commit and push. Distinguish those checks from GPU execution and quality validation on the host.

## GLM candidate and deployment locations

Reverify ordinary Maya-S-v2 at https://huggingface.co/peasantsmith/GLM-5.3-Flash-Maya-GGUF; earlier metadata reported 96.47 decimal GB / 89.84 GiB. Its file size is not runtime memory. Use Project Maya's experimental gfx1151 instructions at https://github.com/mw00/project-maya/blob/main/docs/AMD_MAYA.md and the pinned source. Older published GLM measurements used ROCm 7.2.2; successful DS41F on 7.1 is useful build evidence, not a GLM performance result.

- Development base: C:/GitHub/odysseus; new branch checkout: C:/GitHub/odysseus-maya-glm53.
- Host checkout: /mnt/odysseus-fateblade; management: ~/.config/odysseus-operations/management.json; helper venv: ~/.local/share/odysseus-operations/venv.
- Existing engines: Strata 8081, Ollama 11434, DwarfStar 8000, LiteLLM 4000, Maya DS41F 8092. Verify GLM's proposed port 8093 is free.
- Keep user-requested full prompt/response logging, independent request attribution, the single active host-job queue, and historical scoreboards. Do not infer that gateway logging was installed merely because the code exists.
- Preserve unrelated staged changes in the original code and documentation repositories. No live SSH session has been established; host commands belong in the fresh guide.


## Verified GLM contract and delivery

The ordinary recipe is Maya-S-v2-IQ2_XXS at HF revision f16ee4ba50d8fd2bb726ea1862ca6ca14ba188da: three required text shards total 96468592256 bytes. The pinned source builds target maya and writes maya-maya-s-v2-iq2_xxs-hip.json; it does not use maya-ds4 or DSpark. Tokenizer and MTP tensors are embedded. The pinned gfx1151 code enables native MTP by default, so the guide explicitly sets MAYA_GLM_MTP_DECODE=0 for the baseline; --drafting off alone is insufficient.

The new backend identity is maya_glm, with its own endpoint, user service, native loaded-model context controls, exact task-budget checks, benchmark/scoreboard selection and request attribution. It refuses shared Maya endpoints/units and competing model starts. Native MTP comparison is documented as a stopped-profile config edit; the existing DSpark editor remains DeepSeek-only. Immutable download/hash verification and an explicit pin/flag-checking HIP GLM build helper are included. Local API/worker fixtures pass; actual GLM compilation, memory fit, sustained inference and task quality remain device acceptance work.

Fresh guide: docs/MAYA_GLM53_FLASH_SETUP.md, mirrored under Ometheon/Documentation/MayaGLM53Flash/InstallationGuide.md. Primary-source contract: docs/research/GLM53_MAYA_INSTALL_CONTRACT.md.

## Device findings: 2026-10-25 reserved-VRAM planner mismatch

The first GLM host build and inference succeeded with optimized HIP flags, 8192 context and MTP off. Three Small/distinct/mixed runs measured native decode median 3.0 t/s, prefill 57.8 t/s, TTFT 18.23 s and end-to-end 2.46 t/s. Every answer reached the 256-token cap; these are throughput checks, not full-answer quality acceptance. Task-set seed: 1494806378. No MTP drafts were reported, as intended.

Startup showed GPU total 96 GiB / already used 7.55 GiB, experts 80.17 GiB, Linux MemAvailable 25.28 GiB, GPU expert pool only 8.28 GiB and pinned RAM tier 9.27 GiB. The budget was Linux MemAvailable minus 16 GiB headroom and 1 GiB reserve, ignoring the large firmware VRAM reservation excluded from Linux MemTotal. Expert-cache hits of 42–46% corroborate limited residency; the exact share of disk/host-tier misses is not established.

The isolated source patch adds opt-in MAYA_GLM_RESERVED_VRAM=1 with same-GPU PCI/sysfs and HIP free-memory checks, a large reservation excluded from Linux RAM, retained system headroom, GPU reserve and MMQ scratch. The default remains unchanged. Source/hash guards and standalone C++ budget checks accompany the patch; complete rebuilt HIP execution and performance require the next host trial. See docs/MAYA_GLM53_RESERVED_VRAM_FIX.md. No BIOS, kernel-memory or ROCm replacement is required by this trial. Retain the original scoreboard entry and test the fix MTP-off before MTP-on.

### Host validation after restart

The host subsequently compiled and ran the patch successfully on the existing stack. A first retry still used the original running engine (`/proc/PID/exe` showed `(deleted)`, with the reserved-VRAM switch absent); explicitly restarting maya-glm53.service activated the rebuilt binary/config. Startup then verified PCI 0000:bd:00.0, 96 GiB reservation, 80.17 GiB expert pool, all 288 experts/layer resident and only 0.22 GiB pinned RAM staging. No ROCm or BIOS change was needed.

The fixed MTP-off, 8192-context Small/mixed/distinct benchmark measured medians 18.4 decode t/s, 177 prefill t/s, 6.204 s TTFT, 12.723 end-to-end t/s and 20.121 s elapsed for 256 output tokens. Decode was stable at 18.3–18.4 t/s, including warmup. This is about 6.1x the first native decode baseline. Prompt-cache and draft counts were zero. Linux RAM used after the sample was 5.61 GiB, with 24.87 GiB available. The successful task seed was 1893222011, different from the original 1494806378, so the before/after prompts were not identical. Use 1893222011 for a matched MTP-on trial, keeping context, sampling and output cap unchanged. Long-context stability, complete-answer quality, tools and MTP benefit remain outstanding device acceptance work.

### First MTP-on result

The subsequent 8192-context Small/mixed/distinct export reports native MTP drafts and native decode median 26.9 t/s (research 26.9, summarization 28.3, code review 22.4), prefill 174 t/s, TTFT 6.310 s and end-to-end 22.159 t/s. Median per-sample draft acceptance is 79.02%; code review is lower at 63.49%, research 79.02% and summarization 84.58%. Warmup is excluded and measured 26.7 decode t/s / 85.07% acceptance. Prompt-cache counts remain zero. Linux RAM used after the sample is 5.73 GiB, available 24.75 GiB, and the swap-used snapshot remains unchanged.

This is approximately 46% higher median native decode than the fixed MTP-off run, but not a controlled MTP speedup: task-set seed changed again to 1521369663, prompts differ, and outputs are now 647–1444 tokens instead of exactly 256 (warmup 2785). The configured output cap and finish reasons are not included in this export. Longer outputs amortize prefill, so the end-to-end increase cannot be attributed solely to MTP. For a controlled comparison, run both modes with one explicit task-set seed and the same output cap/context/sampling. Functional project/tool checks and longer-context sustained memory checks remain outstanding; the observed short-context MTP run succeeds but establishes neither maximum context nor output quality.

### Unified Management controls

Maya DS41F, Maya GLM and DwarfStar now share Start, Stop, Refresh status, Logs and reviewed settings controls in an Exclusive engines desktop row, stacking on narrow screens. A separator places Ollama/Strata in Shared engines. GLM native MTP is a dashboard switch: managed context/MTP changes reuse the reviewed backup/restart/recovery transaction, preserving reserved-VRAM and unrelated config. A running engine's actual child-process MTP switch is checked against the saved setting, including detection of a replaced/deleted binary; the UI asks for restart on mismatch. Stopped services save without starting. No automatic backend switching or unloading is introduced.

GLM's default isolated config is discovered automatically; custom paths use maya_glm_setup.config_file/unit_file. Native context-only fallback remains available for unmanaged profiles. DwarfStar start/stop/restart now use the durable host job queue. Dashboard exclusive starts refuse loaded Ollama/Strata or other exclusive engines; shared model loads/benchmarks refuse active exclusive engines. External direct host/API calls remain outside this control boundary. Backend tests cover mode edits, stale reviews, conflicting flags, active child environment verification, recovery and mutual exclusion; desktop/mobile browser fixtures cover the grouped layout, switch, polling and apply flow. Actual dashboard-triggered restart remains a host acceptance check.
