from django.urls import path

from scheduling.apis.client import (
    available_dates_view,
    available_slots_view,
    book_slot_view,
    client_booking_detail_view,
    client_bookings_list_view,
    client_cancel_booking_view,
)
from scheduling.apis.trainer import (
    override_detail_view,
    overrides_list_view,
    schedule_override_detail_view,
    schedule_overrides_list_view,
    schedule_view,
    trainer_booking_detail_view,
    trainer_bookings_list_view,
    trainer_cancel_booking_view,
    trainer_confirm_booking_view,
)

urlpatterns = [
    # --- Trainer: weekly schedule ---
    path('trainer/schedule/',                                  schedule_view,        name='trainer-schedule'),

    # --- Trainer: blocked dates (single-day overrides) ---
    path('trainer/availability/overrides/',                    overrides_list_view,  name='trainer-overrides-list'),
    path('trainer/availability/overrides/<int:override_id>/', override_detail_view, name='trainer-override-detail'),

    # --- Trainer: date-range schedule overrides ---
    path('trainer/schedule-overrides/',                        schedule_overrides_list_view,   name='trainer-schedule-overrides-list'),
    path('trainer/schedule-overrides/<int:override_id>/',      schedule_override_detail_view,  name='trainer-schedule-override-detail'),

    # --- Trainer: bookings ---
    path('trainer/bookings/',                              trainer_bookings_list_view,    name='trainer-bookings-list'),
    path('trainer/bookings/<int:booking_id>/',             trainer_booking_detail_view,   name='trainer-booking-detail'),
    path('trainer/bookings/<int:booking_id>/confirm/',     trainer_confirm_booking_view,  name='trainer-booking-confirm'),
    path('trainer/bookings/<int:booking_id>/cancel/',      trainer_cancel_booking_view,   name='trainer-booking-cancel'),

    # --- Client: trainer availability ---
    path('trainers/<int:trainer_id>/available-slots/',  available_slots_view,  name='client-available-slots'),
    path('trainers/<int:trainer_id>/available-dates/',  available_dates_view,  name='client-available-dates'),

    # --- Client: book & manage bookings ---
    path('trainers/<int:trainer_id>/book/',  book_slot_view,               name='client-book-slot'),
    path('bookings/',                        client_bookings_list_view,    name='client-bookings-list'),
    path('bookings/<int:booking_id>/',       client_booking_detail_view,   name='client-booking-detail'),
    path('bookings/<int:booking_id>/cancel/', client_cancel_booking_view,  name='client-booking-cancel'),
]


