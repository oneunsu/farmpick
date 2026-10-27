"""서비스와 백테스트 공통 TimesFM 추론 어댑터."""
import numpy as np


def forecast_contexts(contexts, horizon, *, model=None):
    if model is None:
        from forecaster.model_loader import get_model
        model = get_model(horizon=horizon)
    points, _ = model.forecast(inputs=contexts, freq=[0] * len(contexts))
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[0] != len(contexts) or points.shape[1] < horizon:
        raise ValueError('모델 출력의 개수 또는 예측 길이가 부족합니다.')
    points = points[:, :horizon]
    if not np.isfinite(points).all():
        raise ValueError('모델 출력에 유효하지 않은 가격이 있습니다.')
    return np.maximum(points, 0)
