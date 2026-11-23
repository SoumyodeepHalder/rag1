from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("courses/create/", views.create_course, name="create_course"),
    path("courses/<int:pk>/", views.course_detail, name="course_detail"),
    path("courses/<int:pk>/ask/", views.ask_rag, name="ask_rag"),
]
