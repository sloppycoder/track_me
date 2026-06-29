"""
URL configuration for track_me project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import path
from django.views.generic import RedirectView

from track_me.api import api

urlpatterns = [
    # django-ninja API + auto docs at /api/docs
    path("api/", api.urls),
    # No web UI yet (Phase 3 on hold); send the bare root to the API docs.
    path("", RedirectView.as_view(url="/api/docs", permanent=False)),
]
