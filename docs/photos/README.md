# Photos

The Photos module shows the photos and videos stored in Files as a timeline, by
the day they were taken. It is a preview module.

## Adding photos

Every photo and video a user keeps in Files is in their library, whatever
folder it sits in. There are three ways to add more:

- **Import** in the Photos header (or on the empty timeline) opens the file
  picker. **Dropping** photos and videos anywhere on the listing does the same.
  Other kinds of files are skipped. A name already taken in the folder keeps
  both files, so two cameras' `IMG_0001.JPG` never replace each other.
- **The import folder** is where both land. It defaults to a root folder named
  `Pictures`, created on the first import (an existing one is reused). The
  Preferences panel of the Photos sidebar changes it to any personal or group
  folder.
- **Phone backup over WebDAV.** The same panel shows the WebDAV address of the
  import folder (`https://<your-domain>/dav/Pictures/` by default). Point a sync
  app at it - PhotoSync or FolderSync on Android, PhotoSync on iOS - with the
  account's username and password (or an API token, from **Settings > API
  Tokens**, when the account signs in through single sign-on). New photos then
  reach the timeline on their own, once their capture date has been read.

## Capture dates

A photo is placed on the timeline by its EXIF capture date, a video by its
container's recording date. When the file carries none - a screenshot, a
picture sent through a messaging app, a scan - the date written in its name
stands in: `IMG_20230415_143022.jpg`, `WhatsApp Image 2023-04-15 at 14.30.22.jpeg`,
`Screenshot 2023-04-15 at 14.30.22.png`, `1998-07-12 Wedding.jpg`... The year
must come first; a name giving only the day reads as noon on that day.
Renaming such a file moves it on the timeline, so an undated scan can be filed
by renaming it. A date read from the file itself always wins over the name.

## Similar photos and search by description

One image embedding per photo - a vector saying what the photo shows, in a
space a sentence can be embedded into too - gives three features:

- **Similar photos**, from a photo's menu: the photos of the library on screen
  that look like it, the **near-duplicates** first (the shots of one burst, a
  re-export), then up to 30 of the merely similar ones.
- **Search by description**, from the search field in the Photos header:
  "dog on the beach", "snow", "birthday cake" ranked against the library,
  with no tag and no name to match. The global search offers it too, as
  *Photos showing "..."*.
- Both follow the library tab on screen (Mine, All, Shared with me, a group),
  and never show a photo the user cannot open: trashed, quarantined, hidden
  and unshared photos stay out.

This is not a way to recognize people: a whole-photo vector describes the
scene, not who is in it. Identity stays with face grouping above.

### Enabling it

```bash
PHOTOS_EMBEDDINGS_ENABLED=1
```

It is an instance-level setting, with no per-user opt-in: every photo of the
instance is embedded, group folders' included. Videos are not, for now. The
embedding runs in the Celery worker, at low priority after an upload and at
background priority for the hourly catch-up (`python manage.py catch_up
embeddings`). A search embeds its text in the worker too, at interactive
priority: the web process never loads a model. A search waits
`PHOTOS_EMBEDDING_QUERY_TIMEOUT` seconds for the answer, then says it could
not run.

The admin dashboard shows a **Photo search** card: whether the backend is
known and ready. Embedded photos are listed, read-only, in the admin under
*Photo embeddings*.

### Backends

| `PHOTOS_EMBEDDING_BACKEND` | Model | Embedding | Weights licence |
|---|---|---|---|
| `siglip2_base` (default) | Google SigLIP 2 base, on the CPU, multilingual | 768-d | Apache-2.0 |
| `clip_vit_b32` | OpenAI CLIP ViT-B/32, on the CPU | 512-d | MIT |
| `jina_clip_v1` | Jina CLIP v1, on the CPU | 768-d | Apache-2.0 |
| `remote` | Any multimodal model behind an OpenAI-compatible `/embeddings` endpoint | `PHOTOS_EMBEDDING_DIMS` | The model's |

**The local backends** run on the CPU through
[fastembed](https://github.com/qdrant/fastembed), which ships each model's
preprocessing, tokenizer and ONNX runtime. The
[embedding bench](embedding-bench.md) measures all three on the same photos:
SigLIP 2, the default, finds the best photos for a description, and the
only ones worth showing for a query typed in French (75% mAP, against 49% for
CLIP). CLIP ViT-B/32 costs a quarter of its CPU per photo (88 ms against
315 ms on one desktop core) and a third of its memory, for a host that cannot
spare them. Jina CLIP v1 costs as much CPU as SigLIP 2 for less. The weights
download from the Hugging Face Hub on first use into
`PHOTOS_MODEL_DIR/fastembed`, or ahead of time, for a worker without internet
access:

```bash
python manage.py download_embedding_models
```

CLIP's 4.4 GFLOPs a photo get a Raspberry Pi 4 through a library of twenty
thousand photos in a night; SigLIP 2 takes about 3.6 times as long. Each
worker process holds the image encoder in memory (and the text encoder once it
has served a search, 1.1 GB for SigLIP 2's multilingual vocabulary): raise
`CELERY_WORKER_MAX_MEMORY_PER_CHILD` to at least 2.5 GB (2621440) for SigLIP 2,
1.5 GB (1572864) for CLIP, or the default 512 MB recycles the worker after
every photo.

**`remote`** sends each photo, as a JPEG downscaled to
`PHOTOS_EMBEDDING_REMOTE_IMAGE_SIZE` px (never the original), to another
service: **every photo of the instance leaves the server.** Only enable it
for an endpoint the instance's users can trust with their photos. There is no
standard for image input on `/embeddings`; the contract is the most common
one:

```json
{"model": "<PHOTOS_EMBEDDING_MODEL>", "input": ["data:image/jpeg;base64,..."], "encoding_format": "float"}
```

for a photo, and the plain text in `input` for a search. The model must embed
both into one space (a CLIP, SigLIP or Jina CLIP model), and answer with
vectors of `PHOTOS_EMBEDDING_DIMS` components.

```bash
PHOTOS_EMBEDDING_BACKEND=remote
PHOTOS_EMBEDDING_BASE_URL=http://litellm:4000/v1
PHOTOS_EMBEDDING_MODEL=jina-clip-v2
PHOTOS_EMBEDDING_DIMS=1024
PHOTOS_EMBEDDING_API_KEY=...   # falls back to AI_API_KEY
```

**Switching backend or model** changes the embedding space, and often its
size. Every embedding records the model that wrote it, so every photo counts
as pending again; vectors of the old model are never compared with the new
ones. After changing the setting:

1. `python manage.py rebuild_vector_index` recreates the vector index at the
   new size (only needed when the size changes).
2. The hourly catch-up embeds the library again, or at once:
   `python manage.py catch_up embeddings --reanalyze`.

### Automatic tags

Each embedded photo is tagged with what it shows, from a vocabulary of 33
classic tags (`workspace/photos/services/tag_vocabulary.py`: dog, beach,
snow, cake, wedding, screenshot...). The tags are the owner's ordinary file
tags: an existing tag of the same name is reused (case-insensitively), a
missing one is created the first time a photo gets it. They show in Files,
filter the Photos timeline and can be taken off like any other; the
properties panel marks them with a sparkle.

- Each backend places only the tags it tells apart well, each under its own
  threshold (`tag_thresholds` in `workspace/photos/services/embedding/local.py`),
  fitted by the [embedding bench](embedding-bench.md) for 90% precision on
  photos it never saw. SigLIP 2 places 24 of the 33 tags and finds about a
  third of the photos each one describes; CLIP places 13. The `remote`
  backend has no table, so it places none until one is measured.
- A photo is tagged once, after it is embedded: a tag its owner takes off
  stays off. A new version of the photo, or a new model, tags it again.
- Only personal photos: a tag belongs to its owner, and a group folder's
  photo is never tagged.
- Users turn it off with *Tag photos automatically* in the Photos
  preferences; the photos embedded meanwhile are not tagged later.
- The tagging runs in the worker right after the embedding, and the hourly
  catch-up (`python manage.py catch_up auto_tags`) tags the photos embedded
  before it existed. It embeds the vocabulary once per model, which loads the
  text encoder in the worker.

### Settings

| Setting | Default | Effect |
|---|---|---|
| `PHOTOS_EMBEDDINGS_ENABLED` | off | Embed the photos of this instance. |
| `PHOTOS_EMBEDDING_BACKEND` | `siglip2_base` | The embedding model, see above. |
| `PHOTOS_EMBEDDING_BASE_URL`, `PHOTOS_EMBEDDING_MODEL`, `PHOTOS_EMBEDDING_API_KEY` | empty | The remote endpoint, its model, its key. |
| `PHOTOS_EMBEDDING_DIMS` | `512` | Size of the remote model's vectors. |
| `PHOTOS_EMBEDDING_REMOTE_IMAGE_SIZE` | `512` | Longest side, in px, of the copy sent to the remote backend. |
| `PHOTOS_EMBEDDING_TIMEOUT` | `30` | Seconds per remote request. |
| `PHOTOS_EMBEDDING_MAX_FILE_BYTES` | 64 MB | Larger originals are not embedded. |
| `PHOTOS_EMBEDDING_QUERY_TIMEOUT` | `15` | Seconds a search waits for the worker to embed its text. |
| `PHOTOS_SIMILAR_MAX_DISTANCE` | backend's own | Cosine distance under which a photo is similar (0.3 for SigLIP 2, 0.25 for CLIP, 0.3 for Jina CLIP). |
| `PHOTOS_DUPLICATE_MAX_DISTANCE` | backend's own | Cosine distance under which it is a near-duplicate (0.07 for SigLIP 2, 0.075 for CLIP, 0.085 for Jina CLIP). |
| `PHOTOS_DESCRIPTION_MAX_DISTANCE` | backend's own | Cosine distance past which a photo does not match a search (0.92 for SigLIP 2, 0.765 for CLIP, 0.89 for Jina CLIP). |

Each local model's defaults are calibrated by the
[embedding bench](embedding-bench.md), on its own embedding space: the same
distance means a different thing to CLIP and to SigLIP 2. A remote model starts
from generic defaults (0.25, 0.06, 0.8); measure it with the bench
(`--backend remote`) before relying on them.

## Sensitive photos

A sensitive photo (nudity, a medical photo) is **blurred wherever it shows as
a thumbnail**: the timeline, albums and their covers, search and similar
photos, a contact's photos in People, the Files mosaic and public share
pages. Opening it shows a *Sensitive content* cover over the viewer, with a
**Show** button; a covered video does not start playing behind it.

- The photo carries a sensitivity level, from 0 (nothing to hide) to 4, not a
  tag of its owner's: everyone who can open it gets the same level, in a share
  or a shared album too.
- Each user chooses how much of it is blurred on their own screen, with the
  **Blur sensitive photos** slider of the Photos preferences, in five steps:

  | Step | Blurs the photos of level | With the default detector, a score of |
  |---|---|---|
  | Strict | 1 and up | 0.25 or more |
  | High | 2 and up | 0.35 or more |
  | Standard (default) | 3 and up | 0.5 or more |
  | Low | 4 | 0.9 or more |
  | Off | none, photos marked by hand included | |

  A visitor of a public link always gets Standard, whoever is signed in.
- Anyone with edit rights on a photo or a video marks it, or unmarks it, from
  its menu in Photos or in Files, or for a whole selection: a marked photo
  goes to level 4, blurred under every step but Off, an unmarked one to 0.
  That choice wins over detection, for good. The menu offers to unmark any
  photo above level 0, blurred on the user's own screen or not.
- Two options below the slider soften the blur without turning it off, both
  off by default and for the user's own screen too. **Unblur on hover** shows a
  thumbnail in clear while the pointer rests on it (a touch screen has no
  hover, so the option is not offered there). **Show in the viewer without
  asking** opens a sensitive photo or video straight away, with no cover. Both
  apply in Files as well; a public link gets neither.

### Detection

```bash
PHOTOS_SENSITIVE_DETECTION_ENABLED=1
```

Off by default, like the other models: without it, photos are only marked by
hand. When on, every photo of the instance is checked in the Celery worker,
at normal priority after an upload (the photo shows in clear until then) and
at background priority for the hourly catch-up (`python manage.py catch_up
sensitive`). Videos are not checked yet.

The default detector, `falconsai_vit`, is
[Falconsai's NSFW classifier](https://huggingface.co/Falconsai/nsfw_image_detection)
(a ViT-base, Apache-2.0) run from its int8 ONNX export: 87 MB of weights,
about 120 ms a photo on one desktop core. It was trained on nudity: a medical
or a gory photo is marked by hand. Its weights download on first use into
`PHOTOS_MODEL_DIR`, or ahead of time:

```bash
python manage.py download_sensitive_models
```

The detector gives every photo a score from 0 to 1, and the photo's level is
the number of steps whose threshold that score reaches. It is not a probability:
the model card defines neither of its two classes, `normal` and `nsfw`, nor
what a score of 0.2 or 0.8 means. Its one published figure is 98% accuracy on
its own 80,000 images, which are not public, taken at the model's decision
boundary of 0.5. That is Standard's threshold, the only one anyone measured.
On 1,971 Open Images photos, none of them explicit, it blurs 9 (0.5%); a
threshold of 0.2 would blur 11 (a bikini, a kiss, a mannequin...), with no
measured gain on explicit photos. Strict's 0.25, High's 0.35 and Low's 0.9 sit
between those figures and are not measured on their own. There
is no explicit evaluation set in the repository: recall has not been measured
on this instance's terms.

The thresholds belong to the detector (`thresholds` in
`photos/services/sensitivity/`). When they change, the photos already checked
get their new level on the next catch-up, from the scores they were given: the
model does not run again, and a photo marked or unmarked by hand keeps that
choice.

The admin dashboard shows a **Sensitive photos** card: whether the detector
is known and ready.

### Settings

| Setting | Default | Effect |
|---|---|---|
| `PHOTOS_SENSITIVE_DETECTION_ENABLED` | off | Check the photos of this instance for sensitive content. |
| `PHOTOS_SENSITIVE_BACKEND` | `falconsai_vit` | The detector. |
| `PHOTOS_SENSITIVE_MAX_FILE_BYTES` | 64 MB | Larger originals are not checked. |

## Face detection and grouping

The **People** tab finds the faces in a user's photos and videos and groups the
ones that look like the same person, so every photo and video of someone is one
click away. Faces
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

The analysis runs in the Celery worker, after the photo's or the video's capture
date has been read. The model weights download on the first analysis into
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
| `yunet_adaface` | YuNet + AdaFace IR-50 | 512-d | MIT, **trained on a non-commercial dataset** |
| `scrfd_arcface` | SCRFD + ArcFace (InsightFace `buffalo_l`) | 512-d | **Non-commercial research only** |

`yunet_adaface` keeps the default's detector and groups faces about as well as
`scrfd_arcface`, for a quarter of its CPU: about twice the default's. Its
weights are published under MIT, but they were trained on WebFace4M, which is
licensed for non-commercial research only, and whether that reaches the model
is not settled: an instance run by or for a company should not enable it
without checking. `scrfd_arcface` is the most accurate on hard faces
(profiles, small faces in group photos), but InsightFace licenses its
pretrained weights for non-commercial research only: an instance run by or for
a company must not enable it. All three run on the CPU through onnxruntime.
The [face bench](face-bench.md) measures them on real photo libraries; run it
to judge any change to detection or grouping.

**Switching backend** changes the embedding space, and both 512-d backends
change its size too. After changing the setting:

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
| `PHOTOS_FACES_MAX_DISTANCE` | backend's own | Cosine distance under which two faces count as one person (0.58 for SFace, 0.6 for AdaFace and ArcFace). |
| `PHOTOS_FACES_CLUSTER_PENDING` | `50` | Ungrouped faces that trigger a grouping run before the nightly one. |
| `PHOTOS_FACES_VIDEO_DECODE_SIZE` | `1280` | Longest side, in px, of the video frames detection runs on. |
| `PHOTOS_FACES_VIDEO_INTERVAL` | `2` | Seconds between two sampled frames at least (more in a long video, so the frames cover all of it). |
| `PHOTOS_FACES_VIDEO_MAX_FRAMES` | `60` | The most frames sampled from one video. |
| `PHOTOS_FACES_VIDEO_MAX_DURATION` | `1200` | Longer videos (seconds) are not read for faces. |
| `PHOTOS_FACES_VIDEO_MAX_FILE_BYTES` | 2 GB | Larger videos are not read for faces. |

### Faces in videos

Videos are read where ffmpeg and ffprobe are installed (the Docker image ships
them); without them, videos are simply left out and never count as pending.
Each video runs in its own low-priority task, so a long one never holds up the
photos behind it.

- **Frames**: ffmpeg samples a frame at every scene change and at least every
  `PHOTOS_FACES_VIDEO_INTERVAL` seconds, up to `PHOTOS_FACES_VIDEO_MAX_FRAMES`.
  The faces of each frame are found like a photo's.
- **Tracking**: a face in one frame and a face in the next one are the same
  person when their boxes overlap and they look much alike. Someone who leaves
  and comes back is recognised by their face alone; two faces of one frame are
  always two people.
- **One face per person and video**: the grouping sees each person once per
  video, as it sees them once per photo. Their face picture is the best one of
  the video, and "People in this video" says when it was seen, with a link
  playing the video from there.

The cost is a few seconds of CPU per video on a small server for detection,
plus the decoding: ffmpeg reads the whole video to find its scene changes, so
a long 4K recording costs far more than a short phone clip. Lower
`PHOTOS_FACES_VIDEO_MAX_DURATION` on a server that should not spend that.

### How grouping works

- Right after a photo is analyzed, each new face joins the group its nearest
  neighbours vote for, if they are close enough. A face the model reads
  poorly - blurred, tiny, turned away - joins only on firm evidence: one of
  the group's good faces very close to it, or three of its faces within the
  threshold (the blurred photo of someone seen often sits among many of
  their faces without being very close to any). How well the model reads a
  face is, with `yunet_sface` and `yunet_adaface`, the length of its
  embedding before normalization, which these models make shorter the less
  sure they are; with `scrfd_arcface`, whose embedding length says no more
  than its detector does, the detector's confidence discounted for a small
  or blurred face.
- Nightly (and once enough faces wait), the faces still ungrouped are
  clustered. Two faces of one photo are never put in the same group; the
  database refuses it too. Faces read poorly never start a group, and a
  single clear face of someone is a group of its own.
- That rule also tells look-alikes apart. When a group keeps turning away
  faces as close to it as its own - two brothers photographed together, the
  group holding one of them each time - it is regrouped without ever putting
  two faces of one photo together, and the faces that side with the ones it
  turned away leave with them, in a new group.
- Corrections always win: a face the user placed is never moved, and a face
  the user took out ("Not this person", "Ungroup") is never grouped again
  automatically. A face the user hid (a stranger, a poster) is out of the
  grouping too, until unhidden.

### Naming people

A group is named after a contact from the **People** module: an existing one,
from the user's own address book or a group's, or a new one created from the
typed name. Naming never changes the contact itself, and deleting the contact
only leaves its groups unnamed.

- **One person, several groups.** The same person often spans several groups
  (a child growing up, a beard, glasses); each stays compact, so the grouping
  keeps matching new photos well, and they all show as one person. Saying a
  face is someone ("Change person") puts it in that person's closest group,
  or starts a new one of theirs when it looks like none of them.
- **Two faces of one photo are two people** at the person level too: a photo
  never shows one person twice, whichever of their groups the faces are in.
- **Merging** two groups named after different people asks which name stays.
- **On the contact's page** in People, a *Photos* section lists the photos
  they are in, and *Use as contact photo* sets the contact's avatar from the
  group's cover. Each user only ever sees their own photos: a contact shared
  in a group address book shows every member their own.
- **Search**: a person's name in the global search opens their photos.

### Reviewing people

*Review* on the People tab lists everything the grouping is waiting on, one
card per group of faces, each asking the same question - *who is this?* - and
answered the same way whatever it holds:

- **An unnamed group**, its faces least like the rest first (the others go
  with the answer unseen). A group whose centroid is within the grouping
  threshold of a named person's suggests that person, unless they are
  already in one of its photos.
- **A named person's doubtful faces** (*Is this Léa?*): faces the grouping put
  with them on its own that sit further than half the threshold from their
  group's centroid, most doubtful first. A confirmed face never comes back.
- **Look-alike faces in no group**, taken out of one by the user or never
  grouped (clear ones only: the blurred crowd behind a subject would bury the
  rest), with the named person they look like when there is one - never the
  one they were taken out of.

Clicking a face leaves it out of its card's answer; everything else on the
card goes with it. The answer is the guess, a contact or a new name typed in
the card's field, or *Hide* for nobody to name. Faces left out are taken out of the group the answer settles,
or left where they were, and come back in a card of their own. A settled card
leaves the list; the cards settling the most photos come first, 30 at a time.

The review runs from the keyboard: the card at hand has the focus in its name
field, from the first card on and after each answer (on a touch screen, only
after a keyboard answer). Enter takes the guess, or the highlighted contact
once the list is open; the arrows open and walk the contacts, Tab and
Shift+Tab move between cards, Alt+H hides, Escape closes the contacts then
clears the name. Clicking a face hands the keyboard back to its card.

### Correcting several faces at once

The *Faces* tab of a person's page and the hidden faces on the *Hidden* page
are boards of face tiles: click to pick (Shift picks a
range), then act from the bar under them:

- **This is...** puts the faces with a contact (their closest group, or one
  new group for all the faces that look like none of theirs), a new contact,
  an unnamed person, or someone new with no name yet.
- **That's right** confirms them where they are; **Not this person** takes
  them out of their group; **Hide** takes them out of the grouping and puts
  them on the *Hidden* page, where **Unhide** hands them back.

A face the correction cannot apply to (its photo already holds that person)
is left as it was, and the toast says so. Every correction can be undone from
its toast for a few minutes, groups it emptied included.

Photos analyzed by the pipeline are listed, read-only, in the admin under
*Face analyses*; faces and groups themselves are not browsable there.
