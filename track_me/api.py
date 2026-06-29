"""Root django-ninja API.

Routers from each app are mounted here. The auto-generated OpenAPI docs live at
``/api/docs``.
"""

from ninja import NinjaAPI

api = NinjaAPI(title="track_me", version="1.0.0", description="Photo timeline API")


@api.get("/ping", tags=["meta"])
def ping(request):
    """Liveness check."""
    return {"status": "ok"}


# App routers are added as they land:
#   from library.api import router as library_router
#   api.add_router("/media", library_router)
