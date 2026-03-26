from rest_framework import serializers

from payment.models import KhaltiPayment


class InitiatePaymentSerializer(serializers.Serializer):
    booking_id = serializers.IntegerField()


class KhaltiPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model  = KhaltiPayment
        fields = ['id', 'booking', 'pidx', 'transaction_id', 'amount', 'status', 'created_at']
        read_only_fields = fields
