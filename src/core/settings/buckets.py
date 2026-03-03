from .environments import (
    AWS_ACCESS_KEY_ID,
    AWS_S3_REGION_NAME,
    AWS_SECRET_ACCESS_KEY,
    AWS_STORAGE_BUCKET_NAME,
    MEDIA_ROOT,
    MEDIA_URL,
    MINIO_ACCESS_KEY,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    STATIC_ROOT,
    STATIC_URL,
)

MINIO_CONFIGURED = all([MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY])

if AWS_STORAGE_BUCKET_NAME and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
            'OPTIONS': {
                'bucket_name': AWS_STORAGE_BUCKET_NAME,
                'access_key': AWS_ACCESS_KEY_ID,
                'secret_key': AWS_SECRET_ACCESS_KEY,
                'region_name': AWS_S3_REGION_NAME,
                'location': 'media',               # CHANGE THIS to your project folder name
                'querystring_auth': False,
                'file_overwrite': False,
                'object_parameters': {
                    'CacheControl': 'max-age=86400',
                },
            },
        },
        'staticfiles': {
            'BACKEND': 'storages.backends.s3boto3.S3StaticStorage',
            'OPTIONS': {
                'bucket_name': AWS_STORAGE_BUCKET_NAME,
                'access_key': AWS_ACCESS_KEY_ID,
                'secret_key': AWS_SECRET_ACCESS_KEY,
                'region_name': AWS_S3_REGION_NAME,
                'location': 'static',              # CHANGE THIS to your project folder name
                'querystring_auth': False,
                'object_parameters': {
                    'CacheControl': 'max-age=86400',
                },
            },
        },
    }
else:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }
    MEDIA_ROOT = MEDIA_ROOT
    STATIC_ROOT = STATIC_ROOT
    MEDIA_URL = MEDIA_URL
    STATIC_URL = STATIC_URL
