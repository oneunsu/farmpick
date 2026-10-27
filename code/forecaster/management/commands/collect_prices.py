from datetime import date

from django.core.management.base import BaseCommand, CommandError
from accounts.models import Farm
from forecaster.kamis_api import fetch_and_save_price


class Command(BaseCommand):
    help = 'KAMIS 기간별 가격 수집과 인증정보 없는 원본·정제 스냅샷 저장'

    def add_arguments(self, parser):
        parser.add_argument('--farm-id', type=int, required=True)
        parser.add_argument('--start', type=date.fromisoformat, required=True)
        parser.add_argument('--end', type=date.fromisoformat, required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        try:
            farm = Farm.objects.get(pk=options['farm_id'])
            result = fetch_and_save_price(
                farm, start_date=options['start'], end_date=options['end'], snapshot_dir=options['output'],
            )
        except (Farm.DoesNotExist, ValueError) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(str(result))
