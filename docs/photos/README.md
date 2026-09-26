# Photos

The Photos module shows the photos and videos stored in Files as a timeline, by
the day they were taken. It is a preview module.

## Face detection and grouping

The **People** tab finds the faces in a user's photos and groups the ones that
look like the same person, so every photo of someone is one click away. Faces
are detected and compared on the server itself; no photo and no face is sent
anywhere.

A face embedding (the numeric description used to compare faces) is biometric
data, and the feature is built around that:

- **Opt-in twice.** The instance allows it (`PHOTOS_FACES_ENABLED=1`), and each
  user turns it on from the People tab, which explains what is computed before
  anything is.
- **Only the user's own photos.** Photos in group folders and photos shared by
  someone else are never read. A photo moved to a group folder loses its faces.
- **Turning it off deletes everything:** faces, groups, their vectors and the
  face pictures on storage. A nightly pass deletes whatever a lost purge left
  behind.

### Enabling it

```bash
PHOTOS_FACES_ENABLED=1
```

The analysis runs in the Celery worker, after the photo's capture date has been
read. The model weights download on the first analysis into
`PHOTOS_MODEL_DIR` (default: `models/` under `MEDIA_ROOT`) and are checked
against pinned SHA-256 hashes; a file that does not match is never loaded. To
ship them in the image instead, for a worker without internet access:

```bash
docker build --build-arg FACE_MODELS=yunet_sface .
# or, on a running installation:
python manage.py download_face_models
```

The admin dashboard shows a **Face detection** card: whether the backend is
known, and whether its weights are there and intact. A misspelt
`PHOTOS_FACE_BACKEND` shows there as an error; it never silently switches the
feature off.

### Backends

| `PHOTOS_FACE_BACKEND` | Models | Embedding | Weights licence |
|---|---|---|---|
| `yunet_sface` (default) | YuNet + SFace (OpenCV Zoo) | 128-d | MIT / Apache-2.0 |
| `scrfd_arcface` | SCRFD + ArcFace (InsightFace `buffalo_l`) | 512-d | **Non-commercial research only** |

`scrfd_arcface` is more accurate on hard faces (profiles, small faces in group
photos), but InsightFace licenses its pretrained weights for non-commercial
research only: an instance run by or for a company must not enable it. Both
run on the CPU through onnxruntime.

**Switching backend** changes the embedding space, and `scrfd_arcface` changes
its size too. After changing the setting:

1. `python manage.py rebuild_vector_index` recreates the face vector index at
   the new size.
2. Every photo counts as pending again (the analysis records which backend read
   it) and is read again by the hourly catch-up, or at once with
   `python manage.py catch_up faces --reanalyze`.

A reanalysis keeps what users corrected: a new face found at the place of an old
one inherits its group and its confirmation.

### Settings

| Setting | Default | Effect |
|---|---|---|
| `PHOTOS_FACES_ENABLED` | off | Offer face grouping on this instance. |
| `PHOTOS_FACE_BACKEND` | `yunet_sface` | Detection and embedding models, see above. |
| `PHOTOS_MODEL_DIR` | `MEDIA_ROOT/models` | Where the weights are downloaded. |
| `PHOTOS_ONNX_THREADS` | `1` | Threads per model. Kept at 1 so several worker processes do not oversubscribe one host. |
| `PHOTOS_FACES_DECODE_SIZE` | `1600` | Longest side, in px, of the copy detection runs on. |
| `PHOTOS_FACES_MAX_FILE_BYTES` | 64 MB | Larger originals are not read for faces. |
| `PHOTOS_FACES_MIN_SIZE` | `24` | Faces smaller than this (px, at the decode size) are dropped. |
| `PHOTOS_FACES_MAX_PER_PHOTO` | `40` | The most confident faces kept per photo. |
| `PHOTOS_FACES_MAX_DISTANCE` | backend's own | Cosine distance under which two faces count as one person (0.58 for SFace, 0.6 for ArcFace). |
| `PHOTOS_FACES_CLUSTER_PENDING` | `50` | Ungrouped faces that trigger a grouping run before the nightly one. |

### How grouping works

- Right after a photo is analyzed, each new face joins the group its nearest
  neighbours vote for, if they are close enough.
- Nightly (and once enough faces wait), the faces still ungrouped are
  clustered. Two faces of one photo are never put in the same group; the
  database refuses it too. Blurred or tiny faces never start a group, and a
  single clear face of someone is a group of its own.
- Corrections always win: a face the user placed is never moved, and a face
  the user took out ("Not this person", "Ungroup") is never grouped again
  automatically.

Photos analyzed by the pipeline are listed, read-only, in the admin under
*Face analyses*; faces and groups themselves are not browsable there.
