"""사용자가 지정한 외부 환경 파일만 읽는다. 프로젝트 폴더를 탐색하지 않는다."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import dotenv_values


def required_env(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'환경변수 {name}를 설정해주세요. FARMCAL_ENV_FILE로 지정한 외부 환경 파일에서도 설정할 수 있습니다.')
    return value


def required_api_setting(name):
    from django.conf import settings
    value = str(getattr(settings, name, '') or '').strip()
    if not value:
        raise ImproperlyConfigured(f'환경변수 {name}를 설정한 뒤 서버를 재시작해주세요.')
    return value


def load_external_env():
    """OS의 FARMCAL_ENV_FILE이 지정됐을 때 Django 시작 시 한 파일을 읽는다."""
    configured_path = os.environ.get('FARMCAL_ENV_FILE', '').strip()
    if not configured_path:
        return False
    path = Path(configured_path)
    if not path.is_absolute():
        raise ImproperlyConfigured('FARMCAL_ENV_FILE에는 외부 환경 파일의 절대 경로를 지정해 주세요.')
    try:
        # UTF-8 BOM 지원. ${...}가 포함된 인증키를 다른 값으로 치환하지 않는다.
        with path.open('r', encoding='utf-8-sig') as stream:
            values = dotenv_values(stream=stream, interpolate=False)
    except (OSError, UnicodeError):
        raise ImproperlyConfigured(
            'FARMCAL_ENV_FILE의 파일을 읽을 수 없습니다. 경로·파일 존재·실행 계정 권한·UTF-8 인코딩을 확인하세요.'
        ) from None
    for key, value in values.items():
        if value is not None:
            # 실행 환경에서 명시한 값이 파일보다 우선한다. 빈 환경변수도 덮어쓰지 않는다.
            os.environ.setdefault(key, value)
    return True
