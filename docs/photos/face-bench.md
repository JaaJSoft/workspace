# Face bench

`scripts/face_bench.py` measures face detection and grouping on real photo
libraries, so a change to a backend, a threshold, the quality score or the
clustering can be judged by numbers instead of by a few hand-picked photos.
Run it before and after the change, and compare.

```bash
uv run python scripts/face_bench.py --json before.json
# ... change something ...
uv run python scripts/face_bench.py --compare before.json
```

The first run downloads about 370 MB of photos and the backend's weights into
`~/.cache/workspace/photo-dataset` (`PHOTO_DATASET_CACHE` or `--cache` to move
it), and migrates a database it caches there too; the next runs start at once.

## What it runs

The real pipeline, as a worker runs it. Each library is uploaded into its own
user's Files, then analyzed photo by photo in the order the photos were taken:
each new face votes for a group, and a grouping run starts once
`PHOTOS_FACES_CLUSTER_PENDING` faces wait, as it would between two analyses.
The nightly grouping pass runs last. Nothing is simulated, so any change under
`workspace/photos/services/` shows up in the numbers.

Every run gets a throwaway SQLite database and media root: the development
database is never touched, and no Redis is shared with whatever else runs on
the machine. An analysis that fails stops the bench rather than scoring as a
photo with nobody in it.

## The dataset

`scripts/photo_dataset.py`: 2,960 photos from the libraries of 25 Flickr
members, 30 to 695 photos each, taken between 1987 and 2014 - fan
conventions, weddings, birthdays, Christmas, parties, graduations, children
growing up. Two public sources, joined:

- **The photos** are YFCC100M's, frozen in 2014 on AWS Open Data (the
  Multimedia Commons bucket), at the 500 px rendition Flickr served then.
  Every one is licensed **CC BY 2.0** by its author. The manifest pins each
  file's sha256.
- **Who is who** comes from **PIPA** (People In Photo Albums, Zhang et al.,
  CVPR 2015): 4,918 head boxes, each with a person id - 1,145 people, 689 of
  them seen in more than one photo. The annotation file states no licence, so
  it is not copied into the repository: the bench downloads it from a pinned
  commit of [coallaoh/PIPA_dataset](https://github.com/coallaoh/PIPA_dataset)
  and checks its sha256.

PIPA annotated the photos at either the 500 px or the 1024 px rendition. The
manifest records which, album by album, voted by where detected faces land
(the album decision agrees with the photo-level vote on 99% of the photos
where a face was found). `scripts/build_photo_dataset.py` rebuilds the
manifest from the two sources; it reads YFCC100M's 65 GB metadata database
through HTTP range requests instead of downloading it.

What to keep in mind when reading the numbers:

- **The photos are small.** A face that is 80 px tall at
  `PHOTOS_FACES_DECODE_SIZE` on a phone photo is 25 px here, under
  `PHOTOS_FACES_MIN_SIZE`. Absolute numbers are harsher than on a real
  library; comparisons between two runs are what the bench is for.
- **A head is not always a face.** PIPA boxes heads, people facing away
  included, so "faces found" cannot reach 100%.
- **Not everybody is annotated.** PIPA only labelled some of the people in a
  crowd, so a face matching no head - *Faces not annotated* in the report - is
  a bystander as often as a false detection. It is not a precision.
- **An id is often valid for one album only.** PIPA gave the same person a new
  id in each album far more often than not: only 70 of the 1,145 ids span two
  albums, while the same toddler can be four ids in four albums of one
  library. So two different ids only mean two people *within one album*.
  Across albums the bench does not know, and leaves the pair out: grouping a
  child's faces from two albums is not counted as a mistake, and grouping two
  strangers from two albums is not caught either. One id, wherever it
  appears, is one person.

## The report

| Line | Meaning |
|---|---|
| Faces found | Annotated people the pipeline stored a face for |
| detector alone | The same, from everything the detector returned, before the pipeline drops faces under `PHOTOS_FACES_MIN_SIZE` or past `PHOTOS_FACES_MAX_PER_PHOTO` |
| Grouped | Faces found that ended up in a group |
| Pairwise precision / recall | Of the pairs of faces put in one group whose relation is known, the share that are one person; of the pairs of faces of one person, the share put in one group |
| BCubed F1 | The per-face equivalent, on known relations too, a face in no group counting as a group of its own |
| People found | People seen twice or more with two of their faces in one group of theirs |
| Groups to merge | Groups beyond the first one of each person: the merges a user would do |
| Mixed groups / Faces to take out | Groups holding two people of one album, and the faces a user would remove from them |
| CPU ms / photo | Analysis CPU time per photo, models, alignment, crops and database writes included; *of which models* is detection and embedding alone |

Faces found are also broken down by the head's height in the image, which is
where detectors differ most. `--json` writes everything, per library too, and
`--compare` prints the new run beside an earlier report. A report also keeps
the faces it was scored from: after a change to the scoring,
`--rescore old.json` scores it again without running anything.

## Options

| Option | Effect |
|---|---|
| `--backend` | `PHOTOS_FACE_BACKEND` to run (`yunet_sface`, `scrfd_arcface`) |
| `--setting NAME=VALUE` | Set any setting read from the environment, e.g. `PHOTOS_FACES_MIN_SIZE=16` or `PHOTOS_FACES_MAX_DISTANCE=0.5`; repeatable |
| `--libraries N`, `--library NSID`, `--max-photos N` | Run a subset: the N largest libraries, named ones, the first photos of each |
| `--keep DIR` | Keep the database and photos, with the password `bench1234` on users `bench01`, `bench02`...; the command to browse them in the app is printed at the end |
| `--onnx-threads N` | `PHOTOS_ONNX_THREADS` |
| `--rescore REPORT` | Score an earlier `--json` report again instead of running |

## Baseline

Measured on 2026-10-09 at commit `a1f24f3`, in a 4-CPU cloud container with
one ONNX thread and sqlite-vec. A full run took 10 minutes with `yunet_sface`
and 50 with `scrfd_arcface` there. In parentheses: the detector alone.

| | yunet_sface | yunet_sface, distance 0.5 | yunet_sface, min size 12 | scrfd_arcface |
|---|---|---|---|---|
| Faces found | 66.7% (80.9%) | 66.7% (80.9%) | 79.2% (80.9%) | 70.6% (89.0%) |
| heads under 24 px | 0.0% (30.1%) | 0.0% (30.1%) | 7.7% (30.1%) | 0.0% (58.2%) |
| heads 24-48 px | 8.9% (67.4%) | 8.9% (67.4%) | 62.9% (67.4%) | 10.5% (82.0%) |
| heads 48-96 px | 77.4% (83.7%) | 77.4% (83.7%) | 83.7% (83.7%) | 82.4% (90.4%) |
| heads 96 px and up | 89.3% (89.3%) | 89.3% (89.3%) | 89.3% (89.3%) | 93.8% (93.9%) |
| Grouped | 81.8% | 76.4% | 72.4% | 79.6% |
| Pairwise precision | 47.4% | 61.3% | 50.1% | 64.2% |
| Pairwise recall | 84.1% | 77.9% | 74.4% | 88.2% |
| BCubed F1 | 77.1% | 80.3% | 75.6% | 85.6% |
| People found | 60.1% | 64.9% | 50.5% | 63.1% |
| Groups to merge | 46 | 81 | 48 | 5 |
| Mixed groups | 87 | 57 | 86 | 53 |
| Faces to take out | 721 | 392 | 743 | 403 |
| CPU ms / photo | 137 | 131 | 162 | 895 |
| of which models | 110 | 106 | 131 | 867 |
| Grouping CPU, s (whole run) | 160 | 202 | 297 | 233 |
| Weights, MB | 37 | 37 | 37 | 182 |

What it says:

- **The size filter loses more faces than the detector.** YuNet finds 67% of
  the heads 24 to 48 px tall; the pipeline keeps 9% of them, the rest falling
  under `PHOTOS_FACES_MIN_SIZE`. Keeping them (`min size 12`) finds 12 points
  more faces but groups worse - most stay alone, and the people found drop
  from 60% to 51%: at that size the embedding, not the detector, is the
  limit. These are 500 px photos; on a real library the same people are three
  times bigger, so this is the line to check on full-size photos.
- **Grouping errs on the side of merging.** With SFace, under half the pairs
  put in one group are one person, while 84% of each person's pairs are
  together: a user would take 721 faces out of 87 mixed groups, but merge
  only 46 groups. A threshold of 0.5 instead of 0.58 halves the faces to take
  out for 35 more merges, and lifts BCubed F1 by 3 points.
- **ArcFace groups far better**: 5 merges instead of 46, 403 faces to take
  out instead of 721, BCubed F1 86% instead of 77%, and its detector finds
  twice as many of the tiniest heads. It costs 6.5 times the CPU and 5 times
  the weights, and its licence keeps it opt-in.
- **Young siblings defeat both.** One library, a family with small children,
  holds 252 of ArcFace's 403 faces to take out - and as many of SFace's: both
  put brothers and sisters a few years old in one group, and no threshold
  tried here separates them.
- **Grouping has a cost of its own**, 160 to 300 CPU seconds over a run, and
  it grows with the faces left in no group: each grouping run offers the
  newest of them to the existing groups again.

## Real photos in the demo seed

The same libraries can fill a demo instance:

```bash
uv run python scripts/seed_demo.py --real-photos 2
```

gives `demo` and the next user one library each, in their Photos import folder,
with the capture dates restored and a `CREDITS.txt` naming every author, and
turns face grouping on for them (it still needs `PHOTOS_FACES_ENABLED=1` on the
instance). The photos are other people's, shared under CC BY: keep the credits
with any screenshot published outside the team.

## Beyond faces

The manifest also carries each photo's title, tags, capture date and album
(from PIPA), which is what a bench for whole-photo embeddings needs - search by
description, similar photos (#1149) - without another dataset.
