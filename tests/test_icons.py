"""The application icon: shipped resource, sizes, and the locked variant."""

from __future__ import annotations

from pathlib import Path

import pytest

from otpvault.ui import icons

# A deliberately non-square SVG, for the letterboxing path the shipped square
# artwork never exercises.
WIDE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50" width="100" height="50">'
    '<rect width="100" height="50" fill="#294a2f"/></svg>'
)


@pytest.fixture(autouse=True)
def clear_icon_cache():
    icons._cache.clear()  # noqa: SLF001
    yield
    icons._cache.clear()  # noqa: SLF001


def test_the_icon_ships_with_the_package() -> None:
    assert icons.ICON_PATH.is_file()
    assert icons.ICON_PATH.name == "qt-otp-icon.svg"
    assert icons.ICON_PATH.parent.name == "resources"


def test_the_shipped_artwork_is_valid_square_vector_art(qapp) -> None:
    renderer = icons._source_renderer()  # noqa: SLF001
    assert renderer is not None, "the shipped SVG must parse"
    size = renderer.defaultSize()
    assert size.width() == size.height(), "square art needs no letterboxing"


def test_app_icon_renders_at_every_size(qapp) -> None:
    icon = icons.app_icon()
    assert not icon.isNull()
    available = {size.width() for size in icon.availableSizes()}
    assert set(icons.ICON_SIZES) <= available

    for size in (16, 32, 256):
        pixmap = icon.pixmap(size, size)
        assert (pixmap.width(), pixmap.height()) == (size, size)
        assert not pixmap.isNull()


def test_app_icon_is_the_shipped_artwork_not_the_fallback(qapp) -> None:
    renderer = icons._source_renderer()  # noqa: SLF001
    expected = icons._square_pixmap(renderer, 64).toImage()  # noqa: SLF001
    assert icons.app_icon().pixmap(64, 64).toImage() == expected


def test_each_size_is_drawn_from_the_vector_source_not_downscaled(qapp) -> None:
    """A 256px render must carry detail a scaled-up 16px render could not."""
    icon = icons.app_icon()
    large = icon.pixmap(256, 256).toImage()
    upscaled = icon.pixmap(16, 16).scaled(256, 256).toImage()
    differing = sum(
        large.pixelColor(x, y) != upscaled.pixelColor(x, y)
        for y in range(0, 256, 4)
        for x in range(0, 256, 4)
    )
    assert differing > 256, "the large icon looks like a blown-up small one"


def test_locked_icon_is_a_greyed_variant(qapp) -> None:
    app_image = icons.app_icon().pixmap(64, 64).toImage()
    locked_image = icons.locked_icon().pixmap(64, 64).toImage()
    assert locked_image.size() == app_image.size()
    assert locked_image != app_image  # visibly different in the tray

    # The wash must not change the artwork's alpha shape.
    assert locked_image.pixelColor(0, 0).alpha() == app_image.pixelColor(0, 0).alpha()
    assert locked_image.pixelColor(32, 32).alpha() == app_image.pixelColor(32, 32).alpha()

    # ... and it should actually desaturate: less green dominance than the original.
    app_center = app_image.pixelColor(32, 32)
    locked_center = locked_image.pixelColor(32, 32)
    app_spread = max(app_center.red(), app_center.green(), app_center.blue()) - min(
        app_center.red(), app_center.green(), app_center.blue()
    )
    locked_spread = max(locked_center.red(), locked_center.green(), locked_center.blue()) - min(
        locked_center.red(), locked_center.green(), locked_center.blue()
    )
    assert locked_spread < app_spread


def test_icons_are_cached(qapp) -> None:
    assert icons.app_icon().cacheKey() == icons.app_icon().cacheKey()
    assert icons.app_icon().cacheKey() != icons.locked_icon().cacheKey()


def test_falls_back_to_the_drawn_icon_when_the_resource_is_missing(
    qapp, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(icons, "ICON_PATH", tmp_path / "not-here.svg")
    icon = icons.app_icon()
    assert not icon.isNull()
    assert icon.pixmap(32, 32).width() == 32


def test_falls_back_to_the_drawn_icon_when_the_svg_will_not_parse(
    qapp, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.svg"
    broken.write_text("<svg><this is not markup")
    monkeypatch.setattr(icons, "ICON_PATH", broken)
    assert icons._source_renderer() is None  # noqa: SLF001
    assert icons.app_icon().pixmap(32, 32).width() == 32


def test_every_size_is_square(qapp) -> None:
    """Taskbars and tray areas lay out square icons."""
    for icon in (icons.app_icon(), icons.locked_icon()):
        for size in icons.ICON_SIZES:
            pixmap = icon.pixmap(size, size)
            assert (pixmap.width(), pixmap.height()) == (size, size)
        assert all(s.width() == s.height() for s in icon.availableSizes())


def test_non_square_art_is_letterboxed_transparently_not_stretched(
    qapp, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wide = tmp_path / "wide.svg"
    wide.write_text(WIDE_SVG)
    monkeypatch.setattr(icons, "ICON_PATH", wide)

    image = icons.app_icon().pixmap(64, 64).toImage()
    assert image.pixelColor(32, 0).alpha() == 0  # padding at the top edge
    assert image.pixelColor(32, 32).alpha() == 255  # artwork through the middle
    assert image.pixelColor(0, 32).alpha() == 255  # 2:1 art spans the full width
