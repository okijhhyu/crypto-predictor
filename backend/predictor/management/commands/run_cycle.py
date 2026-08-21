from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = "Полный цикл: fetch_data -> evaluate_predictions -> run_predictions. Удобно ставить в cron раз в час."

    def handle(self, *args, **options):
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("=== ЭТАП 1: Получение данных ==="))
        
        call_command("fetch_data")
        
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("=== ЭТАП 2: Проверка прогнозов ==="))
        
        call_command("evaluate_predictions")
        
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("=== ЭТАП 3: Создание прогнозов ==="))
        
        call_command("run_predictions")

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS("=== ПОЛНЫЙ ЦИКЛ ЗАВЕРШЁН ===")
        )
