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
