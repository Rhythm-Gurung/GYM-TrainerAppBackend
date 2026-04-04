from django.contrib.auth import get_user_model
from django.db.models import Avg
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

User = get_user_model()


class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField(read_only=True)
    status = serializers.BooleanField(default=True)


class UserBaseDetailSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    id_proof_url = serializers.SerializerMethodField()
    profile_completion = serializers.SerializerMethodField()
    avg_rating = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'uuid', 'email', 'username',
            'first_name', 'last_name', 'profile_image', 'dob',
            'is_email_verified', 'is_trainer', 'role',
            'full_name', 'contact_no', 'bio', 'expertise_categories',
            'years_of_experience', 'pricing_per_session', 'session_type', 'location',
            'is_active', 'is_receiving_promotional_email', 'agreed_to_policies',
            'created_at', 'updated_at',
            'id_proof_url', 'verification_status', 'profile_completion', 'avg_rating',
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_role(self, obj):
        return 'trainer' if obj.is_trainer else 'client'

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_id_proof_url(self, obj):
        if not obj.is_trainer or not obj.id_proof:
            return None
        request = self.context.get('request')
        path = '/api/system/trainer/id-proof/'
        if request:
            return request.build_absolute_uri(path)
        return path

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_profile_completion(self, obj):
        """
        Returns profile completion percentage (0-100) for trainers only.

        Scoring:
          Fields (65 pts):  profile_image=10, id_proof=10, certifications≥1=10,
                            full_name=5, contact_no=5, bio=5, expertise_categories=5,
                            years_of_experience=5, pricing_per_session=5, session_type=5
          Verification (35 pts): verification_status == 'verified'

        Completion drops to ≤65% whenever sensitive data is updated and awaits
        re-verification by an admin.
        """
        if not obj.is_trainer:
            return None

        score = 0

        # Field contributions
        if obj.profile_image:
            score += 10
        if obj.id_proof:
            score += 10
        if obj.certifications.exists():
            score += 10
        if obj.full_name:
            score += 5
        if obj.contact_no:
            score += 5
        if obj.bio:
            score += 5
        if obj.expertise_categories:
            score += 5
        if obj.years_of_experience is not None:
            score += 5
        if obj.pricing_per_session is not None:
            score += 5
        if obj.session_type:
            score += 5

        # Verification bonus — only granted once admin approves current data
        if obj.verification_status == 'verified':
            score += 35

        return score

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_avg_rating(self, obj):
        """
        Returns average rating for trainers only.
        Calculated from TrainerReview table, rounded to 1 decimal place.
        Returns None for non-trainer users.
        """
        if not obj.is_trainer:
            return None

        from trainer_listing.models import TrainerReview
        
        reviews_agg = TrainerReview.objects.filter(trainer=obj).aggregate(
            avg_rating=Avg('rating')
        )
        avg = reviews_agg.get('avg_rating')
        return round(avg, 1) if avg is not None else 0.0


class ClientProfileSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    profile_image_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'uuid', 'email', 'username',
            'first_name', 'last_name', 'dob', 'contact_no',
            'profile_image_url',
            'is_email_verified', 'is_receiving_promotional_email',
            'role', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_role(self, obj):
        return 'client'

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_profile_image_url(self, obj):
        if not obj.profile_image:
            return None
        request = self.context.get('request')
        path = '/api/system/client/profile-image/'
        return request.build_absolute_uri(path) if request else path


class ClientUpdateProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'dob', 'contact_no', 'is_receiving_promotional_email']
        extra_kwargs = {f: {'required': False} for f in fields}

    def validate_contact_no(self, value):
        if value and len(value.strip()) < 7:
            raise serializers.ValidationError('Enter a valid phone number.')
        return value.strip() if value else value


class TrainerUpdateProfileSerializer(serializers.ModelSerializer):
    expertise_categories = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=False
    )

    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'dob',
            'full_name', 'contact_no', 'bio', 'expertise_categories',
            'years_of_experience', 'pricing_per_session', 'session_type', 'location',
            'is_receiving_promotional_email',
        ]
        extra_kwargs = {
            f: {'required': False}
            for f in [
                'first_name', 'last_name', 'dob', 'full_name', 'contact_no',
                'bio', 'expertise_categories', 'years_of_experience',
                'pricing_per_session', 'session_type', 'location', 'is_receiving_promotional_email',
            ]
        }

    def validate_years_of_experience(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('Must be a non-negative integer.')
        return value

    def validate_pricing_per_session(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('Must be a non-negative value.')
        return value

    def validate(self, attrs):
        import json
        expertise = attrs.get('expertise_categories')
        if isinstance(expertise, str):
            try:
                parsed = json.loads(expertise)
                if not isinstance(parsed, list):
                    raise serializers.ValidationError({'expertise_categories': 'Must be a list.'})
                attrs['expertise_categories'] = parsed
            except json.JSONDecodeError:
                attrs['expertise_categories'] = [
                    item.strip() for item in expertise.split(',') if item.strip()
                ]
        return attrs
