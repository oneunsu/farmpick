"""
TimesFM 1.0 zero-shot 가격 예측
- KAMIS 과거 가격 시계열 → 수확일까지 미래 가격 예측
- PriceForecast 모델에 저장
"""
import logging
from datetime import date, timedelta
import math

import numpy as np
import pandas as pd
from django.db import transaction
from django.utils import timezone

from .kamis_api import get_recent_prices
from .date_utils import as_local_date
from .models import PriceForecast
from .forecast_config import (
    CONTEXT_LEN, LOOKBACK_DAYS, MAX_HORIZON, MAX_INTERPOLATION_GAP, MIN_OBSERVATIONS,
)

logger = logging.getLogger(__name__)


def _load_timesfm(horizon=MAX_HORIZON):
    """TimesFM 모델 로드 (싱글톤 - model_loader에서 캐싱)"""
    try:
        from forecaster.model_loader import get_model
        return get_model(horizon=horizon)
    except Exception as e:
        logger.error(f"TimesFM 로드 실패: {e}")
        raise

def _prepare_context(
    price_records: list[dict], context_len: int = CONTEXT_LEN, *, as_of: date | None = None,
) -> np.ndarray:
    """
    가격 시계열을 TimesFM 입력 형식으로 변환.
    최대 7일의 내부 결측만 보간. 장기 공백 이전 자료는 사용하지 않는다.
    """
    if not price_records:
        raise ValueError("가격 데이터가 없습니다.")

    df = pd.DataFrame(price_records)
    df['date'] = pd.to_datetime([as_local_date(d) for d in df['date']])
    df = df.set_index('date').sort_index()
    if as_of is not None:
        df = df.loc[:pd.Timestamp(as_local_date(as_of))]
    if df.index.has_duplicates:
        raise ValueError('같은 날짜의 가격이 중복되어 있습니다.')
    if df.empty:
        raise ValueError('기준일까지의 가격 데이터가 없습니다.')
    values = pd.to_numeric(df['price'], errors='coerce').to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('과거 가격에 유효하지 않은 값이 있습니다.')
    df['price'] = values

    breaks = df.index.to_series().diff().dt.days > MAX_INTERPOLATION_GAP + 1
    if breaks.any():
        df = df.loc[breaks[breaks].index[-1]:]
    if len(df) < MIN_OBSERVATIONS:
        raise ValueError(
            f'장기 공백 이후 최근 구간의 가격 관측이 부족합니다 '
            f'({len(df)}개, 최소 {MIN_OBSERVATIONS}개 필요).'
        )
    # provenance 같은 메타데이터 열에는 보간을 적용하지 않는다.
    df = df[['price']]

    # 날짜 연속성 확보 (결측일 선형 보간)
    full_idx = pd.date_range(df.index.min(), df.index.max(), freq='D')
    df = df.reindex(full_idx).interpolate(method='linear')

    # 최근 context_len 일만 사용
    values = df['price'].values[-context_len:]
    return values.astype(np.float32)


def prepare_forecast_input(price_records, *, as_of, end_date):
    """서비스와 평가 공통: 당시 입력만 사용하고 관측 공백까지 추론 길이에 포함."""
    as_of = as_local_date(as_of)
    end_date = as_local_date(end_date)
    since = as_of - timedelta(days=LOOKBACK_DAYS-1)
    records = [r for r in price_records if since <= r['date'] <= as_of]
    if len(records) < MIN_OBSERVATIONS:
        raise ValueError(f'가격 데이터가 부족합니다 (현재 {len(records)}개, 최소 {MIN_OBSERVATIONS}개 필요).')
    last = max(r['date'] for r in records)
    if (as_of-last).days > MAX_INTERPOLATION_GAP:
        raise ValueError(
            f'최근 가격 관측일({last}) 이후 7일 넘게 자료가 없습니다. '
            '계절적 미공시 또는 수집 누락을 확인하고 최신 가격을 수집해 주세요.'
        )
    horizon = (end_date-last).days
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(
            f'마지막 가격 관측일({last})부터 종료일까지 {horizon}일로 '
            f'최대 예측 범위({MAX_HORIZON}일)를 벗어납니다. '
            '최신 가격을 수집하거나 종료일을 앞당겨 주세요.'
        )
    return _prepare_context(records, as_of=as_of), last, horizon


def predict_price(
    farm, start_date: date, end_date: date, *, as_of: date | None = None,
) -> dict:
    """
    farm: Farm 인스턴스
    start_date ~ end_date: 가격을 예측할 기간 (수확 시작일 ~ 종료일)
    as_of: 입력 조회 기준일 (기본값 Django 현지 날짜).
    마지막 관측일 다음 날부터 추론하고, 요청 기간 중 as_of 이후만 반환한다.
    관측 공백을 포함해 MAX_HORIZON을 초과하거나 모델 출력이 짧으면 오류.
    반환: {
        'forecast_dates': [date, ...],   # start_date ~ end_date 사이 날짜만
        'forecast_prices': [float, ...],
        'context_days': int,
    }
    """
    start_date = as_local_date(start_date)
    end_date = as_local_date(end_date)
    as_of = as_of if as_of is not None else timezone.localdate()
    as_of = as_local_date(as_of)
    if start_date > end_date:
        raise ValueError("수확 시작일은 종료일보다 늦을 수 없습니다.")

    # 기존 서비스와 같이 기준일 이후 날짜만 반환한다.
    result_start = max(start_date, as_of + timedelta(days=1))
    if end_date < result_start:
        return {'forecast_dates': [], 'forecast_prices': [], 'context_days': 0}

    # 과거 가격 로드
    price_records = get_recent_prices(farm, days=LOOKBACK_DAYS, as_of=as_of, require_provenance=True)
    context, last_observed_date, full_horizon = prepare_forecast_input(
        price_records, as_of=as_of, end_date=end_date,
    )
    context_days = len(context)

    # TimesFM 예측
    from .predictors.timesfm import forecast_contexts
    tfm = _load_timesfm(full_horizon)
    preds = forecast_contexts([context], full_horizon, model=tfm)[0].tolist()

    # 모델의 첫 출력은 마지막 관측일의 다음 날이다.
    # 관측 공백과 수확 시작일까지의 출력은 건너뛰되 날짜를 재지정하지 않는다.
    start_offset = (result_start - last_observed_date).days - 1
    result_prices = preds[start_offset:full_horizon]
    result_dates = [
        result_start + timedelta(days=i) for i in range(len(result_prices))
    ]

    return {
        'forecast_dates':  result_dates,
        'forecast_prices': result_prices,
        'context_days':    context_days,
    }


def save_price_forecast(farm, start_date: date, end_date: date) -> dict:
    """
    start_date ~ end_date 기간 전체에 대해 예측 수행 후 PriceForecast DB에 저장.
    horizon은 마지막 관측일부터 end_date까지 관측 공백을 포함해 계산.
    반환: predict_price() 결과 dict (harvest_price =기간 내 마지막 예측일 가격)
    """
    result = predict_price(farm, start_date, end_date)

    # zip의 조용한 잘림, 중복/누락 날짜, 비정상 가격으로 기존 결과를
    # 교체하지 않도록 DB 쓰기 전에 전체 결과를 검증한다.
    dates = result['forecast_dates']
    prices = result['forecast_prices']
    if not dates:
        raise ValueError('저장할 미래 예측이 없습니다. 수확 기간을 확인해 주세요.')
    if len(dates) != len(prices):
        raise ValueError('예측 날짜와 가격 개수가 다릅니다.')
    start = as_local_date(start_date)
    end = as_local_date(end_date)
    if dates[-1] != end:
        raise ValueError('예측 결과에 요청한 종료일이 없습니다.')
    for i, (d, p) in enumerate(zip(dates, prices)):
        if type(d) is not date or not start <= d <= end:
            raise ValueError('예측 날짜가 요청 기간에 맞지 않습니다.')
        if i and d != dates[i-1] + timedelta(days=1):
            raise ValueError('예측 날짜가 중복되거나 연속되지 않습니다.')
        if not math.isfinite(p) or p < 0:
            raise ValueError('예측 가격은 유한한 0 이상의 값이어야 합니다.')

    # 날짜 검증/추론 실패 시 기존 성공 결과를 유지한다.
    if result['forecast_dates']:
        with transaction.atomic():
            PriceForecast.objects.filter(farm=farm).delete()
            PriceForecast.objects.bulk_create([
                PriceForecast(farm=farm, forecast_at=d, price=round(p, 1))
                for d, p in zip(result['forecast_dates'], result['forecast_prices'])
            ])

    if result['forecast_dates']:
        logger.info(
            f"[{farm.name}] 가격 예측 저장 완료 "
            f"{len(result['forecast_dates'])}개 ({start_date} ~ {end_date}), "
            f"마지막 예상가: {result['forecast_prices'][-1]:.0f}원/kg"
        )
    else:
        logger.info(f"[{farm.name}] 가격 예측 결과 없음 ({start_date} ~ {end_date})")

    return result
