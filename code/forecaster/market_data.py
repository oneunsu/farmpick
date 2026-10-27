"""aT 경매 자료의 읽기 전용 검증. KAMIS 가격·농장 DB와 분리한다."""
from datetime import date
import math
import os
from urllib.parse import unquote

import requests

BASE_URL = 'https://apis.data.go.kr/B552845'
CODE_RESOURCES = {'goods', 'wholesaleMarkets', 'units', 'packagings', 'grades', 'sizes'}
API_KEY_ENV = {
    'codes': 'AT_CODE_API_KEY',
    'realtime': 'AT_REALTIME_API_KEY',
    'settlement': 'AT_SETTLEMENT_API_KEY',
}
API_NAMES = {'codes': '표준코드', 'realtime': '실시간 경매정보', 'settlement': '정산정보'}


class MarketDataError(ValueError):
    """인증정보나 요청 URL을 포함하지 않는 오류."""


def parse_response(payload):
    if not isinstance(payload, dict):
        raise MarketDataError('경매 API 응답 형식이 올바르지 않습니다.')
    root = payload.get('response', payload)
    if not isinstance(root, dict) or not isinstance(root.get('header'), dict):
        raise MarketDataError('경매 API 응답에 결과 코드가 없습니다.')
    code = str(root['header'].get('resultCode', ''))
    if code not in {'0', '00', '000', '0000'}:
        # 서버 메시지·requests 예외는 인증키를 포함할 수 있어 그대로 전달하지 않는다.
        safe_code = code if code.isascii() and code.isdigit() and len(code) <= 4 else '확인 불가'
        raise MarketDataError(f'API가 요청을 거부했습니다 (결과 코드 {safe_code}). 활용 승인·인증키·요청 조건을 확인하세요.')
    body = root.get('body')
    if not isinstance(body, dict):
        raise MarketDataError('경매 API 응답에 본문이 없습니다.')
    total = body.get('totalCount')
    try:
        count = int(total)
        if count < 0 or str(total).strip() != str(count):
            raise ValueError
    except (TypeError, ValueError):
        raise MarketDataError('경매 API의 전체 건수가 올바르지 않습니다.') from None
    items = body.get('items')
    if isinstance(items, dict):
        items = items.get('item')
    if items in (None, ''):
        items = []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
        raise MarketDataError('경매 API의 항목 형식이 올바르지 않습니다.')
    if len(items) > count or (count > 0 and not items):
        raise MarketDataError('경매 API의 건수와 항목이 일치하지 않습니다.')
    return items, count


class MarketDataClient:
    def __init__(self, *, code_key=None, realtime_key=None, settlement_key=None, session=None):
        self._keys = {
            # Encoding 키를 붙여 넣은 경우 한 번만 해제한다. '+'는 그대로 보존한다.
            'codes': unquote((code_key or '').strip()),
            'realtime': unquote((realtime_key or '').strip()),
            'settlement': unquote((settlement_key or '').strip()),
        }
        self.session = session or requests.Session()

    @classmethod
    def from_environment(cls, source):
        if source not in {'realtime', 'settlement'}:
            raise MarketDataError('지원하지 않는 가격 자료원입니다.')
        client = cls(
            code_key=os.environ.get(API_KEY_ENV['codes']),
            realtime_key=os.environ.get(API_KEY_ENV['realtime']),
            settlement_key=os.environ.get(API_KEY_ENV['settlement']),
        )
        # 필요한 두 키를 모두 확인한 뒤 API 호출을 시작한다.
        client._key_for('codes')
        client._key_for(source)
        return client

    def _key_for(self, service):
        key = self._keys[service]
        if not key:
            raise MarketDataError(f'환경변수 {API_KEY_ENV[service]}를 설정해 주세요. 해당 API의 활용 승인이 필요합니다.')
        return key

    def _get(self, path, params, *, service):
        key = self._key_for(service)
        # 실제 URL·쿼리 대신 고정 서비스명과 내부 리소스명만 표시한다.
        context = f'{API_NAMES[service]} API ({path.rsplit("/", 1)[-1]})'
        try:
            response = self.session.get(
                BASE_URL + path,
                params={'serviceKey': key, 'returnType': 'JSON', **params},
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.SSLError:
            raise MarketDataError(f'{context}: TLS 인증서 또는 보안 연결 오류입니다. 인증서·프록시 설정을 확인하세요.') from None
        except requests.exceptions.Timeout:
            raise MarketDataError(f'{context}: 연결 또는 응답 대기 시간이 초과됐습니다 (30초 제한).') from None
        except requests.exceptions.ProxyError:
            raise MarketDataError(f'{context}: 프록시 연결에 실패했습니다. 실행 환경의 프록시 설정을 확인하세요.') from None
        except requests.exceptions.ConnectionError:
            raise MarketDataError(f'{context}: 서버 연결에 실패했습니다. DNS·네트워크·방화벽을 확인하세요.') from None
        except requests.exceptions.HTTPError as exc:
            error_response = exc.response
            status = getattr(error_response, 'status_code', None)
            safe_status = str(status) if isinstance(status, int) and 100 <= status <= 599 else '확인 불가'
            if status in (401, 403):
                hint = f'{API_KEY_ENV[service]}의 해당 API 인증키·활용 승인·승인 반영 상태를 확인하세요.'
            elif status == 429:
                hint = '호출 한도 또는 요청 빈도 제한입니다. 잠시 뒤 다시 시도하세요.'
            elif isinstance(status, int) and status >= 500:
                hint = 'API 서버 또는 게이트웨이 오류입니다. 잠시 뒤 다시 시도하세요.'
            else:
                hint = 'API 요청 조건 또는 서비스 상태를 확인하세요.'
            raise MarketDataError(f'{context}: HTTP {safe_status}. {hint}') from None
        except requests.RequestException:
            raise MarketDataError(f'{context}: 요청 처리에 실패했습니다. 요청 URL·인증키는 기록하지 않습니다.') from None
        try:
            payload = response.json()
        except ValueError:
            raise MarketDataError(f'{context}: JSON을 반환하지 않았습니다. 활용 승인·API 서비스 상태를 확인하세요.') from None
        try:
            return parse_response(payload)
        except MarketDataError as exc:
            raise MarketDataError(f'{context}: {exc}') from None

    def codes(self, resource, *, max_pages=30):
        if resource not in CODE_RESOURCES:
            raise MarketDataError('지원하지 않는 표준코드 종류입니다.')
        rows = []
        expected = None
        for page in range(1, max_pages + 1):
            items, total = self._get('/katCode/' + resource, {'pageNo': page, 'numOfRows': 1000}, service='codes')
            if expected is not None and total != expected:
                raise MarketDataError('조회 중 코드표 건수가 바뀌었습니다. 다시 조회해 주세요.')
            expected = total
            rows.extend(items)
            if len(rows) == total:
                return rows
            if len(rows) > total:
                raise MarketDataError('코드표 페이지의 전체 건수가 맞지 않습니다.')
        raise MarketDataError('코드표가 페이지 제한을 초과했습니다. 불완전한 코드표는 사용하지 않습니다.')

    def sample_trades(self, day, *, category, item, page_size=100):
        if not isinstance(day, date) or not category or not item:
            raise MarketDataError('정산일과 공식 대분류·중분류 코드가 필요합니다.')
        if not 1 <= page_size <= 1000:
            raise MarketDataError('표본 크기는 1~1000이어야 합니다.')
        rows, total = self._get('/katRealTime2/trades2', {
            'pageNo': 1, 'numOfRows': page_size,
            'cond[trd_clcln_ymd::EQ]': day.isoformat(),
            'cond[gds_lclsf_cd::EQ]': category,
            'cond[gds_mclsf_cd::EQ]': item,
        }, service='realtime')
        self._check_filters(rows, day, category, item)
        return rows, total

    @staticmethod
    def _check_filters(rows, day, category, item, market=None):
        expected = {'trd_clcln_ymd': day.isoformat(), 'gds_lclsf_cd': category, 'gds_mclsf_cd': item}
        if market is not None:
            expected['whsl_mrkt_cd'] = market
        if any(any(str(row.get(k)) != str(v) for k, v in expected.items()) for row in rows):
            raise MarketDataError('응답의 일자·품목·시장과 요청 조건이 일치하지 않습니다.')

    def all_settlements(self, day, *, category, item, market, page_size=1000, max_pages=30):
        import hashlib
        import json
        if not isinstance(day, date) or not all((category, item, market)) or not 1 <= page_size <= 1000:
            raise MarketDataError('정산일·품목·시장·페이지 크기를 확인하세요.')
        rows, seen, expected = [], set(), None
        for page in range(1, max_pages + 1):
            items, total = self._get('/katSale/trades', {
                'pageNo': page, 'numOfRows': page_size,
                'cond[trd_clcln_ymd::EQ]': day.isoformat(),
                'cond[gds_lclsf_cd::EQ]': category, 'cond[gds_mclsf_cd::EQ]': item,
                'cond[whsl_mrkt_cd::EQ]': market,
            }, service='settlement')
            self._check_filters(items, day, category, item, market)
            if expected is not None and total != expected:
                raise MarketDataError('조회 중 정산 건수가 변경됐습니다. 다시 수집해주세요.')
            expected = total
            for row in items:
                fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if fingerprint in seen:
                    raise MarketDataError('정산 페이지에 중복 행이 있습니다. 전체 수집을 확정하지 않습니다.')
                seen.add(fingerprint)
            rows.extend(items)
            if len(rows) == total:
                return rows, total
            if not items or len(rows) > total:
                raise MarketDataError('정산 페이지 건수가 맞지 않습니다.')
        raise MarketDataError('정산 전체 페이지 제한을 초과했습니다. 시장·날짜 범위를 줄여주세요.')

    def sample_settlements(self, day, *, category, item, market, page_size=100):
        if not isinstance(day, date) or not all((category, item, market)):
            raise MarketDataError('정산일과 공식 대분류·중분류·시장 코드가 필요합니다.')
        if not 1 <= page_size <= 1000:
            raise MarketDataError('표본 크기는 1~1000이어야 합니다.')
        rows, total = self._get('/katSale/trades', {
            'pageNo': 1, 'numOfRows': page_size,
            'cond[trd_clcln_ymd::EQ]': day.isoformat(),
            'cond[gds_lclsf_cd::EQ]': category,
            'cond[gds_mclsf_cd::EQ]': item,
            'cond[whsl_mrkt_cd::EQ]': market,
        }, service='settlement')
        self._check_filters(rows, day, category, item, market)
        return rows, total


def assess_trade(row):
    """필드 존재·환산 가능성만 점검한다. 품질 미확인 자료는 순위에서 제외한다."""
    reasons = []
    required = ('auctn_seq', 'scsbd_dt', 'trd_clcln_ymd', 'whsl_mrkt_cd',
                'gds_lclsf_cd', 'gds_mclsf_cd', 'gds_sclsf_cd', 'pkg_cd')
    for field in required:
        if row.get(field) in (None, ''):
            reasons.append('missing:' + field)
    # 공식 경매 응답 명세에는 등급·크기가 없다. 추측해 상품·대과로 채우지 않는다.
    for field in ('grd_cd', 'sz_cd'):
        if row.get(field) in (None, ''):
            reasons.append('unverified:' + field)
    kg_price = None
    try:
        price, weight, qty = (float(row[k]) for k in ('scsbd_prc', 'unit_qty', 'qty'))
        if not all(math.isfinite(v) and v > 0 for v in (price, weight, qty)):
            raise ValueError
        # 단위코드 숫자를 임의 해석하지 않고 응답 명칭이 명확한 kg/g만 환산한다.
        factor = {'kg': 1.0, '㎏': 1.0, 'g': 0.001}.get(str(row.get('unit_nm', '')).strip().lower())
        if factor is None:
            reasons.append('unverified:weight_unit')
        else:
            kg_price = price / (weight * factor)
            if not math.isfinite(kg_price):
                raise ValueError
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        kg_price = None
        reasons.append('invalid:price_weight_or_quantity')
    return {
        'reference_krw_per_kg': kg_price,
        'eligible_for_quality_matched_comparison': not reasons,
        'exclusion_reasons': reasons,
        'conversion_status': 'provisional_from_documented_unit_price',
    }


def assess_settlement(row):
    """정산 원자료의 비교 키를 검사한다. 총물량 의미의 실측 확인 전 kg 가격은 확정하지 않는다."""
    required = ('trd_clcln_ymd', 'whsl_mrkt_cd', 'corp_cd', 'gds_lclsf_cd',
                'gds_mclsf_cd', 'gds_sclsf_cd', 'grd_cd', 'sz_cd',
                'unit_cd', 'pkg_cd', 'plor_cd', 'trd_se')
    reasons = ['missing:' + k for k in required if row.get(k) in (None, '')]
    for field in ('avgprc', 'totprc', 'unit_qty', 'unit_tot_qty'):
        try:
            value = float(row[field])
            if not math.isfinite(value) or value <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            reasons.append('invalid:' + field)
    reasons.append('unverified:total_quantity_semantics')
    return {
        'reference_krw_per_kg': None,
        'eligible_for_quality_matched_comparison': False,
        'exclusion_reasons': reasons,
        'conversion_status': 'pending_total_quantity_semantics_verification',
    }
