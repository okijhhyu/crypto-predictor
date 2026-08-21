from django.core.management.base import BaseCommand
from predictor.services.evaluation import evaluate_due_predictions, accuracy_summary


class Command(BaseCommand):
    help = "Проверяет прогнозы, у которых истёк горизонт (24ч), сравнивая с фактической ценой."

    def handle(self, *args, **options):
        evaluated = evaluate_due_predictions()
        self.stdout.write(self.style.SUCCESS(f"Проверено прогнозов: {len(evaluated)}"))
        self.stdout.write(str(accuracy_summary()))
