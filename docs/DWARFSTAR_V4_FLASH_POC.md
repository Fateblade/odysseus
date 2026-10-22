# Install DwarfStar V4 Flash and connect Odysseus

Prepared 2026-10-22 for Seth's Ubuntu Server 26.04.1, Ryzen AI Max+ 395 / Radeon 8060S (`gfx1151`), 128 GB installed RAM. The recorded GPU capacity is 96 GiB. This is an installation procedure to execute on that server; it has not been executed there.

The proof of concept uses **DeepSeek V4 Flash 0731 Q2**, about 81 GiB of weights, one resident session and **4096-token context**. It does not install V4.1, PRO, vision or speculative decoding. Start DwarfStar only after unloading competing large models. No automatic backend switching is implemented.

Odysseus child branch: [`feature/linux-ai-operations-dwarfstar`](https://github.com/Fateblade/odysseus/tree/feature/linux-ai-operations-dwarfstar), based on `feature/linux-ai-operations` at `bfbb93f4c8cfbd51b67be9d0720d06494469c748`. Install the committed integration by checking out that branch and rebuilding the existing stack. Subsequent updates use `git pull --ff-only` and the same rebuild procedure.

## 1. Connect to the server

Open an SSH terminal on Ubuntu as `seth`. Run the shell blocks below there using Bash. Default paths match the earlier Fateblade migration: `/mnt/odysseus-fateblade`, `~/dwarfstar`, `/mnt/ai-models/dwarfstar`. If your deployment uses different paths, substitute them consistently before running commands.

This guide assumes the Operations host helper and its SSH connection already work. If Operations says host not connected, complete the existing Operations setup first. Do not replace your `.env`, LiteLLM YAML, database or SSH identity with example files.

## 2. Record hardware, storage and ROCm readiness

```bash
cat /etc/os-release
uname -r
free -h
id
ls -l /dev/kfd /dev/dri/renderD*
command -v hipcc rocminfo
rocminfo > "$HOME/dwarfstar-rocminfo.txt"
grep -E 'gfx1151|GLOBAL|Size:|Allocatable' "$HOME/dwarfstar-rocminfo.txt"
df -h /mnt/ai-models "$HOME"
ss -ltnp '( sport = :8000 )'
```

Expected: `gfx1151`, usable `/dev/kfd`, a sufficiently large GPU allocation pool, and no listener on port 8000. Examine the complete rocminfo report: do not assume that every `Size:` line describes GPU-allocatable memory. Model weights need about 81 GiB **plus** graph, context and runtime buffers. Leave space for the OS, containers and other services; the existing 96 GiB GPU capacity is promising, not a guarantee that every allocation succeeds.

Reserve at least **100 GiB free** on the model filesystem as an initial operational allowance, plus separate room for container builds and logs. This is a planning margin, not a measured runtime cache bound. Stop here if memory access or storage is insufficient. Do not copy kernel memory parameters from another machine or change firmware for this first attempt.

If required build packages are missing, inspect the proposed package changes first:

```bash
sudo apt-get update
sudo apt-get install --simulate build-essential git curl ca-certificates python3 \
  hipcc rocminfo rocm-smi libamdhip64-dev libhipblas-dev libhipblaslt-dev \
  librocblas-dev librocwmma-dev libhipcub-dev
```

If that proposes replacing the working ROCm stack or removing packages, resolve that before proceeding. Otherwise install the same package list without `--simulate`. If the account lacks GPU permissions:

```bash
sudo usermod -aG render,video "$(id -un)"
```

Log out and reconnect after group changes, then rerun `rocminfo`. Missing `rocwmma/internal/` headers require complete headers matching the installed ROCm version; do not mix unrelated header releases. The upstream guide also links a container alternative if native dependencies cannot be reconciled.

## 3. Preserve the current Odysseus deployment

Finish all dashboard host jobs and wait for active inference requests before changing helper source or stopping services. From the current deployment:

```bash
cd /mnt/odysseus-fateblade
git status --short
git branch --show-current
umask 077
export DWARFSTAR_BACKUP="$HOME/.local/share/odysseus-dwarfstar-poc/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DWARFSTAR_BACKUP"
git rev-parse HEAD > "$DWARFSTAR_BACKUP/odysseus-base.txt"
cp -p .env "$DWARFSTAR_BACKUP/odysseus.env"
if test -f docker-compose.override.yml; then
  cp -p docker-compose.override.yml "$DWARFSTAR_BACKUP/docker-compose.override.yml"
fi
if test -f "$HOME/.config/odysseus-operations/management.json"; then
  cp -p "$HOME/.config/odysseus-operations/management.json" "$DWARFSTAR_BACKUP/management.json"
fi
DWARFSTAR_OLD_CONTAINER=$(docker compose ps -q odysseus)
test -n "$DWARFSTAR_OLD_CONTAINER"
DWARFSTAR_OLD_IMAGE=$(docker inspect --format '{{.Image}}' "$DWARFSTAR_OLD_CONTAINER")
docker image tag "$DWARFSTAR_OLD_IMAGE" odysseus-fateblade:before-dwarfstar
printf '%s\n' "$DWARFSTAR_BACKUP"
```

Keep that backup directory path for rollback and subsequent SSH sessions. The backups contain private deployment configuration. Do not continue if image lookup/tagging fails. Preserve your existing consistent database/data backup as well; this extension does not migrate the database. Do not run `docker compose down -v`.

The source tree should be clean before switching branches. If `git status` lists local source edits, review and preserve those separately; do not reset them. Ignored deployment `.env` and data files can remain.

## 4. Check out the published Odysseus child branch

```bash
cd /mnt/odysseus-fateblade
git fetch origin
if git show-ref --verify --quiet refs/heads/feature/linux-ai-operations-dwarfstar; then
  git switch feature/linux-ai-operations-dwarfstar
else
  git switch --track -c feature/linux-ai-operations-dwarfstar origin/feature/linux-ai-operations-dwarfstar
fi
git pull --ff-only
git log -1 --oneline
git status --short
```

Expected: the branch tracks `origin/feature/linux-ai-operations-dwarfstar` and the source tree is clean. If checkout or fast-forward pull fails, stop and reconcile local changes or diverging commits before continuing. This branch contains the existing Operations work as well as the DwarfStar integration. The source also contains this guide at `docs/DWARFSTAR_V4_FLASH_POC.md`.

## 5. Build a pinned DwarfStar checkout

The source revision used for this procedure is `5379c1c9ac315f1540371b06a3db88491d4059ff` (2026-10-19).

```bash
cd "$HOME"
git clone https://github.com/antirez/ds4.git dwarfstar
cd "$HOME/dwarfstar"
git checkout --detach 5379c1c9ac315f1540371b06a3db88491d4059ff
git rev-parse HEAD
make -j2 strix-halo
test -x ./ds4-server
./ds4-server --help
```

If `~/dwarfstar` already exists, inspect its origin, revision and local changes first; do not overwrite it. `-j2` bounds concurrent compiler memory use. Do not rebuild Strata or change its Python environment for this step.

### Recovery: Ubuntu 7.1.0 rocWMMA package omits internal headers

Observed on the server: `librocwmma-dev 7.1.0-0ubuntu1`, `libamdhip64-dev 7.1.0-0ubuntu2`, `hipcc 7.1.1+dfsg-0ubuntu1`, HIP 7.1 and Ubuntu clang 21.1.8. The build fails at `/usr/include/rocwmma/rocwmma.hpp` with missing `internal/accessors.hpp`. The package inventory omits that file. A subsequent missing `ds4-server` means compilation did not complete.

For this specific package version, use the complete upstream ROCm 7.1.0 header tree in a separate checkout. The [upstream tag](https://github.com/ROCm/rocWMMA/tree/rocm-7.1.0) resolves to `27a847f610bfdb4e58dd257889734168b0ef8626`; its public header includes the matching internal headers. No system package installation or system header changes are needed for this workaround:

```bash
git clone --depth 1 --branch rocm-7.1.0 \
  https://github.com/ROCm/rocWMMA.git "$HOME/rocwmma-7.1.0"
git -C "$HOME/rocwmma-7.1.0" rev-parse HEAD
test "$(git -C "$HOME/rocwmma-7.1.0" rev-parse HEAD)" = 27a847f610bfdb4e58dd257889734168b0ef8626
test -f "$HOME/rocwmma-7.1.0/library/include/rocwmma/internal/accessors.hpp"
```

Run clone only if that destination does not already exist; inspect an existing checkout instead. Both checks must succeed. First test header inclusion without building or running a GPU program:

```bash
printf '#include <rocwmma/rocwmma.hpp>\n' | \
  hipcc --offload-arch=gfx1151 -std=c++17 \
    -I"$HOME/rocwmma-7.1.0/library/include" -x hip -fsyntax-only -
```

If this succeeds, rebuild DwarfStar using an explicit include path ahead of the incomplete system headers. The pinned DwarfStar Makefile supports a command-line `HIPCC` override and passes it to the recursive ROCm build:

```bash
cd "$HOME/dwarfstar"
make -j2 strix-halo HIPCC="hipcc -I$HOME/rocwmma-7.1.0/library/include" && \
  ./ds4-server --help
```

Use that same build command for later rebuilds while this Ubuntu packaging issue remains. No separate rocWMMA library build is required: it is a header library. Keep the checkout for rebuilding; the running server does not need it. This addresses the observed missing-header failure; actual compilation with the server's HIP toolchain still needs validation there. If the include test or rebuild fails, retain its first compiler error for diagnosis rather than copying individual headers into `/usr/include`.

## 6. Download only Flash 0731 Q2

```bash
sudo install -d -o "$(id -un)" -g "$(id -gn)" /mnt/ai-models/dwarfstar
cd "$HOME/dwarfstar"
DS4_GGUF_DIR=/mnt/ai-models/dwarfstar ./download_model.sh ds4f-q2
export DWARFSTAR_MODEL=/mnt/ai-models/dwarfstar/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf
test -s "$DWARFSTAR_MODEL"
ls -lh "$DWARFSTAR_MODEL"
```

The downloader can resume an interrupted download by rerunning the same command. This target is public; no token is required for the normal download. Verified metadata on 2026-10-22 identifies an **86,720,111,488-byte** file with SHA-256 `ca22ae2f838e14077c22bc1c1417b71b45b5e5a3687bd96c2ac6e17fdb6261c0`. The following check fetches only public metadata, verifies both the published checksum and local file against that pinned value, and records the metadata revision. Reading the full 81 GiB file to hash it takes time:

```bash
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path
from urllib.request import urlopen

path = Path(os.environ['DWARFSTAR_MODEL'])
url = 'https://huggingface.co/api/models/antirez/deepseek-v4-gguf?blobs=true'
with urlopen(url, timeout=60) as response:
    metadata = json.load(response)
entry = next(item for item in metadata['siblings'] if item['rfilename'] == path.name)
expected = 'ca22ae2f838e14077c22bc1c1417b71b45b5e5a3687bd96c2ac6e17fdb6261c0'
if entry['lfs']['sha256'] != expected or path.stat().st_size != 86720111488:
    raise SystemExit('Model artifact differs from the reviewed version; investigate before continuing.')
with path.open('rb') as file:
    actual = hashlib.file_digest(file, 'sha256').hexdigest()
if actual != expected:
    raise SystemExit('Model checksum mismatch; do not start the server.')
record = {'repository': metadata['id'], 'revision': metadata['sha'],
          'file': path.name, 'sha256': actual, 'bytes': path.stat().st_size}
path.with_suffix('.verified.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
PY
```

If the metadata API is unavailable or no longer supplies the checksum field, obtain a trusted checksum before continuing; do not silently skip a mismatch. The service below uses the explicit verified filename rather than the downloader's mutable default symlink.

## 7. Release competing models and run a foreground test

In Odysseus **Management**, unload the Strata model and each loaded Ollama model. Wait for all operations to finish. Alternatively stop your verified Strata user unit and unload Ollama models through its native API after current requests finish. Do not use a broad process kill.

```bash
curl --fail --silent --show-error http://127.0.0.1:8081/health
curl --fail --silent --show-error http://127.0.0.1:11434/api/ps
free -h
rocm-smi --showmeminfo vram
```

Expected: Strata reports unloaded (or its service is intentionally stopped), Ollama `/api/ps` lists no loaded models, and memory use has fallen. Check host GPU usage too; no other large GPU job should be running. If `rocm-smi` lacks this counter, use the dashboard's host GPU readings and `rocminfo` rather than treating missing data as zero.

Start in the foreground:

```bash
cd "$HOME/dwarfstar"
./ds4-server --rocm \
  -m /mnt/ai-models/dwarfstar/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf \
  --ctx 4096 --host 127.0.0.1 --port 8000
```

Wait for model loading and the listening message. In a **second SSH terminal**, verify:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/v1/models | python3 -m json.tool
curl --fail --silent --show-error --max-time 300 http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"Reply with one short sentence about Redis."}],"max_tokens":64,"temperature":0,"think":false}'
curl --fail --no-buffer --max-time 300 http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"Count from one to five."}],"max_tokens":64,"temperature":0,"think":false,"stream":true,"stream_options":{"include_usage":true}}'
```

Expected: model metadata identifies Flash and context 4096, ordinary chat returns content, and streaming ends with usage and `[DONE]`. Both Flash and Pro request aliases may appear: **they refer to the same startup-selected Flash weights**. The catalog does not prove that PRO is loaded. No `/health` endpoint is assumed for DwarfStar.

If loading fails or the system swaps heavily, stop with Ctrl+C and inspect the error. Do not increase the context, start a second copy or disable memory guards to get past an allocation failure.

## 8. Install the user service without boot autostart

Stop the foreground server with Ctrl+C and confirm port 8000 is free before continuing.

```bash
mkdir -p "$HOME/.config/systemd/user"
if test -e "$HOME/.config/systemd/user/dwarfstar.service"; then
  printf '%s\n' 'A DwarfStar unit already exists; inspect it before continuing.'
else
  install -m 644 /mnt/odysseus-fateblade/scripts/dwarfstar.service.example \
    "$HOME/.config/systemd/user/dwarfstar.service"
fi
```

Inspect the installed unit before running the next block. An existing unit was deliberately not overwritten:

```bash
cat "$HOME/.config/systemd/user/dwarfstar.service"
systemd-analyze --user verify "$HOME/.config/systemd/user/dwarfstar.service"
systemctl --user daemon-reload
systemctl --user start dwarfstar.service
systemctl --user status dwarfstar.service --no-pager
journalctl --user -u dwarfstar.service -n 100 --no-pager
```

The unit uses `%h/dwarfstar`, the explicit model path, loopback port 8000 and 4K context. It deliberately has **no automatic restart or boot enablement** for this memory-sensitive proof of concept. After `start` returns, wait for `/v1/models` to become ready; `Type=simple` is not a model-readiness check.

For dashboard control after logout, ensure the existing user manager is persistent:

```bash
loginctl show-user "$(id -un)" -p Linger
```

If needed, enable it with `sudo loginctl enable-linger "$(id -un)"`. This retains the user manager; it does not enable DwarfStar at boot. Use `systemctl --user stop dwarfstar.service` to release its memory.

## 9. Enable the optional Odysseus connection

Merge the service entry into the existing management JSON; the script preserves other settings and refuses a conflicting existing entry:

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path.home() / '.config/odysseus-operations/management.json'
settings = json.loads(path.read_text())
service = {'unit': 'dwarfstar.service', 'scope': 'user'}
services = settings.setdefault('services', {})
if 'dwarfstar' in services and services['dwarfstar'] != service:
    raise SystemExit('Existing DwarfStar service differs; review it before changing.')
services['dwarfstar'] = service
settings.setdefault('jobs_directory', str(Path.home() / '.local/share/odysseus-operations/jobs'))
temporary = path.with_name(path.name + '.dwarfstar.tmp')
with temporary.open('x') as file:
    os.chmod(temporary, 0o600)
    json.dump(settings, file, indent=2)
    file.write('\n')
os.replace(temporary, path)
PY
```

In `/mnt/odysseus-fateblade/.env`, add or update these values, preserving all other entries:

```dotenv
ODYSSEUS_IMAGE=odysseus-fateblade:dwarfstar-poc
ODYSSEUS_OPERATIONS_DWARFSTAR_URL=http://127.0.0.1:8000
ODYSSEUS_OPERATIONS_HOST_HELPER=/mnt/odysseus-fateblade/scripts/odysseus-litellm.py
```

Keep your existing SSH host, host Python, management path and Compose project name. The DwarfStar URL is relative to the **Linux host helper**, not the browser or bridged Odysseus container. The updated helper and worker source must both be on the host at this checkout.

Validate configuration without printing resolved secrets, build the changed app and recreate only Odysseus:

```bash
cd /mnt/odysseus-fateblade
docker compose config --quiet
docker compose build odysseus
docker compose up -d --no-deps --pull never odysseus
docker compose ps odysseus
```

Keep the same existing Compose overrides, project name, bind addresses and volumes. Do not pull an upstream image over the custom build. This is an update to the existing project, not another parallel stack.

Sign in as admin. **Overview** should show DwarfStar readiness, reported model and context. **Management → DwarfStar residency** should show one model and the accepted request aliases. Start/stop controls use the configured user service. A stopped or starting server has unknown residency until readiness is confirmed; no fabricated zero-memory counter is displayed.

## 10. Add a separate LiteLLM deployment

Verify the existing `litellm` container still uses host networking:

```bash
docker inspect --format '{{.HostConfig.NetworkMode}}' litellm
```

Expected: `host`. Otherwise determine the correct backend address before using the loopback URL below.

In **Models & routing → Add deployment**, enter:

- Public model name: `dwarfstar-flash`
- Backend model: `openai/deepseek-v4-flash`
- Endpoint: `http://127.0.0.1:8000/v1`

Use **Test endpoint**, review the diff, and apply through the existing workflow. This appends to the current model list. The local compatible endpoint receives the existing `not-needed` placeholder key; keep real gateway credentials private. Preserve all current `pa-*` aliases and fallbacks. Do not configure automatic fallback to this route while DwarfStar is normally stopped.

In Odysseus chat, keep the existing LiteLLM connection and refresh its model catalog. Select `dwarfstar-flash` and verify a short streamed response. An API client can use the same gateway URL/key it already uses, changing only the model to `dwarfstar-flash`. A catalog probe alone does not prove generation works.

For a direct coding-agent smoke check, save the following as `~/dwarfstar-tool-check.py` and run `python3 ~/dwarfstar-tool-check.py`. It executes only a fixed addition function locally, then submits the returned tool-call ID and result:

```python
import json
import os
from urllib.request import Request, urlopen

base = os.environ.get('DWARFSTAR_TEST_BASE', 'http://127.0.0.1:8000/v1')
model = os.environ.get('DWARFSTAR_TEST_MODEL', 'deepseek-v4-flash')
headers = {'Content-Type': 'application/json'}
if os.environ.get('LITELLM_API_KEY'):
    headers['Authorization'] = 'Bearer ' + os.environ['LITELLM_API_KEY']

def chat(messages, choice):
    payload = {'model': model, 'messages': messages, 'max_tokens': 256,
               'temperature': 0, 'think': False, 'tools': tools, 'tool_choice': choice}
    request = Request(base.rstrip('/') + '/chat/completions',
                      data=json.dumps(payload).encode(), headers=headers)
    with urlopen(request, timeout=300) as response:
        return json.load(response)['choices'][0]['message']

tools = [{'type': 'function', 'function': {'name': 'add',
    'description': 'Add two integers.', 'parameters': {'type': 'object',
    'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
    'required': ['a', 'b'], 'additionalProperties': False}}}]
messages = [{'role': 'user', 'content': 'Use add to calculate 2 + 3, then state the result.'}]
assistant = chat(messages, {'type': 'function', 'function': {'name': 'add'}})
calls = assistant.get('tool_calls', [])
if len(calls) != 1 or calls[0]['function']['name'] != 'add':
    raise SystemExit('Expected one add tool call; inspect the model response.')
arguments = json.loads(calls[0]['function']['arguments'])
if arguments != {'a': 2, 'b': 3}:
    raise SystemExit('Unexpected arguments; tool quality needs inspection.')
messages.extend([assistant, {'role': 'tool', 'tool_call_id': calls[0]['id'], 'content': '5'}])
final = chat(messages, 'none')
if not final.get('content'):
    raise SystemExit('No final answer after tool result.')
print(final['content'])
```

Expected: one add call with the correct arguments, followed by an answer giving 5. To repeat through LiteLLM, enter the gateway key locally in the SSH terminal without echoing it, and override the base/model:

```bash
read -r -s -p 'Existing LiteLLM gateway key: ' LITELLM_API_KEY
printf '\n'
export LITELLM_API_KEY
DWARFSTAR_TEST_BASE=http://127.0.0.1:4000/v1 DWARFSTAR_TEST_MODEL=dwarfstar-flash \
  python3 "$HOME/dwarfstar-tool-check.py"
unset LITELLM_API_KEY
```

If LiteLLM rejects a DeepSeek-specific optional parameter, inspect the error and adjust only the new deployment's parameter handling before repeating. Do not mark tool support validated based solely on an advertised parameter list. The smoke test is a real-model acceptance check, not a guarantee of every coding-agent workflow.

## 11. Run the first dashboard benchmark

In **Scripts & TPS tests** choose backend `dwarfstar`, model `deepseek-v4-flash`, 3 runs, warmup enabled, and 128 maximum output tokens. Use a short fixed prompt. Keep competing models unloaded.

The benchmark talks directly to DwarfStar through the host helper. It deliberately disables thinking for this proof of concept. Results include server-reported token usage, client TTFT, elapsed time and end-to-end TPS. The inspected server does not supply native prefill/decode timings; those values remain unavailable rather than counting SSE chunks as tokens or inventing native rates. Warmup does not flush caches.

Export the result JSON and retain the engine revision, verified model metadata, context and concurrent-load conditions. After a stable 4K run, consider 8K separately and remeasure memory. Do not copy Strata's 131072-token setting into this service.

## 12. Switch back and recover

For normal switching, finish DwarfStar requests, stop its service, confirm its process has exited and GPU memory has fallen, then reload Strata or an Ollama model. The LiteLLM route can remain configured while the backend is stopped, but calls to that route will fail until you start it.

```bash
systemctl --user stop dwarfstar.service
systemctl --user is-active dwarfstar.service
ss -ltnp '( sport = :8000 )'
```

For application rollback, first wait for host jobs to finish. Restore the saved `.env` and management JSON from your recorded backup path, then recreate only Odysseus using the preserved image:

```bash
cd /mnt/odysseus-fateblade
cp -p "$DWARFSTAR_BACKUP/odysseus.env" .env
if test -f "$DWARFSTAR_BACKUP/management.json"; then
  cp -p "$DWARFSTAR_BACKUP/management.json" "$HOME/.config/odysseus-operations/management.json"
fi
ODYSSEUS_IMAGE=odysseus-fateblade:before-dwarfstar docker compose up -d --no-deps --pull never odysseus
```

If configuration changed after this experiment, reconcile those later changes rather than overwriting them with the snapshot. Remove just the added LiteLLM deployment through Models & routing when no longer needed. Leave model files and caches in place unless you explicitly decide to delete them. The updated helper accepts older app calls that omit the new optional argument. To restore the prior helper source as well, first ensure no host jobs are active, then switch to the branch/revision recorded in your backup; preserve any later local edits before switching.

## Sources and verification limits

- [Pinned Strix Halo setup](https://github.com/antirez/ds4/blob/5379c1c9ac315f1540371b06a3db88491d4059ff/docs/STRIX_HALO.md)
- [Pinned model/download catalog](https://github.com/antirez/ds4/blob/5379c1c9ac315f1540371b06a3db88491d4059ff/download_model.sh)
- [Pinned API source](https://github.com/antirez/ds4/blob/5379c1c9ac315f1540371b06a3db88491d4059ff/ds4_server.c)
- [Serving documentation](https://github.com/antirez/ds4/blob/5379c1c9ac315f1540371b06a3db88491d4059ff/docs/SERVER.md)

Local verification covers discovery, malformed/unavailable/disabled endpoints, helper/worker endpoint propagation, benchmark usage handling and existing Operations regressions. The actual ROCm build, model download, GPU allocation, user-service permissions and real-model chat/tool quality require execution on the Ubuntu server. Checking out and rebuilding Odysseus installs its integration; complete the DwarfStar build, model and service steps separately on the first installation.
