from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from notification.models import Notification
from notification.serializers.notification import (
    NotificationMarkAllReadSerializer,
    NotificationSerializer,
    NotificationStatsSerializer,
)


def _parse_bool(v: str):
    if v is None:
        return None
    v = str(v).strip().lower()
    if v in {'1', 'true', 't', 'yes', 'y'}:
        return True
    if v in {'0', 'false', 'f', 'no', 'n'}:
        return False
    return None


class NotificationListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    @extend_schema(
        summary='List My Notifications',
        parameters=[
            OpenApiParameter(
                name='is_read',
                required=False,
                type=bool,
                description='Filter by read status (true/false). Also accepts isRead.',
            ),
            OpenApiParameter(
                name='type',
                required=False,
                type=str,
                description='Filter by type: booking|payment|review|system',
            ),
        ],
        responses={200: OpenApiResponse(response=NotificationSerializer(many=True))},
        tags=['Notification'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Notification.objects.none()

        qs = Notification.objects.filter(user=self.request.user)

        is_read_param = self.request.query_params.get('is_read')
        if is_read_param is None:
            is_read_param = self.request.query_params.get('isRead')
        parsed = _parse_bool(is_read_param) if is_read_param is not None else None
        if is_read_param is not None and parsed is None:
            raise ValidationError({'is_read': 'Must be a boolean (true/false).'})
        if parsed is not None:
            qs = qs.filter(is_read=parsed)

        type_param = self.request.query_params.get('type')
        if type_param:
            type_param = str(type_param).strip().lower()
            valid_types = {t for (t, _) in Notification.TYPE_CHOICES}
            if type_param in valid_types:
                qs = qs.filter(type=type_param)
            else:
                raise ValidationError({'type': f'Invalid type. Valid: {sorted(valid_types)}'})

        return qs


class NotificationDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer
    http_method_names = ['get', 'put', 'patch', 'head', 'options']

    @extend_schema(
        summary='Get a Notification',
        responses={200: OpenApiResponse(response=NotificationSerializer)},
        tags=['Notification'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        summary='Mark Notification Read/Unread',
        request=NotificationSerializer,
        responses={200: OpenApiResponse(response=NotificationSerializer)},
        tags=['Notification'],
    )
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    @extend_schema(
        summary='Mark Notification Read/Unread (PUT)',
        request=NotificationSerializer,
        responses={200: OpenApiResponse(response=NotificationSerializer)},
        tags=['Notification'],
    )
    def put(self, request, *args, **kwargs):
        # Treat PUT the same as PATCH for this resource (we only support toggling isRead/is_read)
        return super().update(request, *args, **kwargs)

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Notification.objects.none()
        return Notification.objects.filter(user=self.request.user)


class NotificationMarkReadView(generics.GenericAPIView):
    """POST /api/notifications/{id}/read/ — convenience endpoint."""

    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    @extend_schema(
        summary='Mark Notification Read (Convenience)',
        responses={200: OpenApiResponse(response=NotificationSerializer)},
        tags=['Notification'],
    )
    def post(self, request, pk):
        notif = Notification.objects.filter(id=pk, user=request.user).first()
        if not notif:
            return Response({'detail': 'Notification not found.'}, status=status.HTTP_404_NOT_FOUND)
        notif.is_read = True
        notif.save(update_fields=['is_read', 'updated_at'])
        return Response(NotificationSerializer(notif).data)


class NotificationMarkAllReadView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationMarkAllReadSerializer

    @extend_schema(
        summary='Mark All Notifications Read',
        responses={200: OpenApiResponse(response=NotificationMarkAllReadSerializer)},
        tags=['Notification'],
    )
    def post(self, request):
        updated = (
            Notification.objects
            .filter(user=request.user, is_read=False)
            .update(is_read=True)
        )
        return Response({
            'status': True,
            'message': 'All notifications marked as read.',
            'updated': updated,
        })


class NotificationStatsView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationStatsSerializer

    @extend_schema(
        summary='Notification Stats',
        responses={200: OpenApiResponse(response=NotificationStatsSerializer)},
        tags=['Notification'],
    )
    def get(self, request):
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        return Response({'unread_count': unread_count})

