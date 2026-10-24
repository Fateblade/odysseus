#!/usr/bin/env python3
"""Run identical text and fixed-tool fixtures against one already-running backend."""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.operations.benchmark import run
from services.operations.jobs import write_json
from services.operations.litellm_host import NoRedirect
from services.operations.management import Management


FIXTURES = [
    "Reply with exactly the integer result of 137 * 29. No explanation.",
    "Write a C# method that returns the second distinct largest integer from IEnumerable<int>. "
    "Handle null and fewer than two distinct values explicitly. Do not sort the input. Explain complexity.",
    "A WPF view model updates ObservableCollection from a worker thread. Explain the failure and give "
    "a minimal dispatcher-based fix that preserves cancellation. State assumptions.",
]
TOOLS = [{"type": "function", "function": {"name": "add", "description": "Add two integers.",
          "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                         "required": ["a", "b"], "additionalProperties": False}}}]


def tool_check(base, backend, model):
    transcript = []

    def chat(messages, choice):
        payload = {"model": model, "messages": messages, "tools": TOOLS, "tool_choice": choice,
                   "max_tokens": 256, "temperature": 0, "seed": 42,
                   **({"think": False} if backend == "dwarfstar" else {"reasoning_effort": "none"})}
        request = Request(base.rstrip("/") + "/v1/chat/completions", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with build_opener(NoRedirect()).open(request, timeout=300) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Tool response exceeds 2 MiB")
        result = json.loads(raw)
        transcript.append({"request": json.loads(json.dumps(payload)), "response": result})
        return result["choices"][0]["message"]

    messages = [{"role": "user", "content": "Use add to calculate 2 + 3, then state the result."}]
    first = chat(messages, {"type": "function", "function": {"name": "add"}})
    calls = first.get("tool_calls", [])
    valid = (len(calls) == 1 and calls[0].get("id") and calls[0].get("function", {}).get("name") == "add")
    if valid:
        args = json.loads(calls[0]["function"]["arguments"])
        valid = args == {"a": 2, "b": 3} and all(type(value) is int for value in args.values())
    if not valid:
        return {"arguments_correct": False, "transcript": transcript}
    messages.extend([first, {"role": "tool", "tool_call_id": calls[0]["id"], "content": "5"}])
    final = chat(messages, "none")
    return {"arguments_correct": True, "final_content": final.get("content"),
            "final_has_tool_calls": bool(final.get("tool_calls")), "transcript": transcript,
            "note": "Inspect final answer for 5 and tool-result use; no arbitrary model-selected tool is executed."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("maya", "dwarfstar"), required=True)
    parser.add_argument("--base-url", required=True, help="Native loopback endpoint without /v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--label", required=True, help="Checkpoint, quantization, engine SHA, context and drafting mode")
    parser.add_argument("--management-config", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; choose a new result path")
    os.umask(0o077)
    management = Management(args.management_config, "", "",
                            dwarfstar_url=args.base_url if args.backend == "dwarfstar" else "",
                            maya_url=args.base_url if args.backend == "maya" else "")
    result = {"backend": args.backend, "model": args.model, "label": args.label, "fixtures": []}
    try:
        for prompt in FIXTURES:
            result["fixtures"].append({"prompt": prompt, "result": run(management, {
                "backend": args.backend, "model": args.model, "prompt": prompt,
                "runs": 3, "max_tokens": 256, "warmup": True})})
        result["tools"] = tool_check(args.base_url, args.backend, args.model)
    except Exception as error:
        result["error_type"] = type(error).__name__
        raise
    finally:
        write_json(args.output, result)


if __name__ == "__main__":
    main()
