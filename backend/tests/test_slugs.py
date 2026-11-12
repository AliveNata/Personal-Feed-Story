from app.library.slugs import assign_slugs, base_slug


def test_base_slug_is_readable_ascii() -> None:
    # Arrange / Act / Assert
    assert base_slug("Ayu Sastrani") == "ayu-sastrani"
    assert base_slug("  Liburan Bali 2026!! ") == "liburan-bali-2026"
    assert base_slug("Café Ñusa") == "cafe-nusa"
    assert base_slug("🌴🌴") == "folder"
    assert base_slug("2026") == "folder-2026"


def test_clashing_slugs_keep_the_oldest_plain() -> None:
    # Arrange
    folders = [(7, "ayu-sastrani"), (3, "Ayu Sastrani"), (9, "Raneey")]

    # Act
    slugs = assign_slugs(folders)

    # Assert
    assert slugs == {3: "ayu-sastrani", 7: "ayu-sastrani-7", 9: "raneey"}
