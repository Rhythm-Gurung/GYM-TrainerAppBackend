from os import getenv, path
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(path.join(BASE_DIR, ".env"))
SECRET_KEY = getenv("SECRET_KEY")

channel = getenv("CHANNEL")

DEBUG = True
PREFIX_KEY = getenv('PREFIX_KEY')
STABLE = getenv('STABLE')

# Database
DB_USERNAME = getenv("DB_USER")
DB_NAME = getenv("DB_NAME")
DB_PASSWORD = getenv("DB_PASSWORD")
DB_HOST = getenv("DB_HOST")

# Email
EMAIL_HOST_USER = getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = getenv('EMAIL_HOST_PASSWORD')
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = "email-smtp.ap-south-1.amazonaws.com"  # CHANGE THIS if using Gmail: "smtp.gmail.com"
DEFAULT_FROM_EMAIL = getenv('DEFAULT_FROM_EMAIL')
EMAIL_PORT = 587

# Redis
REDIS_HOST = getenv('REDIS_HOST')
REDIS_USERNAME = getenv("REDIS_USERNAME")
REDIS_PASSWORD = getenv("REDIS_PASSWORD")

MEDIA_ROOT = path.join(BASE_DIR, 'media')
STATIC_ROOT = path.join(BASE_DIR, 'staticfiles')
MEDIA_URL = '/media/'
STATIC_URL = '/static/'

MINIO_ENDPOINT = getenv('MINIO_ENDPOINT', 'localhost:9000')
MINIO_ACCESS_KEY = getenv('MINIO_ACCESS_KEY')
MINIO_SECRET_KEY = getenv('MINIO_SECRET_KEY')

GOOGLE_OAUTH_CLIENT_ID = getenv('GOOGLE_OAUTH_CLIENT_ID')
GOOGLE_OAUTH_CLIENT_SECRET = getenv('GOOGLE_OAUTH_CLIENT_SECRET')
GOOGLE_OAUTH_ANDROID_CLIENT_ID = getenv('GOOGLE_OAUTH_ANDROID_CLIENT_ID')
GOOGLE_OAUTH_IOS_CLIENT_ID = getenv('GOOGLE_OAUTH_IOS_CLIENT_ID')

AWS_ACCESS_KEY_ID = getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = getenv('AWS_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = getenv('AWS_STORAGE_BUCKET_NAME')
AWS_S3_REGION_NAME = getenv('AWS_S3_REGION_NAME')
