from datetime import date

from rest_framework import serializers

# ---------------------------------------------------------------------------
# Booking serializers
# ---------------------------------------------------------------------------


class BookingCreateSerializer(serializers.Serializer):
    date         = serializers.DateField()
    start_time   = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])
    end_time     = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])
    session_mode = serializers.ChoiceField(choices=['online', 'offline'])
    notes        = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        if attrs['date'] < date.today():
            raise serializers.ValidationError({'date': 'Cannot book a past date.'})
        if attrs['start_time'] >= attrs['end_time']:
            raise serializers.ValidationError({'start_time': 'start_time must be before end_time.'})
        return attrs


class BookingCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class BookingResponseSerializer(serializers.Serializer):
    id           = serializers.IntegerField()
    trainer_id   = serializers.IntegerField()
    trainer_name = serializers.CharField()
    client_id    = serializers.IntegerField()
    client_name  = serializers.CharField()
    date         = serializers.DateField()
    start_time   = serializers.TimeField(format='%H:%M')
    end_time     = serializers.TimeField(format='%H:%M')
    session_mode = serializers.CharField()
    status       = serializers.CharField()
    notes        = serializers.CharField()
    cancelled_by = serializers.CharField()
    cancel_reason = serializers.CharField()
    created_at   = serializers.DateTimeField()


class TimeSlotSerializer(serializers.Serializer):
    start_time = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])
    end_time   = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])

    def validate(self, attrs):
        if attrs['start_time'] >= attrs['end_time']:
            raise serializers.ValidationError('start_time must be before end_time.')
        return attrs


class ScheduleDaySerializer(serializers.Serializer):
    day_of_week  = serializers.IntegerField(min_value=0, max_value=6)
    enabled      = serializers.BooleanField()
    session_mode = serializers.ChoiceField(choices=['online', 'offline', 'both'])
    slots        = TimeSlotSerializer(many=True)

    def validate(self, attrs):
        if not attrs['enabled'] and attrs['slots']:
            raise serializers.ValidationError('Disabled days must have no slots.')
        return attrs


class WeeklyScheduleInputSerializer(serializers.Serializer):
    schedule        = ScheduleDaySerializer(many=True)
    effective_from  = serializers.DateField(required=False)
    effective_until = serializers.DateField(required=False, allow_null=True, default=None)

    def validate_schedule(self, value):
        if len(value) != 7:
            raise serializers.ValidationError('Must include exactly 7 days.')
        days_present = {d['day_of_week'] for d in value}
        if days_present != set(range(7)):
            raise serializers.ValidationError('Must contain every day_of_week 0–6 exactly once.')
        return value

    def validate(self, attrs):
        effective_from  = attrs.get('effective_from') or date.today()
        effective_until = attrs.get('effective_until')
        attrs['effective_from'] = effective_from
        if effective_until is not None and effective_until < effective_from:
            raise serializers.ValidationError({'effective_until': 'effective_until must be on or after effective_from.'})
        return attrs


class DateOverrideSerializer(serializers.Serializer):
    date   = serializers.DateField()
    reason = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)


class DateOverrideResponseSerializer(serializers.Serializer):
    id     = serializers.IntegerField()
    date   = serializers.DateField()
    reason = serializers.CharField(allow_null=True)


class PatchOverrideReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, allow_blank=True, allow_null=True)


class ScheduleOverrideInputSerializer(serializers.Serializer):
    start_date = serializers.DateField()
    end_date   = serializers.DateField()
    schedule   = ScheduleDaySerializer(many=True)

    def validate_schedule(self, value):
        if len(value) != 7:
            raise serializers.ValidationError('Must include exactly 7 days.')
        days_present = {d['day_of_week'] for d in value}
        if days_present != set(range(7)):
            raise serializers.ValidationError('Must contain every day_of_week 0–6 exactly once.')
        return value

    def validate(self, attrs):
        today = date.today()
        if attrs['start_date'] < today:
            raise serializers.ValidationError({'start_date': 'start_date must be today or a future date.'})
        if attrs['end_date'] < attrs['start_date']:
            raise serializers.ValidationError({'end_date': 'end_date must be >= start_date.'})
        return attrs


class SessionVerificationRequestCreateSerializer(serializers.Serializer):
    request_type = serializers.ChoiceField(choices=['start', 'end'])


class SessionVerificationRespondSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['accept', 'reject'])
    reason = serializers.CharField(required=False, allow_blank=True, default='')
