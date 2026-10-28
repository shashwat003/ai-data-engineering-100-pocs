"""Canonicalise text before matching, to defeat cheap obfuscation (look-alike letters, zero-width, spacing, leetspeak)."""

from __future__ import annotations

import re
import unicodedata

HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ѕ": "s", "ԁ": "d",  # Cyrillic
    "α": "a", "ο": "o", "ν": "v", "ι": "i", "ρ": "p",  # Greek
})  # fmt: skip
LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"}
)
ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
EMAIL_OR_URL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}|://|www\.")
SPACED = re.compile(r"\b(?:[A-Za-z]\s){3,}[A-Za-z]\b")  # "k i l l" -> "kill"


def normalise(text: str, leet: bool = True) -> str:
    t = ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))
    t = t.translate(HOMOGLYPHS)
    t = SPACED.sub(lambda m: m.group().replace(" ", ""), t)
    if leet:
        # decode leetspeak in mixed letter/symbol tokens only; leave emails and URLs intact so structured detectors still match
        def chunk(c: str) -> str:
            if EMAIL_OR_URL.search(c):
                return c
            return re.sub(
                r"[\w@$!]*[A-Za-z][\w@$!]*",
                lambda m: (
                    m.group().translate(LEET)
                    if re.search(r"[0-9@$!]", m.group())
                    and not re.fullmatch(r"[A-Za-z]+\d+", m.group())
                    else m.group()
                ),
                c,
            )

        t = re.sub(r"\S+", lambda m: chunk(m.group()), t)
    return re.sub(r"[ \t]+", " ", t)
