"""웹에서 사용하는 한 종류의 선정 모델. 비교 실험과 운영 예측을 분리한다."""
import json
import math
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
from django.conf import settings
from django.utils import timezone
from threadpoolctl import threadpool_limits

from .date_utils import as_local_date
from .kamis_api import get_price_basis
from .models import PriceData
from .predictors.harvest_models import FEATURE_VERSION, History
from .snapshots import sha256

MODEL_ID = 'harvest-tree-20261026-v1'
MODEL_LABEL = '트리 기반 가격 모델'
CANDIDATES = ('last', 'seasonal', 'seasonal_corrected', 'tree', 'lstm', 'timesfm')


def selection_from_scores(artifact):
    """선정은 2025년 하루 목표, 작물·기간별 동일 가중치. 2026 점수는 사용하지 않는다."""
    cells = {(crop, lead) for crop in ('paprika', 'tomato') for lead in (7, 14, 30, 60, 90, 180)}
    rankings = []
    for method in CANDIDATES:
        values = {}
        for crop, result in artifact['crops'].items():
            for row in result['scores']:
                if row['split'] == 'validation' and row['width'] == 1 and row['method'] == method:
                    key = crop, row['lead']
                    if key in values or not math.isfinite(row['wape']) or row['n'] < 1:
                        raise ValueError('선정용 평가 표본이 중복되거나 올바르지 않습니다.')
                    values[key] = row['wape']
        if set(values) != cells:
            raise ValueError('두 작물·6개 기간의 동일한 검증 표본이 필요합니다.')
        rankings.append(dict(method=method, validation_macro_wape=sum(values.values())/len(cells)))
    rankings.sort(key=lambda r: (r['validation_macro_wape'], CANDIDATES.index(r['method'])))
    return dict(selected=rankings[0]['method'], criterion='2025-point-WAPE-equal-crop-equal-lead', rankings=rankings)


def release_dir():
    return Path(getattr(settings, 'PRICE_MODEL_DIR', Path(settings.BASE_DIR)/'model_artifacts'/MODEL_ID))


def load_release():
    path = release_dir()/'release.json'
    if not path.is_file():
        raise ValueError('선정 가격 모델이 준비되지 않았습니다. 모델 학습 명령을 먼저 실행해 주세요.')
    release = json.loads(path.read_text(encoding='utf-8'))
    if (release['model_id'] != MODEL_ID or release['method'] != 'tree' or
            release['feature_version'] != FEATURE_VERSION or release['selection']['selected'] != 'tree'):
        raise ValueError('선정 가격 모델의 버전이 현재 프로그램과 다릅니다.')
    return release


@lru_cache(maxsize=8)
def _load_tree(path, checksum):
    if sha256(path) != checksum:
        raise ValueError('가격 모델 파일의 해시가 다릅니다. 기존 모델 파일을 확인해 주세요.')
    # 배포 명령이 만든 고정 경로·해시의 파일만 읽는다. 요청에서 파일 경로를 받지 않는다.
    model = joblib.load(path)
    from sklearn.ensemble import HistGradientBoostingRegressor
    if not isinstance(model, HistGradientBoostingRegressor):
        raise ValueError('선정 가격 모델의 종류가 다릅니다.')
    return model


@lru_cache(maxsize=8)
def _load_snapshot(path, checksum, crop):
    if sha256(path) != checksum:
        raise ValueError('모델의 기준 가격 자료가 학습 당시와 다릅니다.')
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if payload['crop'] != crop:
        raise ValueError('모델 자료의 작물이 다릅니다.')
    expected = get_price_basis(crop)
    if not payload['records'] or any(any(r['provenance'].get(k) != v for k, v in expected.items()) for r in payload['records']):
        raise ValueError('모델 기준 자료의 가격 출처를 확인할 수 없습니다.')
    return payload


def prepare_live_input(farm, *, as_of):
    as_of = as_local_date(as_of)
    release = load_release()
    if farm.crop_type not in release['crops']:
        raise ValueError('선정 가격 모델은 현재 토마토·파프리카만 지원합니다.')
    if as_of < date.fromisoformat(release['trained_as_of']):
        raise ValueError('현재 모델의 학습일 이전 전망을 소급해 생성할 수 없습니다.')
    entry = release['crops'][farm.crop_type]
    base_dir = Path(settings.BASE_DIR).resolve()
    snapshot_path = (base_dir/entry['snapshot_path']).resolve()
    if not snapshot_path.is_relative_to(base_dir):
        raise ValueError('가격 자료 경로가 프로젝트 범위를 벗어납니다.')
    payload = _load_snapshot(str(snapshot_path), entry['snapshot_sha256'], farm.crop_type)
    basis = payload['records'][0]['provenance']
    merged = {date.fromisoformat(r['date']): float(r['price']) for r in payload['records'] if date.fromisoformat(r['date']) <= as_of}
    updated = 0
    own = PriceData.objects.filter(farm=farm, date__lte=as_of).order_by('date').values('date', 'price', 'provenance')
    for row in own:
        if row['provenance'] != basis:
            raise ValueError('농장 가격 자료의 출처·품목·품종·등급·단위를 확인할 수 없습니다. 같은 기준으로 재수집해 주세요.')
        if not math.isfinite(row['price']) or row['price'] <= 0:
            raise ValueError('농장 가격 자료에 유효하지 않은 가격이 있습니다.')
        if merged.get(row['date']) != row['price']:
            updated += 1
        merged[row['date']] = row['price']
    if not merged:
        raise ValueError('기준일까지의 관측 가격이 없습니다.')
    last = max(merged)
    if (as_of-last).days > 7:
        raise ValueError(f'마지막 가격 관측일({last}) 이후 7일 넘게 자료가 없습니다. 가격 업데이트를 실행해 주세요.')
    records = [dict(date=d, price=p) for d, p in sorted(merged.items())]
    history = History(records, release['config'], extend_through=as_of)
    model = _load_tree(str(release_dir()/f'{farm.crop_type}.joblib'), entry['model_sha256'])
    if model.n_features_in_ != len(history.features(as_of, 7, 1)[1]):
        raise ValueError('가격 모델의 입력 특징 수가 다릅니다.')
    import hashlib
    input_hash = hashlib.sha256(json.dumps([[r['date'].isoformat(), r['price']] for r in records], separators=(',', ':')).encode()).hexdigest()
    return history, model, release, dict(last_observed=last.isoformat(), observations=len(records),
        input_start=records[0]['date'].isoformat(), input_sha256=input_hash,
        database_updates=updated, price_basis=get_price_basis(farm.crop_type), model_sha256=entry['model_sha256'])


def predict_dates(history, model, *, as_of, dates):
    features = []
    last = None
    for target in dates:
        lead = (target-as_of).days
        if not 1 <= lead <= 365:
            raise ValueError('수확일은 기준일 다음 날부터 365일 이내로 선택해 주세요.')
        _, vector, last = history.features(as_of, lead, 1)
        features.append(vector)
    if not features:
        return []
    with threadpool_limits(limits=4):
        delta = np.asarray(model.predict(np.stack(features)), dtype=float)
    if delta.shape != (len(dates),) or not np.isfinite(delta).all() or (np.abs(delta) > 10).any():
        raise ValueError('선정 모델의 가격 결과가 유효하지 않습니다.')
    prices = float(last)*np.exp(delta)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError('선정 모델의 가격 결과가 유효하지 않습니다.')
    return prices.tolist()


def selected_outlook(farm, target, *, as_of=None):
    as_of, target = as_local_date(as_of or timezone.localdate()), as_local_date(target)
    try:
        lead = (target-as_of).days
        if not 1 <= lead <= 365:
            raise ValueError('수확일은 기준일 다음 날부터 365일 이내로 선택해 주세요.')
        history, model, release, evidence = prepare_live_input(farm, as_of=as_of)
        value = predict_dates(history, model, as_of=as_of, dates=[target])[0]
        return dict(available=True, version=MODEL_ID, model_id=MODEL_ID, method='tree', label='수확일 예상 참고가격',
                    model_name=MODEL_LABEL, trained_as_of=release['trained_as_of'],
                    as_of=as_of.isoformat(), target=target.isoformat(), lead_days=lead, base=value, low=None, high=None,
                    unit='원/kg', source='KAMIS 중도매인 판매 참고가격', extrapolated=lead < 7 or lead > 180,
                    **evidence)
    except (ValueError, OSError) as exc:
        return dict(available=False, reason=str(exc), as_of=as_of.isoformat(), target=target.isoformat())


def selected_curve(farm, start, end, *, as_of=None):
    as_of = as_local_date(as_of or timezone.localdate())
    start, end = max(as_local_date(start), as_of+timedelta(days=1)), as_local_date(end)
    if end < start or (end-as_of).days > 365:
        raise ValueError('가격 전망 기간은 기준일 다음 날부터 365일 이내여야 합니다.')
    dates = [start+timedelta(days=i) for i in range((end-start).days+1)]
    history, model, release, evidence = prepare_live_input(farm, as_of=as_of)
    prices = predict_dates(history, model, as_of=as_of, dates=dates)
    return dict(model_id=MODEL_ID, method='tree', as_of=as_of.isoformat(), **evidence,
                forecasts=[dict(date=d.isoformat(), price=round(p, 1)) for d, p in zip(dates, prices)])
