"""서비스와 과거 평가가 공유하는 고정 입력·모델 규약."""
import os

CONTEXT_LEN = 512
LOOKBACK_DAYS = 512
MAX_HORIZON = 365
MAX_INTERPOLATION_GAP = 7
MIN_OBSERVATIONS = 14
PREPROCESSING_VERSION = 'daily-linear7-latest-segment14-v1'
MODEL_REPO = 'google/timesfm-1.0-200m-pytorch'
MODEL_REVISION = '0581e2c56cb06feb51cfd98fc2b4005b74f7187b'


def runtime_options():
    backend = os.environ.get('TIMESFM_BACKEND', 'gpu')
    if backend not in ('cpu', 'gpu'):
        raise ValueError('TIMESFM_BACKEND는 cpu 또는 gpu여야 합니다.')
    batch_size = int(os.environ.get('TIMESFM_BATCH_SIZE', '8'))
    threads = int(os.environ.get('TIMESFM_CPU_THREADS', '4'))
    if batch_size < 1 or threads < 1:
        raise ValueError('모델 배치 크기와 CPU 스레드는 1 이상이어야 합니다.')
    return backend, batch_size, threads
