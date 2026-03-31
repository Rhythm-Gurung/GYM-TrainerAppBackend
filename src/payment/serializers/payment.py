from rest_framework import serializers

from payment.models import KhaltiPayment, PaymentGroup


class InitiatePaymentSerializer(serializers.Serializer):
    booking_id = serializers.IntegerField()


class BulkInitiatePaymentSerializer(serializers.Serializer):
    booking_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
    )

    def validate_booking_ids(self, value):
        unique_ids = list(dict.fromkeys(value))
        if len(unique_ids) != len(value):
            raise serializers.ValidationError('Duplicate booking IDs are not allowed.')
        return unique_ids


class KhaltiPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model  = KhaltiPayment
        fields = ['id', 'booking', 'pidx', 'transaction_id', 'amount', 'status', 'created_at']
        read_only_fields = fields


class PaymentGroupSerializer(serializers.ModelSerializer):
    booking_ids = serializers.SerializerMethodField()

    class Meta:
        model  = PaymentGroup
        fields = [
            'payment_group_id',
            'booking_ids',
            'pidx',
            'provider_reference',
            'total_amount',
            'status',
            'expires_at',
            'created_at',
        ]
        read_only_fields = fields

    def get_booking_ids(self, obj):
        return list(obj.bookings.values_list('id', flat=True))
