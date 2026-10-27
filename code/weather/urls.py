from django.urls import path
from . import views

urlpatterns = [
    path('forecast/fetch/',     views.fetch_forecast_view, name='fetch_forecast'),
]
