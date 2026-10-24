"""Read active allocations and validate benchmark budgets without running inference."""

from services.operations.benchmark_tasks import MAX_OUTPUT_TOKENS, MAX_RUNS, plan
from services.operations.litellm_config import OperationsError
from services.operations.management import model_name, api

MAYA_RESERVE_TOKENS = 8


def limits(management, payload, workload=None):
    from services.operations import overview, dwarfstar, maya
    backend, model = payload.get("backend"), model_name(payload.get("model"))
    context, reported_model = None, None
    if backend in ("maya", "maya_glm"):
        probe = maya.glm_inventory if backend == "maya_glm" else maya.inventory
        live = probe(getattr(management, backend + "_url", ""))
    elif backend == "dwarfstar":
        live = dwarfstar.inventory(getattr(management, "dwarfstar_url", ""))
    elif backend == "strata":
        live = overview.strata(getattr(management, "strata_url", ""))
    elif backend == "ollama":
        live = overview.ollama(getattr(management, "ollama_url", ""))
        rows = [row for row in live.get("loaded", []) if row.get("name") in (model, model + ":latest")]
        if rows:
            context, reported_model = rows[0].get("context_length"), rows[0].get("name")
    else:
        raise OperationsError("Choose a configured benchmark backend.")
    if backend == "maya_glm" and live.get("loaded") and live.get("model") != "glm-5.3-flash":
        raise OperationsError("The GLM profile is serving another model. Check the isolated endpoint.", 409)
    if backend != "ollama" and live.get("status") == "ok" and live.get("loaded") is not False:
        if live.get("aliases") and model not in live["aliases"]:
            raise OperationsError("The selected model is not the model currently served by this backend.", 409)
        context, reported_model = live.get("max_context"), live.get("model")
    context = context if type(context) is int and context > 0 else None
    reserve = MAYA_RESERVE_TOKENS if backend in ("maya", "maya_glm") else 0
    maximum = min(MAX_OUTPUT_TOKENS, max(0, context - reserve - 1)) if context else MAX_OUTPUT_TOKENS
    result = {"backend": backend, "model": model, "reported_model": reported_model, "context_tokens": context,
              "reserve_tokens": reserve, "max_output_tokens": maximum, "prompt_tokens": None, "exact": False,
              "message": "Active runtime context is not reported. The application cap is not a verified model limit." if context is None else
                         "Active runtime allocation. This ceiling still needs room for the prompt; it is not a verified task budget."}
    if workload is None and payload.get("validate_tasks"):
        runs = payload.get("runs", 3)
        if type(runs) is not int or not 1 <= runs <= MAX_RUNS:
            raise OperationsError(f"Use 1–{MAX_RUNS} measured runs.")
        workload = plan(payload, runs)
    if workload is not None:
        result["task_seed"] = workload.get("task_seed")
    if backend in ("maya", "maya_glm") and context and workload is not None:
        tasks = list(workload["tasks"])
        if payload.get("warmup", True):
            tasks.append(workload["warmup_task"])
        counts = {}
        for item in tasks:
            fingerprint = item["prompt_sha256"]
            if fingerprint in counts:
                continue
            counted = api(getattr(management, backend + "_url"), "/api/tokens", {"model": model,
                          "messages": [{"role": "user", "content": item["prompt"]}], "reasoning_effort": "none"})
            if counted.get("max_context") != context:
                raise OperationsError("Maya context changed while checking task limits. Check again.", 409)
            tokens = counted.get("tokens")
            if type(tokens) is not int or tokens < 1 or counted.get("approximate") is not False:
                raise OperationsError("Maya did not return an exact prompt token count. Check its token-counting API.", 502)
            counts[fingerprint] = tokens
        prompt = max(counts.values())
        result.update(prompt_tokens=prompt, exact=True, max_output_tokens=min(MAX_OUTPUT_TOKENS, max(0, context - reserve - prompt)),
                      message="Exact budget for the largest prompt across measured tasks and warmup, including Maya's engine reserve.")
    return result


def validate_budget(budget, output_tokens):
    if output_tokens > budget["max_output_tokens"]:
        prompt = f"largest prompt {budget['prompt_tokens']:,} + " if budget.get("exact") else "prompt allowance + "
        raise OperationsError(f"{budget['backend']} context is {budget['context_tokens']:,} tokens: {prompt}"
                              f"output cap {output_tokens:,} + reserve {budget['reserve_tokens']:,} will not fit. "
                              f"Use at most {budget['max_output_tokens']:,} output tokens for this budget, "
                              "choose a smaller task, or change runtime context in Management.")
