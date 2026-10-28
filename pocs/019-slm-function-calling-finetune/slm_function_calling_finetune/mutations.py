"""Mutation corpus: guaranteed-invalid variants of valid calls and malformed raw outputs. A schema gate must reject all of them."""

from __future__ import annotations

import copy
import json
import random

from .tools import TOOLS


def mutate(call: dict, rng: random.Random) -> tuple[str, dict] | None:
    c = copy.deepcopy(call)
    args = c["arguments"]
    kind = rng.choice(
        ["drop_required", "extra_key", "wrong_type", "bad_tool", "out_of_range", "bad_format"]
    )
    required = TOOLS[call["name"]]["required"]
    if kind == "drop_required":
        del args[rng.choice(required)]
    elif kind == "extra_key":
        args["debug"] = True
    elif kind == "wrong_type":
        k = rng.choice(list(args))
        args[k] = [args[k]]
    elif kind == "bad_tool":
        c["name"] = "delete_everything"
    elif kind == "out_of_range":
        if call["name"] == "set_timer":
            args["minutes"] = rng.choice([0, -5, 100000])
        elif call["name"] == "convert_currency":
            args["amount"] = rng.choice([0, -3])
        else:
            return None
    elif kind == "bad_format":
        if "date" in args:
            args["date"] = rng.choice(["2025-13-01", "2025-02-30", "tomorrow", "03/04/2025"])
        elif "to" in args:
            args["to"] = "not-an-email"
        elif "origin" in args:
            args["origin"] = "Berlin"
        elif "from_currency" in args:
            args["from_currency"] = "euros"
        else:
            return None
    return kind, c


MALFORMED = [
    '{"name": "get_weather", "arguments": {"city": "Berlin"',  # truncated
    "{'name': 'get_weather', 'arguments': {'city': 'Berlin'}}",  # single quotes
    '{"name": "get_weather", "arguments": {"city": "Berlin",}}',  # trailing comma
    'Sure! {"name": "get_weather", "arguments": {"city": "Berlin"}}',  # chatty prefix
    '```json\n{"name": "get_weather", "arguments": {"city": "Berlin"}}\n```',  # markdown fence
    "[]", "null", '"get_weather"', "", "   ",
    json.dumps({"name": "get_weather"}),  # no arguments
    json.dumps({"name": "get_weather", "arguments": "city=Berlin"}),  # arguments as string
    "x" * 5000,
]  # fmt: skip
