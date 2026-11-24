# Synology NAS

Workspace installs on a Synology NAS as a package of Package Center. The package
is a [Container Manager](https://www.synology.com/dsm/feature/container-manager)
project: it ships no program of its own, it pulls the Workspace image published
on `ghcr.io` and runs it with SQLite, the same way the
[Docker Compose](../docker-compose/) deployment does.

## Architecture

```
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ prepare  │ │   web    │ │  worker  │ │   beat   │ │  redis   │
│ one-shot │ │ gunicorn │ │  celery  │ │ schedule │ │ broker,  │
│ at start │ │ :8765    │ │          │ │          │ │ sessions │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────────┘
     └────────────┴─────┬──────┴────────────┘
                        │
            shared folder "workspace"
       (SQLite, user files, workspace.env)
```

| Container           | Role |
|---------------------|------|
| `workspace-prepare` | Runs at every start, then exits: gives the shared folder to the application's user, writes the settings files on first run, applies the database migrations and creates the administrator typed in the install wizard |
| `workspace-web`     | The web application, published on port **8765** of the NAS |
| `workspace-worker`  | Background tasks (thumbnails, mail sync, photo analysis...) |
| `workspace-beat`    | Schedules the periodic tasks |
| `workspace-redis`   | Task queue, cache and sessions |

## Requirements

- DSM **7.2.1** or later, with **Container Manager** installed (Package Center
  installs it with the package when it is missing).
- A NAS with an **x86-64** or **arm64** processor: the image is published for
  these two. Container Manager itself is only offered on such models.
- About **1.5 GB of free memory**. Photo analysis takes more while it runs.

## Install

1. Download `workspace-<version>-1.spk` from the
   [releases](https://github.com/JaaJSoft/workspace/releases).
2. In **Package Center**, click **Manual Install** and pick the file. The package
   is not signed by Synology: DSM asks for confirmation before installing a
   third-party package (or, depending on the DSM version, needs
   **Settings > General > Trust level > Any publisher**).
3. The wizard asks for the administrator account Workspace creates on its first
   start (username, email, password of at least 8 characters).
4. When the installation ends, the first start downloads the container images:
   it takes a few minutes. Its progress shows in **Container Manager > Project >
   workspace**.

Workspace then answers on `http://<nas-address>:8765`, and an entry in the DSM
main menu opens it. Sign in with the administrator account, then create the
other accounts from the administration.

## Settings

The package creates a `workspace` shared folder. Workspace keeps everything
there:

| Path | Content |
|------|---------|
| `workspace.env` | The settings an administrator edits |
| `.secrets.env`  | `SECRET_KEY` and the Web Push keys, generated on first start. Changing `SECRET_KEY` signs everyone out; changing the Web Push keys drops every browser subscription |
| `db.sqlite3`    | The database |
| `files/`, ...   | User files, thumbnails, avatars, attachments |

To change a setting, edit `workspace.env` (File Station and Text Editor do it),
then **stop** and **run** the package again in Package Center. The file lists
the common settings, commented out; every variable is documented in
[Deployment](../README.md) and [Docker Compose](../docker-compose/README.md#configuration).
It is a shell file: `KEY=value`, one per line, a value holding a space or a
special character between single quotes.

## HTTPS

Out of the box Workspace is served over plain HTTP, which is fine on a home
network and nowhere else. To publish it, put the reverse proxy of DSM in front:

1. **Control Panel > Login Portal > Advanced > Reverse Proxy > Create**: source
   `HTTPS`, your hostname (`workspace.example.com`), port `443`; destination
   `HTTP`, `localhost`, port `8765`.
2. Give that hostname a certificate (**Control Panel > Security > Certificate**,
   Let's Encrypt for instance).
3. In `workspace.env`, uncomment and fill in the **HTTPS** block
   (`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SESSION_COOKIE_SECURE=1`,
   `CSRF_COOKIE_SECURE=1`, `NUM_PROXIES=1`), then restart the package.

Once HTTPS is set up, block port 8765 from outside the local network (**Control
Panel > Security > Firewall**): only the reverse proxy should reach it. See
[Reverse Proxy](../README.md#reverse-proxy) for why.

## Back up

Back up the `workspace` shared folder with Hyper Backup or a snapshot. SQLite
writes through a journal next to the database, so a copy taken while Workspace
runs may miss the last writes: stop the package for the backup, or rely on a
Btrfs snapshot, which captures the database and its journal at the same instant.

Leave the shared folder out of the indexed folders of Synology Photos and
Universal Search: indexing writes `@eaDir` folders among the user files.

## Upgrade

Install the `.spk` of the new version over the old one, the same way. The
containers are recreated on the new image and the database migrations run on
the first start. The shared folder is kept.

## Uninstall

Uninstalling removes the containers and the Container Manager project. The
`workspace` shared folder stays, with every file and the database: delete it in
**Control Panel > Shared Folder** if you do not want to keep it.

## Troubleshooting

**Container Manager > Project > workspace** lists the five containers and their
logs. When the package fails to start, the log of `workspace-prepare` is the one
to read first: a migration that failed stops it, and the other containers wait
for it.

## Building the package

The [Synology package workflow](../../../.github/workflows/synology.yml) attaches
the package to the GitHub release of every `v*` tag, and builds it as a workflow
artifact for a change to `packaging/synology/`. To build it by hand:

```bash
uv run --no-project --with pillow python packaging/synology/build.py \
    --version 0.43.0 --image ghcr.io/jaajsoft/workspace:0.43.0
```

The `.spk` lands in `dist/`. `--build 2` republishes the same version, for a
package change alone.
