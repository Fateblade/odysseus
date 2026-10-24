# GLM53 ordinary Maya-S-v2 installation contract

Research checked 2026-10-24. Read-only investigation of primary HTTPS metadata and the supplied local source. No Ubuntu host access, weight downloads, builds, staging or commits. This note is the sole output.

## Revisions and artifacts

Engine pin: **ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197**, Maya v1.1.22. The supplied source reports this HEAD and clean `git status --short`. [Pinned tree](https://github.com/mw00/project-maya/tree/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197).

HF current main and the separately queried immutable revision both identify **f16ee4ba50d8fd2bb726ea1862ca6ca14ba188da**, last modified **2026-10-09T22:52:28Z**. Values below come from `siblings[].size` and `siblings[].lfs.sha256`, not Git blob IDs or locally computed weight hashes. [Current HF API](https://huggingface.co/api/models/peasantsmith/GLM-5.3-Flash-Maya-GGUF?blobs=true), [pinned HF API](https://huggingface.co/api/models/peasantsmith/GLM-5.3-Flash-Maya-GGUF/revision/f16ee4ba50d8fd2bb726ea1862ca6ca14ba188da?blobs=true).

```json
{
  "repository": "peasantsmith/GLM-5.3-Flash-Maya-GGUF",
  "revision": "f16ee4ba50d8fd2bb726ea1862ca6ca14ba188da",
  "engine_revision": "ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197",
  "recipe": "Maya-S-v2-IQ2_XXS",
  "text_weight_bytes": 96468592256,
  "artifacts": [
    {
      "path": "Maya-S-v2-IQ2_XXS/GLM-5.3-Flash-Maya-S-v2-IQ2_XXS-00001-of-00003.gguf",
      "bytes": 47835993056,
      "sha256": "e56ef50efd71d81ddf6249e08bd2ef5af96ed348c2b7a004491d46168d0ecaa7",
      "required_for_text": true
    },
    {
      "path": "Maya-S-v2-IQ2_XXS/GLM-5.3-Flash-Maya-S-v2-IQ2_XXS-00002-of-00003.gguf",
      "bytes": 47797478080,
      "sha256": "2d65d88a69f8dc124c8d24bd33b33161ddab218918b4253ad4f500aeede84df5",
      "required_for_text": true
    },
    {
      "path": "Maya-S-v2-IQ2_XXS/GLM-5.3-Flash-Maya-S-v2-IQ2_XXS-00003-of-00003.gguf",
      "bytes": 835121120,
      "sha256": "a6a981c4fee7a53d78bdbd97d8f465d48ddf439cf938ff90617f395e0351b5a6",
      "required_for_text": true
    },
    {
      "path": "vision/GLM-5.3-Flash-vocab.gguf",
      "bytes": 9426880,
      "sha256": "8f53cb1bd2e631c14ef413e3284735d9e53f3c508d07a6f609e705b487105912",
      "required_for_text": false
    },
    {
      "path": "vision/mmproj-GLM-5.3-Flash-F16.gguf",
      "bytes": 1128046944,
      "sha256": "3627575df16bd152db0f3fd7e488d270b33f3a9e6c7fa3b1b8ac381faafde882",
      "required_for_text": false
    }
  ]
}
```

Immutable download prefix: `https://huggingface.co/peasantsmith/GLM-5.3-Flash-Maya-GGUF/resolve/f16ee4ba50d8fd2bb726ea1862ca6ca14ba188da/`; append each artifact path. All three ordinary text shards are required, **96,468,592,256 bytes total** (96.469 GB, approximately 89.844 GiB). Do not substitute S24, Derisked, EXL3 or DeepSeek Maya-S.

The publisher says NextN/MTP is included in these GGUFs. No separate MTP weights are required. The main tokenizer is embedded in GGUF and exported by the packer; there is no required standalone HF tokenizer download for this GGUF recipe. The two vision artifacts are needed together only for the optional CPU image encoder. [Pinned publisher card](https://huggingface.co/peasantsmith/GLM-5.3-Flash-Maya-GGUF/blob/f16ee4ba50d8fd2bb726ea1862ca6ca14ba188da/README.md), [tokenizer export](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/tools/iq_pack.py#L396).

## Pinned setup command

Commands below are instructions, not executed research. Run from the pinned engine root after retaining the parent's proven Ubuntu ROCm library, rocWMMA, bitcode and HIP Release flag fixes. Replace `N` with the Radeon 8060S index reported by `--check`:

```sh
./setup.sh --backend hip --gpu N --check
./setup.sh --setup --backend hip --gpu N \
  --model Maya-S-v2-IQ2_XXS --draft mtp \
  --context 8192 --no-vision --yes --no-start \
  --gguf-dir /absolute/path/to/Maya-S-v2-IQ2_XXS
```

Pass `--gguf-dir` after downloading and verifying all three immutable files; it accepts their writable folder or first shard. For installer-managed downloads replace it with `--models-dir /absolute/path/to/models --download-model`. **The pinned installer uses HF revision `main`, not an immutable revision**, although it validates known publisher SHA256 values. There is no model-revision CLI option: a strict manifest should fetch immutable URLs first and use `--gguf-dir`. Exact recipe is `Maya-S-v2-IQ2_XXS`, not model-card shorthand `Maya-S` or DeepSeek `--ds4-quant maya-s`. Supported HIP flag is `--backend hip`, not `--hip`. [Pinned recipe](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/maya.py#L124), [options](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/docs/SETUP.md#options).

For engine root `ROOT` and GGUF directory `D`, outputs are:

- Binary `ROOT/build-hip/maya`, CMake target **maya**, not `maya-ds4`.
- Pack `D/pack/`; if that indexes another model, a lowercase `D/pack-<model>/` alternative.
- Required pack files: `index.txt`, `native_experts.txt`, `dense.bin`, `tokenizer/vocab.json`, `tokenizer/merges.txt`, `tokenizer/token_type.json`, `tokenizer/chat_template.jinja`; provenance/stamp files may also exist. Experts remain in the original adjacent GGUFs.
- Config `ROOT/maya-maya-s-v2-iq2_xxs-hip.json`, launcher `ROOT/run-maya-maya-s-v2-iq2_xxs-hip.sh`, log same config stem with `.log`.
- Start: `./maya.sh --config "$PWD/maya-maya-s-v2-iq2_xxs-hip.json"`.

The config uses `--glm-pack D/pack --max-context 8192 --expert-cache auto --prefill auto --kv-streaming on`, tokenizer `D/pack/tokenizer`, and GPU selection through `HIP_VISIBLE_DEVICES`. [Pack step](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/maya.py#L2041), [config](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/maya.py#L2231).

## Dependency and build contract

There is no tracked `.gitmodules` in this tree: no GLM-specific Git submodule is required. Setup obtains **llama.cpp 3cf03257f219afbe7334045ff7c6a06ac68c627d**, normally as an archive into `third_party/llama.cpp`. It supplies `ggml/` for native IQ CPU experts and HIP prompt MMQ sources, and `gguf-py/` for packing. Maya's `third_party/ggml` is compatibility material with a VERSION pin, not a complete llama.cpp checkout. `--llama-dir` can reuse a checkout, but setup only checks required directories, not its actual revision: parent must enforce the pin. [Dependency pin](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/setup.py#L26), [tools step](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/maya.py#L1296), [CMake dependency](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/CMakeLists.txt#L879).

Pinned HIP configure sets `CMAKE_BUILD_TYPE=Release`, `STRATA_ENABLE_HIP=ON`, `STRATA_ENABLE_CUDA=OFF`, `STRATA_PREFILL_MMQ=ON`, `STRATA_NATIVE_EXPERTS=ON`, `STRATA_BUILD_TESTS=OFF`, `CMAKE_HIP_ARCHITECTURES=gfx1151`, `CMAKE_HIP_COMPILER=<ROCm clang++>`, `CMAKE_PREFIX_PATH=<ROCm root>`, `STRATA_GGML_DIR=<llama.cpp>`, then builds target `maya` with up to four jobs. gfx1151 uses WMMA-capable code, so the parent's rocWMMA fix remains relevant. Explicit `-DCMAKE_HIP_FLAGS_RELEASE='-O3 -DNDEBUG'` is a parent-proven environment fix; this pinned installer only sets build type and does not supply it explicitly. Preserve and verify actual cache/compile flags. [Pinned build](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/maya.py#L1560), [HIP backend](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/cmake/hip_backend.cmake#L63).

Actual GLM packing is equivalent to:

```sh
STRATA_GGUF_PY="$PWD/third_party/llama.cpp/gguf-py" \
  .venv/bin/python tools/iq_pack.py \
  --gguf /absolute/path/to/Maya-S-v2-IQ2_XXS/GLM-5.3-Flash-Maya-S-v2-IQ2_XXS-00001-of-00003.gguf \
  --out /absolute/path/to/Maya-S-v2-IQ2_XXS/pack --compat-bf16
```

`--compat-bf16` upgrades projections that GLM kernels require as BF16. Do not use DeepSeek's packer, Engram tables, DSpark files or the older generic `tools/mtp_pack.py` flow. [Pinned pack invocation](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/maya.py#L2073).

## Architecture, memory and drafting

GLM is a 321B/about 18B-active hybrid MoE: hidden 4096, 45 trunk layers (34 KDA linear attention, 11 NoPE MLA/DSA), first three FFNs dense, 42 MoE layers, 288 routed experts/top-8 plus one shared, mHC, one NextN block, vocabulary 154880. Canonical GGUF architecture **glm5-next**, with legacy **glm5next** also accepted. The pinned first-shard hash list accepts both historical spellings, but the manifest above pins the current canonical file. HF's repository-wide generated summary (`llama`, context 65536, tiny aggregate size) must not replace actual architecture/shard metadata; the repository also contains the small vision vocabulary GGUF. [Official config](https://huggingface.co/zai-org/GLM-5.3-Flash/blob/main/config.json), [pinned architecture guard](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/maya.py#L464), [pinned HF API](https://huggingface.co/api/models/peasantsmith/GLM-5.3-Flash-Maya-GGUF/revision/f16ee4ba50d8fd2bb726ea1862ca6ca14ba188da?blobs=true).

AMD remains experimental. Linux gfx1151 defaults leave `POOL_GB`/`RAM_GB` unset, with `MAYA_GLM_RESERVE_MB=1024`, `MAYA_GLM_RAM_HEADROOM_GB=16`, `MAYA_GLM_PREFILL_SUB=1024`, and 6144 MiB prompt budget on >=96 GiB systems. Pool sizing uses physical `MemAvailable` after dense/KV/staging allocations, subtracts headroom/reserve, and caps at expert bytes. GTT capacity is not free physical RAM. The docs estimate ordinary Maya-S experts at ~80.17 GiB; prompt buffers borrow the pool tail. Unified RAM and GPU memory must not be counted twice. Omit the discrete-card example `MAYA_GLM_RAM_GB=60`. Every-expert residency depends on actual free memory and context; inspect logs and repeated rounds. [Pinned Strix Halo contract](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/docs/AMD_MAYA.md#strix-halo).

Research recommendation: validate at 8192 context before increasing. Installer default is 32768, menu 8192/32768/65536/131072; explicit `--context N` is accepted directly. Native model context 1048576 is not a memory-fit guarantee. Engine capacity is `--max-context`; new config enables `--kv-streaming on`. Keep `--prefill auto`, not an old forced-256 chunk. hipBLASLt tables are guarded by GPU architecture and library version; unmatched ROCm7.1 library versions fall back to hipBLAS. [Context source](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/maya.py#L1142), [prompt controls](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/docs/AMD_MAYA.md#prompt-speed).

**Pinned gfx115x code already enables single-GPU native MTP by default**, despite stale installer wording saying one GPU has no drafting. Installer/config controls: `MAYA_GLM_MTP_DECODE=0|1`, `MAYA_GLM_MTP_DRAFT` (default 3, 0 disables), `MAYA_GLM_MTP_ADAPT=0` (fixed rather than adaptive length), `MAYA_GLM_MTP_CHAIN=0` (disable device-chain drafts). `MAYA_GLM_NO_SPEC` and `MAYA_GLM_NO_MTP` disable by variable **presence**, even text `0`: unset them to restore. Excluded with `--parallel`/multiple sequences or DFlash. `--draft mtp` chooses built-in weights; `--drafting off` is for external DFlash2/DeepSeek DSpark selection, not a reliable built-in MTP off switch. There is no `--native-draft` CLI flag. Older generic `--native`, `--mtp DIR`, `--spec` instructions are not this GLM setup contract. [Pinned MTP implementation](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/src/core/glm_mtp.cu#L54), [settings](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/docs/SETTINGS.md#L86).

Python/config launchers alias `MAYA_*` to `STRATA_*`. If invoking `build-hip/maya` directly, use **STRATA_GLM_MTP_DECODE**, **STRATA_GLM_NO_SPEC**, etc.; do not assume the Python alias layer exists. [Name translation](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/tools/maya_names.py).

DSpark is DeepSeek-specific: no DS41F artifacts apply to this GLM install. DFlash2 is a separate optional GLM drafter documented for NVIDIA and unnecessary here. HIP setup is text-only by default. Optional Linux CPU vision requires separately building `maya-vision` after `tools/vision/glm5next_patch.py` patches pinned llama.cpp, both GLM vision artifacts above, and manual config entries. [Pinned CPU vision instructions](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/docs/AMD_MAYA.md#cpu-image-encoder-on-linux).

## Current docs versus pinned code; limitations

At lookup GitHub main is **4615f346d9311dd1ce9f5738ab04446f023a6e42**, **v1.1.27**. Its AMD_MAYA.md equals the pinned document after newline normalization. However current source/settings add parallel MTP work (`glm_mtp_rows.cu`, `MAYA_GLM_MULTI_MTP`) absent from v1.1.22. Do not apply current-main flags to the pin without checking. `docs/GLM5-FLASH.md` is a historical 2026-09-29 port study against 0.1.27, not current installation instructions. [Main API](https://api.github.com/repos/mw00/project-maya/commits/main), [commit comparison](https://github.com/mw00/project-maya/compare/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197...4615f346d9311dd1ce9f5738ab04446f023a6e42), [historical study](https://github.com/mw00/project-maya/blob/ffe2e5e53fd5254e7f3492a20e8ad2b51b26a197/docs/GLM5-FLASH.md).

Unresolved: actual memory/context ceiling; GLM target compilation/runtime with ROCm7.1/clang21 under the parent's local fixes; downloaded file integrity; actual pack size; MTP acceptance/speed. Published APU results generally use newer ROCm or Windows and do not establish this exact Ubuntu result. Parent must retain/check HIP Release compile flags and validate generation, repeated requests and MTP on/off on the target machine. No multi-GB files or target runtime were accessed.
