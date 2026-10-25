# On-demand local inference

The host router loads a configured model when a request arrives, releases competing models, forwards the response, and releases the model after its keep-alive expires. It supports Strata, Maya DS41F, Maya GLM, DwarfStar and Ollama. This is an optional host service; installing the dashboard alone does not activate it.

## Behaviour

- Requests enter LiteLLM as before. Selected LiteLLM deployments point to the local router at `http://127.0.0.1:8094/v1`.
- Public router aliases map to explicit native backend/model pairs. A request cannot select a new endpoint or systemd unit.
- The first version runs one local request at a time. Waiting requests are processed in order; a waiter times out after 900 seconds. Changing model may require another cold load.
- Before loading, the router stops other managed exclusive engines, unloads Strata and unloads competing Ollama models. It deliberately retains only one routed model, even though Strata and Ollama can otherwise share memory.
- Maya and DwarfStar use their existing systemd services and saved model/context/MTP settings. Strata uses its load/unload API; Ollama uses its native residency API. No runtime, ROCm, BIOS or model installation changes are made.
- Keep-alive starts after the complete upstream response, including streamed output. Each completed request resets the timer. The default is **600 seconds**; **0** releases immediately. There is no negative/forever setting.
- Idle expiry uses unload for Strata/Ollama and stop for Maya/DwarfStar. It does not delete model files. Readiness and release are checked before a competing engine starts.
- Management mutations and host benchmark/configuration jobs share a process lock with routed inference. They receive an explicit conflict while inference is active; UI service mutations disable on the next live sample.
- A disconnected client does not cause a mid-generation model eviction: the router drains the upstream response before releasing the lease. A transport failure that prevents confirming completion blocks further switching and automatic eviction until an operator checks the engine and clears the saved blocked state.
- Existing benchmark history is unchanged. Operations benchmarks still measure native endpoints and acquire the same maintenance lease; they do not measure cold routing latency.

All clients using these local engines must pass through the router for this coordination to apply. Direct native requests, direct shell commands and external services do not acquire its lease. In particular, route local embedding deployments through it too if they share this machine. Cloud deployments remain unchanged. Do not run multiple router workers or replicas for this host.

## 1. Update the host checkout and dashboard

On the Ubuntu host, using the existing checkout:

```bash
cd /mnt/odysseus-fateblade
git switch feature/linux-ai-operations-maya-glm53
git pull --ff-only
docker compose build odysseus
docker compose up -d --no-deps odysseus
```

The host helper must execute this updated checkout, using the same management file as the router. The lock is scoped to that file's directory. Its existing allowlisted service units and endpoints remain the source of host control.

## 2. Install proxy dependencies in the helper virtual environment

This installs Python packages into the existing helper venv; it does not change host ROCm packages.

```bash
ROUTER_PYTHON="$HOME/.local/share/odysseus-operations/venv/bin/python"
"$ROUTER_PYTHON" -m pip install starlette uvicorn httpx
"$ROUTER_PYTHON" -c 'import starlette, uvicorn, httpx'
```

Use your existing helper venv path if it differs. Retain its other dependencies.

## 3. Create the route configuration

```bash
install -d -m 700 "$HOME/.config/odysseus-operations"
ROUTER_CONFIG="$HOME/.config/odysseus-operations/inference-router.json"
test ! -e "$ROUTER_CONFIG" && install -m 600 scripts/inference-router.example.json "$ROUTER_CONFIG"
python3 -m json.tool "$ROUTER_CONFIG"
```

If the file exists, preserve it and edit it instead of overwriting it. Open it in your editor and verify:

- `management_config`: absolute path to the existing management JSON, normally `/home/seth/.config/odysseus-operations/management.json`.
- `port`: unused local port, initially 8094.
- `backends`: native loopback origins, without `/v1`. The example uses Strata 8081, Ollama 11434, DwarfStar 8000, Maya DS41F 8092 and Maya GLM 8093. Keep **every competing engine** listed, even when it has no public route. Remove an endpoint only if that engine is not installed/used on this machine.
- `routes`: public aliases, exact native model names and retention durations. Only installed models should be configured. Automatic model downloads are not performed.

The example initially includes:

| Router alias | Backend | Native model | Keep-alive |
| --- | --- | --- | --- |
| `local-ds41f` | `maya` | `deepseek-v4.1-flash` | 600 s |
| `local-glm53` | `maya_glm` | `glm-5.3-flash` | 600 s |
| `local-ds4` | `dwarfstar` | `deepseek-v4-flash` | 600 s |

For Strata, add a route whose `model` exactly matches the configured model reported by `curl -fsS http://127.0.0.1:8081/health`. For Ollama, use the exact installed name from `ollama list`, including its tag. For example, if installed:

```json
"local-qwen": {
  "backend": "ollama",
  "model": "qwen3:8b",
  "keep_alive_seconds": 600
}
```

The router does not select different Strata quantizations or different native models within a Maya/DwarfStar service; those remain explicit engine profiles. A wrong native model name fails readiness rather than silently serving another model. A missing/unknown competing service state blocks loading: configure its real allowlisted unit in Management, or remove its endpoint only after confirming that engine is not in use.

By default, Management discovers `inference-router.json` beside `management.json`. If using another location, set the host-owned `inference_router_config` value in the existing management JSON to its absolute path.

## 4. Start the host router

```bash
install -d "$HOME/.config/systemd/user"
install -m 644 scripts/odysseus-inference-router.service.example "$HOME/.config/systemd/user/odysseus-inference-router.service"
systemctl --user daemon-reload
systemctl --user enable --now odysseus-inference-router.service
systemctl --user status odysseus-inference-router.service --no-pager
curl -fsS http://127.0.0.1:8094/v1/models
curl -fsS http://127.0.0.1:8094/status
```

Inspect the unit before starting if your checkout/config/venv paths differ. The example binds to loopback only and runs one worker. Keep LiteLLM as the authenticated external entry point. A bridge-network LiteLLM container needs an appropriate local networking arrangement; the loopback instructions assume the existing host-network gateway.

Optional local authentication: configure `ODYSSEUS_INFERENCE_ROUTER_KEY` in a private systemd environment file and use the same bearer key in the LiteLLM deployments. The router does not forward that key to native engines. No key is needed in the default loopback-only setup.

Model listing and status do not load anything. The router adopts no unrecorded manual model at startup; the first routed request reconciles all configured competing engines. Idle deadlines from completed routed requests survive a normal router restart.

## 5. Move selected LiteLLM deployments through the router

In **Models & routing**, edit one local deployment first. Retain its existing public LiteLLM alias, and change its native model/base URL:

| Deployment | LiteLLM model | API base |
| --- | --- | --- |
| Maya DS41F | `openai/local-ds41f` | `http://127.0.0.1:8094/v1` |
| Maya GLM | `openai/local-glm53` | `http://127.0.0.1:8094/v1` |
| DwarfStar | `openai/local-ds4` | `http://127.0.0.1:8094/v1` |

Use the existing Review / Probe / Apply flow. A generation probe can now cold-load a model and evict a competing one. Retain the prior deployment details for rollback. Set client/gateway timeouts high enough for model loading plus generation; a 30-second timeout is insufficient for these cold loads. Start with explicit deployment selection before adding automatic LiteLLM fallbacks.

If a public LiteLLM alias still has multiple direct/routed deployments, traffic can bypass coordination through the direct deployment. Migrate or disable all such competing direct deployments before relying on automatic switching.

The proxy forwards native response bytes and backend HTTP errors without translating model capabilities. `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings` and `/v1/responses` require support in the selected backend. Native `/api/chat`, `/api/generate`, `/api/embed` and `/api/embeddings` are available for Ollama routes. A listed route is a configured option, not proof the model is currently loaded.

## 6. Adjust retention in Operations

Open **Management → On-demand inference**. Set each route's keep-alive in seconds and choose **Save keep-alive**. The router reads changes within a few seconds; no model/server restart is needed. Reload settings discards the local draft. The previous configuration is retained in `inference-router.json.before-keep-alive`.

Changes to aliases, native models, endpoints or service configuration still require editing their host-owned configuration and restarting the router while idle. If an idle timer expires, the next request loads the model again. Calling another model before expiry also switches immediately once preceding work finishes.

Ollama's native API supports explicit retention; the router pins its selected model during ownership and reapplies that pin after a routed response, then unloads it on its own timer. See the [Ollama residency documentation](https://github.com/ollama/ollama/blob/main/docs/faq.mdx). HTTP streaming uses explicit upstream cleanup as described in [HTTPX's async documentation](https://www.python-httpx.org/async/).

## 7. Verify on the device

1. Set one test route to 30 seconds in Management. Confirm every managed exclusive engine is stopped and shared models are unloaded before beginning the test.
2. Submit a short request through its existing LiteLLM public alias. Confirm it loads the intended backend and returns output.
3. Submit a second request within 30 seconds. Confirm the engine remains loaded, then confirm unloading/stopping only after 30 seconds have passed since completion of the second request.
4. Request a different local route. Confirm the old model is released before the new engine starts. Test a streamed response as well.
5. During a longer request, try a model-changing Management action. It should be disabled after refresh or return a conflict, without interrupting inference.
6. Inspect `curl -fsS http://127.0.0.1:8094/status`, `journalctl --user -u odysseus-inference-router.service -n 100 --no-pager`, and **Request journal**. Router entries use `routing.*`, include the backend/model and a request ID, and record releases/idle eviction. Router caller fields identify the connection to this hop; LiteLLM's existing journal retains authenticated user/key attribution and configured prompt/response capture.
7. Restore your preferred keep-alive. Run a native Operations benchmark separately; cold-load latency is additional to native decode TPS.

Automated tests cover lifecycle ordering, unknown-state refusals, idle reset, serialized waiters, disconnect draining, transport failure blocking, restart state and retention updates. Actual service startup, model fit and latency still require these host checks.

## Recovery and rollback

If a load fails, no inference is sent until readiness is verified. Inspect the selected engine and router journals. Already released competitors are not automatically restarted; the next explicit request chooses what should run.

If status is `blocked`, an upstream stream or previous router process ended without confirmed completion. Wait for/inspect the native engine, stop it through Management if needed, then stop the router, preserve its state file, and restart:

```bash
systemctl --user stop odysseus-inference-router.service
mv "$HOME/.config/odysseus-operations/inference-router-state.json" "$HOME/.config/odysseus-operations/inference-router-state.$(date +%Y%m%d-%H%M%S).json"
systemctl --user start odysseus-inference-router.service
```

Do this only after inspecting the engine; clearing the state alone does not prove native work has ended. Graceful router shutdown allows active work to finish, so a stop can wait for a long generation. Automatic idle-unload failures are logged and retried with a 30-second backoff; no competing model is loaded on an unconfirmed release.

To roll back, wait for active routed requests to finish, restore the previous direct LiteLLM model/base URL through Review / Apply, and stop/disable `odysseus-inference-router.service`. Native engine files, services and context/MTP settings remain intact.
