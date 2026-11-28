from django.urls import path

from support import views

app_name = "support"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("tickets/", views.ticket_list, name="ticket_list"),
    path("tickets/<str:ref>/", views.ticket_detail, name="ticket_detail"),
    path("tickets/<str:ref>/resolve/", views.ticket_resolve, name="ticket_resolve"),
    path("tickets/<str:ref>/approve/", views.resolution_approve, name="resolution_approve"),
    path("tickets/<str:ref>/reject/", views.resolution_reject, name="resolution_reject"),
    path("memory/", views.memory_view, name="memory"),
    path("gaps/", views.gap_list, name="gap_list"),
    path("gaps/<int:pk>/", views.gap_detail, name="gap_detail"),
    path("gaps/<int:pk>/save/", views.gap_save, name="gap_save"),
    path("gaps/<int:pk>/publish/", views.gap_publish, name="gap_publish"),
    path("gaps/<int:pk>/dismiss/", views.gap_dismiss, name="gap_dismiss"),
    path("benchmark/", views.benchmark_view, name="benchmark"),
]
