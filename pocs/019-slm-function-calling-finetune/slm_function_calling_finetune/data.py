"""Synthetic tool-calling utterances with exact gold calls and token-level slot labels; train/test use DISJOINT templates."""

from __future__ import annotations

import random

CITIES = [
    "Berlin",
    "Paris",
    "Madrid",
    "Tokyo",
    "Lagos",
    "Lima",
    "Oslo",
    "Cairo",
    "Delhi",
    "Dublin",
    "Sydney",
    "Boston",
]
AIRPORTS = {"Berlin": "BER", "Paris": "CDG", "Madrid": "MAD", "Tokyo": "HND", "Lagos": "LOS", "Lima": "LIM", "Oslo": "OSL", "Cairo": "CAI", "Delhi": "DEL", "Dublin": "DUB", "Sydney": "SYD", "Boston": "BOS"}  # fmt: skip
CURRENCY_WORDS = {
    "euros": "EUR",
    "dollars": "USD",
    "pounds": "GBP",
    "yen": "JPY",
    "rupees": "INR",
    "francs": "CHF",
}
NAMES = ["anna", "bo", "carlos", "dina", "erik", "fatima", "gus", "hana"]
SUBJECTS = [
    "budget review",
    "launch plan",
    "weekly report",
    "invoice question",
    "holiday schedule",
    "contract draft",
]
TITLES = [
    "team lunch",
    "design review",
    "dentist",
    "board meeting",
    "yoga class",
    "project kickoff",
]
NUMBER_WORDS = {
    "five": 5,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty five": 45,
    "sixty": 60,
}
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
UNITS = ["celsius", "fahrenheit"]
DISTRACTORS = ["please", "quickly", "thanks", "for me", "right now", "if you can"]
NONE_UTTERANCES = ["tell me a joke", "what is the meaning of life", "thanks that was helpful", "who wrote hamlet", "can you explain recursion",
                   "I feel tired today", "what is two plus two", "good morning", "how do magnets work", "write a haiku about rain"]  # fmt: skip

# templates: {slot} markers; two disjoint families per tool. Slot names equal argument names.
TEMPLATES = {
    "get_weather": (
        ["what is the weather in {city}", "how is the weather in {city} today", "weather forecast for {city} in {unit}", "is it raining in {city}"],
        ["tell me about conditions over in {city}", "I am travelling to {city} what will it feel like in {unit}", "{city} temperature check"],
        ["give me the {unit} reading for {city}", "{city} weather please", "will it be sunny in {city}"],
    ),
    "set_timer": (
        ["set a timer for {minutes} minutes", "start a {minutes} minute timer", "timer {minutes} minutes called {label}"],
        ["wake me in {minutes} minutes", "count down {minutes} minutes for {label}", "ping me after {minutes} minutes"],
        ["I need {minutes} minutes on the clock for {label}", "set an alarm {minutes} minutes from now", "{minutes} minute countdown"],
    ),
    "send_email": (
        ["send an email to {to} with subject {subject}", "email {to} about {subject}", "write to {to} subject {subject}"],
        ["shoot a message over to {to} regarding {subject}", "drop {to} a line titled {subject}", "I need {to} to get a note on {subject}"],
        ["compose a note for {to} about {subject}", "mail {to} regarding {subject}", "send {to} an email titled {subject}"],
    ),
    "search_flights": (
        ["find flights from {origin} to {destination} on {date}", "flights {origin} to {destination} {date}", "search flights leaving {origin} for {destination} on {date}"],
        ["I want to fly out of {origin} heading to {destination} around {date}", "what is available {origin} {destination} {date}", "get me a seat from {origin} into {destination} on {date}"],
        ["any flights out of {origin} into {destination} on {date}", "book me {origin} to {destination} for {date}", "look up {date} flights from {origin} going to {destination}"],
    ),
    "convert_currency": (
        ["convert {amount} {from_currency} to {to_currency}", "how much is {amount} {from_currency} in {to_currency}", "change {amount} {from_currency} into {to_currency}"],
        ["what would {amount} {from_currency} fetch me in {to_currency}", "exchange {amount} {from_currency} for {to_currency}", "{amount} {from_currency} worth in {to_currency}"],
        ["how many {to_currency} is {amount} {from_currency}", "turn {amount} {from_currency} into {to_currency}", "give me {amount} {from_currency} in {to_currency}"],
    ),
    "create_event": (
        ["create an event {title} on {date}", "add {title} to my calendar on {date}", "schedule {title} for {date}"],
        ["put {title} down for {date}", "block out {date} for {title}", "remind me of {title} on {date}"],
        ["new calendar entry {title} {date}", "mark {date} as {title}", "set up {title} on {date}"],
    ),
}  # fmt: skip


def _value(slot: str, tool: str, rng: random.Random) -> tuple[str, object]:
    """(surface text, canonical argument value)."""
    if slot == "city":
        c = rng.choice(CITIES)
        return c, c
    if slot == "unit":
        u = rng.choice(UNITS)
        return u, u
    if slot == "minutes":
        if rng.random() < 0.5:
            w = rng.choice(list(NUMBER_WORDS))
            return w, NUMBER_WORDS[w]
        n = rng.choice([5, 10, 15, 20, 25, 30, 45, 90])
        return str(n), n
    if slot == "label":
        t = rng.choice(["pasta", "laundry", "tea"])
        return t, t
    if slot == "to":
        e = f"{rng.choice(NAMES)}@example.org"
        return e, e
    if slot == "subject":
        s = rng.choice(SUBJECTS)
        return s, s
    if slot in ("origin", "destination"):
        c = rng.choice(CITIES)
        return (AIRPORTS[c], AIRPORTS[c]) if rng.random() < 0.5 else (c, AIRPORTS[c])
    if slot == "date":
        d = rng.randrange(1, 29)
        m = rng.randrange(1, 13)
        if rng.random() < 0.5:
            return f"2025-{m:02d}-{d:02d}", f"2025-{m:02d}-{d:02d}"
        return f"{MONTHS[m - 1]} {d} 2025", f"2025-{m:02d}-{d:02d}"
    if slot == "amount":
        a = rng.choice([5, 12.5, 40, 100, 250, 7.25])
        return str(a), float(a)
    if slot in ("from_currency", "to_currency"):
        w = rng.choice(list(CURRENCY_WORDS))
        return (
            (CURRENCY_WORDS[w], CURRENCY_WORDS[w]) if rng.random() < 0.4 else (w, CURRENCY_WORDS[w])
        )
    if slot == "title":
        t = rng.choice(TITLES)
        return t, t
    raise KeyError(slot)


def make_example(tool: str | None, split: str, rng: random.Random) -> dict:
    if tool is None:
        text = rng.choice(NONE_UTTERANCES)
        return {"text": text, "tags": ["O"] * len(text.split()), "gold": None, "tool": "none"}
    fam = {"train": [0], "test": [1], "aug": [2], "train+aug": [0, 2]}[split]
    templ = rng.choice([t for i in fam for t in TEMPLATES[tool][i]])
    words, tags, args = [], [], {}
    for tok in templ.split():
        if tok.startswith("{") and tok.endswith("}"):
            slot = tok[1:-1]
            surface, canon = _value(slot, tool, rng)
            sw = surface.split()
            words += sw
            tags += [slot] * len(sw)
            args.setdefault(slot, canon)
        else:
            words.append(tok)
            tags.append("O")
    if rng.random() < 0.4:  # trailing distractor words
        d = rng.choice(DISTRACTORS).split()
        words += d
        tags += ["O"] * len(d)
    return {
        "text": " ".join(words),
        "tags": tags,
        "gold": {"name": tool, "arguments": args},
        "tool": tool,
    }


def generate(n: int, split: str, seed: int, none_share: float = 0.12) -> list[dict]:
    rng = random.Random(seed)
    tools = sorted(TEMPLATES)
    return [
        make_example(None if rng.random() < none_share else rng.choice(tools), split, rng)
        for _ in range(n)
    ]
