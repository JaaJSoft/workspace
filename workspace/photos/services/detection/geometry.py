"""Image geometry the detection backends share: letterboxing, NMS, alignment.

numpy and Pillow only. These are the few dozen lines of OpenCV that face
recognition actually needs; owning them keeps opencv, scipy and scikit-image
out of the image.
"""

import numpy as np
from PIL import Image

# Where the five landmarks sit on a 112 x 112 aligned face: the reference both
# ArcFace and SFace were trained on (insightface's `arcface_dst`, OpenCV's
# FaceRecognizerSF::alignCrop).
ALIGNED_SIZE = 112
_REFERENCE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float64,
)


def letterbox(image, size):
    """*image* scaled to fit a *size* x *size* square, padded bottom and right.

    Returns (square RGB uint8, scale): a point (x, y) found in the square is
    (x / scale, y / scale) in *image*.
    """
    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    new_w = max(1, round(width * scale))
    new_h = max(1, round(height * scale))
    resized = Image.fromarray(image).resize((new_w, new_h), Image.BILINEAR)
    square = np.zeros((size, size, 3), dtype=np.uint8)
    square[:new_h, :new_w] = np.asarray(resized)
    return square, scale


def nms(boxes, scores, threshold):
    """Indices of the boxes kept by non-maximum suppression, best first.

    *boxes* is N x 4 as x1, y1, x2, y2.
    """
    order = np.argsort(scores)[::-1]
    x1, y1, x2, y2 = boxes.T
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        w = np.maximum(0, np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]))
        h = np.maximum(0, np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]))
        inter = w * h
        iou = inter / np.maximum(areas[i] + areas[rest] - inter, 1e-9)
        order = rest[iou <= threshold]
    return keep


def similarity_transform(src, dst):
    """The 2 x 3 similarity (rotation, uniform scale, shift) taking *src* to *dst*.

    Umeyama's least squares, as scikit-image's SimilarityTransform computes it.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_c, dst_c = src - src_mean, dst - dst_mean
    cov = dst_c.T @ src_c / len(src)
    u, s, vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(cov) < 0:
        d[1] = -1
    rotation = u @ np.diag(d) @ vt
    variance = (src_c**2).sum() / len(src)
    scale = (s * d).sum() / variance if variance else 1.0
    shift = dst_mean - scale * rotation @ src_mean
    return np.hstack([scale * rotation, shift[:, None]])


def align(image, landmarks):
    """The 112 x 112 face whose landmarks are *landmarks*, eyes level.

    *image* is RGB uint8; *landmarks* 5 x 2 in its pixel coordinates.
    """
    matrix = similarity_transform(landmarks, _REFERENCE)
    # Pillow's AFFINE maps each output pixel back to the input, so it takes
    # the inverse of the transform.
    inverse = np.linalg.inv(np.vstack([matrix, [0, 0, 1]]))[:2]
    aligned = Image.fromarray(image).transform(
        (ALIGNED_SIZE, ALIGNED_SIZE),
        Image.AFFINE,
        data=tuple(inverse.ravel()),
        resample=Image.BILINEAR,
    )
    return np.asarray(aligned)
