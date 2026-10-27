"""기본은 파일로 검증 자료를 보존한다. --import-to-web 지정 시 정산자료 DB에도 가져온다."""
from collections import Counter
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.cache import cache
from django.utils import timezone

from forecaster.market_data import MarketDataClient, MarketDataError, assess_trade, assess_settlement
from forecaster.snapshots import write_json
from forecaster.settlements import SettlementError, import_settlement_snapshot


class Command(BaseCommand):
    help = 'aT 코드·날짜별 표본을 확인합니다. 정산자료는 --import-to-web으로 웹 DB에 연결할 수 있습니다.'

    def add_arguments(self, parser):
        parser.add_argument('--crop-name', default='파프리카')
        parser.add_argument('--dates', nargs='+', required=True, help='정산일 YYYY-MM-DD. 여러 날짜 지정 가능.')
        parser.add_argument('--output', required=True)
        parser.add_argument('--page-size', type=int, default=100)
        parser.add_argument('--source', choices=('realtime', 'settlement'), default='settlement')
        parser.add_argument('--market-names', nargs='+', help='공식 시장명. 생략하면 코드표의 모든 시장 확인.')
        parser.add_argument('--import-to-web', action='store_true', help='정산 수집 성공 후 웹 조회용 DB에도 가져옵니다.')
        parser.add_argument('--reuse-code-cache', action='store_true', help='15분간 공식 코드표를 재사용합니다. 웹 순차 수집용입니다.')
        parser.add_argument('--all-pages', action='store_true', help='정산 전체 페이지를 검증해 수집합니다. 시장·날짜·품목당 최대 30페이지.')

    def handle(self, *args, **options):
        output = Path(options['output'])
        if output.exists():
            raise CommandError('기존 결과 폴더는 덮어쓰지 않습니다. 새 경로를 지정하세요.')
        try:
            dates = list(dict.fromkeys(date.fromisoformat(x) for x in options['dates']))
            if any(d > timezone.localdate() for d in dates):
                raise ValueError
        except ValueError:
            raise CommandError('조회일은 오늘 이하의 YYYY-MM-DD 날짜여야 합니다.') from None
        if not 1 <= options['page_size'] <= 1000:
            raise CommandError('표본 크기는 1~1000이어야 합니다.')
        if options['import_to_web'] and options['source'] != 'settlement':
            raise CommandError('--import-to-web은 정산정보에만 사용할 수 있습니다.')
        if options['all_pages'] and options['source'] != 'settlement':
            raise CommandError('--all-pages는 정산정보에만 사용할 수 있습니다.')
        try:
            client = MarketDataClient.from_environment(options['source'])
            cached = cache.get('at-official-code-tables-v1') if options['reuse_code_cache'] else None
            if cached is None:
                code_tables = {resource: client.codes(resource) for resource in (
                    'goods', 'wholesaleMarkets', 'units', 'packagings', 'grades', 'sizes',
                )}
                if not code_tables['goods'] or not code_tables['wholesaleMarkets']:
                    raise MarketDataError('공식 품목·시장 코드표가 비어 있습니다. 잠시 후 다시 조회해주세요.')
                code_checked_at = timezone.now().isoformat()
                if options['reuse_code_cache']:
                    cache.set('at-official-code-tables-v1', (code_tables, code_checked_at), timeout=900)
            else:
                code_tables, code_checked_at = cached
            name = options['crop_name']
            # 농장 작물은 중분류로 선택한다. 소분류명만 같은 가공식품을 섞지 않는다.
            candidates = [r for r in code_tables['goods'] if r.get('gds_mclsf_nm') == name]
            if not candidates:
                raise MarketDataError('공식 코드표에 일치하는 작물명이 없습니다. 코드를 추측하지 않습니다.')
            pairs = sorted({(r['gds_lclsf_cd'], r['gds_mclsf_cd']) for r in candidates})
            markets = code_tables['wholesaleMarkets']
            if not markets:
                raise MarketDataError('공식 시장 코드표가 비어 있습니다.')
            if options['market_names']:
                selected = set(options['market_names'])
                markets = [r for r in markets if r.get('whsl_mrkt_nm') in selected]
                if {r.get('whsl_mrkt_nm') for r in markets} != selected:
                    raise MarketDataError('공식 코드표에 없는 시장명이 있습니다.')
            source = options['source']
            # 정산 API는 시장 코드가 필수다. 실시간 API도 명시한 시장은 별도로 조회한다.
            if source == 'realtime' and options['market_names']:
                raise MarketDataError('--market-names는 정산정보 조회에서만 지원합니다.')
            scope = markets if source == 'settlement' else [None]
            probes = []
            for day in dates:
                for category, item in pairs:
                    for market in scope:
                        if source == 'settlement':
                            fetch = client.all_settlements if options['all_pages'] else client.sample_settlements
                            rows, total = fetch(
                                day, category=category, item=item, market=market['whsl_mrkt_cd'],
                                page_size=options['page_size'],
                            )
                            if options['all_pages'] and len(rows) != total:
                                raise MarketDataError('정산 전체 수집 건수가 맞지 않습니다. 다시 수집해주세요.')
                            assessments = [assess_settlement(row) for row in rows]
                        else:
                            rows, total = client.sample_trades(
                                day, category=category, item=item, page_size=options['page_size'],
                            )
                            assessments = [assess_trade(row) for row in rows]
                        probes.append({
                            'settlement_date': day.isoformat(), 'category_code': category,
                            'market_code': market['whsl_mrkt_cd'] if market else None,
                            'item_code': item, 'total_count': total, 'sample_count': len(rows),
                            'sample_only': len(rows) < total,
                            'observed_market_codes_in_sample': sorted({r.get('whsl_mrkt_cd', '') for r in rows}),
                            'exclusion_counts': dict(Counter(reason for a in assessments for reason in a['exclusion_reasons'])),
                            'rows': rows, 'assessments': assessments,
                        })
        except MarketDataError as exc:
            raise CommandError(str(exc)) from None
        # 모든 호출 성공 후 새 폴더에 출력한다. 인증정보·요청 URL은 저장하지 않는다.
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / 'codes.json', code_tables)
        write_json(output / 'probes.json', {
            'checked_at': timezone.now().isoformat(), 'crop_name': name,
            'source': 'https://www.data.go.kr/data/' + ('15141809' if source == 'settlement' else '15141808') + '/openapi.do',
            'source_kind': source,
            'code_source': 'https://www.data.go.kr/data/15141818/openapi.do',
            'code_tables_checked_at': code_checked_at,
            'matching_candidates': candidates, 'probes': probes,
            'limitations': [
                ('전체 페이지를 확인했으나 조회 날짜·시장의 범위 밖 거래와 시계열 연속성을 증명하지 않는다.'
                 if options['all_pages'] else '첫 페이지 표본이며 전체 시장의 포괄성·가격 순위·시계열 연속성을 증명하지 않는다.'),
                '정산일은 낙찰일시와 다를 수 있다. 단가 환산은 실거래 정산과 대조 전 잠정값이다.',
                '등급·크기가 없으면 같은 품질의 판로 비교에 사용하지 않는다.',
                '이 자료는 KAMIS 중도매인 판매가격과 다른 경매가격이다.',
            ],
        })
        suffix = '수집 파일 저장 완료' if options['import_to_web'] else 'DB 변경 없음'
        self.stdout.write(self.style.SUCCESS(f'코드표와 경매 표본 확인 완료: {output} ({suffix})'))
        if options['import_to_web']:
            try:
                snapshot, created = import_settlement_snapshot(output)
            except SettlementError as exc:
                raise CommandError(f'수집 파일은 보존했습니다. 웹 가져오기 실패: {exc}') from None
            self.stdout.write(self.style.SUCCESS(f'웹 자료 {"추가" if created else "확인"} 완료: {snapshot.row_count}행'))
