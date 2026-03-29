from rest_framework import serializers


class ChatMessageSerializer(serializers.Serializer):
    """Serializer for chat messages"""
    message = serializers.CharField(required=True, help_text="User's message to send to Gemini")


class ChatResponseSerializer(serializers.Serializer):
    """Serializer for chat responses"""
    response = serializers.CharField(help_text="Gemini's response to the user")
    status = serializers.BooleanField(default=True)


class ChatWithHistorySerializer(serializers.Serializer):
    """Serializer for chat with conversation history"""
    message = serializers.CharField(required=True, help_text="User's message")
    conversation_history = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        help_text="Previous conversation messages for context"
    )


class ChatResponseWithHistorySerializer(serializers.Serializer):
    """Response serializer with message context"""
    response = serializers.CharField(help_text="Gemini's response")
    status = serializers.BooleanField(default=True)
    message = serializers.CharField(help_text="User's original message")
