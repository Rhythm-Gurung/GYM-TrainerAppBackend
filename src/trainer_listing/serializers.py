from rest_framework import serializers


class TrainerReviewCreateSerializer(serializers.Serializer):
    booking_id = serializers.IntegerField(min_value=1)
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, default='')