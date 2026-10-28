#!/usr/bin/env python3
"""Copy each POC's generated results files into its README between marker pairs.

Marker `RESULTS` embeds results/results.md, `RESULTS_NEURAL` embeds results/results_neural.md,
`RESULTS_OLLAMA` embeds results/results_ollama.md. If a results file does not exist the section
says so explicitly, so a README can never show numbers that were not produced by a run.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r"<!-- (RESULTS[A-Z_]*):START -->\n.*?<!-- \1:END -->", re.DOTALL)


def block(poc_dir: Path, marker: str) -> str:
    name = marker.lower() + ".md"
    path = poc_dir / "results" / name
    if not path.exists():
        body = (
            f"_No `results/{name}` exists in this repository: this configuration has **not been "
            "run here**, so there are no numbers to show. See the run instructions above._"
        )
    else:
        title, _, rest = path.read_text().partition("\n")
        body = f"**{title.lstrip('# ')}**\n{rest.strip()}".replace("\n## ", "\n#### ")
    return f"<!-- {marker}:START -->\n\n{body}\n\n<!-- {marker}:END -->"


for readme in sorted(ROOT.glob("pocs/*/README.md")):
    text = readme.read_text()
    new = PATTERN.sub(lambda m, d=readme.parent: block(d, m.group(1)), text)
    if new != text:
        readme.write_text(new)
        print(f"embedded results into {readme.relative_to(ROOT)}")
