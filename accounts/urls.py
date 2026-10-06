from django.urls import path
from django.contrib.auth import views as auth_views

from . import views

app_name = 'accounts'

urlpatterns = [
    path('signup/', views.signup_view, name='signup'),
    path('signup/pending/', views.pending_view, name='pending'),
    path('signup/pending-notice/', views.pending_notice, name='pending_notice'),
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='core:home'), name='logout'),
    path('approvals/', views.pending_approvals, name='pending_approvals'),
    path('approvals/<int:pk>/approve/', views.approve_user, name='approve_user'),
]
