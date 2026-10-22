# Inference request journal installation

Branch: `feature/linux-ai-operations-dwarfstar`. Run the host commands as `seth` on the Ubuntu server. Source checkout: `/mnt/odysseus-fateblade`. This adds logging to the existing stack; no ROCm or system package changes are required.

## 1. Update Odysseus

Finish active host jobs before updating the helper files.

```bash
cd /mnt/odysseus-fateblade
git switch feature/linux-ai-operations-dwarfstar
git pull --ff-only
ODYSSEUS_IMAGE=odysseus-fateblade:local docker compose build odysseus
ODYSSEUS_IMAGE=odysseus-fateblade:local docker compose up -d --no-deps --pull never odysseus
```

The new **Operations → Request journal** reads the host journal through the existing SSH helper. Management actions and TPS benchmarks begin recording when the updated helper runs. Capturing other applications requires the gateway installation below.

## 2. Prepare the shared private directory

```bash
install -d -m 700 "$HOME/.local/share/odysseus-operations/audit"
```

The default host journal path is `~/.local/share/odysseus-operations/audit`. If your management JSON has `audit_directory`, use that path for the installer and gateway mount too. Keep this directory outside the web root.

Journal files are created with mode `0600`, directories with `0700`. Full conversation history, tool definitions, tool calls and generated text are captured for supported chat requests. Authorization headers and API keys from request metadata are excluded; secrets that are themselves part of a prompt or response remain in the captured content.

## 3. Build the gateway extension from the current image

This preserves the installed LiteLLM version. The callback interfaces were checked against LiteLLM 1.90.0. A different installed version needs its own smoke test before being considered supported.

```bash
cd /mnt/odysseus-fateblade
LITELLM_BASE_IMAGE=$(docker inspect --format '{{.Image}}' litellm)
docker tag "$LITELLM_BASE_IMAGE" litellm:odysseus-audit-base
docker build --build-arg LITELLM_IMAGE=litellm:odysseus-audit-base \
  -f docker/litellm-audit.Dockerfile -t litellm:odysseus-audit .
```

The extension adds two Python modules. It installs no dependencies and changes no models or provider settings.

## 4. Review and install on the existing standalone gateway

Use the existing helper virtual environment, which already contains `ruamel.yaml`. Confirm that `/home/seth/litellm/config.yaml` is the configuration actually mounted into the running gateway under `/app` (usually `/app/config.yaml`). LiteLLM resolves custom callbacks beside its YAML file. For other container paths, place both callback modules beside that YAML in your deployment instead of using this installer. Mount inspection below shows paths, not environment secrets:

```bash
docker inspect --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' litellm
PYTHON="$HOME/.local/share/odysseus-operations/venv/bin/python"
"$PYTHON" scripts/odysseus-install-gateway-audit.py \
  --config "$HOME/litellm/config.yaml" --image litellm:odysseus-audit
```

Without `--apply`, the installer only validates and reports the plan. It requires a standalone, persistent, host-network container and the default local Docker socket. It refuses containers owned by Compose/Swarm and users whose permissions need separate handling. For an owned container, make the equivalent image, environment, mount and callback additions in its existing deployment definition instead; do not create a second gateway on port 4000.

When ready for a brief gateway interruption:

```bash
"$PYTHON" scripts/odysseus-install-gateway-audit.py \
  --config "$HOME/litellm/config.yaml" --image litellm:odysseus-audit --apply
curl --fail http://127.0.0.1:4000/health/liveliness
docker logs --tail 50 litellm
```

The installer preserves Docker configuration, including environment, command, entrypoint, host networking, mounts and restart policy. It adds the log-directory bind mount and enables content capture. It appends `odysseus_audit.audit_logger` to `litellm_settings.callbacks`, preserving existing callbacks. YAML is written in place to preserve single-file bind mounts. The old stopped container is retained as `litellm-before-audit-TIMESTAMP`. Private YAML and Docker-inspection backups are written beside the audit directory. These backups contain credentials and are retained until you choose to remove them.

Docker creation/start errors trigger an attempt to restore the YAML and original container. A successful Docker start is not a health or inference guarantee: run step 6 immediately. A host crash during setup can require recovery from the backups.

For a deployment managed elsewhere, the equivalent settings are:

```yaml
# Add to the existing gateway service definition; retain all current settings.
image: litellm:odysseus-audit
volumes:
  - /home/seth/.local/share/odysseus-operations/audit:/var/log/odysseus-audit
environment:
  ODYSSEUS_AUDIT_DIRECTORY: /var/log/odysseus-audit
  ODYSSEUS_AUDIT_CAPTURE_CONTENT: 'true'
  ODYSSEUS_AUDIT_OWNER_UID: '1000' # use: id -u
  ODYSSEUS_AUDIT_OWNER_GID: '1000' # use: id -g
```

Append this callback to the existing gateway YAML rather than replacing other callbacks:

```yaml
litellm_settings:
  callbacks:
    - odysseus_audit.audit_logger
```

## 5. Enable the independent residency observer

The observer uses read-only backend endpoints every five seconds, even when the dashboard is closed. It never sends generation or model-load requests. Adjust the example paths/endpoints if your installation differs; omit `--dwarfstar-url` if disabled.

```bash
install -d -m 700 "$HOME/.config/systemd/user"
install -m 600 scripts/odysseus-audit-monitor.service.example \
  "$HOME/.config/systemd/user/odysseus-audit-monitor.service"
systemctl --user daemon-reload
systemctl --user enable --now odysseus-audit-monitor.service
systemctl --user status odysseus-audit-monitor.service
```

The user service follows the same login/linger behavior as the existing DwarfStar user service. Its first observation records a baseline. Later changes record previous/current model lists and context when the API provides it. Unavailable backends are recorded separately and do not imply an unload. The poll interval may miss changes that occur entirely between samples.

## 6. Verify content and caller attribution

Send a short request through the existing client using its LiteLLM base URL on port 4000. Configure a distinct `X-Client-Name` header, for example `odysseus`, `desktop-coding-agent` or `benchmark-client`. Use separate named gateway keys/user IDs where available. A shared key and shared user agent cannot reliably identify which application sent a request.

In **Operations → Request journal**, refresh and filter by model or client name. A normal successful inference produces `request.received`, `request.dispatched` and `request.succeeded` entries with a common request ID. Inspect the received entry to see the full prompt; inspect the successful entry to see the response, including tool calls where returned. Streaming responses are captured from LiteLLM's completed response. Token counts remain unavailable when the provider does not supply them. Failures record exception class/status without raw exception text, which may contain credentials. Retries can produce multiple dispatch/attempt-failure entries.

Then run a small dashboard TPS benchmark. Confirm its prompt and response appear with source `benchmark`, and that the job has the initiating dashboard user. Perform a model load/unload through Management and confirm `operation.*`, `job.*` and a later residency entry. Keep the dashboard closed briefly, reopen it and verify that observer entries persisted.

Caller fields have different evidential value:

- `actor`/`key_alias`: gateway-authenticated identity when available; Operations uses the dashboard session identity.
- `client_label`, `claimed_user`, `client_request_id`, user agent: client-supplied labels, not authenticated identities.
- Gateway `client_ip`: LiteLLM-reported requester address, which may incorporate forwarded headers. Operations records the direct peer address, which may be a reverse proxy.
- `residency.changed`: observed state change, with no invented caller or causal request link.

Direct calls to native Ollama/Strata/DwarfStar endpoints bypass gateway content logging. The dashboard benchmark has its own logging, but arbitrary external native calls do not. Use the audited gateway for clients you want to trace. Catalog/health requests and authentication failures before the callback are outside this inference journal. Cancelled or interrupted streams can have a received/dispatch entry without a complete response.

## 7. Retention and troubleshooting

The store rotates at 20 MiB with four backups, approximately 100 MiB total. Retention is based on size, not days. Captured content is limited to 2 MiB of serialized JSON per event; oversized content is explicitly marked `truncated`. JSON escaping can make a truncated entry larger than 2 MiB on disk. The journal view loads the latest 200 summaries; Inspect retrieves content for one entry. Older entries remain on disk until rotation removes them. Browser responses use `Cache-Control: no-store`. Access follows the existing Operations admin policy, including explicitly disabled authentication and internal-tool access. Enable dashboard authentication to restrict journal access to signed-in administrators.

If entries are missing, check the observer service, gateway startup logs, callback configuration, matching host/container log paths and directory ownership. A write failure logs a warning and allows inference to continue. Logging is diagnostic and not a tamper-proof compliance record. Validation: 72 Operations tests pass, JavaScript syntax checks pass, and desktop/mobile browser checks confirmed inspection and safe text rendering. The implementation has local fixture tests; real Docker recreation, callback invocation in the installed gateway and GPU inference require the on-device checks above.

To disable future content capture, set gateway `ODYSSEUS_AUDIT_CAPTURE_CONTENT=false` in its deployment and recreate it. Set `audit_capture_content` to `false` in the management JSON to disable benchmark content capture. Existing journal files remain until rotated or explicitly removed.

## 8. Roll back the gateway

Use the exact retained container name and YAML backup path printed by the installer. Stop the new gateway, restore the saved YAML **in place**, then rename the current container aside and restore the old container name. For example, substitute the actual timestamp:

```bash
docker stop litellm
cat "$HOME/.local/share/odysseus-operations/litellm-before-audit-TIMESTAMP.config.yaml" \
  > "$HOME/litellm/config.yaml"
docker rename litellm litellm-audit-disabled
docker rename litellm-before-audit-TIMESTAMP litellm
docker start litellm
curl --fail http://127.0.0.1:4000/health/liveliness
```

Do not overwrite newer gateway edits with an old backup without reconciling them. The observer can be stopped independently with `systemctl --user disable --now odysseus-audit-monitor.service`. Journal files remain available for inspection.

## API references

- [LiteLLM proxy callback hooks](https://docs.litellm.ai/docs/proxy/call_hooks)
- [Pinned LiteLLM 1.90.0 callback interface](https://github.com/BerriAI/litellm/blob/v1.90.0/litellm/integrations/custom_logger.py)
