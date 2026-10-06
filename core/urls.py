from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),
    path('guide/how-to-write-explainers/', views.explainer, name='explainer'),
    path('stats/', views.system_stats, name='system_stats'),
    path('ga/create/', views.ga_create, name='ga_create_new'),
    path('ga/<slug:slug>/', views.ga_detail, name='ga_detail'),
    path('ga/<slug:slug>/open-poll/', views.ga_open_poll, name='ga_open_poll'),
    path('ga/<slug:slug>/poll/', views.ga_poll, name='ga_poll'),
    path('ga/<slug:slug>/assign/', views.ga_assign, name='ga_assign'),
    path('ga/<slug:slug>/close/', views.ga_close, name='ga_close'),
    path('ga/<slug:slug>/map/', views.ga_map, name='ga_map'),
    path('ga/<slug:slug>/mine/', views.ga_mine, name='ga_mine'),
    path('ga/<slug:slug>/mine/<int:slot_id>/', views.ga_mine_submit, name='ga_mine_submit'),
    path('ga/<slug:slug>/mine/<int:slot_id>/preview/', views.ga_mine_preview, name='ga_mine_preview'),
    path('ga/<slug:slug>/review/', views.ga_review, name='ga_review'),
    path('ga/<slug:slug>/review/<int:solution_id>/', views.ga_review_solution, name='ga_review_solution'),
    path('ga/<slug:slug>/solutions/', views.ga_solutions, name='ga_solutions'),
    path('ga/<slug:slug>/solutions/<int:solution_id>/', views.ga_solution_detail, name='ga_solution_detail'),
]
