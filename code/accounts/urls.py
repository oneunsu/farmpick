from django.urls import path
from . import views

urlpatterns = [
    path('signup/',         views.signup_view,      name='signup'),
    path('login/',          views.login_view,        name='login'),
    path('logout/',         views.logout_view,       name='logout'),
    path('farms/',          views.farm_list_view,    name='farm_list'),
    path('farms/create/',   views.farm_create_view,  name='farm_create'),
    path('farms/<int:pk>/edit/',   views.farm_update_view,  name='farm_update'),
    path('farms/<int:pk>/delete/', views.farm_delete_view,  name='farm_delete'),
]
