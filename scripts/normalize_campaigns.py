from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import constants
from app.io_utils import list_json_files, read_json, write_model
from app.models import Campaign
from app.normalizers import normalize_campaign


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normaliza campanhas salvas em disco.")
    parser.add_argument(
        "--input-dir",
        default=str(constants.CAMPAIGNS_DIR),
        help="Diretorio com arquivos JSON de campanha.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Diretorio de saida. Se omitido, sobrescreve no mesmo arquivo.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir) if args.output_dir else None
    files = list_json_files(input_dir)

    if not files:
        print("Nenhum arquivo de campanha encontrado.")
        return

    count = 0
    for path in files:
        campaign = Campaign.model_validate(read_json(path))
        normalized = normalize_campaign(campaign)
        target = (output_dir / path.name) if output_dir else path
        write_model(target, normalized)
        count += 1

    print(f"Campanhas normalizadas: {count}")


if __name__ == "__main__":
    main()

