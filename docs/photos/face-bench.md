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
  `--full-size` (below) runs the same photos at 1024 px.
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

### Full-size photos

`--full-size` runs the same libraries on the 1024 px copies Flickr still
serves, where faces are twice the size: closer to a real library, and the run
to check anything that depends on the size of faces (the minimum size, the
quality score, the detector on small heads). It needs a machine that reaches
`live.staticflickr.com` - the cloud sandbox these numbers were measured in
does not, so there is no full-size baseline below yet.

Flickr is no frozen archive: photos deleted or made private since 2014 are
gone, and the copies are pinned nowhere. The first run downloads several
hundred MB into `large/` in the cache and records, for each photo, the copy's
sha256 or that Flickr no longer has it at 1024 px; every later run on that
machine uses exactly that, whatever Flickr serves by then. Two machines can
end up with different photos, so a report says how many photos it ran at
which size, and `--compare` says when two reports do not share them. A
network error records nothing: run again.

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
| `--backend` | `PHOTOS_FACE_BACKEND` to run (`yunet_sface`, `yunet_adaface`, `scrfd_arcface`) |
| `--setting NAME=VALUE` | Set any setting read from the environment, e.g. `PHOTOS_FACES_MIN_SIZE=16` or `PHOTOS_FACES_MAX_DISTANCE=0.5`; repeatable |
| `--libraries N`, `--library NSID`, `--max-photos N` | Run a subset: the N largest libraries, named ones, the first photos of each |
| `--full-size` | Run on Flickr's 1024 px copies (*Full-size photos* above) |
| `--keep DIR` | Keep the database and photos, with the password `bench1234` on users `bench01`, `bench02`...; the command to browse them in the app is printed at the end |
| `--onnx-threads N` | `PHOTOS_ONNX_THREADS` |
| `--rescore REPORT` | Score an earlier `--json` report again instead of running |

## Baseline

Measured on 2026-10-09 in a 4-CPU cloud container, with one ONNX thread and
sqlite-vec. A full run took about 10 minutes with `yunet_sface` and 50 with
`scrfd_arcface` there. In parentheses: the detector alone.

### Backends

The pipeline as of `a1f24f3`, and with the look-alike split of `c3dd507`
(*Separating look-alikes* below).

| | yunet_sface | yunet_sface, split | scrfd_arcface | scrfd_arcface, split |
|---|---|---|---|---|
| Faces found | 66.7% (80.9%) | 66.7% (80.9%) | 70.6% (89.0%) | 70.6% (89.0%) |
| heads under 24 px | 0.0% (30.1%) | 0.0% (30.1%) | 0.0% (58.2%) | 0.0% (58.2%) |
| heads 24-48 px | 8.9% (67.4%) | 8.9% (67.4%) | 10.5% (82.0%) | 10.5% (82.0%) |
| heads 48-96 px | 77.4% (83.7%) | 77.4% (83.7%) | 82.4% (90.4%) | 82.4% (90.4%) |
| heads 96 px and up | 89.3% (89.3%) | 89.3% (89.3%) | 93.8% (93.9%) | 93.8% (93.9%) |
| Grouped | 81.8% | 81.8% | 79.6% | 79.6% |
| Pairwise precision | 95.4% | 96.0% | 99.2% | 99.4% |
| Pairwise recall | 84.1% | 84.7% | 88.2% | 88.6% |
| BCubed F1 | 87.8% | 87.9% | 91.6% | 91.7% |
| People found | 68.9% | 69.6% | 75.0% | 75.0% |
| Groups to merge | 95 | 95 | 12 | 12 |
| Mixed groups | 29 | 26 | 13 | 13 |
| Faces to take out | 120 | 98 | 42 | 31 |
| CPU ms / photo | 132 | 126 | 894 | 894 |
| of which models | 105 | 101 | 866 | 866 |
| Grouping CPU, s (whole run) | 171 | 180 | 229 | 219 |
| Weights, MB | 37 | 37 | 182 | 182 |

- **The size filter loses more faces than the detector.** YuNet finds 67% of
  the heads 24 to 48 px tall; the pipeline keeps 9% of them, the rest falling
  under `PHOTOS_FACES_MIN_SIZE`. Keeping them (`PHOTOS_FACES_MIN_SIZE=12`)
  finds 12 points more faces, but nearly four in five of the extra ones stay
  in no group: at that size the embedding, not the detector, is the limit. These are 500 px photos; on a real library the same
  people are three times bigger, so this is the line to check on full-size
  photos.
- **ArcFace groups far better**: 12 groups to merge instead of 95, a third of
  the faces in the wrong group, and its detector finds twice as many of the
  tiniest heads. It costs about 7 times the CPU and 5 times the weights, and
  its licence keeps it opt-in.
- **Grouping has a cost of its own**, 170 to 230 CPU seconds over a run, and
  it grows with the faces left in no group: each grouping run offers the
  newest of them to the existing groups again.

### The grouping threshold

`yunet_sface` at several `PHOTOS_FACES_MAX_DISTANCE`, without the split:

| | 0.45 | 0.50 | 0.55 | 0.58 | 0.60 | 0.62 |
|---|---|---|---|---|---|---|
| Grouped | 71.5% | 76.4% | 79.4% | 81.8% | 83.4% | 84.7% |
| Pairwise precision | 98.7% | 98.2% | 95.6% | 95.4% | 94.5% | 95.4% |
| Pairwise recall | 69.4% | 77.9% | 84.5% | 84.1% | 86.9% | 87.3% |
| BCubed F1 | 81.0% | 85.2% | 87.1% | 87.8% | 88.3% | 88.3% |
| People found | 61.0% | 65.3% | 66.9% | 68.9% | 68.9% | 70.0% |
| Groups to merge | 163 | 118 | 91 | 95 | 87 | 83 |
| Mixed groups | 14 | 13 | 21 | 29 | 35 | 44 |
| Faces to take out | 47 | 66 | 111 | 120 | 155 | 176 |

The default, 0.58, sits at the knee. Tighter, the groups to merge and the
faces in no group climb fast; looser, the faces in the wrong group do, for a
BCubed F1 that barely moves. With the split the picture is the same: at 0.60
it leaves 128 faces to take out and 32 mixed groups, at 0.62 181 and 42.

### Separating look-alikes

105 of the 120 faces in the wrong group at the default threshold sat with
someone they appear beside in at least one photo: brothers and sisters,
couples, friends - people the embedding barely tells apart, and that a
photo of the two of them proves are two. `split_look_alikes` uses that proof
(see docs/photos/README.md, *How grouping works*): 22 of the 120 leave the
wrong group and mixed groups go from 29 to 26, with as many groups to merge,
precision and recall both up, for 5% more grouping time. With
`scrfd_arcface` it takes 11 of the 42 faces out, for no extra time.

Most of the rest are out of its reach: in the photos the two share, the
other person's face was not found, or had already joined a group of its own.
An extension that also moved faces into that group took two more faces out,
for five more groups to merge and four times the extra grouping time; it was
left out.

### AdaFace

`yunet_adaface` is the default pipeline with AdaFace's embedding in place of
SFace's. Measured on 2026-10-10 with the look-alike split, and the quality
score of the time (detector score, size, sharpness); several runs shared the
machine, which moves the CPU figures by about 15% from run to run.

| | yunet_sface | AdaFace IR-18, 0.65 | AdaFace IR-50, 0.60 | AdaFace IR-50, 0.65 | scrfd_arcface |
|---|---|---|---|---|---|
| Grouped | 81.8% | 79.2% | 81.5% | 83.3% | 79.6% |
| Pairwise precision | 96.0% | 98.8% | 98.8% | 98.6% | 99.4% |
| Pairwise recall | 84.7% | 83.0% | 88.4% | 91.0% | 88.6% |
| BCubed F1 | 87.9% | 88.4% | 91.0% | 92.0% | 91.7% |
| People found | 69.6% | 70.0% | 73.6% | 75.0% | 75.0% |
| Groups to merge | 95 | 69 | 51 | 49 | 12 |
| Mixed groups | 26 | 15 | 14 | 17 | 13 |
| Faces to take out | 98 | 55 | 42 | 51 | 31 |
| CPU ms / photo | 126 | 139 | 235 | 232 | 894 |
| of which models | 101 | 112 | 208 | 205 | 866 |
| Weights, MB | 37 | 92 | 167 | 167 | 182 |

- **IR-50 at 0.60 is the backend's setting**: half the groups to merge of
  SFace and under half the faces in the wrong group, a BCubed F1 within a
  point of ArcFace's, for about twice SFace's CPU and a quarter of ArcFace's.
  Its detector is YuNet, so it finds no more faces than the default. At 0.65
  it gains a point of F1 and two merges, for nine more faces in the wrong
  group.
- **IR-18 is not worth its weights.** At no threshold does it come close to
  IR-50: tight, its faces stay ungrouped; loose, they land in the wrong group.

  | | 0.60 | 0.65 | 0.70 | 0.75 |
  |---|---|---|---|---|
  | Grouped | 75.8% | 79.2% | 82.4% | 85.9% |
  | BCubed F1 | 85.9% | 88.4% | 89.2% | 90.5% |
  | Groups to merge | 90 | 69 | 81 | 60 |
  | Faces to take out | 33 | 55 | 92 | 127 |

- **ArcFace still merges far less** (12 groups): its detector and its larger
  network show there. IR-101 (249 MB) was too slow to finish a run on this
  machine.
- **The licence keeps it opt-in.** The weights are published under MIT, but
  trained on WebFace4M, a dataset licensed for non-commercial research.

### Face quality

The quality score decides which faces may start a group, which join one only
on firm evidence, and how much each weighs in a group's centroid. It was the
detector's confidence, discounted for a small or blurred face, and still is
with `scrfd_arcface`. On the bench that barely tells a face the grouping will
recognize from one it will not. The length of the raw embedding does far
better with SFace and AdaFace; with ArcFace, no better than its detector
score. A face counts as recognizable here when its nearest face of the same
person, in another photo, is closer than its nearest face of a known other
person of the album, and within the threshold:

| How well it predicts a recognizable face (AUC) | yunet_sface | yunet_adaface | scrfd_arcface |
|---|---|---|---|
| Detector score, size and sharpness | 0.59 | 0.59 | 0.67 |
| Detector score alone | 0.78 | 0.79 | 0.74 |
| Sharpness alone | 0.49 | 0.49 | 0.48 |
| Embedding length | 0.81 | 0.80 | 0.74 |

The length maps linearly to 0-1, fitted so the same share of the bench's faces
falls under the two thresholds as before: 30% under 0.5 and 71% under 0.7.

Alone, the new score cut the groups to merge by two thirds but left more faces
of the people seen most often in no group: their hard faces now counted as
low quality, and could join a group only through one very close good face. A
group holding three of a face's neighbours within the threshold now counts as
firm evidence too, and brings them back. `yunet_sface`, each change alone and
both:

| | before | witnesses | embedding length | both |
|---|---|---|---|---|
| Grouped | 81.8% | 82.8% | 79.9% | 82.7% |
| Pairwise precision | 96.0% | 95.9% | 95.8% | 95.6% |
| Pairwise recall | 84.7% | 87.1% | 78.4% | 87.4% |
| BCubed F1 | 87.9% | 88.5% | 87.5% | 89.7% |
| People found | 69.6% | 69.8% | 75.0% | 75.5% |
| Groups to merge | 95 | 100 | 33 | 33 |
| Mixed groups | 26 | 26 | 22 | 23 |
| Faces to take out | 98 | 101 | 88 | 97 |
| Grouping CPU, s | 180 | 178 | 132 | 127 |

With the three backends, before and after (`scrfd_arcface` keeps the old
score, see below):

| | yunet_sface, before | after | yunet_adaface, before | after | scrfd_arcface, before | after |
|---|---|---|---|---|---|---|
| Grouped | 81.8% | 82.7% | 81.5% | 83.4% | 79.6% | 80.6% |
| Pairwise precision | 96.0% | 95.6% | 98.8% | 99.3% | 99.4% | 99.4% |
| Pairwise recall | 84.7% | 87.4% | 88.4% | 89.9% | 88.6% | 91.3% |
| BCubed F1 | 87.9% | 89.7% | 91.0% | 92.3% | 91.7% | 92.6% |
| People found | 69.6% | 75.5% | 73.6% | 78.8% | 75.0% | 75.0% |
| Groups to merge | 95 | 33 | 51 | 17 | 12 | 13 |
| Mixed groups | 26 | 23 | 14 | 12 | 13 | 13 |
| Faces to take out | 98 | 97 | 42 | 30 | 31 | 32 |
| Grouping CPU, s | 180 | 127 | 202 | 124 | 219 | 214 |

- **A third of the merges, a third less grouping time.** The faces that used
  to start small stray groups of a known person are now the ones the score
  holds back, and fewer faces wait to be offered to the groups again.
- **ArcFace keeps the old score.** Its embedding length predicts a
  recognizable face no better than its detector score does, and on the bench
  it put 46 faces in the wrong group instead of 32 and left 22 groups to merge
  instead of 13. The witnesses alone still help it: 2.7 points of recall and
  nearly one of BCubed F1, for one more merge.
- **`yunet_adaface` now groups about as well as ArcFace**, with the
  default's detector and a quarter of ArcFace's CPU.

### int8 models

OpenCV Zoo publishes int8 copies of both default models, a quarter of the
size, meant for small CPUs. Under onnxruntime they do not pay:

- YuNet's does not run at all: it fails in a `Gather` node, and the
  block-quantized `int8bq` copies of both models fail too.
- SFace's runs, at nearly twice the CPU of the float model (185 ms of models
  per photo instead of 101) on the bench machine, whose CPU has the VNNI and
  AMX int8 instructions. It groups about as well: 114 faces to take out
  instead of 98, 90 groups to merge instead of 95.

The float models stay. An ARM host may differ - onnxruntime's int8 kernels
are another code path there - and the bench is the way to check one.

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
