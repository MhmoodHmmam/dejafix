"""URL configuration: the staff pages, login/logout and the Django admin."""

from django.contrib import admin
from django.contrib.auth import views as auth_views

from support.auth import ThrottledLoginView
from django.urls import include, path

admin.site.site_header = "DejaFix review"

urlpatterns = [
    path("", include("support.urls")),
    path("login/", ThrottledLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
