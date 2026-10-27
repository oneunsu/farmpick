from django.urls import path
from . import views

app_name = 'calendar_app'

urlpatterns = [
    path('summary/', views.summary, name='summary'),
    path('', views.index, name='index'),
    path('api/schedule/', views.schedule_api, name='schedule_api'),
    path('schedule/generate/', views.generate_schedule, name='generate_schedule'),
    path('schedule/toggle/', views.toggle_done, name='toggle_done'),
    path('schedule/add/', views.add_schedule, name='add_schedule'),
    path('schedule/update/', views.update_schedule, name='update_schedule'),  # 신규
    path('schedule/delete/', views.delete_schedule, name='delete_schedule'),
]
