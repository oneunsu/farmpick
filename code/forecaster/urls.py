from django.urls import path
from . import views
from . import market_views
from . import revenue_views
from . import outlook_views
from . import forecast_record_views
from . import market_analysis_views

app_name = 'forecaster'

urlpatterns = [
    path('markets/<int:farm_id>/analysis/', market_analysis_views.analysis, name='market_analysis'),
    path('api/markets/<int:farm_id>/analysis/', market_analysis_views.analysis, {'api': True}, name='market_analysis_api'),
    path('outlook/history/', forecast_record_views.run_list, name='forecast_runs'),
    path('outlook/runs/<uuid:pk>/', forecast_record_views.run_detail, name='forecast_run'),
    path('outlook/<int:farm_id>/issue/', forecast_record_views.issue, name='issue_forecast'),
    path('outlook/result/', outlook_views.model_result, name='model_result'),
    path('outlook/comparison/', outlook_views.model_comparison, name='model_comparison'),
    path('outlook/validation/', outlook_views.validation, name='outlook_validation'),
    path('outlook/<int:farm_id>/', outlook_views.outlook, name='harvest_outlook'),
    path('api/outlook/<int:farm_id>/', outlook_views.outlook, {'api': True}, name='harvest_outlook_api'),
    path('revenue/', revenue_views.plan_list, name='revenue_list'),
    path('revenue/new/', revenue_views.plan_editor, name='revenue_create'),
    path('revenue/<int:pk>/', revenue_views.plan_detail, name='revenue_detail'),
    path('revenue/<int:pk>/edit/', revenue_views.plan_editor, name='revenue_edit'),
    path('api/revenue/<int:pk>/', revenue_views.plan_api, name='revenue_api'),
    path('markets/', market_views.market_prices, name='markets'),
    path('markets/collect/', market_views.collect_market_data, name='market_collect'),
    path('api/markets/', market_views.market_prices_api, name='markets_api'),
    # 가격 데이터 수집 (KAMIS)
    path('update/<int:farm_id>/', views.update_price_data,  name='update_price'),
    # 선정 모델 가격 전망 실행
    path('forecast/<int:farm_id>/', views.run_price_forecast, name='run_forecast'),
    # 캘린더 Ajax API
    path('api/forecast/<int:farm_id>/', views.price_forecast_api, name='forecast_api'),
]
