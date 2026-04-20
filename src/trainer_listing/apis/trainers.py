"""
Client-facing trainer listing endpoints.

GET    /api/trainers/                                        — list approved trainers (filterable)
GET    /api/trainers/{trainer_id}/                           — full trainer profile + schedule + certs + gallery
GET    /api/trainers/{trainer_id}/profile-image/            — trainer profile image (binary)
GET    /api/trainers/{trainer_id}/certifications/           — list certifications (metadata + image URLs)
GET    /api/trainers/{trainer_id}/certifications/{cert_id}/ — single certification image (binary)
GET    /api/trainers/{trainer_id}/gallery/                  — list gallery images (metadata + image URLs)
GET    /api/trainers/{trainer_id}/gallery/{image_id}/       — single gallery image (binary)
GET    /api/trainers/{trainer_id}/reviews/                  — list reviews for a trainer
POST   /api/trainers/{trainer_id}/reviews/                  — client submits a review
GET    /api/favourites/                                      — client's favourited trainers
POST   /api/trainers/{trainer_id}/favourite/                — toggle favourite (add / remove)
"""

from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from scheduling.models import TrainerScheduleScope, WeeklyScheduleDay
from system.models import TrainerCertification, TrainerGalleryImage, UserBase
from system.serializers.users import MessageResponseSerializer
from trainer_listing.models import TrainerFavourite, TrainerReview
from trainer_listing.serializers import TrainerReviewCreateSerializer
from scheduling.models import Booking

_IMAGE_CONTENT_TYPES = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'gif': 'image/gif',
    'webp': 'image/webp',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_approved_trainer_or_404(trainer_id):
    try:
        return UserBase.objects.get(id=trainer_id, is_trainer=True, is_admin_approved=True)
    except UserBase.DoesNotExist:
        return None


def _profile_image_url(request, trainer_id, has_image):
    if not has_image:
        return None
    return request.build_absolute_uri(f'/api/trainers/{trainer_id}/profile-image/')


def _cert_image_url(request, trainer_id, cert_id):
    return request.build_absolute_uri(f'/api/trainers/{trainer_id}/certifications/{cert_id}/')


def _gallery_image_url(request, trainer_id, image_id):
    return request.build_absolute_uri(f'/api/trainers/{trainer_id}/gallery/{image_id}/')


def _profile_completeness(trainer):
    """Same scoring as UserBaseDetailSerializer.get_profile_completion."""
    score = 0
    if trainer.profile_image:
        score += 10
    if trainer.id_proof:
        score += 10
    if trainer.certifications.exists():
        score += 10
    if trainer.first_name and trainer.last_name:
        score += 5
    if trainer.dob:
        score += 5
    if trainer.bio:
        score += 5
    if trainer.expertise_categories:
        score += 5
    if trainer.years_of_experience is not None:
        score += 5
    if trainer.pricing_per_session is not None:
        score += 5
    if trainer.session_type:
        score += 5
    if trainer.verification_status == 'verified':
        score += 35
    return score


def _build_schedule(trainer):
    days = WeeklyScheduleDay.objects.filter(user=trainer).prefetch_related('slots').order_by('day_of_week')
    days_data = [
        {
            'day_of_week':  d.day_of_week,
            'enabled':      d.enabled,
            'session_mode': d.session_mode,
            'slots': [
                {'start_time': s.start_time.strftime('%H:%M'), 'end_time': s.end_time.strftime('%H:%M')}
                for s in d.slots.all()
            ],
        }
        for d in days
    ]
    present = {d['day_of_week'] for d in days_data}
    for i in range(7):
        if i not in present:
            days_data.append({'day_of_week': i, 'enabled': False, 'session_mode': 'both', 'slots': []})
    days_data.sort(key=lambda x: x['day_of_week'])

    try:
        scope = trainer.schedule_scope
        effective_from  = scope.effective_from.strftime('%Y-%m-%d')
        effective_until = scope.effective_until.strftime('%Y-%m-%d') if scope.effective_until else None
    except TrainerScheduleScope.DoesNotExist:
        effective_from  = None
        effective_until = None

    return {'effective_from': effective_from, 'effective_until': effective_until, 'days': days_data}


def _trainer_list_item(request, trainer, favourited_ids=None, rating_map=None):
    """Lightweight dict for the list endpoint."""
    days_enabled = list(
        WeeklyScheduleDay.objects.filter(user=trainer, enabled=True)
        .values_list('day_of_week', flat=True)
        .order_by('day_of_week')
    )
    try:
        scope = trainer.schedule_scope
        eff_from  = scope.effective_from.strftime('%Y-%m-%d')
        eff_until = scope.effective_until.strftime('%Y-%m-%d') if scope.effective_until else None
    except TrainerScheduleScope.DoesNotExist:
        eff_from = eff_until = None

    r = rating_map.get(trainer.id, {}) if rating_map else {}

    return {
        'id':                    trainer.id,
        'uuid':                  str(trainer.uuid),
        'full_name':             trainer.full_name or f'{trainer.first_name} {trainer.last_name}'.strip() or trainer.username,
        'username':              trainer.username,
        'profile_image_url':     _profile_image_url(request, trainer.id, bool(trainer.profile_image)),
        'bio':                   trainer.bio,
        'location':              trainer.location,
        'expertise_categories':  trainer.expertise_categories,
        'years_of_experience':   trainer.years_of_experience,
        'pricing_per_session':   str(trainer.pricing_per_session) if trainer.pricing_per_session is not None else None,
        'session_type':          trainer.session_type,
        'verification_status':   trainer.verification_status,
        'is_verified':           trainer.verification_status == 'verified',
        'profile_completeness':  _profile_completeness(trainer),
        'rating':                round(r.get('avg_rating') or 0, 1),
        'review_count':          r.get('review_count', 0),
        'active_days':           days_enabled,
        'schedule_effective_from':  eff_from,
        'schedule_effective_until': eff_until,
        'is_favourited':         trainer.id in favourited_ids if favourited_ids is not None else False,
    }


def _trainer_detail(request, trainer, is_favourited=False):
    """Full dict for the detail endpoint."""
    reviews_agg = TrainerReview.objects.filter(trainer=trainer).aggregate(
        avg_rating=Avg('rating'), review_count=Count('id')
    )

    base = {
        'id':                    trainer.id,
        'uuid':                  str(trainer.uuid),
        'full_name':             trainer.full_name or f'{trainer.first_name} {trainer.last_name}'.strip() or trainer.username,
        'username':              trainer.username,
        'profile_image_url':     _profile_image_url(request, trainer.id, bool(trainer.profile_image)),
        'bio':                   trainer.bio,
        'location':              trainer.location,
        'contact_no':            trainer.contact_no,
        'expertise_categories':  trainer.expertise_categories,
        'years_of_experience':   trainer.years_of_experience,
        'pricing_per_session':   str(trainer.pricing_per_session) if trainer.pricing_per_session is not None else None,
        'session_type':          trainer.session_type,
        'verification_status':   trainer.verification_status,
        'is_verified':           trainer.verification_status == 'verified',
        'profile_completeness':  _profile_completeness(trainer),
        'rating':                round(reviews_agg['avg_rating'] or 0, 1),
        'review_count':          reviews_agg['review_count'],
        'is_favourited':         is_favourited,
    }

    base['schedule'] = _build_schedule(trainer)

    certs = TrainerCertification.objects.filter(user=trainer).values(
        'id', 'name', 'issuer', 'year', 'content_type', 'created_at'
    )
    base['certifications'] = [
        {
            'id':           c['id'],
            'name':         c['name'],
            'issuer':       c['issuer'],
            'year':         c['year'],
            'content_type': c['content_type'],
            'created_at':   c['created_at'],
            'image_url':    _cert_image_url(request, trainer.id, c['id']),
        }
        for c in certs
    ]

    gallery = TrainerGalleryImage.objects.filter(user=trainer).values(
        'id', 'caption', 'collection_id', 'content_type', 'created_at'
    )
    base['gallery'] = [
        {
            'id':            g['id'],
            'caption':       g['caption'],
            'collection_id': str(g['collection_id']) if g['collection_id'] else None,
            'content_type':  g['content_type'],
            'created_at':    g['created_at'],
            'image_url':     _gallery_image_url(request, trainer.id, g['id']),
        }
        for g in gallery
    ]

    return base


# ---------------------------------------------------------------------------
# GET /api/trainers/
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="List Approved Trainers",
    parameters=[
        OpenApiParameter(name='search',       description='Search by name or bio',                            required=False, type=str),
        OpenApiParameter(name='session_type', description='Filter by session type: online | offline | both',  required=False, type=str),
        OpenApiParameter(name='expertise',    description='Filter by expertise category (partial match)',      required=False, type=str),
        OpenApiParameter(name='min_price',    description='Minimum pricing per session',                      required=False, type=float),
        OpenApiParameter(name='max_price',    description='Maximum pricing per session',                      required=False, type=float),
        OpenApiParameter(name='location',     description='Filter by location (partial match)',                required=False, type=str),
        OpenApiParameter(name='verified',     description='true = only verified trainers',                    required=False, type=bool),
    ],
    responses={
        200: OpenApiResponse(description="List of approved trainers"),
    },
    tags=["Client — Trainers"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trainer_list_view(request):
    qs = UserBase.objects.filter(is_trainer=True, is_admin_approved=True, is_active=True)

    search = request.query_params.get('search')
    if search:
        qs = qs.filter(
            Q(full_name__icontains=search) | Q(first_name__icontains=search) |
            Q(last_name__icontains=search) | Q(bio__icontains=search) |
            Q(username__icontains=search)
        )

    session_type = request.query_params.get('session_type')
    if session_type:
        qs = qs.filter(session_type=session_type)

    expertise = request.query_params.get('expertise')
    if expertise:
        qs = qs.filter(expertise_categories__icontains=expertise)

    location = request.query_params.get('location')
    if location:
        qs = qs.filter(location__icontains=location)

    min_price = request.query_params.get('min_price')
    if min_price:
        try:
            qs = qs.filter(pricing_per_session__gte=float(min_price))
        except ValueError:
            return Response({'status': False, 'message': 'Invalid min_price.'}, status=status.HTTP_400_BAD_REQUEST)

    max_price = request.query_params.get('max_price')
    if max_price:
        try:
            qs = qs.filter(pricing_per_session__lte=float(max_price))
        except ValueError:
            return Response({'status': False, 'message': 'Invalid max_price.'}, status=status.HTTP_400_BAD_REQUEST)

    if request.query_params.get('verified', '').lower() == 'true':
        qs = qs.filter(verification_status='verified')

    trainers = list(qs)
    trainer_ids = [t.id for t in trainers]

    # Batch-fetch favourites for the current user (avoids N+1)
    favourited_ids = set(
        TrainerFavourite.objects.filter(client=request.user, trainer_id__in=trainer_ids)
        .values_list('trainer_id', flat=True)
    )

    # Batch-fetch ratings (avoids N+1)
    rating_qs = (
        TrainerReview.objects.filter(trainer_id__in=trainer_ids)
        .values('trainer_id')
        .annotate(avg_rating=Avg('rating'), review_count=Count('id'))
    )
    rating_map = {r['trainer_id']: r for r in rating_qs}

    data = [_trainer_list_item(request, t, favourited_ids, rating_map) for t in trainers]
    return Response({'status': True, 'count': len(data), 'data': data}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# GET /api/trainers/{trainer_id}/
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="Get Trainer Detail",
    responses={
        200: OpenApiResponse(description="Full trainer profile with schedule, certifications, gallery, reviews"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Trainer not found"),
    },
    tags=["Client — Trainers"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trainer_detail_view(request, trainer_id):
    trainer = _get_approved_trainer_or_404(trainer_id)
    if not trainer:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)

    is_favourited = TrainerFavourite.objects.filter(client=request.user, trainer=trainer).exists()
    return Response({'status': True, 'data': _trainer_detail(request, trainer, is_favourited)}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# GET /api/trainers/{trainer_id}/profile-image/
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="Get Trainer Profile Image",
    responses={
        200: OpenApiResponse(description="Profile image binary"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Client — Trainers"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trainer_profile_image_view(request, trainer_id):
    trainer = _get_approved_trainer_or_404(trainer_id)
    if not trainer:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)
    if not trainer.profile_image:
        return Response({'status': False, 'message': 'No profile image.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        name = trainer.profile_image.name or ''
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else 'jpeg'
        content_type = _IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
        trainer.profile_image.open('rb')
        data = trainer.profile_image.read()
        trainer.profile_image.close()
        return HttpResponse(data, content_type=content_type)
    except Exception:
        return Response({'status': False, 'message': 'Profile image not accessible.'}, status=status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# GET /api/trainers/{trainer_id}/certifications/
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="List Trainer Certifications",
    responses={
        200: OpenApiResponse(description="Certification metadata list with issuer and year"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Trainer not found"),
    },
    tags=["Client — Trainers"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trainer_certifications_view(request, trainer_id):
    trainer = _get_approved_trainer_or_404(trainer_id)
    if not trainer:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)

    certs = TrainerCertification.objects.filter(user=trainer).values(
        'id', 'name', 'issuer', 'year', 'content_type', 'created_at'
    )
    data = [
        {**c, 'image_url': _cert_image_url(request, trainer_id, c['id'])}
        for c in certs
    ]
    return Response({'status': True, 'data': data}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# GET /api/trainers/{trainer_id}/certifications/{cert_id}/
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="Get Trainer Certification Image",
    responses={
        200: OpenApiResponse(description="Certification image binary"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Client — Trainers"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trainer_certification_image_view(request, trainer_id, cert_id):
    trainer = _get_approved_trainer_or_404(trainer_id)
    if not trainer:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        cert = TrainerCertification.objects.get(id=cert_id, user=trainer)
    except TrainerCertification.DoesNotExist:
        return Response({'status': False, 'message': 'Certification not found.'}, status=status.HTTP_404_NOT_FOUND)
    return HttpResponse(bytes(cert.image), content_type=cert.content_type)


# ---------------------------------------------------------------------------
# GET /api/trainers/{trainer_id}/gallery/
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="List Trainer Gallery",
    responses={
        200: OpenApiResponse(description="Gallery image metadata list"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Trainer not found"),
    },
    tags=["Client — Trainers"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trainer_gallery_view(request, trainer_id):
    trainer = _get_approved_trainer_or_404(trainer_id)
    if not trainer:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)

    images = TrainerGalleryImage.objects.filter(user=trainer).values(
        'id', 'caption', 'collection_id', 'content_type', 'created_at'
    )
    data = [
        {
            **g,
            'collection_id': str(g['collection_id']) if g['collection_id'] else None,
            'image_url':     _gallery_image_url(request, trainer_id, g['id']),
        }
        for g in images
    ]
    return Response({'status': True, 'data': data}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# GET /api/trainers/{trainer_id}/gallery/{image_id}/
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="Get Trainer Gallery Image",
    responses={
        200: OpenApiResponse(description="Gallery image binary"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Client — Trainers"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trainer_gallery_image_view(request, trainer_id, image_id):
    trainer = _get_approved_trainer_or_404(trainer_id)
    if not trainer:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        img = TrainerGalleryImage.objects.get(id=image_id, user=trainer)
    except TrainerGalleryImage.DoesNotExist:
        return Response({'status': False, 'message': 'Image not found.'}, status=status.HTTP_404_NOT_FOUND)
    return HttpResponse(bytes(img.image), content_type=img.content_type)


# ---------------------------------------------------------------------------
# GET + POST /api/trainers/{trainer_id}/reviews/
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="List Trainer Reviews",
    responses={
        200: OpenApiResponse(description="Paginated list of reviews"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Trainer not found"),
    },
    tags=["Client — Trainers"],
)
@extend_schema(
    methods=['POST'],
    summary="Submit Trainer Review",
    request=TrainerReviewCreateSerializer,
    responses={
        201: OpenApiResponse(description="Review created"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Validation error"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Trainers cannot post reviews"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Trainer not found"),
        409: OpenApiResponse(response=MessageResponseSerializer, description="Already reviewed"),
    },
    tags=["Client — Trainers"],
)
@extend_schema(
    methods=['DELETE'],
    summary="Delete Trainer Review",
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Review deleted successfully"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="You can only delete your own reviews"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Review not found"),
    },
    tags=["Client — Trainers"],
)
@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def trainer_reviews_view(request, trainer_id, review_id=None):
    trainer = _get_approved_trainer_or_404(trainer_id)
    if not trainer:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        if not review_id:
            return Response(
                {'status': False, 'message': 'Review ID is required for deletion.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            review = TrainerReview.objects.get(id=review_id, trainer=trainer)
        except TrainerReview.DoesNotExist:
            return Response(
                {'status': False, 'message': 'Review not found.'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Verify the requester is the reviewer
        if review.reviewer_id != request.user.id:
            return Response(
                {'status': False, 'message': 'You can only delete your own reviews.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Delete the review
        review.delete()
        
        return Response(
            {'status': True, 'message': 'Review deleted successfully.'},
            status=status.HTTP_200_OK
        )

    if request.method == 'GET':
        reviews = TrainerReview.objects.filter(trainer=trainer).select_related('reviewer', 'booking')
        data = [
            {
                'id':          r.id,
                'booking_id':  r.booking_id,
                'reviewer_id': r.reviewer_id,
                'reviewer_name': (
                    r.reviewer.full_name or
                    f'{r.reviewer.first_name} {r.reviewer.last_name}'.strip() or
                    r.reviewer.username
                ),
                'reviewer_avatar': (
                    request.build_absolute_uri(f'/api/system/client/{r.reviewer_id}/profile-image/')
                    if r.reviewer.profile_image else None
                ),
                'rating':     r.rating,
                'comment':    r.comment,
                'created_at': r.created_at,
            }
            for r in reviews
        ]
        avg = round(sum(r['rating'] for r in data) / len(data), 1) if data else 0
        return Response({'status': True, 'count': len(data), 'average_rating': avg, 'data': data}, status=status.HTTP_200_OK)

    # POST
    if request.user.is_trainer:
        return Response({'status': False, 'message': 'Trainers cannot post reviews.'}, status=status.HTTP_403_FORBIDDEN)

    serializer = TrainerReviewCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    booking = get_object_or_404(
        Booking.objects.select_related('trainer', 'client'),
        id=serializer.validated_data['booking_id'],
        trainer=trainer,
        client=request.user,
    )

    if booking.status != Booking.STATUS_COMPLETED:
        return Response(
            {'status': False, 'message': 'Only completed bookings can be reviewed.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if TrainerReview.objects.filter(booking=booking, reviewer=request.user).exists():
        return Response({'status': False, 'message': 'You have already reviewed this booking.'}, status=status.HTTP_409_CONFLICT)

    rating = serializer.validated_data['rating']
    comment = serializer.validated_data['comment']

    review = TrainerReview.objects.create(
        trainer=trainer,
        reviewer=request.user,
        booking=booking,
        rating=rating,
        comment=comment,
    )
    return Response({
        'status': True,
        'data': {
            'id':         review.id,
            'booking_id': review.booking_id,
            'rating':     review.rating,
            'comment':    review.comment,
            'created_at': review.created_at,
        },
    }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# POST /api/trainers/{trainer_id}/favourite/  — toggle
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['POST'],
    summary="Toggle Trainer Favourite",
    responses={
        200: OpenApiResponse(description="Favourite removed"),
        201: OpenApiResponse(description="Favourite added"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Trainers cannot favourite"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Trainer not found"),
    },
    tags=["Client — Trainers"],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def toggle_favourite_view(request, trainer_id):
    if request.user.is_trainer:
        return Response({'status': False, 'message': 'Trainers cannot favourite other trainers.'}, status=status.HTTP_403_FORBIDDEN)

    trainer = _get_approved_trainer_or_404(trainer_id)
    if not trainer:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)

    fav, created = TrainerFavourite.objects.get_or_create(client=request.user, trainer=trainer)
    if not created:
        fav.delete()
        return Response({'status': True, 'is_favourited': False, 'message': 'Removed from favourites.'}, status=status.HTTP_200_OK)

    return Response({'status': True, 'is_favourited': True, 'message': 'Added to favourites.'}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# GET /api/favourites/  — list client's favourited trainers
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="List My Favourited Trainers",
    responses={
        200: OpenApiResponse(description="List of favourited trainers"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Trainers cannot use favourites"),
    },
    tags=["Client — Trainers"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_favourites_view(request):
    if request.user.is_trainer:
        return Response({'status': False, 'message': 'This endpoint is for clients only.'}, status=status.HTTP_403_FORBIDDEN)

    trainer_ids = TrainerFavourite.objects.filter(client=request.user).values_list('trainer_id', flat=True)
    trainers    = UserBase.objects.filter(id__in=trainer_ids, is_trainer=True, is_admin_approved=True, is_active=True)

    t_ids = [t.id for t in trainers]
    rating_qs  = (
        TrainerReview.objects.filter(trainer_id__in=t_ids)
        .values('trainer_id')
        .annotate(avg_rating=Avg('rating'), review_count=Count('id'))
    )
    rating_map  = {r['trainer_id']: r for r in rating_qs}
    favourited_ids = set(t_ids)   # all of these are favourited by definition

    data = [_trainer_list_item(request, t, favourited_ids, rating_map) for t in trainers]
    return Response({'status': True, 'count': len(data), 'data': data}, status=status.HTTP_200_OK)
