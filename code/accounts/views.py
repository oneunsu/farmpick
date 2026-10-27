from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from .forms import SignUpForm, FarmForm
from .models import Farm
from weather.kma_api import NX_NY_TABLE


# ── 회원가입 ──────────────────────────────────────────────────────────────────
def signup_view(request):
    if request.user.is_authenticated:
        return redirect('calendar_app:summary')

    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'{user.username}님, 환영합니다! 농장을 등록해 주세요.')
            return redirect('farm_create')
    else:
        form = SignUpForm()
    return render(request, 'accounts/signup.html', {'form': form})


# ── 로그인 ────────────────────────────────────────────────────────────────────
def login_view(request):
    if request.user.is_authenticated:
        return redirect('calendar_app:summary')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f'{user.username}님, 로그인되었습니다.')
            next_url = request.GET.get('next') or 'calendar_app:summary'
            return redirect(next_url)
        else:
            messages.error(request, '아이디 또는 비밀번호가 올바르지 않습니다.')
    else:
        form = AuthenticationForm()
    return render(request, 'accounts/login.html', {'form': form})


# ── 로그아웃 ──────────────────────────────────────────────────────────────────
@login_required
def logout_view(request):
    logout(request)
    messages.info(request, '로그아웃되었습니다.')
    return redirect('login')


# ── 농장 목록 ─────────────────────────────────────────────────────────────────
@login_required
def farm_list_view(request):
    farms = Farm.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'accounts/farm_list.html', {'farms': farms})


# ── 농장 등록 ─────────────────────────────────────────────────────────────────
@login_required
def farm_create_view(request):
    if request.method == 'POST':
        form = FarmForm(request.POST)
        if form.is_valid():
            farm = form.save(commit=False)
            farm.user = request.user
            form.save()
            messages.success(request, f'"{farm.name}" 농장이 등록되었습니다.')
            return redirect('farm_list')
    else:
        form = FarmForm()
    return render(request, 'accounts/farm_form.html', {
        'form': form, 'action': '등록', 'grid_data': NX_NY_TABLE,
    })


# ── 농장 수정 ─────────────────────────────────────────────────────────────────
@login_required
def farm_update_view(request, pk):
    farm = get_object_or_404(Farm, pk=pk, user=request.user)
    if request.method == 'POST':
        form = FarmForm(request.POST, instance=farm)
        if form.is_valid():
            form.save()
            messages.success(request, f'"{farm.name}" 농장 정보가 수정되었습니다.')
            return redirect('farm_list')
    else:
        # 지역 초기값
        form = FarmForm(instance=farm, initial={'region': farm.region_code})
    return render(request, 'accounts/farm_form.html', {
        'form': form, 'action': '수정', 'farm': farm, 'grid_data': NX_NY_TABLE,
    })


# ── 농장 삭제 ─────────────────────────────────────────────────────────────────
@login_required
def farm_delete_view(request, pk):
    farm = get_object_or_404(Farm, pk=pk, user=request.user)
    if request.method == 'POST':
        name = farm.name
        farm.delete()
        messages.success(request, f'"{name}" 농장이 삭제되었습니다.')
        return redirect('farm_list')
    return render(request, 'accounts/farm_confirm_delete.html', {'farm': farm})
