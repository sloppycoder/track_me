from django.shortcuts import redirect
from django.urls import path

from . import views

app_name = "myphoto"

urlpatterns = [
    path("", lambda request: redirect("myphoto:photo_list"), name="home"),
    path("geotag/", views.photo_list, name="photo_list"),
    path("footprints/", views.footprints_view, name="footprints"),
    path("api/search/", views.api_photo_search, name="api_search"),  # type: ignore[arg-type]
    path("api/footprints/steps/", views.api_footprints_steps, name="api_footprints_steps"),  # type: ignore[arg-type]
    path("api/thumbnail/<int:photo_id>/", views.api_thumbnail, name="thumbnail"),  # type: ignore[arg-type]
    path("api/preview/<int:photo_id>/", views.api_photo_preview, name="photo_preview"),  # type: ignore[arg-type]
    path("api/update-location/", views.api_update_location, name="api_update_location"),  # type: ignore[arg-type]
    path("api/reverse-geocode/", views.api_reverse_geocode, name="api_reverse_geocode"),  # type: ignore[arg-type]
]
