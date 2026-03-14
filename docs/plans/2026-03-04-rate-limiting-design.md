# Rate Limiting Global — Design

## Contexte

Le projet n'a pas de rate limiting global. Seul l'endpoint `SharedPollVoteView` a un rate limiting manuel (10 votes/heure/IP via le cache Django). Il faut protéger toutes les API et pages publiques contre les abus.

## Décision

Utiliser **`django-ratelimit`** comme solution unifiée pour les vues DRF et Django classiques, avec le cache Django (Redis) comme backend.

## Gestion des proxies

- Production derrière **Nginx** (reverse proxy)
- `RATELIMIT_IP_META_KEY = "HTTP_X_FORWARDED_FOR"`
- `django-ratelimit` parse automatiquement le premier IP du header (client réel)

## Limites

| Catégorie | Limite | Clé |
|-----------|--------|-----|
| Anonymes (global) | 60/min, 500/heure | IP (via X-Forwarded-For) |
| Authentifiés (global) | 300/min, 3000/heure | User ID |
| Endpoints sensibles (vote, create, invite) | 30/min | IP ou User ID |

## Comportement au dépassement (429)

- **API** : réponse JSON `{"detail": "Too many requests. Please try again later."}` + header `Retry-After`
- **Pages HTML** : template `429.html` custom stylé avec le design du site

## Composants

1. Ajouter `django-ratelimit` aux dépendances (`pyproject.toml`)
2. Settings `RATELIMIT_*` dans `settings.py`
3. Décorateurs `@ratelimit` sur les vues publiques et sensibles
4. Helper/mixin pour centraliser la config des rates
5. Template `429.html` custom
6. Handler DRF pour réponses 429 JSON avec `Retry-After`
7. Supprimer le rate limiting manuel dans `SharedPollVoteView`
8. Tests
