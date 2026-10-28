"""Synthetic person records with duplicate entities and realistic noise; ground-truth entity ids are known."""

from __future__ import annotations

import random

import pandas as pd

FIRST = "Ana Ben Chen Dara Eli Fay Gus Hana Ivan Jia Kai Lena Milo Nia Omar Pia Raj Sara Tom Uma Vera Will Xia Yan Zoe Alba Boris Clara Dmitri Elena Felix Greta Hugo Ines Jonas Katya Luca Maya Nils Olga Pavel Rosa Stefan Talia".split()
NICK = {
    "Alexander": "Alex",
    "Elizabeth": "Liz",
    "Robert": "Bob",
    "William": "Will",
    "Katherine": "Kate",
    "Michael": "Mike",
    "Jennifer": "Jen",
    "Richard": "Rick",
}
LAST = "Adams Brown Clark Diaz Evans Frost Garcia Hill Ito Jones Khan Lopez Meyer Nguyen Okoye Patel Quinn Rossi Singh Tanaka Usman Vega Wong Xu Young Zimmer Aalto Berg Costa Dvorak Eriksen Fischer Gruber Horvat Ivanov Jovanovic Kovacs Larsen Moreau Novak Olsen".split()
CITIES = [
    "Leeds",
    "Lyon",
    "Pune",
    "Perth",
    "Bergen",
    "Quito",
    "Osaka",
    "Denver",
    "Lisbon",
    "Tampere",
]


def typo(s: str, rng: random.Random) -> str:
    if len(s) < 4:
        return s
    i = rng.randrange(1, len(s) - 1)
    kind = rng.choice(["swap", "drop", "sub"])
    if kind == "swap":
        return s[:i] + s[i + 1] + s[i] + s[i + 2 :]
    if kind == "drop":
        return s[:i] + s[i + 1 :]
    return s[:i] + rng.choice("abcdefghijklmnopqrstuvwxyz") + s[i + 1 :]


def make(n_entities: int = 3000, dup_rate: float = 0.4, seed: int = 4) -> pd.DataFrame:
    """Each entity gets 1 record, plus 1-3 noisy duplicates with probability dup_rate. Distinct people can share a surname or city."""
    rng = random.Random(seed)
    rows = []
    uid = 0
    for e in range(n_entities):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        dob = f"{rng.randint(1940, 2004)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        pc = f"{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.randint(1, 99)} {rng.randint(1, 9)}{rng.choice('ABDEFGHJLNPQRSTUWXYZ')}{rng.choice('ABDEFGHJLNPQRSTUWXYZ')}"
        city = rng.choice(CITIES)
        copies = 1 + (rng.randint(1, 3) if rng.random() < dup_rate else 0)
        for c in range(copies):
            r = {
                "unique_id": uid,
                "entity": e,
                "first_name": first,
                "surname": last,
                "dob": dob,
                "postcode": pc,
                "city": city,
            }
            if c > 0:  # a duplicate record from another source: noise
                if rng.random() < 0.30:
                    r["first_name"] = typo(first, rng)
                if rng.random() < 0.30:
                    r["surname"] = typo(last, rng)
                if rng.random() < 0.10:
                    r["first_name"], r["surname"] = r["surname"], r["first_name"]
                if rng.random() < 0.20:
                    r["dob"] = None
                elif rng.random() < 0.08:
                    r["dob"] = f"{dob[:5]}{int(dob[5:7]) % 12 + 1:02d}{dob[7:]}"  # wrong month
                if rng.random() < 0.25:
                    r["postcode"] = None
                elif rng.random() < 0.15:
                    r["postcode"] = typo(pc.replace(" ", ""), rng)
                if rng.random() < 0.20:
                    r["city"] = None
            rows.append(r)
            uid += 1
    df = pd.DataFrame(rows).sample(frac=1, random_state=seed).reset_index(drop=True)
    df["unique_id"] = range(len(df))
    return df


def true_pairs(df: pd.DataFrame) -> set[tuple[int, int]]:
    out = set()
    for _, g in df.groupby("entity"):
        ids = sorted(g["unique_id"])
        out |= {(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]}
    return out
