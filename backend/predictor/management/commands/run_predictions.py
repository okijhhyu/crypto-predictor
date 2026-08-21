from django.core.management.base import BaseCommand
from predictor.services.scoring import generate_predictions


class Command(BaseCommand):
    help = "Считает Prediction по каждому активу из последних NormalizedMetric."

    def handle(self, *args, **options):
        preds = generate_predictions()
        for p in preds:
            self.stdout.write(f"{p.asset.symbol}: {p.direction} p={p.probability_up:.2f} conf={p.confidence:.2f} risk={p.risk_level}")
        self.stdout.write(self.style.SUCCESS(f"Создано прогнозов: {len(preds)}"))
