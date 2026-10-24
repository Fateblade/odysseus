"""Versioned, reproducible benchmark workloads with distinct source material."""

import hashlib
import random
import secrets

from services.operations.litellm_config import OperationsError

VERSION = 2
MAX_OUTPUT_TOKENS = 262144
MAX_RUNS = 20
MAX_CUSTOM_CHARACTERS = 32768
SIZES = {"small": 4000, "medium": 16000, "large": 64000}
FAMILIES = {
    "research": ("Research synthesis", "Compare supplied fictional study notes, reconcile conflicting results, cite note IDs and identify evidence gaps. No web access is needed."),
    "summarization": ("Summarization", "Summarize supplied meeting records into decisions, risks, owners and unresolved questions."),
    "code_review": ("Code review", "Review supplied Python code for correctness, security, concurrency and test gaps, citing function names."),
    "code_generation": ("Code generation", "Implement a Python event-processing library and tests from supplied requirements and example events."),
    "text_generation": ("Text generation", "Write a coherent scene using supplied fictional setting notes, characters and constraints."),
}


def catalog():
    return {"version": VERSION, "families": [{"id": key, "label": value[0], "description": value[1]} for key, value in FAMILIES.items()],
            "sizes": [{"id": key, "characters": value} for key, value in SIZES.items()],
            "limits": {"runs": MAX_RUNS, "output_tokens": MAX_OUTPUT_TOKENS, "custom_characters": MAX_CUSTOM_CHARACTERS,
                       "timeout_min": 60, "timeout_max": 604800, "task_seed_max": 2147483647}}


def task(family, size, variant, seed=42):
    rng = random.Random(seed * 1000 + variant)
    places = ("Aster", "Boreal", "Cedar", "Delta", "Ember", "Fjord", "Granite", "Harbor")
    topic = rng.choice(("water monitoring", "night transit", "sensor repair", "energy storage", "habitat restoration"))
    prefix = f"Case {variant + 1}, set {seed}: {rng.choice(places)} / {topic}. All source material is synthetic.\n"
    pieces = [prefix]
    length, index = len(prefix), 0
    while length < SIZES[size]:
        index += 1
        identifier = f"case_{variant + 1}_item_{index}"
        count, days, effect = rng.randint(20, 2000), rng.randint(7, 365), rng.randint(-15, 35)
        location = rng.choice(places)
        if family == "research":
            block = (f"[Study {identifier}] {location} observed {count} participants over {days} days in {topic}. "
                     f"The reported change was {effect} percent against a baseline of {rng.randint(40, 90)} units. "
                     f"Design: {rng.choice(('randomized pilot', 'retrospective survey', 'uncontrolled field trial'))}. "
                     f"Missing measurements: {rng.randint(0, 30)} percent. Limitation: {rng.choice(('seasonal differences', 'self-selection', 'short follow-up', 'instrument drift'))}. "
                     "The authors distinguish correlation from causation and request a replication.\n")
        elif family == "summarization":
            block = (f"Record {identifier}, day {days}, team {location}: {count} sensor events were reviewed for {topic}. "
                     f"{rng.choice(('Mira', 'Jon', 'Lea', 'Kai'))} owns the next validation, due in {rng.randint(2, 20)} days. "
                     f"Decision: {rng.choice(('extend the pilot', 'hold rollout pending validation', 'repair the older devices', 'request independent review'))}. "
                     f"Risk: {rng.choice(('supply delays', 'inconsistent timestamps', 'missing consent records', 'budget approval'))}. "
                     f"Open question: whether the {effect} percent change is reproducible. Status supersedes only earlier records for item {index}.\n")
        elif family == "code_review":
            block = (f"# Module {identifier}: {location}, retention {days} days\n"
                     f"def ingest_{identifier}(events, cache={{}}):\n"
                     f"    for event in events:\n        key = event.get('id')\n        cache[key] = int(event.get('amount', 0)) / {count}\n"
                     f"    return {{'values': cache, 'accepted': len(events)}}\n\n"
                     f"def lookup_{identifier}(connection, user):\n"
                     f"    return connection.execute(\"SELECT * FROM events WHERE owner='\" + user + \"'\").fetchall()\n\n")
        elif family == "code_generation":
            block = (f"Rule {identifier}: source {location} uses a {days}-day retention window and at most {count} events per batch. "
                     f"Example event: {{'id': '{identifier}', 'source': '{location}', 'amount': {effect}, 'day': {days}}}. "
                     "Reject missing IDs and non-integer amounts; duplicates keep the newest day, with the last event winning ties. "
                     "Preserve deterministic output ordering, never mutate caller objects, and record rejected events with reasons. "
                     "Every source has independent totals and retention; do not silently coerce invalid types.\n")
        else:
            block = (f"Setting note {identifier}: at {location}, {count} lanterns mark a route maintained for {days} days. "
                     f"The {topic} project changed supplies by {effect} percent. "
                     f"{rng.choice(('Mira', 'Jon', 'Lea', 'Kai'))} wants {rng.choice(('to repair the signal', 'to leave before the storm', 'to recover an old map', 'to reveal a disputed discovery'))}. "
                     f"Obstacle: {rng.choice(('a locked archive', 'a flooded crossing', 'a broken radio', 'a promise to a rival'))}. "
                     "Treat this as world background; choose a small coherent subset rather than listing every detail.\n")
        pieces.append(block)
        length += len(block)
    instructions = {
        "research": "Write a research synthesis using only the notes above. Contrast stronger and weaker evidence, cite study IDs, identify conflicts and propose follow-up work. Do not invent external citations.",
        "summarization": "Summarize the records above. Separate decisions, owners and deadlines, unresolved questions and risks. Preserve quantities and uncertainty, and cite record IDs where useful.",
        "code_review": "Review the Python modules above. Prioritize concrete defects and risks, reference affected function names, explain failure scenarios, and propose fixes and regression tests. Do not execute the code.",
        "code_generation": "Implement a reusable Python library for the rules above. Define a clear API, validation, deduplication, retention and per-source totals. Include unit tests and explain ambiguous requirements before choosing consistent semantics.",
        "text_generation": "Write an original scene using the setting notes above. Keep a consistent viewpoint, develop a concrete conflict and resolution, and use selected details naturally. Do not summarize the notes.",
    }
    prompt = "".join(pieces) + "\nTASK\n" + instructions[family]
    return {"id": f"v{VERSION}/{family}/{size}/{seed}/{variant}", "family": family, "size": size,
            "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "characters": len(prompt)}


def plan(payload, runs):
    family = payload.get("task_family", "custom")
    size, mode = payload.get("task_size", "small"), payload.get("task_mode", "repeat")
    if (not all(isinstance(value, str) for value in (family, size, mode))
            or family not in ("custom", "mixed", *FAMILIES) or size not in SIZES or mode not in ("repeat", "different")):
        raise OperationsError("Choose a supported task, size and repetition mode.")
    seed = payload.get("task_seed")
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 2147483647):
        raise OperationsError("Task set seed must be 0–2147483647, or omitted for a fresh set.")
    if family != "custom" and seed is None:
        seed = secrets.randbelow(2147483648)
    if family == "custom":
        prompt = payload.get("prompt", "")
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= MAX_CUSTOM_CHARACTERS:
            raise OperationsError(f"Supply a custom prompt of 1–{MAX_CUSTOM_CHARACTERS} characters.")
        if mode != "repeat":
            raise OperationsError("Different tasks per run require a predefined workload.")
        item = {"id": "custom", "family": "custom", "size": None, "prompt": prompt,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "characters": len(prompt)}
        tasks, warmup = [item] * runs, item
    else:
        if family == "mixed":
            mode = "different"
        families = list(FAMILIES)
        def build(index):
            return task(families[index % len(families)] if family == "mixed" else family, size, index if mode == "different" else 0, seed)
        tasks = [build(index) for index in range(runs)]
        warmup = build(runs) if mode == "different" else tasks[0]
    return {"benchmark_version": VERSION, "task_family": family, "task_size": None if family == "custom" else size,
            "task_mode": mode, "task_seed": seed if family != "custom" else None, "tasks": tasks, "warmup_task": warmup, "prompt": tasks[0]["prompt"]}
