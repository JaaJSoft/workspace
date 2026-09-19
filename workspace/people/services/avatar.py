from workspace.common.services.image import (
    delete_image,
    get_image_etag,
    process_image_to_webp,
    save_image,
)


def avatar_path(person):
    return f"people/avatars/{person.uuid}.webp"


def save_avatar(person, image_file, crop_x, crop_y, crop_w, crop_h):
    image_bytes = process_image_to_webp(image_file, crop_x, crop_y, crop_w, crop_h)
    save_image(avatar_path(person), image_bytes)
    person.has_avatar = True
    # The row is written even on a re-upload: the blob keeps its path, so
    # `updated_at` is the only thing that can bust a cached avatar.
    person.save(update_fields=["has_avatar", "updated_at"])


def delete_avatar(person):
    delete_image(avatar_path(person))
    if person.has_avatar:
        person.has_avatar = False
        person.save(update_fields=["has_avatar", "updated_at"])


def avatar_etag(person):
    return get_image_etag(avatar_path(person))
