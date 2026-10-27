from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/',    admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('weather/',  include('weather.urls')),
    path('calendar/', include('calendar_app.urls')),
    path('forecaster/', include('forecaster.urls')),
    path('',          RedirectView.as_view(url='/accounts/login/', permanent=False)),
]
