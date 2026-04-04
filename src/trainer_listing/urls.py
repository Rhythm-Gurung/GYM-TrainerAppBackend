from django.urls import path

from trainer_listing.apis.trainers import (
    my_favourites_view,
    toggle_favourite_view,
    trainer_certification_image_view,
    trainer_certifications_view,
    trainer_detail_view,
    trainer_gallery_image_view,
    trainer_gallery_view,
    trainer_list_view,
    trainer_profile_image_view,
    trainer_reviews_view,
)

urlpatterns = [
    path('trainers/',                                                      trainer_list_view,                name='client-trainer-list'),
    path('trainers/<int:trainer_id>/',                                     trainer_detail_view,              name='client-trainer-detail'),
    path('trainers/<int:trainer_id>/profile-image/',                       trainer_profile_image_view,       name='client-trainer-profile-image'),
    path('trainers/<int:trainer_id>/certifications/',                      trainer_certifications_view,      name='client-trainer-certifications'),
    path('trainers/<int:trainer_id>/certifications/<int:cert_id>/',        trainer_certification_image_view, name='client-trainer-cert-image'),
    path('trainers/<int:trainer_id>/gallery/',                             trainer_gallery_view,             name='client-trainer-gallery'),
    path('trainers/<int:trainer_id>/gallery/<int:image_id>/',              trainer_gallery_image_view,       name='client-trainer-gallery-image'),
    path('trainers/<int:trainer_id>/reviews/',                             trainer_reviews_view,             name='client-trainer-reviews-list'),
    path('trainers/<int:trainer_id>/reviews/<int:review_id>/',             trainer_reviews_view,             name='client-trainer-reviews-detail'),
    path('trainers/<int:trainer_id>/favourite/',                           toggle_favourite_view,            name='client-trainer-favourite'),
    path('favourites/',                                                    my_favourites_view,               name='client-my-favourites'),
]
