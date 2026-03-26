"""
Client profile endpoints.

GET   /api/system/client/profile/          — get own profile
PATCH /api/system/client/profile/          — update profile fields
GET   /api/system/client/profile-image/    — view profile image
PUT   /api/system/client/profile-image/    — upload / replace profile image
DELETE /api/system/client/profile-image/   — remove profile image
"""

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from system.serializers.users import (
    ClientProfileSerializer,
    ClientUpdateProfileSerializer,
    MessageResponseSerializer,
)

_IMAGE_CONTENT_TYPES = {
    'jpg':  'image/jpeg',
    'jpeg': 'image/jpeg',
    'png':  'image/png',
    'gif':  'image/gif',
    'webp': 'image/webp',
}


def _client_only(user):
    if user.is_trainer:
        return Response(
            {'status': False, 'message': 'This endpoint is for clients only.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


# ---------------------------------------------------------------------------
# Profile  –  GET / PATCH
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary='Get Client Profile',
    responses={
        200: ClientProfileSerializer,
        403: OpenApiResponse(response=MessageResponseSerializer, description='Forbidden'),
    },
    tags=['Client'],
)
@extend_schema(
    methods=['PATCH'],
    summary='Update Client Profile',
    request=ClientUpdateProfileSerializer,
    responses={
        200: ClientProfileSerializer,
        400: OpenApiResponse(response=MessageResponseSerializer, description='Validation error'),
        403: OpenApiResponse(response=MessageResponseSerializer, description='Forbidden'),
    },
    tags=['Client'],
)
@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def client_profile_view(request):
    user = request.user
    err = _client_only(user)
    if err:
        return err

    if request.method == 'GET':
        serializer = ClientProfileSerializer(user, context={'request': request})
        return Response({'status': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    # PATCH
    serializer = ClientUpdateProfileSerializer(user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response(
        {'status': True, 'data': ClientProfileSerializer(user, context={'request': request}).data},
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Profile Image  –  GET / PUT / DELETE
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary='Get Client Profile Image',
    responses={
        200: OpenApiResponse(description='Image binary'),
        404: OpenApiResponse(response=MessageResponseSerializer, description='No image'),
    },
    tags=['Client'],
)
@extend_schema(
    methods=['PUT'],
    summary='Upload / Replace Client Profile Image',
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
        200: OpenApiResponse(response=MessageResponseSerializer, description='Updated'),
        400: OpenApiResponse(response=MessageResponseSerializer, description='Bad Request'),
        403: OpenApiResponse(response=MessageResponseSerializer, description='Forbidden'),
    },
    tags=['Client'],
)
@extend_schema(
    methods=['DELETE'],
    summary='Remove Client Profile Image',
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description='Removed'),
        404: OpenApiResponse(response=MessageResponseSerializer, description='No image'),
    },
    tags=['Client'],
)
@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def client_profile_image_view(request):
    user = request.user
    err = _client_only(user)
    if err:
        return err

    if request.method == 'GET':
        if not user.profile_image:
            return Response(
                {'status': False, 'message': 'No profile image found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            name = user.profile_image.name or ''
            ext  = name.rsplit('.', 1)[-1].lower() if '.' in name else 'jpeg'
            content_type = _IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
            user.profile_image.open('rb')
            image_data = user.profile_image.read()
            user.profile_image.close()
            return HttpResponse(image_data, content_type=content_type)
        except Exception:
            return Response(
                {'status': False, 'message': 'Profile image not accessible.'},
                status=status.HTTP_404_NOT_FOUND,
            )

    if request.method == 'PUT':
        image_file = request.FILES.get('profile_image')
        if not image_file:
            return Response(
                {'status': False, 'message': 'profile_image file is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user.profile_image:
            try:
                user.profile_image.delete(save=False)
            except Exception:
                pass
        user.profile_image.save(image_file.name, image_file, save=True)
        return Response(
            {'status': True, 'message': 'Profile image updated successfully.'},
            status=status.HTTP_200_OK,
        )

    # DELETE
    if not user.profile_image:
        return Response(
            {'status': False, 'message': 'No profile image to remove.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    try:
        user.profile_image.delete(save=True)
    except Exception:
        user.profile_image = None
        user.save(update_fields=['profile_image'])
    return Response(
        {'status': True, 'message': 'Profile image removed.'},
        status=status.HTTP_200_OK,
    )
