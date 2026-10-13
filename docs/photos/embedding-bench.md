# Embedding bench

`scripts/embedding_bench.py` measures the photo embedding models - the ones
behind similar photos, near-duplicates and search by description - on the
same photos, so the choice of `PHOTOS_EMBEDDING_BACKEND` and of its
thresholds rests on numbers. It runs every local backend by default:

```bash
uv run python scripts/embedding_bench.py --json bench.json
uv run python scripts/embedding_bench.py --backend siglip2_base --compare bench.json
uv run python scripts/embedding_bench.py --images 300 --duplicates 50   # a quick run
uv run python scripts/embedding_bench.py --no-video-frames              # photos only
```

The first run downloads 1,500 photos (about 450 MB, kept downscaled: 85 MB)
into `~/.cache/workspace/embedding-dataset` (beside the face bench's,
`PHOTO_DATASET_CACHE` moves both, `--cache-dir` this one alone), and each
model's weights into `PHOTOS_MODEL_DIR`, where the app finds them afterwards.
The `remote` backend is measured too when its settings are in the
environment. The video frames need ffmpeg: without it they are skipped. On
Windows, run it with `PYTHONUTF8=1`: SigLIP 2's and Jina CLIP's tokenizers are
read in the system code page otherwise, and their run fails.

## What it runs

Each backend in a process of its own, exactly as a worker runs it: one photo
at a time through `embed_image`, at the size the library decodes it, one
query at a time through `embed_text`. Its thresholds are the ones the app
would use: the backend's defaults, or the `PHOTOS_*_MAX_DISTANCE` settings in
the environment. A backend that cannot run (weights out of reach, an endpoint
down) is reported and skipped; the others are still measured.

## The dataset

`scripts/embedding_dataset.py`: photos of Open Images V7's validation set,
from Flickr, licensed CC BY 2.0 by their authors. Every label on them was
verified by people (Google's annotations, CC BY 4.0), so whether a photo shows
a dog is known, not guessed. The two annotation files are downloaded from a
pinned version and checked against their sha256; the photos are picked from
them in a fixed order, the same on every machine:

- up to 30 photos for each of 45 queries of what people look for in their own
  library - animals, vehicles, landscapes, food, objects, and pairs such as
  "dog in the snow" or "cake with candles", which ask for both labels;
- 300 photos showing none of them, the rest of a library a search must leave
  out.

Each query has a French twin ("chien dans la neige"), scored on its own:
CLIP and Jina CLIP were trained on English, SigLIP 2 on many languages.

## What it measures

**Search by description.** Each query ranks every photo. *mAP* is the mean
average precision of those rankings (100% when every right photo comes before
every wrong one), in English and in French; *precision in the first 10* is
what the first screen of results holds. Then the backend's description
threshold, as `P` (precision of what it returns), `R` (recall: share of the
right photos it returns) and `n` (photos returned per query), and the
threshold that would have given the best F1.

**Near-duplicates.** 200 photos are each copied five ways - re-encoded at JPEG
quality 40, halved, cropped to 85%, brightened by 25%, tilted by 3 degrees -
as a burst, a re-export or a screenshot leaves them. A photo's right answers
are its own copies; every other photo, and the other photos' copies, are
mistakes. The duplicate threshold is scored the same way, then the loosest
threshold that keeps 99% of what it finds right. *Closest two other photos*
is how near two photos that are not copies of each other come: a duplicate
threshold past it starts grouping photos that are merely alike.

**Similar photos.** Each photo showing one of the queries' labels against all
the others; a right answer shows one of the same. mAP and precision in the
first 10, the similar threshold, and the one with the best F1.

**Video frames.** Each photo is filmed (with ffmpeg, before any backend runs):
a 2-second clip pushing in slowly on it, 4% over the clip, encoded in H.264
4:2:0 at CRF 30, as a messaging app leaves a phone video. Two of its frames
are read back, a second apart, and embedded the way the library embeds a
video's key frames. *Photo vs its frame* is the near-duplicate test with the
frame as the only copy, among every photo and the other photos' frames. *Two
frames of a shot* is the share of those pairs within the duplicate threshold,
which is what tells a video's key frames apart: within it, the second frame
is dropped. Search by description and similar photos then run with the
frames in place of the photos.

**Cost.** Time to load both encoders, CPU time per photo and per query (one
thread: `PHOTOS_ONNX_THREADS`), the weights on disk, and how much the process
grew. CPU time, not wall time: it is what a Raspberry Pi pays, whatever the
machine running the bench.

## Reading the results

The best threshold columns are what to set a model's defaults to
(`similar_max_distance`, `duplicate_max_distance`,
`description_max_distance` in `workspace/photos/services/embedding/local.py`),
or what an instance sets in `PHOTOS_SIMILAR_MAX_DISTANCE`,
`PHOTOS_DUPLICATE_MAX_DISTANCE` and `PHOTOS_DESCRIPTION_MAX_DISTANCE`. A
change to a model's preprocessing or to its thresholds is judged by running
the bench before and after it (`--json` then `--compare`).

The near-duplicate suggestion is a ceiling, not a default. Open Images holds
one photo per moment, so nothing in it looks like a burst; a personal library
is shot in bursts, and its look-alikes come close. On the 695 photos of one
photographer's library (`seed_demo.py --real-photos`), past 0.07 for SigLIP 2
more and more of the pairs under the threshold were taken on different days:
four in ten at 0.105, its loosest at 99% precision here. The defaults are the
tightest threshold that still finds 95% of the copies.

The video frames answer whether a video needs thresholds of its own:

| Model | Photo vs its frame found | Loosest at 99% | Search mAP, photos / frames | Best description threshold | Best similar threshold |
|---|---|---|---|---|---|
| SigLIP 2 | 86% | 0.085 (96%) | 83.2% / 83.4% | 0.920 / 0.920 | 0.385 / 0.405 |
| CLIP ViT-B/32 | 81% | 0.075 (81%) | 69.8% / 67.0% | 0.765 / 0.760 | 0.385 / 0.405 |
| Jina CLIP v1 | 92% | 0.085 (92%) | 80.1% / 79.4% | 0.890 / 0.895 | 0.440 / 0.470 |

Search and similar photos lose little on the frames (Jina CLIP's similar
photos the most, 3 points of mAP), and their best thresholds barely move: one
description and one similar threshold serve photos and videos alike.

The near-duplicate threshold finds fewer frames than photo copies (86%
against 96% for SigLIP 2): a compressed, moving frame of a photo is further
from it than a re-export. It stays the same all the same, for the reason
above: a frame it misses still comes first among the similar photos, while a
looser threshold would group moments of different days. Two frames of one
shot a second apart are always within it (a median distance of 0.01): a still
or slowly moving shot is one key frame.

Open Images labels are complete for the labels verified on a photo, not for
every label: a photo may show a dog nobody asked about, and count as a wrong
answer to "dog". Every model pays that alike, so the numbers compare models;
their absolute value is a lower bound.
