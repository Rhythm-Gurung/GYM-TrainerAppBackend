from django.http import HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from system.models import TrainerCertification
from system.serializers.users import MessageResponseSerializer


@extend_schema(
    summary="Get Trainer ID Proof Image",
    responses={
        200: OpenApiResponse(description="Image binary"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Trainer"]
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_id_proof(request):
    user = request.user
    if not user.id_proof:
        return Response({"detail": "No ID proof found."}, status=status.HTTP_404_NOT_FOUND)
    content_type = user.id_proof_content_type or "image/jpeg"
    return HttpResponse(bytes(user.id_proof), content_type=content_type)


@extend_schema(
    summary="Get Trainer Certification Image",
    responses={
        200: OpenApiResponse(description="Image binary"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Trainer"]
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_certification(request, cert_id):
    try:
        cert = TrainerCertification.objects.get(id=cert_id, user=request.user)
    except TrainerCertification.DoesNotExist:
        return Response({"detail": "Certification not found."}, status=status.HTTP_404_NOT_FOUND)
    return HttpResponse(bytes(cert.image), content_type=cert.content_type)


@extend_schema(
    summary="List Trainer Certifications",
    responses={
        200: OpenApiResponse(description="List of certification metadata"),
    },
    tags=["Trainer"]
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_certifications(request):
    certs = TrainerCertification.objects.filter(user=request.user).values(
        'id', 'name', 'content_type', 'created_at'
    )
    return Response(list(certs), status=status.HTTP_200_OK)
