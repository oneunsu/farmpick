"""저장된 정산 스냅샷 정규화·가져오기. 외부 API·인증 파일은 사용하지 않는다."""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import MarketSnapshot, MarketSettlement

NORMALIZATION_VERSION = 'settlement-observed-v1'
SOURCE = 'https://www.data.go.kr/data/15141809/openapi.do'
CROPS = {'토마토': 'tomato', '딸기': 'strawberry', '파프리카': 'paprika', '오이': 'cucumber'}
DIMENSIONS = {
    'market': ('whsl_mrkt', 'wholesaleMarkets'),
    'variety': ('gds_sclsf', 'goods'),
    'grade': ('grd', 'grades'), 'size': ('sz', 'sizes'),
    'package': ('pkg', 'packagings'), 'unit': ('unit', 'units'),
    'corporation': ('corp', None), 'origin': ('plor', None),
}
MISSING = {'', '.', '-', 'None', 'null'}


class SettlementError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def text(value):
    return '' if value is None else str(value).strip()


def number(value, flags, field):
    try:
        result = Decimal(text(value))
        if not result.is_finite() or result <= 0 or result >= Decimal('1e18') or result.as_tuple().exponent < -6:
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        flags.append('invalid:' + field)
        return None


def normalize_row(row, probe, catalogs):
    expected = {'trd_clcln_ymd': probe['settlement_date'], 'whsl_mrkt_cd': probe['market_code'],
                'gds_lclsf_cd': probe['category_code'], 'gds_mclsf_cd': probe['item_code']}
    if any(text(row.get(key)) != text(value) for key, value in expected.items()):
        raise SettlementError('정산 행의 날짜·시장·품목과 조회 조건이 다릅니다.')
    flags = ['unverified:total_quantity_semantics']
    dimensions = {}
    for name, (prefix, catalog) in DIMENSIONS.items():
        code, label = text(row.get(prefix + '_cd')), text(row.get(prefix + '_nm'))
        verified = code not in MISSING and label not in MISSING
        if catalog:
            if name == 'variety':
                lookup = tuple(text(row.get(k)) for k in ('gds_lclsf_cd', 'gds_mclsf_cd', 'gds_sclsf_cd'))
            else:
                lookup = code
            verified = verified and catalogs.get(catalog, {}).get(lookup) == label
        if not verified:
            flags.append('unverified:' + name)
        dimensions[name] = {'code': code, 'name': label if label not in MISSING else '미확인', 'verified': verified}
    dimensions['trade'] = {'code': text(row.get('trd_se')), 'name': text(row.get('trd_se')) or '미확인', 'verified': False}
    if dimensions['unit']['name'].lower() not in {'kg', '㎏', 'g'}:
        flags.append('unverified:weight_unit')
    values = {target: number(row.get(source), flags, source) for source, target in (
        ('unit_qty', 'unit_weight'), ('avgprc', 'average_price'), ('totprc', 'total_amount'),
        ('unit_tot_qty', 'total_quantity'), ('lwprc', 'minimum_price'), ('hgprc', 'maximum_price'),
    )}
    if values['minimum_price'] is not None and values['maximum_price'] is not None:
        if values['minimum_price'] > values['maximum_price']:
            flags.append('invalid:price_range')
    product_key = '/'.join(text(row.get(k)) for k in ('gds_lclsf_cd', 'gds_mclsf_cd', 'gds_sclsf_cd'))
    # 동일 조건의 중복을 검사하고 서로 다른 가격을 임의로 합산하지 않는다.
    identity = {'date': probe['settlement_date'], 'product': product_key,
                'dimensions': {k: v['code'] for k, v in dimensions.items()}, 'weight': str(values['unit_weight'])}
    return {
        'fingerprint': digest(identity), 'settlement_date': date.fromisoformat(probe['settlement_date']),
        'market_code': text(row.get('whsl_mrkt_cd')), 'market_name': dimensions['market']['name'],
        'product_key': product_key, 'variety_name': dimensions['variety']['name'],
        'dimensions': dimensions, **values, 'quality_flags': sorted(set(flags)), 'raw': row,
    }


def import_settlement_snapshot(directory):
    directory = Path(directory)
    try:
        payload = json.loads((directory / 'probes.json').read_text(encoding='utf-8'))
        codes = json.loads((directory / 'codes.json').read_text(encoding='utf-8'))
        if payload.get('source_kind') != 'settlement' or payload.get('source') != SOURCE:
            raise SettlementError('정산정보 스냅샷만 가져올 수 있습니다. source_kind를 확인하세요.')
        crop_name = payload['crop_name']
        if crop_name not in CROPS:
            raise SettlementError('지원하지 않는 작물입니다.')
        collected_at = parse_datetime(payload['checked_at'])
        if collected_at is None or timezone.is_naive(collected_at) or collected_at > timezone.now():
            raise SettlementError('수집시각은 시간대가 있는 과거·현재 시각이어야 합니다.')
        catalogs = {}
        for name, (prefix, catalog) in DIMENSIONS.items():
            if catalog:
                mapping = {}
                for code_row in codes[catalog]:
                    key = (tuple(text(code_row[k]) for k in ('gds_lclsf_cd', 'gds_mclsf_cd', 'gds_sclsf_cd'))
                           if name == 'variety' else text(code_row[prefix + '_cd']))
                    value = text(code_row[prefix + '_nm'])
                    if key in mapping and mapping[key] != value:
                        raise SettlementError('공식 코드표의 동일 코드 명칭이 충돌합니다.')
                    mapping[key] = value
                catalogs[catalog] = mapping
        product_pairs = {(text(c['gds_lclsf_cd']), text(c['gds_mclsf_cd'])) for c in codes['goods']
                         if text(c.get('gds_mclsf_nm')) == crop_name}
        coverage, normalized, seen_probes, identities = [], [], set(), set()
        for probe in payload['probes']:
            day = date.fromisoformat(probe['settlement_date'])
            if day > timezone.localdate(collected_at):
                raise SettlementError('정산일이 수집일 이후입니다.')
            key = (probe['settlement_date'], text(probe['market_code']), text(probe['category_code']), text(probe['item_code']))
            if key in seen_probes or not key[1] or key[1] not in catalogs['wholesaleMarkets']:
                raise SettlementError('시장 조회 조건이 중복되거나 공식 코드표에 없습니다.')
            if (key[2], key[3]) not in product_pairs:
                raise SettlementError('조회 품목 코드가 스냅샷의 작물명과 맞지 않습니다.')
            seen_probes.add(key)
            rows, total = probe['rows'], probe['total_count']
            if not isinstance(total, int) or isinstance(total, bool) or total < len(rows) or probe['sample_count'] != len(rows):
                raise SettlementError('전체 건수·표본 건수·실제 행 수가 일치하지 않습니다.')
            if bool(probe['sample_only']) != (len(rows) < total) or (total > 0 and not rows):
                raise SettlementError('표본 상태와 전체 건수가 일치하지 않습니다.')
            coverage.append({'date': key[0], 'market': key[1], 'category': key[2], 'item': key[3],
                             'total': total, 'received': len(rows), 'partial': len(rows) < total})
            for row in rows:
                record = normalize_row(row, probe, catalogs)
                if record['fingerprint'] in identities:
                    raise SettlementError('같은 날짜·시장·품질·규격의 정산 행이 중복됩니다. 합산하지 않습니다.')
                identities.add(record['fingerprint'])
                normalized.append(record)
        if not coverage:
            raise SettlementError('조회 범위가 없는 스냅샷입니다.')
        checksum = digest({'payload': payload, 'codes': codes, 'normalization': NORMALIZATION_VERSION})
    except SettlementError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SettlementError('정산 스냅샷 형식 또는 코드표가 올바르지 않습니다.') from None
    # 모든 검증 후 일괄 저장한다. 동일 스냅샷은 다시 저장하지 않는다.
    with transaction.atomic():
        snapshot, created = MarketSnapshot.objects.get_or_create(checksum=checksum, defaults={
            'crop_type': CROPS[crop_name], 'crop_name': crop_name, 'collected_at': collected_at,
            'source': SOURCE, 'source_name': directory.name, 'coverage': coverage,
            'row_count': len(normalized), 'normalization_version': NORMALIZATION_VERSION,
        })
        if created:
            MarketSettlement.objects.bulk_create([MarketSettlement(snapshot=snapshot, **row) for row in normalized])
    return snapshot, created
