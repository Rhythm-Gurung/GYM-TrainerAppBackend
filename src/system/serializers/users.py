from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField(read_only=True)
    status = serializers.BooleanField(default=True)


class UserBaseDetailSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'uuid', 'email', 'username',
            'first_name', 'last_name', 'profile_image', 'dob',
            'is_email_verified', 'is_trainer', 'role',
            'full_name', 'contact_no', 'bio', 'expertise_categories',
            'years_of_experience', 'pricing_per_session', 'session_type',
            'is_active', 'is_receiving_promotional_email', 'agreed_to_policies',
            'social_provider', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_role(self, obj):
        return 'trainer' if obj.is_trainer else 'client'
