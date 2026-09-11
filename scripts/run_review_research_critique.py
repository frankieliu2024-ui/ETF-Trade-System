"""Optional post-market review critique; never blocks formal review or trading."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from llm_research_adapter import build_review_input, critique_review
except ModuleNotFoundError:
    from scripts.llm_research_adapter import build_review_input, critique_review


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review_json", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    review = json.loads(args.review_json.read_text(encoding="utf-8"))
    result = critique_review(build_review_input(review))
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        sys.stdout.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
