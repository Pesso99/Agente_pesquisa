from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.feedback import learn_from_feedback
from app.runtime_db import RuntimeDB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Confirma ou nega campanhas do historico para alimentar o feedback loop.",
    )
    parser.add_argument("--campaign-id", default=None, help="Revisar campanha especifica.")
    parser.add_argument("--status", default=None, help="Filtrar por status (ex: review, validated).")
    parser.add_argument("--institution", default=None, help="Filtrar por institution_id.")
    parser.add_argument("--limit", type=int, default=20, help="Maximo de campanhas para revisar.")
    parser.add_argument("--batch", action="store_true", help="Processar varias de uma vez.")
    parser.add_argument("--relearn", action="store_true", help="Apenas recalcular padroes sem revisar.")
    parser.add_argument("--stats", action="store_true", help="Exibir estatisticas de feedback.")
    parser.add_argument("--reviewed-by", default="human_reviewer", help="Identificacao do revisor.")
    return parser.parse_args()


def _display_campaign(camp: dict) -> None:
    print("\n" + "=" * 70)
    print(f"  ID:           {camp['campaign_id']}")
    print(f"  Instituicao:  {camp['institution_id']}")
    print(f"  Nome:         {camp['campaign_name']}")
    print(f"  Tipo:         {camp.get('campaign_type', '-')}")
    print(f"  URL:          {camp['source_url']}")
    print(f"  Fonte:        {camp.get('source_type', '-')}")
    print(f"  Status:       {camp['status']}")
    print(f"  Confianca:    {camp.get('confidence_final', '-')}")
    print(f"  Beneficio:    {camp.get('benefit', '-')}")
    print(f"  Publico:      {camp.get('audience', '-')}")
    print(f"  Inicio:       {camp.get('start_date', '-')}")
    print(f"  Fim:          {camp.get('end_date', '-')}")
    print(f"  Notas:        {camp.get('validation_notes', '-')}")
    print("=" * 70)


def _ask_verdict() -> str | None:
    while True:
        choice = input("\n  [c]onfirmar  [d]enegar  [u]ncertain  [s]kip  [q]uit > ").strip().lower()
        if choice in ("c", "confirm", "confirmar"):
            return "confirmed"
        if choice in ("d", "deny", "denegar", "negar"):
            return "denied"
        if choice in ("u", "uncertain", "incerto"):
            return "uncertain"
        if choice in ("s", "skip", "pular"):
            return None
        if choice in ("q", "quit", "sair"):
            raise KeyboardInterrupt
        print("  Opcao invalida. Tente novamente.")


def _review_single(db: RuntimeDB, campaign_id: str, reviewed_by: str) -> bool:
    camp = None
    for r in db.conn.execute(
        "SELECT * FROM campaign_history WHERE campaign_id = ? ORDER BY created_at DESC LIMIT 1",
        (campaign_id,),
    ).fetchall():
        camp = dict(r)
        break
    if not camp:
        print(f"Campanha {campaign_id} nao encontrada no historico.")
        return False

    existing = db.get_feedback_for_campaign(campaign_id)
    if existing:
        print(f"  [Ja tem feedback: {existing[0]['verdict']}]")

    _display_campaign(camp)
    verdict = _ask_verdict()
    if verdict is None:
        return False

    reason = input("  Motivo (opcional, Enter para pular): ").strip() or None

    pipeline_status = camp["status"]
    if verdict == "confirmed":
        was_correct = pipeline_status in ("validated", "validated_with_reservations")
    elif verdict == "denied":
        was_correct = pipeline_status == "discarded"
    else:
        was_correct = None

    db.add_feedback(
        campaign_id,
        verdict=verdict,
        reason=reason,
        reviewed_by=reviewed_by,
        was_correct=was_correct,
    )
    print(f"  -> Feedback registrado: {verdict}")
    return True


def _review_batch(db: RuntimeDB, campaigns: list[dict], reviewed_by: str) -> int:
    reviewed = 0
    for camp in campaigns:
        existing = db.get_feedback_for_campaign(camp["campaign_id"])
        if existing:
            continue
        try:
            _display_campaign(camp)
            verdict = _ask_verdict()
        except KeyboardInterrupt:
            print("\n\nRevisao interrompida.")
            break

        if verdict is None:
            continue

        reason = input("  Motivo (opcional, Enter para pular): ").strip() or None

        pipeline_status = camp["status"]
        if verdict == "confirmed":
            was_correct = pipeline_status in ("validated", "validated_with_reservations")
        elif verdict == "denied":
            was_correct = pipeline_status == "discarded"
        else:
            was_correct = None

        db.add_feedback(
            camp["campaign_id"],
            verdict=verdict,
            reason=reason,
            reviewed_by=reviewed_by,
            was_correct=was_correct,
        )
        reviewed += 1
        print(f"  -> Feedback registrado: {verdict}")

    return reviewed


def _show_stats(db: RuntimeDB) -> None:
    stats = db.get_feedback_stats()
    print("\n--- Estatisticas de Feedback ---")
    print(f"  Total de feedbacks:   {stats['total']}")
    print(f"  Confirmadas:          {stats['confirmed']}")
    print(f"  Negadas:              {stats['denied']}")
    print(f"  Incertas:             {stats['uncertain']}")
    print(f"  Pipeline acertou:     {stats['correct']}")
    print(f"  Pipeline errou:       {stats['incorrect']}")
    accuracy = stats.get("accuracy")
    if accuracy is not None:
        print(f"  Taxa de acerto:       {accuracy:.1%}")
    else:
        print("  Taxa de acerto:       (sem dados suficientes)")
    print()

    patterns = db.get_learned_patterns()
    if patterns:
        print("--- Padroes Aprendidos ---")
        current_type = None
        for p in patterns:
            if p["pattern_type"] != current_type:
                current_type = p["pattern_type"]
                print(f"\n  [{current_type}]")
            print(f"    {p['pattern_key']}: {p['pattern_value']:.3f} (n={p['sample_count']})")
    else:
        print("Nenhum padrao aprendido ainda. Execute com --relearn apos adicionar feedback.")
    print()


def main() -> None:
    args = parse_args()

    with RuntimeDB() as db:
        if args.stats:
            _show_stats(db)
            return

        if args.relearn:
            counts = learn_from_feedback(db)
            print("Padroes recalculados:")
            for k, v in counts.items():
                print(f"  {k}: {v} padroes")
            return

        if args.campaign_id:
            _review_single(db, args.campaign_id, args.reviewed_by)
            print("\nRecalculando padroes...")
            learn_from_feedback(db)
            return

        campaigns = db.list_campaigns_without_feedback(
            status=args.status,
            institution_id=args.institution,
            limit=args.limit,
        )

        if not campaigns:
            print("Nenhuma campanha pendente de feedback encontrada.")
            if not args.status:
                print("Dica: as campanhas precisam estar no historico. Execute um ciclo primeiro.")
            return

        print(f"\n{len(campaigns)} campanha(s) pendente(s) de feedback.\n")

        if args.batch:
            try:
                reviewed = _review_batch(db, campaigns, args.reviewed_by)
            except KeyboardInterrupt:
                reviewed = 0
                print("\nRevisao interrompida.")
        else:
            reviewed = 0
            for camp in campaigns:
                try:
                    if _review_single(db, camp["campaign_id"], args.reviewed_by):
                        reviewed += 1
                except KeyboardInterrupt:
                    print("\nRevisao interrompida.")
                    break

        if reviewed > 0:
            print(f"\n{reviewed} campanha(s) revisada(s). Recalculando padroes...")
            counts = learn_from_feedback(db)
            for k, v in counts.items():
                print(f"  {k}: {v} padroes")
        else:
            print("\nNenhuma campanha revisada.")


if __name__ == "__main__":
    main()
