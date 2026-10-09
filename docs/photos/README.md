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
| `scrfd_arcface` | SCRFD + ArcFace (InsightFace `buffalo_l`) | 512-d | **Non-commercial research only** |

`scrfd_arcface` is more accurate on hard faces (profiles, small faces in group
photos), but InsightFace licenses its pretrained weights for non-commercial
research only: an instance run by or for a company must not enable it. Both
run on the CPU through onnxruntime. The [face bench](face-bench.md) measures
both on real photo libraries; run it to judge any change to detection or
grouping.

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
  neighbours vote for, if they are close enough.
- Nightly (and once enough faces wait), the faces still ungrouped are
  clustered. Two faces of one photo are never put in the same group; the
  database refuses it too. Blurred or tiny faces never start a group, and a
  single clear face of someone is a group of its own.
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
