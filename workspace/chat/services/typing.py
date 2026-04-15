import time

from django.core.cache import cache

TYPING_TTL = 6  # seconds
TYPING_STALE = 4  # entries older than this are filtered out


def _cache_key(conversation_id):
    return f'chat:typing:{conversation_id}'


def set_typing(conversation_id, user_id, display_name):
    key = _cache_key(conversation_id)
    data = cache.get(key) or {}
    data[str(user_id)] = {'display_name': display_name, 'ts': time.time()}
    cache.set(key, data, TYPING_TTL)


def get_typing_users(conversation_ids, exclude_user_id=None):
    if not conversation_ids:
        return {}

    keys = {_cache_key(cid): cid for cid in conversation_ids}
    raw = cache.get_many(list(keys.keys()))

    now = time.time()
    result = {}
    exclude_str = str(exclude_user_id) if exclude_user_id is not None else None

    for cache_key, entries in raw.items():
        cid = keys[cache_key]
        users = []
        for uid_str, info in entries.items():
            if uid_str == exclude_str:
                continue
            if now - info['ts'] > TYPING_STALE:
                continue
            users.append({'user_id': uid_str, 'display_name': info['display_name']})
        if users:
            result[str(cid)] = users

    return result


def clear_typing(conversation_id, user_id):
    key = _cache_key(conversation_id)
    data = cache.get(key)
    if data and str(user_id) in data:
        del data[str(user_id)]
        if data:
            cache.set(key, data, TYPING_TTL)
        else:
            cache.delete(key)
