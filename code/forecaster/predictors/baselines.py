"""동일한 전처리 컨텍스트를 사용하는 고정 비교 모델."""
import numpy as np

NAMES = ('last', 'mean7', 'mean28', 'seasonal7')


def forecast(name, context, horizon):
    values = np.asarray(context, dtype=float)
    if not len(values) or horizon < 1:
        raise ValueError('입력과 예측 길이를 확인해 주세요.')
    if name == 'last':
        return np.full(horizon, values[-1])
    if name in ('mean7', 'mean28'):
        window = 7 if name == 'mean7' else 28
        return np.full(horizon, values[-window:].mean())
    if name == 'seasonal7':
        return np.resize(values[-7:], horizon)
    raise ValueError(f'알 수 없는 예측 방법: {name}')
