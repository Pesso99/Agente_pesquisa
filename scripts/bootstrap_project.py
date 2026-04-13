from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import constants
from app.io_utils import ensure_project_structure
from app.runtime_db import RuntimeDB


def main() -> None:
    ensure_project_structure()

    required_configs = [
        constants.CONFIG_DIR / "institutions.json",
        constants.CONFIG_DIR / "historical_seeds.json",
        constants.CONFIG_DIR / "report_settings.json",
        constants.CONFIG_DIR / "scoring_rules.json",
        constants.CONFIG_DIR / "email_settings.json",
        constants.CONFIG_DIR / "routing_rules.json",
    ]
    missing = [str(path) for path in required_configs if not path.exists()]

    if missing:
        print("Configuracoes ausentes:")
        for path in missing:
            print(f"- {path}")
        raise SystemExit(1)

    with RuntimeDB() as _db:
        pass

    print("Projeto inicializado com sucesso.")
    print(f"Workspace: {ROOT}")
    print("Estrutura de dados pronta em data/.")
    print(f"Runtime DB pronto: {constants.RUNTIME_DB_PATH}")


if __name__ == "__main__":
    main()
