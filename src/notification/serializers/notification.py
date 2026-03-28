from rest_framework import serializers

from notification.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    # Frontend-friendly camelCase fields
    isRead = serializers.BooleanField(source='is_read', required=False)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id',
            'type',
            'title',
            'message',
            'isRead',
            'createdAt',
        ]
        read_only_fields = ['id', 'type', 'title', 'message', 'createdAt']

    def to_internal_value(self, data):
        # Accept both camelCase and snake_case for write operations
        if isinstance(data, dict):
            if 'is_read' in data and 'isRead' not in data:
                data = {**data, 'isRead': data.get('is_read')}
            if 'is_read' in data:
                # Drop snake_case to avoid DRF "unknown field" validation errors
                data = {k: v for k, v in data.items() if k != 'is_read'}
        return super().to_internal_value(data)


class NotificationStatsSerializer(serializers.Serializer):
    unread_count = serializers.IntegerField(read_only=True)


class NotificationMarkAllReadSerializer(serializers.Serializer):
    status = serializers.BooleanField(read_only=True)
    message = serializers.CharField(read_only=True)
    updated = serializers.IntegerField(read_only=True)

