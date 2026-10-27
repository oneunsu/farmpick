"""UI 파일 내용이 바뀌면 브라우저 캐시 키도 바뀐다."""
from functools import lru_cache
import hashlib
from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@lru_cache(maxsize=64)
def content_version(path, modified, size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


@register.simple_tag
def ui_asset(name):
    url = static(name)
    path = finders.find(name)
    if path:
        stat = Path(path).stat()
        version = content_version(path, stat.st_mtime_ns, stat.st_size)
        return f'{url}{"&" if "?" in url else "?"}v={version}'
    return url
