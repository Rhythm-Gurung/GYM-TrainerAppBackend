from django.contrib import admin
from messaging.models import ChatSession, ChatMessage


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'trainer', 'client', 'booking', 'created_at', 'updated_at']
    list_filter = ['created_at', 'updated_at']
    search_fields = ['trainer__username', 'client__username', 'booking__id']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Participants', {
            'fields': ('trainer', 'client', 'booking')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'sender', 'get_session_booking', 'timestamp', 'is_read']
    list_filter = ['is_read', 'timestamp', 'sender']
    search_fields = ['sender__username', 'content', 'session__booking__id']
    readonly_fields = ['timestamp']
    
    fieldsets = (
        ('Message Details', {
            'fields': ('session', 'sender', 'content', 'timestamp')
        }),
        ('Status', {
            'fields': ('is_read',)
        }),
    )
    
    def get_session_booking(self, obj):
        return f"Booking #{obj.session.booking.id}"
    get_session_booking.short_description = 'Booking'
