"""농장 작물에 맞는 과거 시장 정산자료 조회. 예측·수익 계산은 하지 않는다."""
from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts.models import Farm
from .models import MarketSnapshot, MarketSettlement
from .market_collection import MarketCollectionForm
from .settlements import SettlementError, import_settlement_snapshot
from .market_pricing import convert_row

CROP_LABELS = dict(Farm.CROP_CHOICES)
FILTERS = {'market': '시장', 'variety': '품종', 'grade': '등급', 'size': '크기',
           'package': '포장', 'weight': '출하단위', 'corporation': '법인', 'origin': '산지', 'trade': '매매구분'}


def filter_value(row, key):
    if key == 'market':
        return row.market_code
    if key == 'variety':
        return row.product_key
    if key == 'weight':
        return f"{row.dimensions['unit']['code']}:{row.unit_weight}"
    return row.dimensions[key]['code'] or '__unknown__'


def filter_label(row, key):
    if key == 'market':
        return row.market_name
    if key == 'variety':
        return row.variety_name
    if key == 'weight':
        weight = format(row.unit_weight.normalize(), 'f') if row.unit_weight is not None else '미확인'
        return f"{weight} {row.dimensions['unit']['name']}"
    return row.dimensions[key]['name']


def build_context(request):
    farms = Farm.objects.filter(user=request.user).order_by('-created_at')
    farm = get_object_or_404(farms, pk=request.GET['farm']) if request.GET.get('farm') else farms.first()
    crop = request.GET.get('crop') or (farm.crop_type if farm else next(iter(CROP_LABELS)))
    if crop not in CROP_LABELS:
        raise ValueError('지원하지 않는 작물입니다.')
    snapshots = list(MarketSnapshot.objects.filter(crop_type=crop, collected_at__lte=timezone.now()).order_by('-collected_at', '-pk'))
    dates = sorted({c['date'] for snapshot in snapshots for c in snapshot.coverage}, reverse=True)
    selected_date = request.GET.get('date') or (dates[0] if dates else '')
    if selected_date:
        try:
            date.fromisoformat(selected_date)
        except ValueError:
            raise ValueError('정산일 형식이 올바르지 않습니다.') from None
        if selected_date not in dates:
            raise ValueError('선택한 날짜의 수집 자료가 없습니다.')
    # 새 조회의 정상 0건도 이전 관측을 가린다. 날짜·시장·품목별 최신 판본만 선택한다.
    selected_coverage = {}
    for snapshot in snapshots:
        for coverage in snapshot.coverage:
            if coverage['date'] == selected_date:
                key = (coverage['market'], coverage['category'], coverage['item'])
                selected_coverage.setdefault(key, (snapshot.pk, coverage, snapshot))
    all_rows = []
    if selected_date and selected_coverage:
        qs = MarketSettlement.objects.filter(
            snapshot_id__in={entry[0] for entry in selected_coverage.values()}, settlement_date=selected_date,
        ).select_related('snapshot').defer('raw', 'snapshot__coverage')
        for row in qs:
            category, item, _ = row.product_key.split('/')
            current = selected_coverage.get((row.market_code, category, item))
            if current and current[0] == row.snapshot_id:
                all_rows.append(row)
    filter_options, filtered = [], all_rows
    for key, label in FILTERS.items():
        choices = sorted({(filter_value(row, key), filter_label(row, key)) for row in all_rows}, key=lambda x: (x[1], x[0]))
        selected = request.GET.get(key, '')
        if selected and selected not in {value for value, _ in choices}:
            raise ValueError(f'{label} 선택값이 수집 자료에 없습니다.')
        filter_options.append({'key': key, 'label': label, 'choices': choices, 'selected': selected})
        if selected:
            filtered = [row for row in filtered if filter_value(row, key) == selected]
    for row in filtered:
        row.conversion = convert_row(row)
        row.dimensions_display = [
            {'label': label, **row.dimensions[key]} for key, label in
            (('grade', '등급'), ('size', '크기'), ('package', '포장'))
        ]
        row.weight_label = filter_label(row, 'weight')
        row.invalid_values = any(flag.startswith('invalid:') for flag in row.quality_flags)
    paginator = Paginator(filtered, 50)
    try:
        page_num = int(request.GET.get('page', '1'))
        if not 1 <= page_num <= paginator.num_pages:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('페이지 번호가 올바르지 않습니다.') from None
    page = paginator.page(page_num)
    params = request.GET.copy()
    params.pop('page', None)
    available_crops = set(MarketSnapshot.objects.values_list('crop_type', flat=True))
    return {
        'collection_form': MarketCollectionForm(user=request.user, initial={
            'farm': farm, 'crop': crop, 'date': timezone.localdate(), 'end_date': timezone.localdate(),
        }),
        'farm': farm, 'farms': farms, 'crop': crop, 'crop_label': CROP_LABELS[crop],
        'crop_choices': [{'value': key, 'label': label, 'available': key in available_crops} for key, label in Farm.CROP_CHOICES],
        'crop_mismatch': bool(farm and farm.crop_type != crop), 'dates': dates, 'selected_date': selected_date,
        'filters': filter_options, 'page_obj': page, 'query_without_page': params.urlencode(),
        'row_count': len(filtered), 'market_count': len({r.market_code for r in filtered}),
        'covered_market_count': len({key[0] for key in selected_coverage}),
        'total_count': sum(entry[1]['total'] for entry in selected_coverage.values()),
        'received_count': len(all_rows), 'partial': any(entry[1]['partial'] for entry in selected_coverage.values()),
        'collected_at': max((entry[2].collected_at for entry in selected_coverage.values()), default=None),
        'quality_unknown_count': sum(any(flag.startswith('unverified:') and flag != 'unverified:total_quantity_semantics'
                                         for flag in r.quality_flags) for r in filtered),
    }


@login_required
def collect_market_data(request):
    from io import StringIO
    from pathlib import Path
    from uuid import uuid4
    from django.conf import settings
    from django.core.cache import cache
    from django.core.management import call_command, CommandError
    if request.method != 'POST':
        return JsonResponse({'error': 'POST 요청만 가능합니다.'}, status=405)
    form = MarketCollectionForm(request.POST, user=request.user)
    if not form.is_valid():
        return JsonResponse({'error': '수집 조건을 확인해주세요.', 'errors': form.errors.get_json_data()}, status=400)
    data = form.cleaned_data
    if len(data['market']) != 1:
        return JsonResponse({'error': '진행 상태를 확인할 수 있도록 한 시장씩 요청해주세요.'}, status=400)
    if data['end_date'] != data['date']:
        return JsonResponse({'error': '진행 상태를 확인할 수 있도록 하루씩 요청해주세요.'}, status=400)
    market_name = dict(form.fields['market'].choices)[data['market'][0]]
    lock = f'market-collection-user-{request.user.pk}'
    if not cache.add(lock, True, timeout=1800):
        return JsonResponse({'error': '이미 수집 중입니다. 기존 요청이 끝난 뒤 다시 시도해주세요.'}, status=409)
    output = Path(settings.BASE_DIR) / 'data/markets' / ('web_' + uuid4().hex)
    try:
        call_command('inspect_market_data', source='settlement', crop_name=CROP_LABELS[data['crop']],
                     dates=[data['date'].isoformat()], market_names=[market_name], page_size=1000,
                     output=str(output), import_to_web=False, reuse_code_cache=True, all_pages=True, stdout=StringIO())
        # 새 폴더의 정규화 결과만 읽는다. 다른 사용자의 농장 가격에는 쓰지 않는다.
        snapshot, _ = import_settlement_snapshot(output)
        coverage = snapshot.coverage
        return JsonResponse({'ok': True, 'market': market_name, 'rows': snapshot.row_count,
                             'total': sum(c['total'] for c in coverage),
                             'partial': any(c['partial'] for c in coverage),
                             'date': data['date'].isoformat(), 'crop': data['crop']})
    except CommandError as exc:
        return JsonResponse({'error': str(exc), 'stop_collection': True}, status=502)
    except SettlementError as exc:
        return JsonResponse({'error': f'수집 파일은 보존됐지만 자료 검증에 실패했습니다. {exc}'}, status=502)
    except Exception:
        # 예외 문자열에는 요청 URL·키가 있을 수 있으므로 반환/기록하지 않는다.
        return JsonResponse({'error': '수집 결과를 처리하지 못했습니다. 기존 자료는 보존됩니다. 잠시 후 다시 시도해주세요.',
                             'stop_collection': True}, status=502)
    finally:
        cache.delete(lock)


@login_required
@require_GET
def market_prices(request):
    try:
        context = build_context(request)
    except ValueError as exc:
        return render(request, 'forecaster/market_error.html', {'error': str(exc)}, status=400)
    response = render(request, 'forecaster/markets.html', context)
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def market_prices_api(request):
    try:
        context = build_context(request)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    rows = [{
        'date': row.settlement_date.isoformat(), 'market_code': row.market_code,
        'market_name': row.market_name, 'product_key': row.product_key,
        'variety': row.variety_name, 'dimensions': row.dimensions,
        'unit_weight': row.unit_weight, 'average_price_original': row.average_price,
        'total_amount_original': row.total_amount, 'total_quantity_original': row.total_quantity,
        'krw_per_kg': row.conversion.get('krw_per_kg'), 'conversion': row.conversion,
        'quality_flags': row.quality_flags,
        'collected_at': row.snapshot.collected_at.isoformat(), 'snapshot_checksum': row.snapshot.checksum,
    } for row in context['page_obj']]
    response = JsonResponse({
        'source_kind': 'settlement', 'price_kind': 'observed_original', 'crop': context['crop'],
        'date': context['selected_date'], 'count': context['row_count'],
        'page': context['page_obj'].number, 'pages': context['page_obj'].paginator.num_pages,
        'coverage': {'markets_checked': context['covered_market_count'], 'total_count': context['total_count'],
                     'received_count': context['received_count'], 'partial': context['partial']},
        'rows': rows,
    })
    response['Cache-Control'] = 'private, no-store'
    return response
