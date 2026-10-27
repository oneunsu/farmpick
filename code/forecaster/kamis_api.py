import ssl
import time
import logging
import math
from datetime import date, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from urllib3.util.retry import Retry

from django.db import transaction
from django.utils import timezone

from .date_utils import as_local_date
from .models import PriceData
from .snapshots import write_json, quality_report
from .forecast_config import LOOKBACK_DAYS
from config.environment import required_api_setting
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 작물별 KAMIS 품목 코드 ──────────────────────────────────────────────────
CROP_ITEM_MAP = {
    'tomato':     {'p_itemcategorycode': '200', 'p_itemcode': '225', 'p_kindcode': '00'},
    'strawberry': {'p_itemcategorycode': '200', 'p_itemcode': '226', 'p_kindcode': '00'},
    'paprika':    {'p_itemcategorycode': '200', 'p_itemcode': '256', 'p_kindcode': '00'},
    # 공식 코드표의 가시계통. 존재하지 않는 오이 품종 00을 사용하지 않는다.
    'cucumber':   {'p_itemcategorycode': '200', 'p_itemcode': '223', 'p_kindcode': '01'},
}
CROP_NAMES = {'tomato': '토마토', 'strawberry': '딸기', 'paprika': '파프리카', 'cucumber': '오이'}

KAMIS_URL = 'https://www.kamis.or.kr/service/price/xml.do'


class KamisError(ValueError):
    """사용자에게 표시 가능한, 인증정보를 포함하지 않는 수집 오류."""


class KamisTransportError(KamisError):
    pass


class KamisAuthenticationError(KamisError):
    pass


class KamisResponseError(KamisError):
    pass


def get_price_basis(crop_type):
    if crop_type not in CROP_ITEM_MAP:
        raise KamisResponseError('지원하지 않는 작물입니다.')
    return {
        'source': KAMIS_URL, 'action': 'periodWholesaleProductList',
        'request': {
            **CROP_ITEM_MAP[crop_type],
            'p_productrankcode': '04', 'p_countrycode': '', 'p_convert_kg_yn': 'Y',
        },
        'price_type': 'wholesale', 'region': '전국 평균', 'unit': 'KRW/kg',
        'unit_basis': 'p_convert_kg_yn=Y',
        'grade': '상품',
        'code_reference': 'https://www.kamis.or.kr/customer/board/board_file.do?brdno=4&brdctsno=424245&brdctsfileno=15636',
        'code_reference_sha256': '98d00a808a758b89b95588d98e76ecae996150a6ed90d921ffa6b87c174e3b46',
    }


# ── SSL 우회 어댑터 ────────────────────────────────────────────────────────
class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers('ALL:@SECLEVEL=0')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=0)          # 재시도 비활성화
    adapter = LegacySSLAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
        ),
    })
    return session


# ── 가격 문자열 파싱 ───────────────────────────────────────────────────────
def _parse_price(price_str: str) -> float | None:
    """'2,040' → 2040.0 / '-' 또는 빈 값 → None"""
    if not price_str or price_str.strip() in ('-', ''):
        return None
    try:
        value = float(price_str.replace(',', '').strip())
        return value if math.isfinite(value) else None
    except (ValueError, AttributeError):
        return None


# ── KAMIS API 단일 기간 조회 ───────────────────────────────────────────────
def _fetch_period(session: requests.Session,
                  crop_type: str,
                  start_date: str,
                  end_date: str, *, raw_sink=None) -> list[dict]:
    """
    KAMIS periodWholesaleProductList 호출.
    반환: [{'date': date, 'price': float}, ...]
    """
    basis = get_price_basis(crop_type)

    params = {
        'action':               'periodWholesaleProductList',
        'p_cert_key':           required_api_setting('KAMIS_API_KEY'),
        'p_cert_id':            required_api_setting('KAMIS_ID'),
        'p_returntype':         'json',
        **basis['request'],  # 코드의 명칭은 실응답·공식 코드표 검증 전 단정하지 않는다.
        'p_startday':           start_date, # YYYY-MM-DD
        'p_endday':             end_date,
    }

    try:
        resp = session.get(KAMIS_URL, params=params, timeout=15, verify=False)
        resp.raise_for_status()
    except requests.RequestException:
        # requests 예외에는 인증키가 든 URL이 포함될 수 있다.
        raise KamisTransportError('KAMIS 통신에 실패했습니다. 기존 가격은 유지됩니다.') from None
    try:
        data = resp.json()
    except ValueError:
        raise KamisResponseError('KAMIS 응답을 JSON으로 해석할 수 없습니다.') from None

    if not isinstance(data, dict):
        raise KamisResponseError('KAMIS 응답 형식이 올바르지 않습니다.')
    body = data.get('data')
    if raw_sink is not None:
        # condition과 원본 URL에는 자격증명이 들어갈 수 있어 저장하지 않는다.
        raw_sink({'basis': basis, 'start_date': start_date, 'end_date': end_date, 'data': body})
    code = body.get('error_code') if isinstance(body, dict) else (
        body[0] if isinstance(body, list) and len(body) == 1 else None
    )
    code = str(code).zfill(3)
    if code == '001':
        return []
    if code == '900':
        raise KamisAuthenticationError('KAMIS 인증에 실패했습니다. 인증 설정을 확인해 주세요.')
    if code == '200':
        raise KamisResponseError('KAMIS 요청 조건이 잘못되었습니다.')
    if code != '000' or not isinstance(body, dict):
        raise KamisResponseError('KAMIS의 성공 상태를 확인할 수 없습니다.')
    items = body.get('item')
    if not isinstance(items, list):
        raise KamisResponseError('KAMIS 가격 목록 형식이 올바르지 않습니다.')

    # 실제 API의 평균 행은 명칭이 null이며 지역별 행에 명칭이 있다.
    # 응답 전체에서 단일 품목·품종인지 확인한 경우에만 평균 행에 연결한다.
    identity = {}
    for key in ('itemname', 'kindname'):
        names = {item[key] for item in items if isinstance(item, dict) and item.get(key)}
        if len(names) > 1:
            raise KamisResponseError('KAMIS 응답에 여러 품목·품종이 섞여 있습니다.')
        identity[key] = next(iter(names), None)
    if items and not all(identity.values()):
        raise KamisResponseError('KAMIS 응답에서 품목·품종 명칭을 확인할 수 없습니다.')
    if items and identity['itemname'] != CROP_NAMES[crop_type]:
        raise KamisResponseError('KAMIS 응답 품목이 요청한 작물과 다릅니다.')

    results = []
    period_start = date.fromisoformat(start_date)
    period_end = date.fromisoformat(end_date)
    for item in items:
        if not isinstance(item, dict):
            raise KamisResponseError('KAMIS 가격 행 형식이 올바르지 않습니다.')
        # countyname == '평균' 행만 사용
        if item.get('countyname') != '평균':
            continue

        yyyy     = item.get('yyyy', '')
        regday   = item.get('regday', '')   # 'MM/DD'
        price_str = item.get('price', '')

        if not yyyy or not regday:
            continue

        price = _parse_price(price_str)
        if price is None or price <= 0:
            continue

        try:
            month, day = regday.split('/')
            record_date = date(int(yyyy), int(month), int(day))
        except (ValueError, AttributeError):
            continue

        if not period_start <= record_date <= period_end:
            logger.warning('KAMIS 조회 범위 밖 날짜 제외: %s', record_date)
            continue
        # 품종/등급을 추측하지 않고 응답의 실제 명칭과 요청 코드를 함께 보존.
        response_basis = {
            key: item.get(key, '') for key in
            ('itemname', 'kindname', 'countyname', 'marketname', 'unit', 'rank')
        }
        response_basis.update(identity)
        results.append({
            'date': record_date, 'price': price,
            'provenance': {**basis, 'response': response_basis},
        })

    return results


# ── 메인 함수: 3년치 수집 후 DB 저장 ──────────────────────────────────────
def fetch_and_save_price(farm, *, start_date=None, end_date=None, snapshot_dir=None, save_to_db=True) -> dict:
    """
    farm: accounts.models.Farm 인스턴스
    save_to_db=False: 농장 ID 없이 원본·정제 스냅샷 파일만 저장한다.
    반환: {'created': int, 'updated': int, 'skipped': int}
    """
    crop_type = farm.crop_type

    if not save_to_db and snapshot_dir is None:
        raise ValueError('DB에 저장하지 않을 때는 스냅샷 출력 폴더가 필요합니다.')

    if crop_type not in CROP_ITEM_MAP:
        raise KamisResponseError('지원하지 않는 작물입니다.')

    today      = timezone.localdate()
    end_date = as_local_date(end_date) if end_date is not None else today - timedelta(days=1)
    start_date = as_local_date(start_date) if start_date is not None else end_date - timedelta(days=365*3)
    if start_date > end_date or end_date >= today:
        raise ValueError('수집 기간은 시작일~어제 사이여야 합니다.')
    if snapshot_dir is not None:
        snapshot_dir = Path(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        if (snapshot_dir / 'snapshot.json').exists():
            raise ValueError('완료된 스냅샷을 덮어쓸 수 없습니다. 새 출력 폴더를 선택해 주세요.')

    session = _make_session()

    # 6개월(180일)씩 분할 조회
    all_records: list[dict] = []

    cursor = start_date
    try:
        while cursor <= end_date:
            chunk_end = min(cursor + timedelta(days=180), end_date)
            try:
                args = (session, crop_type, cursor.isoformat(), chunk_end.isoformat())
                if snapshot_dir is None:
                    records = _fetch_period(*args)
                else:
                    raw_path = snapshot_dir / 'raw' / f'{cursor}_{chunk_end}.json'
                    records = _fetch_period(*args, raw_sink=lambda body: write_json(raw_path, body))
            except KamisError as exc:
                raise type(exc)(
                    f'{cursor}~{chunk_end} 수집 실패: {exc} 전체 수집을 저장하지 않았습니다.'
                ) from None
            all_records.extend(records)
            cursor = chunk_end + timedelta(days=1)
            time.sleep(3)   # 서버 부하 방지
    finally:
        session.close()

    # 같은 날짜 여러 평균 행을 순서에 따라 덮어쓰지 않는다.
    by_date = {}
    provenance_by_date = {}
    series_basis = None
    skipped = 0
    for rec in all_records:
        record_date = rec['date']
        if type(record_date) is not date or not start_date <= record_date <= end_date:
            raise ValueError('수집 가격의 날짜가 조회 범위에 맞지 않습니다.')
        if record_date in by_date:
            if by_date[record_date] != rec['price']:
                raise ValueError(f'{record_date}에 서로 다른 가격이 있습니다. 가격 기준을 확인해 주세요.')
            skipped += 1
        provenance = rec.get('provenance', {})
        if provenance:
            if series_basis is not None and provenance != series_basis:
                raise KamisResponseError('수집 자료에 서로 다른 품목·품종·등급·단위 기준이 섞여 있습니다.')
            series_basis = provenance
        by_date[record_date] = rec['price']
        provenance_by_date[record_date] = provenance

    snapshot_records = [
        {'date': d.isoformat(), 'price': by_date[d], 'provenance': provenance_by_date[d]}
        for d in sorted(by_date)
    ]
    if snapshot_dir is not None and snapshot_records:
        audit = quality_report(snapshot_records, start_date, end_date)

    # ── DB 저장 ────────────────────────────────────────────────────────────
    created = updated = 0

    if save_to_db:
        with transaction.atomic():
            for record_date, price in sorted(by_date.items()):
                obj, is_new = PriceData.objects.update_or_create(
                    farm=farm,
                    date=record_date,
                    defaults={'price': price, 'provenance': provenance_by_date[record_date]},
                )
                if is_new:
                    created += 1
                else:
                    updated += 1

    logger.info(
        f"[{farm.name}/{crop_type}] 가격 수집 완료 "
        f"created={created} updated={updated} skipped={skipped}"
    )
    if snapshot_dir is not None and snapshot_records:
        write_json(snapshot_dir / 'snapshot.json', {
            'schema_version': 1, 'crop': crop_type, 'collected_at': timezone.now().isoformat(),
            'requested_start': start_date.isoformat(), 'requested_end': end_date.isoformat(),
            'records': snapshot_records,
        })
        write_json(snapshot_dir / 'quality.json', audit)
    return {'created': created, 'updated': updated, 'skipped': skipped,
            'status': 'complete' if by_date else 'no_data'}


def get_recent_prices(
    farm, days: int = LOOKBACK_DAYS, *, as_of: date | None = None, require_provenance: bool = False,
) -> list[dict]:
    """
    기준일(as_of, 기본값 한국 현지 오늘)까지 최근 N일치 가격 반환.
    반환: [{'date': date, 'price': float}, ...]
    """
    as_of = as_of if as_of is not None else timezone.localdate()
    as_of = as_local_date(as_of)
    if days < 1:
        raise ValueError('조회 기간은 1일 이상이어야 합니다.')
    since = as_of - timedelta(days=days-1)
    qs = PriceData.objects.filter(
        farm=farm, date__gte=since, date__lte=as_of,
    ).order_by('date')
    records = list(qs)
    if require_provenance and records:
        expected = get_price_basis(farm.crop_type)
        signatures = []
        for obj in records:
            provenance = obj.provenance
            if not provenance or any(provenance.get(k) != v for k, v in expected.items()):
                raise KamisResponseError('과거 가격의 출처·가격 기준을 확인할 수 없습니다. 가격을 다시 수집해 주세요.')
            response = provenance.get('response', {})
            if not response.get('itemname') or not response.get('kindname'):
                raise KamisResponseError('과거 가격의 품목·품종을 확인할 수 없습니다. 가격을 다시 수집해 주세요.')
            signatures.append(response)
        if any(signature != signatures[0] for signature in signatures):
            raise KamisResponseError('과거 가격에 서로 다른 가격 기준이 섞여 있습니다. 가격을 다시 수집해 주세요.')
    return [{'date': obj.date, 'price': obj.price} for obj in records]
