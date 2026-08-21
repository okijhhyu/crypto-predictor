from django.core.management.base import BaseCommand
from predictor.services.data_fetch import run_full_fetch


class Command(BaseCommand):
    help = "Собирает сырые данные из CoinGecko (цены) и RSS-лент (новости), считает нормализованные метрики."

    def handle(self, *args, **options):
        result = run_full_fetch()
        self.stdout.write(self.style.SUCCESS(f"Готово: {result}"))
