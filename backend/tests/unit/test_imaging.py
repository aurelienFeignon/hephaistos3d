import io
from pathlib import Path

import pytest
from PIL import Image

from hephaistos.imaging import ImageRejected, RejectReason, normalize_image

EXIF_ORIENTATION = 0x0112


def save(path: Path, image: Image.Image, fmt: str, **options) -> Path:
    image.save(path, fmt, **options)
    return path


def gradient(size=(40, 30), mode="RGB") -> Image.Image:
    """Image où chaque pixel est distinct, pour vérifier une rotation sans ambiguïté."""
    image = Image.new("RGB", size)
    image.putdata([(x * 6 % 256, y * 8 % 256, (x + y) % 256) for y in range(size[1]) for x in range(size[0])])
    return image.convert(mode)


@pytest.mark.parametrize(("fmt", "suffix"), [("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp")])
def test_accepted_formats_become_png(tmp_path, fmt, suffix):
    src = save(tmp_path / f"in.{suffix}", gradient(), fmt)
    info = normalize_image(src, tmp_path / "out.png", max_pixels=10_000)
    with Image.open(tmp_path / "out.png") as out:
        assert out.format == "PNG"
        assert out.size == (40, 30)
    assert info.source_format == fmt
    assert (info.width, info.height) == (40, 30)


def test_format_is_read_from_content_not_extension(tmp_path):
    src = save(tmp_path / "trompeur.png", gradient(), "GIF")
    with pytest.raises(ImageRejected) as caught:
        normalize_image(src, tmp_path / "out.png", max_pixels=10_000)
    assert caught.value.reason is RejectReason.UNSUPPORTED_FORMAT


def test_not_an_image_is_undecodable(tmp_path):
    src = tmp_path / "faux.jpg"
    src.write_bytes(b"%PDF-1.7 pas une image")
    with pytest.raises(ImageRejected) as caught:
        normalize_image(src, tmp_path / "out.png", max_pixels=10_000)
    assert caught.value.reason is RejectReason.UNDECODABLE


def test_truncated_image_is_undecodable(tmp_path):
    buffer = io.BytesIO()
    gradient((200, 200)).save(buffer, "PNG")
    src = tmp_path / "tronque.png"
    src.write_bytes(buffer.getvalue()[: len(buffer.getvalue()) // 2])
    with pytest.raises(ImageRejected) as caught:
        normalize_image(src, tmp_path / "out.png", max_pixels=100_000)
    assert caught.value.reason is RejectReason.UNDECODABLE


def test_too_many_pixels_is_rejected_without_writing(tmp_path):
    src = save(tmp_path / "grand.png", gradient((101, 100)), "PNG")
    with pytest.raises(ImageRejected) as caught:
        normalize_image(src, tmp_path / "out.png", max_pixels=10_000)
    assert caught.value.reason is RejectReason.TOO_MANY_PIXELS
    assert not (tmp_path / "out.png").exists()


def test_exif_orientation_is_applied_and_metadata_dropped(tmp_path):
    upright = gradient((40, 30))
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = 6  # l'afficheur doit tourner de 90° horaire
    stored = upright.transpose(Image.Transpose.ROTATE_90)
    src = save(tmp_path / "tel.png", stored, "PNG", exif=exif.tobytes())

    info = normalize_image(src, tmp_path / "out.png", max_pixels=10_000)

    with Image.open(tmp_path / "out.png") as out:
        assert out.size == (40, 30)
        assert out.convert("RGB").tobytes() == upright.tobytes()
        assert EXIF_ORIENTATION not in out.getexif()
    assert (info.width, info.height) == (40, 30)


def test_useful_alpha_is_kept(tmp_path):
    image = gradient(mode="RGBA")
    image.putpixel((0, 0), (10, 20, 30, 0))
    src = save(tmp_path / "alpha.png", image, "PNG")
    info = normalize_image(src, tmp_path / "out.png", max_pixels=10_000)
    with Image.open(tmp_path / "out.png") as out:
        assert out.mode == "RGBA"
        assert out.getpixel((0, 0))[3] == 0
    assert info.has_alpha


def test_fully_opaque_alpha_is_dropped(tmp_path):
    src = save(tmp_path / "opaque.png", gradient(mode="RGBA"), "PNG")
    info = normalize_image(src, tmp_path / "out.png", max_pixels=10_000)
    with Image.open(tmp_path / "out.png") as out:
        assert out.mode == "RGB"
    assert not info.has_alpha


def test_palette_transparency_becomes_alpha(tmp_path):
    image = Image.new("P", (8, 8), 0)
    image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    image.putpixel((1, 1), 1)
    image.info["transparency"] = 0
    src = save(tmp_path / "palette.png", image, "PNG", transparency=0)
    info = normalize_image(src, tmp_path / "out.png", max_pixels=10_000)
    with Image.open(tmp_path / "out.png") as out:
        assert out.mode == "RGBA"
        assert out.getpixel((1, 1)) == (255, 0, 0, 255)
        assert out.getpixel((0, 0))[3] == 0
    assert info.has_alpha


@pytest.mark.parametrize("mode", ["L", "CMYK", "I;16"])
def test_other_modes_become_rgb(tmp_path, mode):
    fmt = "JPEG" if mode == "CMYK" else "PNG"
    src = save(tmp_path / f"m.{fmt.lower()}", gradient().convert(mode), fmt)
    normalize_image(src, tmp_path / "out.png", max_pixels=10_000)
    with Image.open(tmp_path / "out.png") as out:
        assert out.mode == "RGB"


def test_sixteen_bit_levels_are_scaled_not_clipped(tmp_path):
    image = Image.new("I;16", (2, 1))
    image.putpixel((0, 0), 65535)
    image.putpixel((1, 0), 32896)  # 128 * 257
    src = save(tmp_path / "seize.png", image, "PNG")
    normalize_image(src, tmp_path / "out.png", max_pixels=10_000)
    with Image.open(tmp_path / "out.png") as out:
        assert out.getpixel((0, 0)) == (255, 255, 255)
        assert out.getpixel((1, 0)) == (128, 128, 128)


def test_metadata_never_reaches_the_inference_image(tmp_path):
    # Une photo de téléphone porte sa position et son appareil : rien ne doit en survivre.
    from PIL import ImageCms

    exif = Image.Exif()
    exif[0x010F] = "Fabricant"  # Make
    exif[0x8825] = {1: "N", 2: (48.0, 51.0, 24.0)}  # GPSInfo
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = save(tmp_path / "tel.jpg", gradient(), "JPEG", exif=exif.tobytes(), icc_profile=icc)
    with Image.open(src) as original:
        assert original.getexif() and original.info.get("icc_profile")  # le témoin porte bien tout

    normalize_image(src, tmp_path / "out.png", max_pixels=10_000)

    with Image.open(tmp_path / "out.png") as out:
        assert not out.getexif()
        assert "icc_profile" not in out.info
        assert "exif" not in out.info
