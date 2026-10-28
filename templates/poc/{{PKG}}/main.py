"""Entry point for POC {{ID}}: {{TITLE}}."""

from pathlib import Path

from pocs_common import write_json

POC_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    raise NotImplementedError("implement the POC, then write measured output to results/")


if __name__ == "__main__":
    main()
