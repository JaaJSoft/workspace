#!/bin/sh
# Starts one Workspace process. PROCESS_TYPE picks which, so every container of
# a deployment runs this same command and differs only by its environment:
#
#   web     gunicorn serving the Django app (the default)
#   worker  a Celery worker; with CELERY_WORKER_BEAT=1 it also runs the
#           scheduler, which spares a single-node deployment the beat process
#   beat    the Celery scheduler, exactly one per deployment
#
# No option needs forwarding from here. Celery reads every option of
# `celery worker` from CELERY_WORKER_<OPTION> and of `celery beat` from
# CELERY_BEAT_<OPTION> (CELERY_WORKER_AUTOSCALE=8,2 is --autoscale=8,2), and
# gunicorn takes extra flags from GUNICORN_CMD_ARGS.
set -eu

# glibc raises its mmap threshold to the size of each mapped block it frees, up
# to 32 MiB, after which large buffers (a file read whole, a decoded image) are
# carved out of the heap and stay there once freed. A fixed threshold keeps
# them mapped, so freeing one hands the memory back.
export MALLOC_MMAP_THRESHOLD_="${MALLOC_MMAP_THRESHOLD_:-131072}"

access_log_format='%(h)s %(l)s %(u)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

case "${PROCESS_TYPE:-web}" in
web)
    exec gunicorn workspace.wsgi:application -c gunicorn.conf.py \
        --bind "${GUNICORN_BIND:-0.0.0.0:8000}" \
        --worker-class gevent \
        --workers "${GUNICORN_WORKERS:-2}" \
        --log-level "${GUNICORN_LOG_LEVEL:-info}" \
        --error-logfile - \
        --access-logfile - \
        --access-logformat "${GUNICORN_ACCESS_LOGFORMAT:-$access_log_format}" \
        --capture-output
    ;;
worker)
    exec celery -A workspace worker --loglevel "${CELERY_WORKER_LOGLEVEL:-info}"
    ;;
beat)
    exec celery -A workspace beat --loglevel "${CELERY_BEAT_LOGLEVEL:-info}"
    ;;
*)
    echo "run.sh: unknown PROCESS_TYPE '$PROCESS_TYPE', expected web, worker or beat" >&2
    exit 1
    ;;
esac
