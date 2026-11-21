#!/bin/sh
# Entrypoint of every Workspace container of the Synology package.
#
#   prepare  one-shot, as root: hand the shared folder to the image's user,
#            write the settings files on first run, migrate, create the
#            administrator entered in the install wizard
#   run      load the settings files, then start what PROCESS_TYPE names
#
# Settings come from two files of the shared folder, read at every start:
#   .secrets.env   generated once (SECRET_KEY, Web Push keys), never edited
#   workspace.env  the administrator's settings, loaded last so it wins
set -eu

DATA=/app/data
SECRETS_FILE="$DATA/.secrets.env"
SETTINGS_FILE="$DATA/workspace.env"
SETTINGS_TEMPLATE=/opt/synology/workspace.env.template
# Written by the package's postinst from the install wizard, removed as soon
# as the account exists: it holds a password in clear.
ADMIN_BOOTSTRAP="$DATA/.admin-bootstrap"
APP_USER=appuser

load_settings() {
    for file in "$SECRETS_FILE" "$SETTINGS_FILE"; do
        if [ -r "$file" ]; then
            set -a
            # shellcheck source=/dev/null
            . "$file"
            set +a
        fi
    done
}

as_app_user() {
    HOME="/home/$APP_USER" setpriv \
        --reuid "$(id -u "$APP_USER")" --regid "$(id -g "$APP_USER")" --init-groups \
        -- "$@"
}

# The shared folder is created by DSM, owned by the package's user, and files
# copied in through File Station belong to whoever copied them. The app runs
# as the image's user and needs to own all of it. DSM's own metadata folders
# are left alone.
take_ownership() {
    find "$DATA" -xdev \
        \( -name '@eaDir' -o -name '#recycle' -o -name '#snapshot' \) -prune \
        -o \( ! -user "$APP_USER" -o ! -group "$APP_USER" \) \
        -exec chown -h "$APP_USER:$APP_USER" {} +
}

write_secrets() {
    [ -s "$SECRETS_FILE" ] && return 0
    echo "synology: generating $SECRETS_FILE"
    secret_key=$(python -c 'import secrets; print(secrets.token_urlsafe(50))')
    # As the app user: loading the settings opens the database and creates
    # folders under the media root, which root would then own. They also want
    # a key of their own.
    vapid_keys=$(SECRET_KEY="$secret_key" as_app_user python manage.py generate_vapid_keys \
        | grep '^WEBPUSH_VAPID_')
    umask 077
    {
        echo "# Generated on first start. Changing SECRET_KEY signs everyone out;"
        echo "# changing the Web Push keys drops every browser subscription."
        echo "SECRET_KEY='$secret_key'"
        echo "$vapid_keys"
    } > "$SECRETS_FILE.tmp"
    mv "$SECRETS_FILE.tmp" "$SECRETS_FILE"
    chown "$APP_USER:$APP_USER" "$SECRETS_FILE"
}

write_settings() {
    [ -e "$SETTINGS_FILE" ] && return 0
    echo "synology: writing $SETTINGS_FILE"
    cp "$SETTINGS_TEMPLATE" "$SETTINGS_FILE"
    chown "$APP_USER:$APP_USER" "$SETTINGS_FILE"
    chmod 0644 "$SETTINGS_FILE"
}

create_admin() {
    [ -e "$ADMIN_BOOTSTRAP" ] || return 0
    username='' email='' password=''
    {
        IFS= read -r username || true
        IFS= read -r email || true
        IFS= read -r password || true
    } < "$ADMIN_BOOTSTRAP"
    if [ -n "$username" ] && [ -n "$password" ]; then
        echo "synology: creating the administrator account '$username'"
        if ! DJANGO_SUPERUSER_PASSWORD="$password" as_app_user \
            python manage.py createsuperuser --noinput \
            --username "$username" --email "${email:-$username@localhost}"; then
            echo "synology: the account was not created (it may already exist)" >&2
        fi
    fi
    rm -f "$ADMIN_BOOTSTRAP"
}

case "${1:-run}" in
prepare)
    take_ownership
    write_secrets
    write_settings
    load_settings
    as_app_user python manage.py migrate --noinput
    create_admin
    ;;
run)
    load_settings
    exec ./run.sh
    ;;
*)
    echo "synology.sh: unknown command '$1', expected prepare or run" >&2
    exit 1
    ;;
esac
