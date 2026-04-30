"""Prometheus metrics for the files app.

All metric names in this file MUST start with "files_".
"""

from prometheus_client import Counter, Histogram

_P = 'files'

FILES_UPLOAD_BYTES = Counter(
    f'{_P}_upload_bytes_total',
    'Bytes of file content received from clients',
)

FILES_DOWNLOAD_BYTES = Counter(
    f'{_P}_download_bytes_total',
    'Bytes of file content sent to clients',
)

FILES_THUMBNAIL_DURATION = Histogram(
    f'{_P}_thumbnail_generation_duration_seconds',
    'Wall-clock time of one generate_thumbnail() call, by source mime family',
    ['mime_family'],
)

FILES_THUMBNAIL_RESULT = Counter(
    f'{_P}_thumbnail_generation_total',
    'Thumbnail generation outcomes (success/failed/skipped)',
    ['result'],
)
