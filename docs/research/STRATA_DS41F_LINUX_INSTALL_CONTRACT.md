# Strata DS41F Linux installation contract

Lookup date: 2026-11-02. Source-only research; no Linux host access, compilation, service operations or model downloads performed. Target supplied by parent: Ubuntu Ryzen AI MAX+395 / Radeon 8060S gfx1151, distro ROCm 7.1 / clang21, 96 GiB firmware GPU reservation and 30.48 GiB Linux RAM. Local adapter files read from C:/GitHub/odysseus-maya-glm53. No claim that source inspection constitutes host qualification.

## Verdict and implementation gate

A separate **experimental** `strata_ds41f` backend is justified. Do not publish a verified working Ubuntu installer yet. The DS41F engine is already HIP-ported: a GPU kernel port is not an established prerequisite. Top-level CMake builds `strata-dsv41` for CUDA OR HIP; the target is guarded by `STRATA_BUILD_DSV41 AND NOT STRATA_HIP_GFX906`, not WIN32. HIP force-includes `include/strata/dsv41/hip_extra.h`, compiles the CUDA-shaped sources as HIP and links HIP runtime/BLAS. gfx1151 is explicitly accepted with an unvalidated warning in `cmake/hip_backend.cmake`. Therefore Windows-only Strix measurements do not prove Linux impossibility.

Author-tested Strix DS41F results are Windows results. The exact Ubuntu ROCm7.1/clang21 combination is **source-plausible, build-unverified and runtime-unverified**. Keep enablement gated behind a successful isolated build, no-model executable/kernel tests, then model smoke and resource checks. A failed build must report its exact compiler/linker error; do not invent a missing kernel port or declare success from configure alone.

## Provenance and build contract

Use Niko1221/Strata app v0.2.9 at `c62d5e3a15b40ea51c96aae9e8d36928965f53ab`. Engine/control version is independently 0.1.55 (CMake project version); target `strata-dsv41`, implementation `src/dsv41`, vendored Project Maya ancestry, MIT notices in `third_party/project-maya`. This is not the older mw00/strata Qwen fork and not the installed Maya executable. Pin llama.cpp `3cf03257f219afbe7334045ff7c6a06ac68c627d`, as setup.py declares; set `STRATA_GGML_DIR` to that checkout. Record source hash, dependencies, build command and `strata-dsv41 --version` output independently.

Candidate configure contract (for later execution only):

```sh
cmake -S SOURCE -B BUILD -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DSTRATA_ENABLE_CUDA=OFF -DSTRATA_ENABLE_HIP=ON \
  -DSTRATA_BUILD_DSV41=ON -DSTRATA_NATIVE_EXPERTS=ON \
  -DCMAKE_HIP_ARCHITECTURES=gfx1151 \
  -DCMAKE_HIP_COMPILER=/usr/lib/llvm-21/bin/clang++ \
  -DSTRATA_GGML_DIR=PINNED_LLAMA_CHECKOUT
cmake --build BUILD --target strata-dsv41 ds4_iq_test ds4_tq_test ds4_tq_test_portable
```

This is a proposed trial, not an executed recipe. Verify actual distro compiler location, HIP ROCm root and CMake package discovery before fixing paths. Requirements: CMake >=3.24, Ninja, Git, C++ toolchain, HIP headers/device libraries/runtime, hipBLAS and ggml CPU dependencies; ROCm CMake packages must provide `hip::host` and `roc::hipblas`. rocBLAS is required for hipBLAS <1.0 and detected otherwise. hipBLASLt is optional (`CONFIG QUIET`), with dispatch unavailable if absent. Preserve existing distro ROCm7.1 and multiarch library paths; never install the Windows package's ROCm10.2 runtime into Linux or replace working drivers to satisfy this experiment. Check complete rocWMMA requirements for selected prefill sources against the existing installation rather than copying Maya patches wholesale. Capture effective HIP Release optimization (`-O3 -DNDEBUG`), include/link paths and gfx1151 code object.

No explicit ROCm7.1 minimum-version rejection was established in the inspected HIP configuration. This does not establish compiler/header/intrinsic or library compatibility with clang21. Tests above need the actual GPU but no weights; run version/library resolution before them. Verify numerical kernel tests, then Paris smoke, repeated prompts, cancellation and recovery. No architecture spoof (`HSA_OVERRIDE_GFX_VERSION`) is warranted by source's gfx1151 allowlist.

The signed-feed installer installs DS41F separately into `engine-dsv41/`; its Linux HIP package default coverage is gfx1200/gfx1201, not an assurance of gfx1151 coverage. `dsv41_fit.run_cards` consults package arch coverage and uses that Linux default when absent. Do not force an RDNA4 package onto this APU. A reviewed local `STRATA_DSV41_EXE=/absolute/path/strata-dsv41` bypasses package selection and automatic package upgrades. Verify planner still selects the actual GPU and does not silently discard it. `--dsv41-package` accepts signed matching-platform engine archives, not arbitrary binaries. Qwen `engine/` remains untouched.

## Model, pack and disk contract

Default recommended comparison candidate is official IST-DAS 2.00 bpw (IQ2_XXS); 1.75 bpw (IQ1_M) is a separate lower-quality/memory-pressure trial. Upstream source pins ISTA-DASLab/DeepSeek-V4.1-Flash-1017-GSQ-RCO-GGUF revision `cb02422c900df489cea5833e8a92171224059b3c`; exact catalog sizes and SHA256 are reproduced below. Unsloth support files use `8a6ec9cf118e785a362ea94ee123c61c70553be6`. Use immutable HF `/resolve/<revision>/<filename>` links, never main. Default download is four model shards plus shared Engram, DSpark and metadata, with optional vision file. Later six-shard or replaced-template layouts are accepted only by explicit size/hash alternatives; do not mix catalog versions casually.

Nominal decimal downloads: 1.75 =239 GB; 2.00 =257 GB; 2.25 =274 GB, including ~111 GB Engram and ~8 GB drafter. All three share Engram/support files (~533 GB for all). These are not resident RAM requirements. Planner adds 6 GB pack/work headroom; reserve additional measured space for builds, logs, interrupted transfers, snapshots and rollback copies. Do not advertise 257 GB as an exact all-in disk allocation. Compute required free bytes from selected catalog files plus actual pack writes and independent deployment files; count retained Maya and Qwen data separately. Slots default to a 2 GB cap and week retention. SSD read traffic can dominate latency and warrants recording separately from persistent writes.

`tools/dsv41/pack_gguf.py` uses selected `--shards`, `--engram`, `--engram-meta`, `--dspark-gguf`, noise token 128799 and Markov rank 256, optional mmproj. Do not pass `--skip-tensors` or `--dense-override`: upstream warns these can produce garbage. Copy matching shipped expert profile; verify `--pack-info` and Paris smoke. Keep source GGUFs because file-backed expert/Engram accesses remain relevant. Existing Maya-XS packs and Strata packs are NOT interchangeable; sharing the DS41F family or ancestry does not establish file or quant equivalence.

## Memory: firmware reservation is not a single allocatable Linux pool

Planner `uma_total_gb` adds OS RAM plus reported `dedicated_gb`; otherwise subtracts shared GPU memory if known, or uses max(OS RAM, GPU figure). On a Linux report missing dedicated metadata it can therefore report 96 rather than 126.48 GiB. It models one pool minus dense weights and 12 decimal GB reserve; this is an estimate, not a guarantee that Linux CPU and HIP can allocate interchangeable pages. Keep GiB versus decimal GB conversion explicit.

The engine independently sizes GPU placement from device allocations/free memory. Its RAM store uses `ram_avail()` minus 12 decimal GB, requires at least 4 GB room and is recomputed after GPU allocations; context beyond 16384 may subtract additional UMA scratch estimate (0.00011 GB/token). Explicit `--ssd-cache` / `DS4_STORE_GB` bypass auto sizing. Thus 30.48 GiB Linux RAM cannot be treated as 126.48 GiB CPU cache. Confirm GPU-free memory, Linux MemAvailable, anonymous/cached memory, scratch/context allocation and actual expert counts after load. Do not reuse Maya's reserved-VRAM patch without reviewing Strata's distinct allocation path. Avoid DS4_COPY_ALL and DS4_STORE_ZC overrides; upstream warns integrated-memory copying/registration can cause severe pressure or GPU faults.

With 2.00's ~132.07 decimal GB routed experts and ~6.17 GB dense, plus cache/scratch, full residency is not promised on this host. 1.75 reduces routed experts to ~114.56 GB but is still a measured-fit question. Engram stays SSD-backed. Never require all weights resident as a prerequisite for a streaming trial; instead require bounded memory, no swap thrashing, correct outputs and report spill/IO explicitly.

## Launcher, API and lifecycle

Use an independent source root, Python environment, model directory, config, logs, slots and user unit (`odysseus-strata-ds41f.service` proposed), loopback port **8095**. Use the pinned CLI/installer, e.g. later:

```sh
strata launch --family deepseek41 --model ist-das-2.00bpw --plan
# Or setup, only after the local engine passes the build gate:
STRATA_DSV41_EXE=ABSOLUTE_BINARY ./setup.sh --setup --yes \
  --family deepseek41 --model IST-DAS-2.00bpw --backend hip \
  --context 8192 --data-dir ISOLATED_DATA --port 8095 --no-start
```

Check pinned CLI help before making launch flags executable documentation; `setup.sh` is the directly documented unattended setup path. Review its generated script and bind host explicitly to 127.0.0.1. User unit should exec that isolated generated launcher in its own WorkingDirectory, with absolute paths, appropriate render-device access, journal logs and whole-cgroup termination. Keep restart-on-failure bounded; avoid restart loops during OOM. Do not enable/start until validation. No updates, symlinks, home/default-model state or environment may touch Qwen.

Generated JSON uses `exe`, `args` (array), `cwd`, `tokenizer`, `model_name: deepseek-v4.1-flash`, `arch: deepseek41`, `log`, `lib_dirs`, `port`, `engine_ready_s: 3600`, optional `env`, `backend: hip`, host/API key and GPU selection. Native args include `--pack`, `--max-context`, `--eos 1`, `--vision`, `--dspark auto` and official recipe `--dspark-conf 0.65`. Python server wraps native stdin/stdout protocol; native binary alone is not HTTP. Launch through generated script/server config, not a fabricated `strata-dsv41 --port` command.

Readiness: `/health` always returns status ok for the server, so require `loaded === true`, correct model ID and positive `max_context`; cross-check authenticated `/v1/models` meta.n_ctx and loaded status. Health alone is not model readiness. Engine reports READY context after loading. Allow up to 3600 s rather than existing Operations' 600 s. `/metrics` provides engine/live/request data; optional telemetry must not determine readiness. `/props`, `/slots`, `/v1/status` also report runtime state/context. Do not assume Maya `/api/tokens` exists with Maya's exact schema.

OpenAI `/v1/chat/completions` and Anthropic `/v1/messages` support streaming, tool calls and cancellation; DSML is parsed into tool structures. Thinking default on, high effort; `reasoning_effort: none` or template enable_thinking false disables it. Default temperature/top_p are 1/1. Test actual router tool/reasoning/usage behavior. One sequence at a time; parallel ignored with warning.

Server supports POST `/load`, `/unload` (also v1 aliases), unlike a stop-only native engine. Unload can return busy/unsupported; busy is HTTP409. It supports optional wait and reload on generation, so leaving an unloaded server reachable permits future loads outside Operations ownership. For this experimental backend prefer **service stop/start** under exclusive lease, despite available APIs. Confirm cgroup stopped and endpoint no longer loaded before admitting another model. Auth/Host/Origin/control-body policy must be exercised, not bypassed.

Context is an engine startup allocation: default8192, native upper bound1048576 read from pack. Source docs say >256K untested. Begin8K; qualify32K/64K and only then128K for Maya comparison. Rewrite only isolated config's single `--max-context`, review revision/fingerprint and restart; verify runtime context rather than trusting requested value.

Speculation/quality: auto DSpark is default; `off` still loads drafter and consumes GPU memory; use explicit no-drafter mode only after checking native help (source uses `none` on Mac). Integrated/<=16GiB defaults choose five experts (“Faster”), whereas full quality uses six (`--speed full`/`--full-quality` translated to engine `--experts 6`). Record both expert count and drafter mode/acceptance. Upstream UMA setup may write a large prefill chunk and DS4_ASYNC_T=4 based on Windows results; these are not qualified Ubuntu tuning. Start conservative, record generated values. STRATA_DSV41_NO_BIG_CHUNK=1 suppresses setup big-chunk choice; STRATA_DSV41_ASYNC_T=8 restores engine default. Keep Qwen/GLM tuning flags out of this profile.

## Parent adapter and UI contract

Read maya.py, overview.py, strata_config.py, exclusivity.py, inference_backends.py and benchmark_limits.py. Existing `strata` is Qwen: strata_config holds one `strata_setup`, uses management.strata_url, setup-generated JSON and a Qwen variant symlink. Do not add DS41F as its model variant or point its link at this installation.

Add separate URL/settings/service allowlist and setup profile keyed `strata_ds41f`, separate inventory adapter, management/control job dispatch, context review/fingerprint paths and telemetry association. Add it to EXCLUSIVE alongside Maya/GLM/DwarfStar; NativeEngines.live needs explicit mapping, release should stop service, prepare must release all peers under the existing inference lease and verify exact model/context. Ensure all existing Qwen/Ollama/manual-load entrypoints also see this peer. Unknown/changing states fail closed; do not interpret an unreachable probe as stopped. Preserve user-visible switch semantics and current ownership rules; manual control should not silently terminate other models.

UI: separate experimental card, source/build verification status, start/stop/restart, configured versus runtime context, quant/experts/drafter, residency/SSD streaming, logs and clear build-unverified errors. Setup button must not promise success before host qualification. Benchmark and scoreboard use distinct backend ID and native model identity; router route is a distinct opt-in alias such as strata-ds41f, endpoint http://127.0.0.1:8095/v1. Never silently replace Maya route or Qwen. Tests should cover exclusivity, false-positive health, wrong model/context, failed service stop and unchanged Qwen configuration.

benchmark_limits currently dispatches explicit backend names; add this one, use observed runtime context and model match. Its exact task counts currently depend on Maya /api/tokens: do not label DS41F exact without equivalent verified template tokenization. Reject over-budget workloads conservatively and report approximate limits honestly. Carry experimental backend through benchmark allowlists, artifacts, history, scoreboard filters and route schema.

Maya-XS supplied baseline ~25.8 native decode TPS (18K prompt,128K capacity) is a different quant/pack from IST-DAS. Report this as a deployment/task-quality comparison, not an engine-only speedup. Match prompts, output budget, context, sampler, reasoning, tools, expert count and speculation where possible; record unavoidable differences. Separate cold load, cold/warm prefill, TTFT, native decode and end-to-end TPS, IO, cache reuse and quality scores. Include code/tests, tool/JSON validity, long context and prose; 1.75's upstream prose degradation makes task-only headline averages misleading. Do not transfer Windows19–21TPS claims to Ubuntu.

Host guide must present the build gate first, immutable manifests and space calculation second, isolated install/unit configuration third, readiness/resource/API qualification fourth, then optional Operations enablement and rollback (stop candidate, restore prior route/service; Qwen/Maya artifacts unchanged). No pretend working installation.

## Immutable primary evidence and exact excerpts

All links below are pinned to the reviewed commit. Excerpts retain exact source data rather than paraphrasing hashes. Model URLs can be constructed from the pinned revisions and filenames in the catalog.

### docs/DEEPSEEK41.md

[Immutable primary source](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/docs/DEEPSEEK41.md).

### CMakeLists.txt

[Immutable primary source](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/CMakeLists.txt#L906).

```text
906:   # DeepSeek-V4.1-Flash: the third engine path (src/dsv41, from Project Maya's maya-ds4, MIT, third_party/project-maya).
907:   # Its own executable for now (strata-dsv41); a family switch in `strata` comes after first light.  OFF by default: it
908:   # changes no existing target (the vendored kernels live in strata::dsv41k, so no symbol is shared with strata_kernels).
909:   option(STRATA_BUILD_DSV41 "Build the DeepSeek-V4.1 engine path (strata-dsv41)" OFF)
910:   if(STRATA_BUILD_DSV41 AND NOT STRATA_HIP_GFX906)
911:     add_library(strata_dsv41_kernels STATIC src/dsv41/vendor/exl3.cu src/dsv41/vendor/sampler.cu src/dsv41/vendor/moe_win.cu
912:                 src/dsv41/vendor/exl3_cpu.cpp)
913:     if(NOT STRATA_ENABLE_HIP)   # (the pre-sm_80 NVIDIA decode kernels exl3.cu dispatches to)
914:       target_sources(strata_dsv41_kernels PRIVATE src/dsv41/vendor/exl3_legacy.cu)
915:     endif()
916:     target_include_directories(strata_dsv41_kernels PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include ${_strata_gpu_include_directories})
917:     target_link_libraries(strata_dsv41_kernels PUBLIC ${_strata_gpu_runtime_target})
918:     add_executable(strata-dsv41 src/dsv41/ds4_main.cpp src/dsv41/ds4_model.cu src/dsv41/ds4_kernels.cu src/dsv41/ds4_vision.cu
919:                    src/dsv41/ds4_gguf.cu src/dsv41/ds4_gguf_cpu.cpp src/dsv41/ds4_iq.cu src/dsv41/ds4_iq_cpu.cpp
920:                    src/dsv41/ds4_tq.cu src/dsv41/ds4_tq_cpu.cpp src/dsv41/ds4_gg_cpu.cpp src/dsv41/ds4_packinfo.cpp)
921:     # the commit the binary was built from, readable with --version (set -DSTRATA_BUILD_ID=... for a tree without .git)
922:     if(NOT STRATA_BUILD_ID AND EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/BUILD_ID)   # (a git archive has no .git: the packer writes the commit here)
923:       file(STRINGS ${CMAKE_CURRENT_SOURCE_DIR}/BUILD_ID STRATA_BUILD_ID LIMIT_COUNT 1)
924:     endif()
925:     if(NOT STRATA_BUILD_ID)
926:       execute_process(COMMAND git -C ${CMAKE_CURRENT_SOURCE_DIR} rev-parse --short=9 HEAD OUTPUT_VARIABLE STRATA_BUILD_ID
927:                       OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
928:     endif()
929:     if(NOT STRATA_BUILD_ID)
930:       set(STRATA_BUILD_ID unknown)
931:     endif()
932:     set_source_files_properties(src/dsv41/ds4_main.cpp PROPERTIES COMPILE_DEFINITIONS "DS4_BUILD_ID=\"${STRATA_BUILD_ID}\"")
933:     target_include_directories(strata-dsv41 PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include ${_strata_gpu_include_directories}
934:                                ${CMAKE_CURRENT_SOURCE_DIR}/third_party/ggml ${CMAKE_CURRENT_SOURCE_DIR}/src/prefill)
935:     # strata_prefill: the RDNA3 WMMA dense GEMM (src/prefill/wmma_gemm.cu), which gemm() prefers over hipBLAS on gfx11 (measured
936:     # 4.97 -> 20-40 TFLOPS on this part: the chunked prompt's dense GEMM is 11.1 s of a 4K prompt at the library's rate)
937:     target_link_libraries(strata-dsv41 PRIVATE strata_prefill strata_dsv41_kernels ${_strata_gpu_runtime_target} ${_strata_gpu_blas_target})
938:     find_package(Threads REQUIRED)
939:     target_link_libraries(strata-dsv41 PRIVATE Threads::Threads)
940:     if(WIN32 AND STRATA_ENABLE_HIP)   # cudaMemGetInfo is the DXGI-budgeted hip_compat::mem_get_info (src/core/device.cu): on a Strix Halo the
941:       # carve-out is not all the GPU may use, and the engine sizes its expert tiers by what the budget says is free
942:       target_link_libraries(strata-dsv41 PRIVATE strata_core)
943:     endif()
```

### cmake/hip_backend.cmake

[Immutable primary source](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/cmake/hip_backend.cmake#L1).

```text
1: # Opt-in HIP configuration. Strata's CUDA-shaped kernels target wave32 RDNA2 / RDNA3 / RDNA4 (64 KiB LDS per
2: # workgroup, a signed dot4 instruction). CMake/compiler discovery stays machine-independent; pass CMAKE_HIP_COMPILER when it
3: # is not on PATH.
4: if(NOT DEFINED CMAKE_HIP_ARCHITECTURES OR CMAKE_HIP_ARCHITECTURES STREQUAL "")
5:   set(CMAKE_HIP_ARCHITECTURES gfx1100 CACHE STRING "Strata HIP target architecture(s), e.g. gfx1100 or gfx1100;gfx1201")
6: endif()
7: # Validated on real cards: gfx1100 (RX 7900 XT / XTX) and gfx1201 (RX 9070 / 9070 XT, Radeon AI PRO R9700) by the
8: # maintainers; gfx1101 (RX 7800 XT, #254) and gfx1200 (RX 9060 XT, #256) by their owners. gfx1102 (RX 7600) has the
9: # same LDS limit and dot4 instruction and passed ctest (#192), but no model run has been reported yet. RDNA2 gfx1030 (RX 6800 / 6900) has the
10: # same LDS limit and wave32 but an older dot4 instruction (v_dot4_i32_i8, hip_compat/intrinsics.hpp); a community
11: # report ran it (#311), the maintainers have not. gfx1151 (Ryzen AI Max / Strix Halo, Radeon 8060S, RDNA3.5) is the
12: # same wave32 / 64 KiB LDS / sudot4 family as gfx1100, on a unified-memory APU; experimental.
13: set(_strata_hip_validated gfx1100 gfx1201)
14: set(_strata_hip_community gfx1101 gfx1200)
15: set(_strata_hip_unvalidated gfx1010 gfx1011 gfx1012 gfx1102 gfx1103 gfx1030 gfx1031 gfx1032 gfx1034 gfx1150 gfx1151 gfx1152)   # gfx1103: Radeon 780M iGPU, opt-in, WMMA kernels like gfx1100; gfx1152: Krackan, Radeon 860M / 840M (#1625), builds with the portable kernels, no card report yet
16: # CMake hands HIP a ';' list, but a -DCMAKE_HIP_ARCHITECTURES typed by hand (or ROCm's own Windows tooling) may use
17: # spaces, which foreach(IN LISTS) would otherwise treat as one element.
18: string(REPLACE " " ";" _strata_hip_norm "${CMAKE_HIP_ARCHITECTURES}")
19: set(STRATA_HIP_ARCH_LIST "")
20: foreach(_arch IN LISTS _strata_hip_norm)
21:   if(_arch STREQUAL "")
22:     continue()
23:   endif()
24:   string(REGEX REPLACE ":.*$" "" _base "${_arch}")      # gfx1100:xnack- -> gfx1100
25:   if(_base IN_LIST _strata_hip_validated)
26:   elseif(_base IN_LIST _strata_hip_community)
27:     message(STATUS "Strata HIP: ${_base} was validated by community reports (docs/AMD_HIP.md)")
28:   elseif(_base IN_LIST _strata_hip_unvalidated)
29:     message(WARNING "Strata HIP: ${_base} builds, but it is not validated on a real card yet; please report results")
30:   else()
31:     message(FATAL_ERROR
32:       "Strata HIP supports wave32 gfx1100, gfx1101, gfx1200 and gfx1201 (unvalidated: ${_strata_hip_unvalidated}); "
33:       "CMAKE_HIP_ARCHITECTURES is '${CMAKE_HIP_ARCHITECTURES}'")
34:   endif()
35:   list(APPEND STRATA_HIP_ARCH_LIST "${_base}")
36: endforeach()
37: list(REMOVE_DUPLICATES STRATA_HIP_ARCH_LIST)
38: if(NOT STRATA_HIP_ARCH_LIST)
39:   message(FATAL_ERROR "Strata HIP: CMAKE_HIP_ARCHITECTURES is empty")
40: endif()
41: # The compiled architectures reach the runtime device check (src/core/device.cu) as "gfx1100,gfx1201": a binary
42: # carried to a card it has no code for stops at startup with a clear message instead of "invalid device function".
43: string(REPLACE ";" "," STRATA_HIP_ARCHS "${STRATA_HIP_ARCH_LIST}")
44: 
45: enable_language(HIP)
46: # CMake caches CMAKE_HIP_FLAGS_<CONFIG> while it enables HIP, before it tests the compiler.  A configure that stopped
47: # there because HIP could not compile at all (Visual Studio 2026's <cmath> with the HIP SDK 7.2, a wrong ROCm path)
48: # cached them empty, and every later configure of that build folder keeps them: setup's Release build then compiled
49: # all kernels at -O0 (an RX 6800 decoded at 0.24-0.27 tok/s instead of 28).  Empty ones get what CMake gives a fresh
50: # build folder.
51: foreach(_cfg IN ITEMS DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
52:   string(STRIP "${CMAKE_HIP_FLAGS_${_cfg}_INIT}" _strata_init)
53:   if("${CMAKE_HIP_FLAGS_${_cfg}}" STREQUAL "" AND NOT "${_strata_init}" STREQUAL "")
54:     set(CMAKE_HIP_FLAGS_${_cfg} "${_strata_init}" CACHE STRING
55:         "Flags used by the HIP compiler during ${_cfg} builds." FORCE)
56:   endif()
57: endforeach()
58: find_package(hip CONFIG REQUIRED)
59: find_package(hipblas CONFIG REQUIRED)
60: # Older distro hipBLAS has no workspace API. The compatibility shim uses
61: # rocBLAS directly for that version; newer hipBLAS keeps its existing path.
62: set(STRATA_HIP_BLAS_TARGETS roc::hipblas)
63: if(hipblas_VERSION VERSION_LESS "1.0")
64:   find_package(rocblas CONFIG REQUIRED)
65:   list(APPEND STRATA_HIP_BLAS_TARGETS roc::rocblas)
66: else()
67:   # the prompt path's rocBLAS solution table (STRATA_ROCBLAS_TUNING, gfx103x) calls rocBLAS by name
68:   find_package(rocblas CONFIG QUIET)
69: endif()
70: find_package(hipblaslt CONFIG QUIET)
71: 
72: if(NOT TARGET hip::host)
73:   message(FATAL_ERROR "The ROCm hip CMake package did not provide hip::host")
74: endif()
75: if(NOT TARGET roc::hipblas)
76:   message(FATAL_ERROR "The ROCm hipblas CMake package did not provide roc::hipblas")
77: endif()
78: if(TARGET roc::hipblaslt)
79:   set(STRATA_HIPBLASLT_AVAILABLE ON)
80: else()
81:   set(STRATA_HIPBLASLT_AVAILABLE OFF)
82:   message(STATUS "Strata: hipBLASLt not found; solution-table dispatch is unavailable")
83: endif()
84: if(TARGET roc::rocblas)
85:   set(STRATA_ROCBLAS_AVAILABLE ON)
86: else()
87:   set(STRATA_ROCBLAS_AVAILABLE OFF)
88:   message(STATUS "Strata: rocBLAS CMake package not found; the rocBLAS solution table (gfx103x) is unavailable")
89: endif()
90: 
91: # HIP's link step produces a PIE; make Strata and ggml objects PIC for the ROCm linker.
92: set(CMAKE_POSITION_INDEPENDENT_CODE ON)
93: 
94: set(STRATA_HIP_COMPAT_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/include/strata/hip_compat")
95: add_library(strata_hip_runtime INTERFACE)
96: target_include_directories(strata_hip_runtime BEFORE INTERFACE
97:   "${STRATA_HIP_COMPAT_INCLUDE_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}/include")
98: target_compile_definitions(strata_hip_runtime INTERFACE STRATA_USE_HIP=1 "STRATA_HIP_ARCHS=\"${STRATA_HIP_ARCHS}\"")
99: option(STRATA_GFX1012_PORTABLE_DOT "Use the portable signed-byte dot control on gfx1012" OFF)
100: if(STRATA_GFX1012_PORTABLE_DOT)
101:   target_compile_definitions(strata_hip_runtime INTERFACE STRATA_GFX1012_PORTABLE_DOT=1)
102: endif()
103: # #313: the RDNA3 WMMA GEMM's intrinsics only compile for gfx11 targets and the host pass does not define
104: # __gfx11xx__, so the build supplies the macro for gfx11 configs (the kernels also gate on the device at runtime)
105: if(CMAKE_HIP_ARCHITECTURES MATCHES "gfx11")
106:   target_compile_definitions(strata_hip_runtime INTERFACE STRATA_WMMA_GFX11=1)
107: endif()
108: target_link_libraries(strata_hip_runtime INTERFACE hip::host)
109: # The shim renames the CUDA runtime to HIP, force-included into every host and device source. On Windows the host
110: # compiler is ROCm's clang++ too (tools/hip/build_windows.bat: CMake refuses to mix cl.exe with Clang HIP), which takes
111: # -include like it does on Linux; an MSVC-style front end (cl / clang-cl) takes /FI instead. Forward slashes, so /FI
112: # does not read the path's backslashes as escapes.
113: file(TO_CMAKE_PATH "${STRATA_HIP_COMPAT_INCLUDE_DIR}/cuda_runtime.h" _strata_hip_force)
114: if(MSVC)
115:   target_compile_options(strata_hip_runtime INTERFACE "$<$<COMPILE_LANGUAGE:CXX>:/FI${_strata_hip_force}>")
116: else()
117:   target_compile_options(strata_hip_runtime INTERFACE
118:     "$<$<COMPILE_LANGUAGE:CXX>:-include>" "$<$<COMPILE_LANGUAGE:CXX>:${_strata_hip_force}>")
119: endif()
120: target_compile_options(strata_hip_runtime INTERFACE
121:   "$<$<COMPILE_LANGUAGE:HIP>:-include>" "$<$<COMPILE_LANGUAGE:HIP>:${_strata_hip_force}>")
122: 
123: # CMake does not infer HIP from Strata's existing CUDA-shaped .cu suffixes.
124: file(GLOB_RECURSE _strata_hip_sources CONFIGURE_DEPENDS
125:   "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cu"
126:   "${CMAKE_CURRENT_SOURCE_DIR}/bench/*.cu"
127:   "${CMAKE_CURRENT_SOURCE_DIR}/tests/*.cu")
128: if(_strata_hip_sources)
129:   set_source_files_properties(${_strata_hip_sources} PROPERTIES LANGUAGE HIP)
130: endif()
131: foreach(_source IN ITEMS tests/hip/intrinsics.cpp tests/hip/native_qsa_score.cpp)
132:   if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${_source}")
133:     set_source_files_properties("${_source}" PROPERTIES LANGUAGE HIP)
134:   endif()
135: endforeach()
136: 
137: message(STATUS "Strata: HIP enabled, arch ${STRATA_HIP_ARCHS}")
```

### setup.py

[Immutable primary source](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/setup.py#L650).

```text
650:                "sha256": QWEN36_FILES,
651:                # the engine's KV cache per context token: 10 attention layers + the MTP block, 2 KV heads x 256, K and
652:                # V in fp32 (strata-q36)
653:                "kv_bytes_per_token": 11 * 2 * 256 * 2 * 4,
654:                "mtp": 2,                     # drafts per step (greedy requests): measured best on the R9700
655:                # pictures: Unsloth's mmproj (the same projector family as Flash-Next's: qwen3vl_merger) through
656:                # strata-vision; a parallel slot's session: its KV cache (kv_bytes_per_token) and the recurrent state of
657:                # the 30 GDN layers (state_gb)
658:                "vision": True, "state_gb": 0.07}
659: }
660: PREVIEW_FAMILIES = OWN_PATH_FAMILIES             # the earlier name (tools and tests that import it)
661: 
662: # DeepSeek-V4.1-Flash (GGUF architecture deepseek41), Unsloth's UD-Q8_K_XL at the revision the port was checked against: a
663: # family with an engine of its own (strata-dsv41, src/dsv41, ported from Project Maya's maya-ds4, MIT) that is not a
664: # Flash-Next pack and not a single GGUF: nine shards (508.4 GB, the routed experts as MXFP4, the dense weights in BF16, and
665: # in shard 8 the 202.8 GB n-gram table that stays on the SSD), the engine's pack folder (tools/dsv41/pack_gguf.py: hash
666: # tables, tensor map, the tokenizer and the GGUF's own chat template, a few MB) beside them.  MULTI_FILE_FAMILIES, offered
667: # in the same menu and installed by install_dsv41.  Where it fits: strata_control/dsv41_fit.py (real numbers).  The DSpark
668: # drafter (dspark-...-Q8_0.gguf, 8.0 GB) and the picture reader (mmproj-BF16.gguf, 1.0 GB) are pinned here but not fetched: the
669: # engine does not use them yet.
670: DSV41_REPO = "unsloth/DeepSeek-V4.1-Flash-1017-GGUF"
671: DSV41_SHARD = "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-{i:05d}-of-00009.gguf"
672: DSV41_FILES = {
673:     "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-00001-of-00009.gguf": (5780589, "cc15d470afcb030558bbc4d8e2ccd840182e2e95de1a95198138efe63a5fd99f"),
674:     "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-00002-of-00009.gguf": (50536121984, "9cb2047676bd20e448be2377b480080c97fb38fae117f4846e07de2c65bb8522"),
675:     "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-00003-of-00009.gguf": (50536121984, "17d398dd327e7ff1991b382677814db75d970b074661757564b6f2ac3d0c8d95"),
676:     "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-00004-of-00009.gguf": (50536122016, "7ea8da1143b9a6bf1424a361eb1b0d6f66c7ce79eac9711162f212cc4f39c319"),
677:     "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-00005-of-00009.gguf": (50536122016, "1952da19a841176b46f9681c99ea57c674f8a8861e16d76fb35cec4befe039b2"),
678:     "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-00006-of-00009.gguf": (50536122016, "d6711781d8effab79b410af9c40920bffcc0c28976e509bc1227ff0c6669c33a"),
679:     "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-00007-of-00009.gguf": (36097230048, "2ebc8d198d03a14865e8a85df637285ceb5dd79b1ffc78424bd3c3e06df57823"),
680:     "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-00008-of-00009.gguf": (202758032592, "12df293355efbd104b088a77e13ee22410fb1ec3f7ab3863947f09896e64bec7"),
681:     "DeepSeek-V4.1-Flash-1017-UD-Q8_K_XL-00009-of-00009.gguf": (16835109952, "df33722d1dcf5ffe0347ed8406f7acb3b18061f4ded2c13c2b0a184b8d41c9ea"),
682:     "dspark-DeepSeek-V4.1-Flash-1017-Q8_0.gguf": (7976914240, "6ba6ac1b331c540691a209e7fc97dec2a24e295acb1e755c84c7d24a4373927a"),
683:     "mmproj-BF16.gguf": (972208288, "9a1ef7c40d0d702c7ed8c5f863f60abae64091387ba3fce0ad19ce9beeda3bf9"),
684: }
685: # The two OFFICIAL IST-DAS sets (ISTA-DASLab/DeepSeek-V4.1-Flash-1017-GSQ-RCO-GGUF at the revision above, a flat repository; Hub LFS sha256 = what the
686: # files hash to): IQ2_XXS is the 2.00 bpw set, IQ2_XS the 2.25 bpw set and IQ1_M the 1.75 bpw set, 4 shards each, plus ONE Engram table (110.6 GB, the same file for both sets:
687: # downloaded once into the shared folder) and the DSpark drafter and the 5.8 MB metadata shard of Unsloth's set that pack_gguf --engram-meta reads
688: # (both pinned in DSV41_FILES above).  This is the set a normal PC runs (64 GB RAM + a 12 GB card; docs/DEEPSEEK41.md); the UD-Q8_K_XL entry stays
689: # for the big machines.
690: DSV41_ISTA_REPO = "ISTA-DASLab/DeepSeek-V4.1-Flash-1017-GSQ-RCO-GGUF"
691: DSV41_ISTA_ENGRAM = "DeepSeek-V4.1-Flash-Engram-IQ4_NL.gguf"
692: DSV41_ISTA_SHARD = "DeepSeek-V4.1-Flash-1017-GSQ-RCO-{quant}-{i:05d}-of-00004.gguf"
693: DSV41_DSPARK = "dspark-DeepSeek-V4.1-Flash-1017-Q8_0.gguf"
694: DSV41_ISTA_FILES = {
695:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ1_M-00001-of-00004.gguf": (39358830464, "6e3f0d5ff53d00428ec32c079db9e8255cb410ccaa435e5342aaa8a47fe1339d"),
696:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ1_M-00002-of-00004.gguf": (39176113248, "34d68c99fdae9948f9f0af0dafe308e805ce0a78d2c48641b24cc57242fdc0af"),
697:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ1_M-00003-of-00004.gguf": (39997334720, "a08468c83955e0c4d3a9eb3f7b86c45b7bbac55d99c4640039a296a8a4b327ea"),
698:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ1_M-00004-of-00004.gguf": (2188841824, "b05f26a92325aa6845cebcb3ec1f93d2e65ebf89bdc3f805695960d2f0681832"),
699:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ2_XS-00001-of-00004.gguf": (39535776992, "4c65307992ab586c527ca1f6f64062aa96a33b23c02b7fed103f45f75e854798"),
700:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ2_XS-00002-of-00004.gguf": (39494617536, "933461a974fcccee741bf2f9798201e72ab4aa3f8a952be56f849896ca6ea30e"),
701:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ2_XS-00003-of-00004.gguf": (39423838496, "bbf91fa346a85ebaf32dd4fd5c456b534d780d381e54f46ac90783a481373240"),
702:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ2_XS-00004-of-00004.gguf": (36771591232, "e5b2f8781fab688cd4bc3bc7a69651b5dbb90ed94965657c6bead8bb4774f3cf"),
703:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ2_XXS-00001-of-00004.gguf": (39075714624, "d46efb67ccffd8fb5d7f18b423896381b9e06d8491e161c370ddf3b05b07e050"),
704:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ2_XXS-00002-of-00004.gguf": (39388449504, "cec1b2c3da429dc768b8bb9bc4dc4403d81f74d34db660a6a34ee3e555bf2c70"),
705:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ2_XXS-00003-of-00004.gguf": (39246891616, "9d9c2140fcb134a0b9a74dedd0e5167e68077af6d08ce7f856bce112f0a35dd3"),
706:     "DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ2_XXS-00004-of-00004.gguf": (20527837312, "9b790a2297ed3cdede379fc984f4a52bb95f3b23724c797af11d1fa6a69db059"),
707:     "DeepSeek-V4.1-Flash-Engram-IQ4_NL.gguf": (110595290816, "eb19f30d368d393784e1fc54fdfaffc427ebb2110bf3762fdf5a8fbb9a2d740a"),
708: }
```

### setup.py

[Immutable primary source](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/setup.py#L8741).

```text
8741:     # A Mac (0.2.7.1): the drafter is not loaded at all (--dspark none). It was exact there but not faster (15.1 and 11.0 tokens/s against 15.3
8742:     # without it on the M5 Max), and the engine's "auto" would load it on a 128 GB Mac: its 7 GB of experts take memory the trunk's experts need.
8743:     args = ["--pack", str(pack), "--max-context", str(ctx), "--eos", "1", "--vision", ds_vision, "--dspark", "none" if mac else "auto",
8744:             *(["--dspark-conf", "0.65"] if md.get("recipe") == "ista" and not mac else []), *dsv41_prefill_chunk_args(cards, ram),
8745:             *plan["args"]]
8746:     env = dsv41_config_env(cards)
8747:     cfg = {"exe": str(exe), "args": args, "cwd": str(ROOT), "tokenizer": str(pack / "tokenizer"),
8748:            "model_name": fam["name"], "arch": fam["arch"], "log": str(home() / f"strata-{tag.lower()}.log"),
8749:            "lib_dirs": lib_dirs, "port": port, "engine_ready_s": 3600,      # 300+ GB read from the files: a slow disk needs its time
8750:            **({"env": env} if env else {})}
8751:     if cuda_tk == 12:
8752:         cfg["cuda"] = 12
8753:     if hip:
8754:         cfg["backend"] = "hip"
8755:     if mac:
8756:         cfg["backend"] = "metal"
8757:     idx = [c["index"] for c in cards]
8758:     if len(idx) > 1 or a.gpu is not None or a.gpus:      # (a.gpu is also setup's own pick: one card pinned when several devices are in the PC)
8759:         cfg["gpu"] = idx if len(idx) > 1 else idx[0]
8760:         cfg["gpus_asked"] = True
8761:         if hip:                                          # who chose them (strata_control/gpu_pick.py): the person, or setup itself
8762:             cfg["gpus_by"] = "person" if named else "setup"
8763:     if a.host:
8764:         cfg["host"] = a.host
8765:     if a.api_key:
8766:         cfg["api_key"] = a.api_key
8767:     if a.browser is not None:
8768:         cfg["open_browser"] = a.browser
8769:     cfg_path = home() / f"strata-{tag.lower()}.json"
8770:     write_setup_config(cfg_path, cfg, adopted if adopted is not None and adopted.name == cfg_path.name else None, explicit_args(a))
8771:     script = write_run_script(tag, cfg_path, port, cfg.get("open_browser") is not False)
```

### strata_control/dsv41_fit.py

[Immutable primary source](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/strata_control/dsv41_fit.py#L130).

```text
130:     small = [c for c in keep if float(c.get("vram_gb") or 0) < SMALLEST_CARD_GB]
131:     keep = [c for c in keep if float(c.get("vram_gb") or 0) >= SMALLEST_CARD_GB]
132:     ignored = list(ignored) + [{**c, "why_ignored": f"only {float(c.get('vram_gb') or 0):.0f} GB of graphics memory"} for c in small]
133:     if not named and len(keep) > 1 and all(str(c.get("vendor") or "").lower() == "nvidia" for c in keep):
134:         # NVIDIA: setup's role rule (gpu_roles.recommend): a card of 8 GB or less starts Off beside a bigger one, so the plan counts what the run uses
135:         # (an AMD card of 8 GB counts in both the plan and the run)
136:         from strata_control import gpu_roles
137:         if any(round(float(c.get("vram_gb") or 0)) > gpu_roles.OFF_AT_OR_BELOW_GB for c in keep):
138:             off = [c for c in keep if round(float(c.get("vram_gb") or 0)) <= gpu_roles.OFF_AT_OR_BELOW_GB]
139:             ignored += [{**c, "why_ignored": f"a card with {gpu_roles.OFF_AT_OR_BELOW_GB:.0f} GB or less starts Off beside a bigger card (setup does the same)"}
140:                         for c in off]
141:             keep = [c for c in keep if c not in off]
142:     amd = not named and bool(keep) and all(str(c.get("vendor") or vendor or "").lower() == "amd" for c in keep)
143:     if amd:
144:         win = _on_windows() if windows is None else windows
145:         # the list of architectures: the package's own (`covered`), else the Linux package's (gfx1200, gfx1201).  Windows ships a different
146:         # DeepSeek package (hip-rdna3), so without a list of its own nothing is filtered there.
147:         archs = tuple(covered) if covered else (() if win else DSV41_HIP_ARCHS)
148:         if archs:
149:             known = [c for c in keep if c.get("arch")]
150:             ok = [c for c in keep if not c.get("arch") or c["arch"] in archs]
151:             if known and ok and len(ok) < len(keep):
152:                 ignored += [{**c, "why_ignored": f"the DeepSeek engine has no code for {c['arch']} (it runs on {', '.join(archs)})"}
153:                             for c in keep if c not in ok]
154:                 keep = ok
155:         if win and len(keep) > 1:
156:             # the card setup takes (setup.py amd_rank): a card with memory of its own before an APU, then the most memory, then the lower number
157:             first = min(keep, key=lambda c: (bool(c.get("uma") or c.get("integrated")), -round(float(c.get("vram_gb") or 0)), c.get("index", 0)))
158:             ignored += [{**c, "why_ignored": "on Windows a model runs on one AMD card"} for c in keep if c is not first]
159:             keep = [first]
160:     keep = sorted(keep, key=lambda c: c.get("index", 0))
161:     return keep, ignored
162: 
163: 
164: 
165: def uma_card(cards: list[dict] | None) -> dict | None:
166:     """The integrated Radeon a run uses, when the graphics memory is the PC's RAM (Ryzen AI Max, gfx1151): setup marks it `uma` (and
167:     `integrated`); a discrete card never is.  None on every other computer."""
168:     return next((c for c in cards or [] if c.get("uma") or c.get("integrated")), None)
169: 
170: 
171: def uma_total_gb(card: dict, ram_gb: float | None) -> float:
172:     """The one pool of a unified-memory PC, counted once: the RAM Windows sees plus the BIOS carve-out the GPU owns (the "shared"
173:     part of the GPU's figure is that same RAM).  On the owner's Strix Halo: 64 GB + a 64 GB carve-out = 128 GB.  Without the carve-out
174:     in the card's data, the GPU's figure less its shared part, or the larger of the two, is the best that can be said."""
175:     ram = float(ram_gb or 0.0)
176:     dedicated = float(card.get("dedicated_gb") or 0.0)
177:     if dedicated > 0:
178:         return ram + dedicated
179:     vram = float(card.get("vram_gb") or 0.0)
180:     shared = float(card.get("shared_gb") or 0.0)
181:     if vram > 0 and shared > 0:
182:         return ram + max(0.0, vram - shared)
183:     return max(ram, vram)
```

### strata_control/dsv41_fit.py

[Immutable primary source](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/strata_control/dsv41_fit.py#L343).

```text
343: def _plan_uma(key: str, model: str, card: dict, ram_gb: float | None, disk_free_gb: float | None, context: int) -> dict[str, Any]:
344:     """plan() for an official IST-DAS set on a unified-memory APU (Ryzen AI Max, gfx1151, Windows): the graphics memory is the RAM,
345:     counted once.  The engine holds what fits beside the dense weights and the reserve and reads the rest from the SSD; the numbers in
346:     UMA_MEASURED are the measured 64 GB box.  Never a refusal."""
347:     v = ISTA_SETS[key]
348:     total = uma_total_gb(card, ram_gb)
349:     expert_gb = v["experts_gb"] / EXPERTS
350:     set_gb = v["set_bytes"] / GB
351:     download = ista_download_bytes(model) / GB
352:     room = max(0.0, total - ISTA_DENSE_GB - UMA_RESERVE_GB)
353:     resident = min(float(v["experts_gb"]), room)
354:     streamed = max(0.0, float(v["experts_gb"]) - room)
355:     in_mem = int(resident / expert_gb)
```

- [serve/server.py](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/serve/server.py)
- [strata_cli/launch/runner.py](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/strata_cli/launch/runner.py)
- [strata_control/dsv41_engine.py](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/strata_control/dsv41_engine.py)
- [src/dsv41/ds4_model.cu](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/src/dsv41/ds4_model.cu)
- [include/strata/dsv41/hip_extra.h](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/include/strata/dsv41/hip_extra.h)
- [third_party/project-maya/NOTICE.md](https://github.com/Niko1221/Strata/blob/c62d5e3a15b40ea51c96aae9e8d36928965f53ab/third_party/project-maya/NOTICE.md)

Pinned model trees: [ISTA-DAS](https://huggingface.co/ISTA-DASLab/DeepSeek-V4.1-Flash-1017-GSQ-RCO-GGUF/tree/cb02422c900df489cea5833e8a92171224059b3c), [Unsloth support files](https://huggingface.co/unsloth/DeepSeek-V4.1-Flash-1017-GGUF/tree/8a6ec9cf118e785a362ea94ee123c61c70553be6). Source-based contract is stronger than a mutable README inference, but exact host compilation, package-feed availability and runtime performance remain unverified.
