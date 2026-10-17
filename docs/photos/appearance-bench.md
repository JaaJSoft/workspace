# Appearance bench

`scripts/appearance_bench.py` measures whether what people wear can name them
when their face cannot: from behind, in the dark, too blurred or too small.
People keep their clothes for the evening, so within one event the torso of
someone seen from behind should come close to the torso of the same person
on a photo where their face named them. The bench measures how often that
holds, with which embedding model, and at what threshold, before anything of
it is built (issue #1060).

```bash
uv run python scripts/appearance_bench.py
uv run python scripts/appearance_bench.py --backend siglip2_base --json siglip.json
uv run python scripts/appearance_bench.py --compare siglip.json
uv run python scripts/appearance_bench.py --full-size --vectors ~/appearance-vectors
uv run python scripts/appearance_bench.py --window 12 --burst 0
```

On Windows, run it with `PYTHONUTF8=1`, as the embedding bench: fastembed
reads SigLIP 2's tokenizer configuration in the console's code page otherwise.
The photos are the face bench's, in the same cache (`PHOTO_DATASET_CACHE`);
the weights are the photo embedding models', in `PHOTOS_MODEL_DIR`.

## What it runs

For every person PIPA annotated, the torso under their head box: three heads
wide and three heads tall, right under the head, which is PIPA's body box
without its head. The face is never in the crop, so a torso stands for the
same person seen from behind. A torso of which less than a third is in the
photo (a close-up portrait) is left out: it shows no clothing.

Each torso is embedded with a photo embedding backend, one crop at a time
through `EmbeddingBackend.embed_image`, as a worker would, and compared with
the torsos of the people whose face the face pipeline would have named: those
with a head 48 px tall or more, of which the pipeline keeps 77 to 89% (face
bench, 500 px photos). Those are the anchors. The candidates for one person
are the anchors:

- in another photo of the same album (PIPA's ids only tell two people apart
  within one album);
- taken within `--window` hours of it (6 by default), but not within
  `--burst` minutes (5): a burst shows the same people in the same pose, and
  would flatter the result;
- leaving out the people the photo's own faces name, since a person is in a
  photo once.

The person is named after the nearest candidate, if it is near enough and
far enough ahead of the nearest candidate of anybody else.

`histogram` runs the same way with a colour histogram of the crop instead of
a model: what a model has to beat.

## The report

| Line | Meaning |
|---|---|
| Torsos with a candidate | Torsos with at least one candidate: the others are never named. A torso is one person in one photo, so someone seen in five photos counts five times |
| Anchored | Those among them whose own anchor is a candidate. The others can only be named wrongly, and no threshold tells them apart |
| Nearest anchor right | For the anchored, how often the nearest candidate is them; *most photographed instead* always answers the album's most photographed candidate |
| Named at 90, 95, 98% precision | The share of the torsos a threshold and a margin name, *torsos named* the count, and the precision they get. Both are fitted on half the libraries and scored on the other half, then the halves swap: these are numbers on libraries the threshold never saw |
| threshold / margin | The pair fitted on every library: what a backend would ship as its default |
| CPU ms / crop | Embedding CPU time per torso, one thread unless `--onnx-threads` says otherwise |

The same is broken down by head size, at 95% precision. `--json` writes
everything, and `--compare` prints the differences with an earlier report.

The shares are of the torsos with a candidate, and other `--window` or
`--burst` values change how many there are. Two such runs compare by the
torsos named, not by the shares.

## Options

| Option | Effect |
|---|---|
| `--backend` | `PHOTOS_EMBEDDING_BACKEND` to run, or `histogram`; repeatable. Default: the three local backends and the histogram |
| `--window HOURS`, `--burst MINUTES` | Which anchors are candidates (*What it runs* above) |
| `--libraries N` | Run the N largest libraries only |
| `--full-size` | Run on Flickr's 1024 px copies, as the face bench does |
| `--vectors DIR` | Keep each backend's vectors there, and read them back on a later run over the same crops: a change to the scoring or to the candidates takes seconds. Delete the folder after a change to a backend |
| `--onnx-threads N` | `PHOTOS_ONNX_THREADS` |

## Baseline

Measured on 2026-10-17 on a Windows desktop, one ONNX thread per backend,
the runs side by side. That inflates the CPU times a little: alone, the
embedding bench measures CLIP at 88 ms and SigLIP 2 at 315 ms per photo. In
parentheses: the precision held out, on the libraries the threshold was not
fitted on.

### 500 px

3,861 torsos in 2,960 photos, 2,775 of them anchors; 2,723 torsos have a
candidate within 6 hours.

| | CLIP ViT-B/32 | SigLIP 2 | Jina CLIP v1 | histogram |
|---|---|---|---|---|
| Anchored | 74.9% | 74.9% | 74.9% | 74.9% |
| Nearest anchor right | 70.3% | 74.8% | 74.7% | 69.9% |
| most photographed instead | 56.3% | 56.3% | 56.3% | 56.3% |
| Named at 90% | 25.3% (87.7%) | 27.1% (81.7%) | 31.4% (87.2%) | 14.5% (87.6%) |
| Named at 95% | 17.8% (94.6%) | 18.4% (90.4%) | 18.9% (96.9%) | 8.6% (94.0%) |
| torsos named at 95% | 485 | 500 | 514 | 234 |
| Named at 98% | 11.6% (97.1%) | 15.4% (96.9%) | 16.0% (96.8%) | 0.7% (95.0%) |
| Threshold / margin at 95% | 0.130 / 0.08 | 0.105 / 0.03 | 0.180 / 0.08 | 0.100 / 0.05 |
| CPU ms / crop | 121 | 439 | 437 | 1 |

By head size, named at 95%:

| Head | Torsos | Anchored | CLIP ViT-B/32 | SigLIP 2 | Jina CLIP v1 | histogram |
|---|---|---|---|---|---|---|
| under 24 px | 123 | 48.8% | 0.8% (100%) | 0.0% | 0.8% (100%) | 0.0% |
| 24-48 px | 564 | 58.5% | 5.1% (69.0%) | 5.5% (54.8%) | 3.4% (78.9%) | 0.5% (33.3%) |
| 48-96 px | 1,199 | 78.7% | 19.3% (95.7%) | 20.4% (93.0%) | 20.9% (98.0%) | 10.4% (92.0%) |
| 96 px and up | 837 | 84.2% | 26.8% (96.9%) | 26.9% (92.4%) | 29.2% (97.1%) | 12.7% (98.1%) |

### Which anchors

The same vectors, other candidates. Each column has its own number of torsos
with a candidate, so this table counts torsos instead of giving shares:

| | 6 h, bursts left out (default) | 6 h, bursts in (`--burst 0`) | whole album, bursts left out |
|---|---|---|---|
| Torsos with a candidate | 2,723 | 3,000 | 3,105 |
| anchored | 2,039 | 2,403 | 2,462 |
| Nearest anchor right, CLIP / SigLIP 2 | 1,433 / 1,526 | 1,825 / 1,902 | 1,665 / 1,778 |
| Named at 95%, CLIP | 485 (94.6%) | 807 (94.7%) | 500 (95.6%) |
| Named at 95%, SigLIP 2 | 500 (90.4%) | 899 (94.3%) | 537 (90.9%) |

### 1024 px

2,247 torsos in the 1,629 photos Flickr still serves at that size, 2,136 of
them anchors; 1,804 torsos have a candidate.

| | CLIP ViT-B/32 | SigLIP 2 | Jina CLIP v1 | histogram |
|---|---|---|---|---|
| Anchored | 80.7% | 80.7% | 80.7% | 80.7% |
| Nearest anchor right | 68.5% | 72.8% | 73.8% | 67.8% |
| Named at 90% | 32.8% (87.0%) | 34.1% (86.2%) | 35.1% (90.8%) | 4.2% (68.0%) |
| Named at 95% | 19.0% (96.2%) | 22.9% (93.7%) | 13.7% (95.6%) | 0.7% (91.7%) |
| Named at 98% | 2.3% (95.2%) | 2.3% (92.7%) | 12.7% (94.8%) | 0.0% |
| heads 96 px and up, at 95% | 23.6% (97.1%) | 27.4% (94.8%) | 17.4% (96.1%) | 0.8% (90.9%) |
| CPU ms / crop | 134 | 498 | 496 | 3 |

## Reading the results

- **Clothing names about one person in five, without their face.** At 95%
  precision, 14 to 23% of the torsos are named alone, and about a quarter
  of those under a big head. Within one event the signal is real; it is a
  complement to faces, not a replacement.
- **One person in four or five has no anchor around.** Nobody named them
  by their face in the 6 hours around, so the nearest anchor is always
  someone else, and nothing in the distances says so. That is the ceiling
  on precision, and the reason to offer these names as suggestions rather
  than assign them.
- **A threshold fitted on some libraries holds a little worse on others.**
  The precision held out usually lands under the one aimed at, by up to 8
  points, and the 98% row is noise on this many torsos. A default fitted
  here needs a margin of safety, and the 95% row is the one to read.
- **A model is needed, any of the three.** A colour histogram picks the
  right person first nearly as often as CLIP (69.9% against 70.3%), but its
  distances do not tell a right answer from a wrong one: it names half as
  many at 95% on 500 px photos, and almost nobody at 1024 px. The three
  models are close; CLIP costs a quarter of the other two.
- **People with small heads are out of reach.** Under 48 px, at most 6%
  are named, at 55-79% precision: the torso is too small to show anything.
  An appearance needs a minimum size, as faces do.
- **A window of hours comes close to the album.** PIPA's albums are Flickr
  albums, mostly one event each: what an event table would try to find.
  Six hours around each photo, with nothing to cut, name 3 to 7% fewer
  torsos than the whole album (485 against 500 with CLIP, 500 against 537
  with SigLIP 2), at about the same precision. Cutting events would buy
  little over a window.
- **Bursts help, and flatter.** Photos taken within 5 minutes are the same
  moment, the same pose: let in, they name two thirds more torsos (807
  against 485 with CLIP, 899 against 500 with SigLIP 2). A real library has
  bursts too, so the truth sits between the two columns.
- **More pixels change little.** At 1024 px the torsos under a big head are
  named no more often than at 500 px: past a hundred pixels, the model sees
  the clothing, and what it lacks is not resolution.

## What the numbers do not say

- **A torso from the front is not a back.** Both sides of the comparison
  are torsos seen from the front, since PIPA does not say who faces away. A
  printed front and a plain back, a jacket open on one side, make the real
  case harder than this.
- **Few night photos.** PIPA's albums are mostly daylight: weddings,
  conventions, family days. Colour-lit party photos, where a face fails most,
  are the case left to check on a real evening.
- **The anchors are right.** Every anchor is named after the person it is;
  in the app, a face in the wrong group passes its mistake on.
