"""공유할 수 없는 서명 토큰과 불변 발행 이력. GET에서는 저장하지 않는다."""
from datetime import date, timedelta
import hashlib
import json
import math

from django.core import signing
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import ForecastRun
from .selected_forecast import MODEL_ID, prepare_live_input

SALT = 'farmcal-forecast-issue-v1'


def make_payload(farm, target, outlook, *, chart=None):
    result = dict(outlook)
    result.setdefault('as_of', timezone.localdate().isoformat())
    result.setdefault('target', target.isoformat())
    input_rows = []
    if result.get('available'):
        if not math.isfinite(float(result['base'])) or float(result['base']) <= 0:
            raise ValueError('전망 가격이 올바르지 않습니다.')
        history, _, _, evidence = prepare_live_input(farm, as_of=date.fromisoformat(result['as_of']))
        if evidence['input_sha256'] != result.get('input_sha256'):
            raise ValueError('전망 확인 후 입력 자료가 바뀌었습니다. 새로고침해 다시 확인해주세요.')
        if chart and chart.get('available') and chart.get('input_sha256') != evidence['input_sha256']:
            raise ValueError('그래프와 가격의 입력이 다릅니다. 새로고침해주세요.')
        for index, value in enumerate(history.observed):
            if math.isfinite(value):
                input_rows.append({'date': (history.first+timedelta(days=index)).isoformat(), 'price': float(value)})
    return {'version': 1, 'farm': farm.pk, 'user': farm.user_id, 'crop': farm.crop_type,
            'issued_at': timezone.now().isoformat(), 'outlook': result,
            'input_records': input_rows, 'chart': chart or {
                'available': False, 'reason': '이 발행에는 대상일 가격을 보존했습니다. 당시 전체 곡선은 저장되지 않았습니다.'}}


def make_token(payload):
    return signing.dumps(payload, salt=SALT, compress=True)


def read_token(token, farm, user):
    payload = signing.loads(token, salt=SALT, max_age=86400)
    if (payload.get('version') != 1 or payload.get('farm') != farm.pk or payload.get('user') != user.pk
            or payload.get('crop') != farm.crop_type):
        raise signing.BadSignature('현재 농장의 전망이 아닙니다.')
    return payload


def save_payload(farm, payload):
    if payload.get('farm') != farm.pk or payload.get('user') != farm.user_id:
        raise ValueError('다른 농장의 전망을 저장할 수 없습니다.')
    result = payload['outlook']
    issued = parse_datetime(payload['issued_at'])
    as_of, target = date.fromisoformat(result['as_of']), date.fromisoformat(result['target'])
    if issued is None or timezone.is_naive(issued) or issued > timezone.now() or as_of > timezone.localdate():
        raise ValueError('발행일이 올바르지 않습니다.')
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    checksum = hashlib.sha256(canonical.encode()).hexdigest()
    run, _ = ForecastRun.objects.get_or_create(checksum=checksum, defaults={
        'farm': farm, 'crop_type': payload['crop'], 'issued_at': issued, 'as_of': as_of, 'target_date': target,
        'status': 'success' if result.get('available') else 'unavailable',
        'model_id': result.get('model_id', MODEL_ID), 'input_sha256': result.get('input_sha256', ''), 'payload': payload,
    })
    return run


def stored_outlook(plan):
    if not plan.forecast_run_id:
        return {'available': False, 'reason': '이전 계획에는 당시 전망이 저장되지 않았습니다. 현재 전망은 별도로 확인할 수 있습니다.'}
    return plan.forecast_run.payload['outlook']
