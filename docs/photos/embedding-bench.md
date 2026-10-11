# Embedding bench

`scripts/embedding_bench.py` measures the photo embedding models - the ones
behind similar photos, near-duplicates and search by description - on the
same photos, so the choice of `PHOTOS_EMBEDDING_BACKEND` and of its
thresholds rests on numbers. It runs every local backend by default:

```bash
uv run python scripts/embedding_bench.py --json bench.json
uv run python scripts/embedding_bench.py --backend siglip2_base --compare bench.json
uv run python scripts/embedding_bench.py --images 300 --duplicates 50   # a quick run
```

The first run downloads 1,500 photos (about 450 MB, kept downscaled: 85 MB)
into `~/.cache/workspace/embedding-dataset` (beside the face bench's,
`PHOTO_DATASET_CACHE` moves both, `--cache-dir` this one alone), and each
model's weights into `PHOTOS_MODEL_DIR`, where the app finds them afterwards.
The `remote` backend is measured too when its settings are in the
environment.

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

Open Images labels are complete for the labels verified on a photo, not for
every label: a photo may show a dog nobody asked about, and count as a wrong
answer to "dog". Every model pays that alike, so the numbers compare models;
their absolute value is a lower bound.
