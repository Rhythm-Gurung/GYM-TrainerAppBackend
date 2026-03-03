import time

from django.db import connection
from django.http import JsonResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny


class HealthCheckSerializer(serializers.Serializer):
    status = serializers.CharField()
    timestamp = serializers.FloatField()
    checks = serializers.DictField()


@extend_schema(
    summary="Health Check",
    responses={
        200: OpenApiResponse(description='Healthy', response=HealthCheckSerializer),
        503: OpenApiResponse(description='Unhealthy', response=HealthCheckSerializer),
    },
    tags=['System']
)
@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    health_status = {
        "status": "healthy",
        "timestamp": time.time(),
        "checks": {
            "database": _check_database(),
            "api": _check_api()
        }
    }

    if any(check["status"] != "healthy" for check in health_status["checks"].values()):
        health_status["status"] = "unhealthy"

    status_code = 200 if health_status["status"] == "healthy" else 503
    return JsonResponse(health_status, status=status_code)


def _check_database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"status": "healthy", "message": "Database connection successful"}
    except Exception as e:
        return {"status": "unhealthy", "message": f"Database connection failed: {str(e)}"}


def _check_api():
    try:
        return {"status": "healthy", "message": "API endpoint accessible"}
    except Exception as e:
        return {"status": "unhealthy", "message": f"API check failed: {str(e)}"}
