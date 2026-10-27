from .models import Farm


def workspace(request):
    """로그인한 사용자의 농장과 공통 메뉴만 제공한다."""
    if not request.user.is_authenticated:
        return {'workspace_farms': [], 'workspace_farm': None}
    farms = list(Farm.objects.filter(user=request.user).order_by('-created_at'))
    match = request.resolver_match
    requested = request.GET.get('farm') or (match.kwargs.get('farm_id') if match else None)
    farm = next((f for f in farms if str(f.pk) == str(requested)), None)
    if farm is None and farms:
        farm = farms[0]
    return {'workspace_farms': farms, 'workspace_farm': farm}
