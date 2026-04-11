from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.reporter import render_html


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Converte Markdown de relatorio em HTML.")
    parser.add_argument("--markdown", required=True)
    parser.add_argument("--html", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    md_path = Path(args.markdown)
    html_path = Path(args.html)

    markdown_text = md_path.read_text(encoding="utf-8")
    html = render_html(markdown_text)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    print(f"HTML exportado em: {html_path}")


if __name__ == "__main__":
    main()

