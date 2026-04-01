from rest_framework import serializers
from django.contrib.auth import get_user_model
from messaging.models import ChatMessage, ChatSession

UserBase = get_user_model()


class UserBasicSerializer(serializers.ModelSerializer):
    """Minimal user serializer for chat messages."""
    
    class Meta:
        model = UserBase
        fields = ['id', 'username', 'email', 'is_trainer', 'profile_image']
        read_only_fields = fields


class ChatMessageSerializer(serializers.ModelSerializer):
    """Serializer for individual chat messages."""
    sender = UserBasicSerializer(read_only=True)
    sender_id = serializers.IntegerField(source='sender.id', read_only=True)
    
    class Meta:
        model = ChatMessage
        fields = ['id', 'sender', 'sender_id', 'content', 'timestamp', 'is_read']
        read_only_fields = ['id', 'sender', 'timestamp']


class ChatSessionSerializer(serializers.ModelSerializer):
    """Serializer for chat sessions with recent message preview."""
    trainer = UserBasicSerializer(read_only=True)
    client = UserBasicSerializer(read_only=True)
    booking_id = serializers.IntegerField(source='booking.id', read_only=True)
    booking_date = serializers.DateField(source='booking.date', read_only=True)
    booking_status = serializers.CharField(source='booking.status', read_only=True)
    latest_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    
    class Meta:
        model = ChatSession
        fields = [
            'id', 'trainer', 'client', 'booking_id', 'booking_date', 'booking_status',
            'latest_message', 'unread_count', 'created_at', 'updated_at'
        ]
        read_only_fields = fields
    
    def get_latest_message(self, obj):
        """Get the most recent message in this session."""
        latest = obj.messages.last()
        if latest:
            return {
                'id': latest.id,
                'content': latest.content,
                'sender_id': latest.sender_id,
                'sender_username': latest.sender.username,
                'timestamp': latest.timestamp.isoformat(),
            }
        return None
    
    def get_unread_count(self, obj):
        """Get count of unread messages for the current user."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            # Count unread messages not sent by current user
            return obj.messages.filter(is_read=False).exclude(sender=request.user).count()
        return 0


class ChatHistorySerializer(serializers.Serializer):
    """Serializer for retrieving paginated chat history."""
    messages = ChatMessageSerializer(many=True, read_only=True)
    count = serializers.IntegerField(read_only=True)
    next = serializers.CharField(allow_null=True, read_only=True)
    previous = serializers.CharField(allow_null=True, read_only=True)


class MarkMessagesReadSerializer(serializers.Serializer):
    """Serializer for marking messages as read."""
    message_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="List of message IDs to mark as read. If omitted, marks all unread messages."
    )
    
    class Meta:
        fields = ['message_ids']


class MarkMessagesReadResponseSerializer(serializers.Serializer):
    """Response serializer for mark-messages-read endpoint."""
    marked_count = serializers.IntegerField(read_only=True)
