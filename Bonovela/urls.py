from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    # Raw Django admin, kept off the obvious URL.
    path('bv-admin/database/', admin.site.urls),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/', include('apps.api.urls')),
    path('', include('apps.dashboard.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    # Uploads on Render's persistent disk. Fine for modest traffic; switch to
    # object storage (AWS_STORAGE_BUCKET_NAME) for heavy video use.
    urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]
