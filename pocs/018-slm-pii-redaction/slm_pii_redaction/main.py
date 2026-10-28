"""POC 018: hybrid PII redaction (validated patterns + small trained tagger), evaluated on unseen names."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .data import dataset
from .evaluate import TYPES, run
from .redact import redact
from .tagger import Tagger

POC_DIR = Path(__file__).resolve().parent.parent

HARD = [
    "reach me at john [at] example [dot] com tonight",
    "call five five five 123 4567 after noon",
    "mail JOHN.SMITH@EXAMPLE.COM now",
    "my friend maria gonzalez lives at 12 baker street",
    "Contact: Ana Ng (ana.ng@example.com), 555-123-4567",
]


def main() -> None:
    res = run()
    tagger = Tagger(identity=False).fit(dataset(1500, 1, False))
    demo = [redact(t, tagger)[0] for t, _ in dataset(3, 5, True)]
    hard = [(h, redact(h, tagger)[0]) for h in HARD]
    res["examples"], res["hard_cases"] = demo, hard
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [
        (
            lab,
            *[
                res[k]["per_type"][lab][m]
                for k in ("unseen_names", "unseen_names_and_templates", "patterns_only")
                for m in ("precision", "recall")
            ],
            res["unseen_names"]["per_type"][lab]["support"],
        )
        for lab in TYPES
    ]
    md = (
        "# Results: hybrid PII redaction on synthetic text (names in the test set were never seen in training)\n\n"
        "Hybrid = validated pattern detectors (Luhn, IBAN mod-97, SSN rules, email, phone) from POC 034 plus a small logistic-regression tagger "
        "for person names and street addresses. 'Patterns only' is the same pipeline without the tagger. No Presidio, spaCy or neural NER was used.\n\n"
        + markdown_table(
            [
                "type",
                "unseen names: P",
                "R",
                "unseen names + new templates: P",
                "R",
                "patterns-only P",
                "R",
                "support",
            ],
            rows,
        )
        + f"\n\nCharacter-level PII leak rate: unseen names {res['unseen_names']['char_leak_rate']}, unseen names AND unseen sentence templates {res['unseen_names_and_templates']['char_leak_rate']}, patterns only {res['patterns_only']['char_leak_rate']}. "
        "The near-perfect 'unseen names' column reflects test sentences that reuse the training templates; the held-out-template column is the fairer estimate. "
        f"A variant that also uses token identity and suffix features (identity features) scores PERSON recall {res['identity_features']['unseen_names']['per_type']['PERSON']['recall']} on unseen names but "
        f"{res['identity_features']['seen_names']['per_type']['PERSON']['recall']} on names seen in training: it memorises names. "
        f"Over-redaction of non-PII characters: {res['unseen_names']['over_redaction_rate']}. Seen-name leak rate: {res['seen_names']['char_leak_rate']}. "
        f"Throughput on one CPU core: about {res['texts_per_second']} texts per second.\n\n"
        "Hard cases (obfuscation, lowercase names) the system is expected to miss:\n\n"
        + markdown_table(["input", "redacted"], [(a, b) for a, b in hard])
        + "\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
