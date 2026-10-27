"""공통 규약과 고정 체크포인트를 사용하는 TimesFM Torch 로더."""
import hashlib
import logging
import os
import threading
from functools import lru_cache

from .forecast_config import CONTEXT_LEN, MAX_HORIZON, MODEL_REPO, MODEL_REVISION, runtime_options

logger = logging.getLogger(__name__)
_lock = threading.Lock()


@lru_cache(maxsize=1)
def checkpoint_path():
    from huggingface_hub import hf_hub_download
    return hf_hub_download(
        MODEL_REPO, 'torch_model.ckpt', revision=MODEL_REVISION,
        local_files_only=os.environ.get('TIMESFM_ALLOW_DOWNLOAD', '0') != '1',
    )


@lru_cache(maxsize=1)
def checkpoint_sha256():
    digest = hashlib.sha256()
    with open(checkpoint_path(), 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=2)
def _create_model(horizon, backend, batch_size, threads):
    import torch
    from timesfm import TimesFmHparams, TimesFmCheckpoint
    from timesfm.timesfm_torch import TimesFmTorch
    if backend == 'gpu' and not torch.cuda.is_available():
        raise ValueError('GPU를 요청했지만 PyTorch CUDA를 사용할 수 없습니다. CUDA 지원 설치를 확인해 주세요.')
    torch.set_num_threads(threads)
    torch.manual_seed(0)
    model = TimesFmTorch(
        hparams=TimesFmHparams(
            backend=backend, per_core_batch_size=batch_size,
            horizon_len=horizon, context_len=CONTEXT_LEN,
        ),
        checkpoint=TimesFmCheckpoint(path=checkpoint_path(), version='torch'),
    )
    logger.info('TimesFM 로딩 완료: backend=%s, horizon=%s, checkpoint=%s', backend, horizon, MODEL_REVISION)
    return model


def get_model(*, horizon=MAX_HORIZON):
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValueError('모델 예측 길이는 1~365일이어야 합니다.')
    backend, batch_size, threads = runtime_options()
    # 체크포인트 출력 패치 128의 배수만 계산한다.
    rounded_horizon = ((horizon + 127) // 128) * 128
    with _lock:
        return _create_model(rounded_horizon, backend, batch_size, threads)
