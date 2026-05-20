import hashlib

from django.core.cache import cache


TRAINER_CACHE_TIMEOUT = 60 * 5
TRAINER_CACHE_VERSION_KEY = 'trainer_listing:version'


def get_trainer_cache_version():
    version = cache.get(TRAINER_CACHE_VERSION_KEY)
    if version is None:
        version = 1
        cache.set(TRAINER_CACHE_VERSION_KEY, version, None)
    return version


def make_trainer_cache_key(scope, *parts):
    raw = ':'.join(str(part) for part in parts)
    digest = hashlib.md5(raw.encode('utf-8')).hexdigest()
    return f'trainer_listing:v{get_trainer_cache_version()}:{scope}:{digest}'


def invalidate_trainer_cache():
    version = get_trainer_cache_version()
    cache.set(TRAINER_CACHE_VERSION_KEY, version + 1, None)
