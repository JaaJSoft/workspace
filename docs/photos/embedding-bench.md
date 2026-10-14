# Embedding bench

`scripts/embedding_bench.py` measures the photo embedding models - the ones
behind similar photos, near-duplicates and search by description - on the
same photos, so the choice of `PHOTOS_EMBEDDING_BACKEND` and of its
thresholds rests on numbers. It runs every local backend by default:

```bash
uv run python scripts/embedding_bench.py --json bench.json
uv run python scripts/embedding_bench.py --backend siglip2_base --compare bench.json
uv run python scripts/embedding_bench.py --images 300 --duplicates 50   # a quick run
uv run python scripts/embedding_bench.py --vectors ~/bench-vectors       # keep the vectors
```

`--vectors` keeps each backend's vectors in that folder, and a later run
over the same photos, queries and tags reads them back instead of embedding
again: a change to the scoring or to a threshold is measured in seconds
rather than the better part of an hour. The vectors are scored at the
thresholds the app reads at that moment.

On Windows, run it with `PYTHONUTF8=1`: fastembed reads SigLIP 2's tokenizer
configuration in the console's code page, and fails on it otherwise.

The first run downloads about 2,000 photos (about 600 MB, kept downscaled: 110 MB)
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
- up to 30 photos for each of the app's 33 classic tags
  (`workspace/photos/services/tag_vocabulary.py`), the automatic tags;
- 300 photos showing none of the queries, the rest of a library a search
  must leave out.

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

**Automatic tags.** Each classic tag, placed on every photo whose distance to
the tag's name is under a threshold. A tag shows on a photo alone, with no
ranking to bury a mistake under better answers, so it needs a precision a
search does without: 90% (`TAG_PRECISION`). `P` is the share of the tags
placed that are right, `R` the share of each tag's photos it tags, averaged
over the tags, then the tags per photo and how many tags are ever placed.
Rows:

- *at the description threshold*: the search threshold reused as it is;
- *one threshold*: the loosest single threshold keeping 90% over every tag;
- *one threshold per tag*: what a backend ships (`tag_thresholds`). A tag
  whose ranking is under 75% average precision is left out (`TAG_MIN_AP`):
  no threshold separates it from the rest. The others get their loosest
  threshold at 97% (`TAG_FIT_PRECISION`), tighter than the 90% aimed at,
  because 30 photos a tag are few and a threshold fitted on them keeps less
  on photos it never saw;
- *at its tag table*: the backend's own table, scored on the photos it was
  fitted on, so flattered: the row above is the honest one;
- *from 3, 5, 10 tagged photos*: a tag learnt from the mean of a few photos
  tagged by hand, as the owner's own tags would be learnt.

Fitted thresholds are scored on the half of the photos they were not fitted
on, then the halves swap. After the table, the bench prints the per-tag
table fitted on every photo for each backend whose own table differs: that
is what goes into `tag_thresholds` in
`workspace/photos/services/embedding/local.py`. Since each tag is its own
yes or no, its threshold does not depend on how many other tags the
vocabulary holds.

On 1,971 photos, the description threshold tags at 69% precision with
SigLIP 2: one tag in three wrong. One shared threshold misses 90% (86%),
because the right threshold differs a lot from one tag to the next: it
tags "car" on 2% of its photos and "skiing" on 93%, with 30% of them wrong.
One threshold per tag holds 90% with 35% recall, on the 24 tags SigLIP 2
tells apart (23 for Jina CLIP, 13 for CLIP). Events and settings rank
badly on every model: "party", "city" and "meal" are never placed.

Tags learnt from a few photos are not worth it: at 90% precision they find
about one photo in ten, from 3 photos as from 10, and a classifier trained
with negatives did no better (13-19%). Open Images labels are broader than
a user's own tag (every dog, not one's dog), so this is a lower bound, but
not one that justifies the feature yet.

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

Open Images labels are complete for the labels verified on a photo, not for
every label: a photo may show a dog nobody asked about, and count as a wrong
answer to "dog". Every model pays that alike, so the numbers compare models;
their absolute value is a lower bound.
