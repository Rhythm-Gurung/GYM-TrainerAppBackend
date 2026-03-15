import os
import uuid

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from system.models import TrainerCertification, TrainerGalleryImage, TrainerProfileChangeLog, UserBase
from system.serializers.users import MessageResponseSerializer, TrainerUpdateProfileSerializer

_IMAGE_CONTENT_TYPES = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'gif': 'image/gif',
    'webp': 'image/webp',
}


def _save_file_locally(file_bytes, filename):
    local_dir = os.path.join(settings.MEDIA_ROOT, 'trainer_uploads')
    os.makedirs(local_dir, exist_ok=True)
    file_path = os.path.join(local_dir, filename)
    with open(file_path, 'wb') as f:
        f.write(file_bytes)
    return file_path


# ---------------------------------------------------------------------------
# ID Proof  –  GET (view) / PATCH (replace)
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="Get Trainer ID Proof Image",
    responses={
        200: OpenApiResponse(description="Image binary"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Trainer"],
)
@extend_schema(
    methods=['PATCH'],
    summary="Upload / Replace Trainer ID Proof",
    request={
        'multipart/form-data': {
            'type': 'object',
            'properties': {
                'id_proof': {'type': 'string', 'format': 'binary'},
            },
            'required': ['id_proof'],
        }
    },
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Updated"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer"],
)
@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def id_proof_view(request):
    user = request.user
    if not user.is_trainer:
        return Response(
            {"status": False, "message": "Only trainers can access this."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == "GET":
        if not user.id_proof:
            return Response(
                {"status": False, "message": "No ID proof found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        content_type = user.id_proof_content_type or "image/jpeg"
        return HttpResponse(bytes(user.id_proof), content_type=content_type)

    # PUT – replace existing (or set for the first time)
    id_proof_file = request.FILES.get('id_proof')
    if not id_proof_file:
        return Response(
            {"status": False, "message": "id_proof file is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    id_proof_bytes = id_proof_file.read()
    id_proof_content_type = id_proof_file.content_type or 'image/jpeg'
    user.id_proof = id_proof_bytes
    user.id_proof_content_type = id_proof_content_type
    user.verification_status = 're_verification_required'
    user.save(update_fields=['id_proof', 'id_proof_content_type', 'verification_status'])

    TrainerProfileChangeLog.objects.create(user=user, action='id_proof_updated')

    ext = id_proof_content_type.split('/')[-1]
    _save_file_locally(id_proof_bytes, f"id_proof_{user.id}.{ext}")

    return Response(
        {"status": True, "message": "ID proof updated successfully."},
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Certifications  –  GET list / POST upload
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="List Trainer Certifications",
    responses={
        200: OpenApiResponse(description="List of certification metadata"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer"],
)
@extend_schema(
    methods=['POST'],
    summary="Upload Trainer Certifications",
    request={
        'multipart/form-data': {
            'type': 'object',
            'properties': {
                'certifications': {
                    'type': 'array',
                    'items': {'type': 'string', 'format': 'binary'},
                },
            },
            'required': ['certifications'],
        }
    },
    responses={
        201: OpenApiResponse(description="Created – list of new certification metadata"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer"],
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def certifications_list_view(request):
    user = request.user
    if not user.is_trainer:
        return Response(
            {"status": False, "message": "Only trainers can access this."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == "GET":
        certs = TrainerCertification.objects.filter(user=user).values(
            'id', 'name', 'content_type', 'created_at'
        )
        data = []
        for cert in certs:
            path = f"/api/system/trainer/certifications/{cert['id']}/"
            cert['image_url'] = request.build_absolute_uri(path)
            data.append(cert)
        return Response({"status": True, "data": data}, status=status.HTTP_200_OK)

    # POST – upload one or more certification files
    cert_files = request.FILES.getlist('certifications')
    if not cert_files:
        return Response(
            {"status": False, "message": "At least one certification file is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    created = []
    for cert_file in cert_files:
        cert_bytes = cert_file.read()
        cert_content_type = cert_file.content_type or 'image/jpeg'
        ext = cert_content_type.split('/')[-1]
        _save_file_locally(cert_bytes, f"cert_{user.id}_{cert_file.name}")
        cert = TrainerCertification.objects.create(
            user=user,
            name=cert_file.name or f"certification.{ext}",
            image=cert_bytes,
            content_type=cert_content_type,
        )
        path = f"/api/system/trainer/certifications/{cert.id}/"
        created.append({
            'id': cert.id,
            'name': cert.name,
            'content_type': cert.content_type,
            'created_at': cert.created_at,
            'image_url': request.build_absolute_uri(path),
        })

    user.verification_status = 're_verification_required'
    user.save(update_fields=['verification_status'])

    for c in created:
        TrainerProfileChangeLog.objects.create(
            user=user,
            action='certification_added',
            changes={'name': c['name']},
        )

    return Response({"status": True, "data": created}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Certification detail  –  GET image / DELETE
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="Get Trainer Certification Image",
    responses={
        200: OpenApiResponse(description="Image binary"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Trainer"],
)
@extend_schema(
    methods=['DELETE'],
    summary="Delete Trainer Certification",
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Deleted"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Trainer"],
)
@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def certification_detail_view(request, cert_id):
    user = request.user
    if not user.is_trainer:
        return Response(
            {"status": False, "message": "Only trainers can access this."},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        cert = TrainerCertification.objects.get(id=cert_id, user=user)
    except TrainerCertification.DoesNotExist:
        return Response(
            {"status": False, "message": "Certification not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        return HttpResponse(bytes(cert.image), content_type=cert.content_type)

    # DELETE
    cert_name = cert.name
    cert.delete()
    user.verification_status = 're_verification_required'
    user.save(update_fields=['verification_status'])
    TrainerProfileChangeLog.objects.create(
        user=user,
        action='certification_deleted',
        changes={'name': cert_name},
    )
    return Response(
        {"status": True, "message": "Certification deleted successfully."},
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Profile Image  –  GET (view) / PUT (replace)
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="Get Trainer Profile Image",
    responses={
        200: OpenApiResponse(description="Image binary"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Trainer"],
)
@extend_schema(
    methods=['PUT'],
    summary="Upload / Replace Trainer Profile Image",
    request={
        'multipart/form-data': {
            'type': 'object',
            'properties': {
                'profile_image': {'type': 'string', 'format': 'binary'},
            },
            'required': ['profile_image'],
        }
    },
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Updated"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer"],
)
@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def profile_image_view(request):
    user = request.user
    if not user.is_trainer:
        return Response(
            {"status": False, "message": "Only trainers can access this."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == "GET":
        if not user.profile_image:
            return Response(
                {"status": False, "message": "No profile image found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            name = user.profile_image.name or ''
            ext = name.rsplit('.', 1)[-1].lower() if '.' in name else 'jpeg'
            content_type = _IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
            user.profile_image.open('rb')
            image_data = user.profile_image.read()
            user.profile_image.close()
            return HttpResponse(image_data, content_type=content_type)
        except Exception:
            return Response(
                {"status": False, "message": "Profile image not accessible."},
                status=status.HTTP_404_NOT_FOUND,
            )

    # PUT – replace existing (or set for the first time)
    profile_image_file = request.FILES.get('profile_image')
    if not profile_image_file:
        return Response(
            {"status": False, "message": "profile_image file is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if user.profile_image:
        try:
            user.profile_image.delete(save=False)
        except Exception:
            pass

    user.profile_image.save(profile_image_file.name, profile_image_file, save=True)

    return Response(
        {"status": True, "message": "Profile image updated successfully."},
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Trainer Update Profile  –  PATCH
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Update Trainer Profile",
    request=TrainerUpdateProfileSerializer,
    responses={
        200: OpenApiResponse(description="Updated profile data"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer"],
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_profile_view(request):
    user = request.user
    if not user.is_trainer:
        return Response(
            {"status": False, "message": "Only trainers can access this."},
            status=status.HTTP_403_FORBIDDEN,
        )

    sensitive_fields = {'years_of_experience', 'pricing_per_session', 'session_type', 'expertise_categories'}
    old_values = {f: getattr(user, f) for f in sensitive_fields}

    serializer = TrainerUpdateProfileSerializer(user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)

    changed_sensitive = any(
        f in serializer.validated_data and serializer.validated_data[f] != old_values[f]
        for f in sensitive_fields
    )

    serializer.save()

    if changed_sensitive:
        user.verification_status = 're_verification_required'
        user.save(update_fields=['verification_status'])
        field_diff = {
            f: {'old': str(old_values[f]), 'new': str(serializer.validated_data[f])}
            for f in sensitive_fields
            if f in serializer.validated_data and serializer.validated_data[f] != old_values[f]
        }
        TrainerProfileChangeLog.objects.create(
            user=user,
            action='profile_fields_updated',
            changes=field_diff,
        )

    return Response({"status": True, "data": serializer.data}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Gallery  –  GET list / POST upload  |  GET image / DELETE
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="List Trainer Gallery Images",
    responses={
        200: OpenApiResponse(description="List of gallery image metadata"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer"],
)
@extend_schema(
    methods=['POST'],
    summary="Upload Trainer Gallery Images",
    request={
        'multipart/form-data': {
            'type': 'object',
            'properties': {
                'images': {
                    'type': 'array',
                    'items': {'type': 'string', 'format': 'binary'},
                },
                'caption': {'type': 'string'},
            },
            'required': ['images'],
        }
    },
    responses={
        201: OpenApiResponse(description="Created – list of new gallery image metadata"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer"],
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def gallery_list_view(request):
    user = request.user
    if not user.is_trainer:
        return Response(
            {"status": False, "message": "Only trainers can access this."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == "GET":
        images = TrainerGalleryImage.objects.filter(user=user).values(
            'id', 'caption', 'collection_id', 'content_type', 'created_at'
        )
        data = []
        for img in images:
            img['collection_id'] = str(img['collection_id']) if img['collection_id'] else None
            img['image_url'] = request.build_absolute_uri(
                f"/api/system/trainer/gallery/{img['id']}/"
            )
            data.append(img)
        return Response({"status": True, "data": data}, status=status.HTTP_200_OK)

    # POST – upload one or more images
    image_files = request.FILES.getlist('images')
    if not image_files:
        return Response(
            {"status": False, "message": "At least one image file is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    caption = request.data.get('caption', '')
    collection_id = uuid.uuid4()
    created = []
    for image_file in image_files:
        image_bytes = image_file.read()
        content_type = image_file.content_type or 'image/jpeg'
        gallery_img = TrainerGalleryImage.objects.create(
            user=user,
            image=image_bytes,
            content_type=content_type,
            caption=caption,
            collection_id=collection_id,
        )
        created.append({
            'id': gallery_img.id,
            'caption': gallery_img.caption,
            'collection_id': str(gallery_img.collection_id),
            'content_type': gallery_img.content_type,
            'created_at': gallery_img.created_at,
            'image_url': request.build_absolute_uri(
                f"/api/system/trainer/gallery/{gallery_img.id}/"
            ),
        })

    return Response({"status": True, "data": created}, status=status.HTTP_201_CREATED)


@extend_schema(
    methods=['GET'],
    summary="Get Trainer Gallery Image",
    responses={
        200: OpenApiResponse(description="Image binary"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Trainer"],
)
@extend_schema(
    methods=['DELETE'],
    summary="Delete Trainer Gallery Image",
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Deleted"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Trainer"],
)
@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def gallery_detail_view(request, image_id):
    user = request.user
    if not user.is_trainer:
        return Response(
            {"status": False, "message": "Only trainers can access this."},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        gallery_img = TrainerGalleryImage.objects.get(id=image_id, user=user)
    except TrainerGalleryImage.DoesNotExist:
        return Response(
            {"status": False, "message": "Image not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        return HttpResponse(bytes(gallery_img.image), content_type=gallery_img.content_type)

    # DELETE
    gallery_img.delete()
    return Response(
        {"status": True, "message": "Gallery image deleted successfully."},
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Admin – Verify Trainer  –  PATCH
# ---------------------------------------------------------------------------

class _VerifyTrainerSerializer(serializers.Serializer):
    verification_status = serializers.ChoiceField(
        choices=['pending', 'verified', 're_verification_required', 'reverification_rejected']
    )


@extend_schema(
    summary="Admin: Set Trainer Verification Status",
    request=_VerifyTrainerSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Updated"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Admin"],
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def admin_verify_trainer_view(request, trainer_id):
    if not request.user.is_staff:
        return Response(
            {"status": False, "message": "Admin access required."},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        trainer = UserBase.objects.get(id=trainer_id, is_trainer=True)
    except UserBase.DoesNotExist:
        return Response(
            {"status": False, "message": "Trainer not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = _VerifyTrainerSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    trainer.verification_status = serializer.validated_data['verification_status']
    trainer.save(update_fields=['verification_status'])

    return Response(
        {"status": True, "message": f"Verification status set to '{trainer.verification_status}'."},
        status=status.HTTP_200_OK,
    )
