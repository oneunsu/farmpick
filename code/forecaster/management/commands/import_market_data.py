from django.core.management.base import BaseCommand, CommandError

from forecaster.settlements import SettlementError, import_settlement_snapshot


class Command(BaseCommand):
    help = '저장된 정산 스냅샷을 검증·정규화하여 웹 조회용 DB에 가져옵니다. API 호출 없음.'

    def add_arguments(self, parser):
        parser.add_argument('directory', help='probes.json과 codes.json이 있는 디렉터리')

    def handle(self, *args, **options):
        try:
            snapshot, created = import_settlement_snapshot(options['directory'])
        except SettlementError as exc:
            raise CommandError(str(exc)) from None
        status = '가져오기 완료' if created else '이미 가져온 자료'
        self.stdout.write(self.style.SUCCESS(f'{snapshot.crop_name}: {status}, {snapshot.row_count}행 (스냅샷 {snapshot.pk})'))
