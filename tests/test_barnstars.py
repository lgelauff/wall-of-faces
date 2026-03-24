"""Tests for src/barnstars.py"""
import pytest
from src.barnstars import detect_barnstars, REGISTRY, MAX_BARNSTARS


class TestRegistry:
    def test_all_keys_lowercase(self):
        for key in REGISTRY:
            assert key == key.lower(), f"Key not lowercase: {key!r}"

    def test_no_empty_names(self):
        for key, name in REGISTRY.items():
            assert name.strip(), f"Empty name for {key!r}"


class TestDetectBarnstars:
    def test_bestand_prefix_detected(self):
        result = detect_barnstars(["[[Bestand:Original barnstar.png|50px]]"])
        assert len(result) == 1
        assert result[0]["filename"] == "Original barnstar.png"
        assert result[0]["name"] == "Ster van verdienste"

    def test_file_prefix_detected(self):
        result = detect_barnstars(["[[File:writers_barnstar_hires.png]]"])
        assert len(result) == 1
        assert result[0]["name"] == "Schrijversster"

    def test_case_insensitive_filename(self):
        result = detect_barnstars(["Bestand:ORIGINAL BARNSTAR.PNG"])
        assert len(result) == 1

    def test_case_insensitive_prefix(self):
        result = detect_barnstars(["bestand:Original barnstar.png"])
        assert len(result) == 1

    def test_unknown_filename_ignored(self):
        result = detect_barnstars(["Bestand:Some_random_image.jpg"])
        assert result == []

    def test_deduplication_across_pages(self):
        page1 = "[[Bestand:Original barnstar.png]]"
        page2 = "[[Bestand:Original barnstar.png]] again"
        result = detect_barnstars([page1, page2])
        assert len(result) == 1

    def test_cap_at_max_barnstars(self):
        # Put every registry key in one big wikitext
        all_wikitext = "\n".join(f"Bestand:{k}" for k in REGISTRY)
        result = detect_barnstars([all_wikitext])
        assert len(result) == MAX_BARNSTARS

    def test_empty_pages_ignored(self):
        result = detect_barnstars(["", "", ""])
        assert result == []

    def test_empty_list(self):
        assert detect_barnstars([]) == []

    def test_caption_stripped(self):
        # 'Bestand:Original barnstar.png|50px|thumb' — only filename before |
        result = detect_barnstars(["[[Bestand:Original barnstar.png|50px|thumb]]"])
        assert len(result) == 1
        assert "|" not in result[0]["filename"]

    def test_multiple_unique_barnstars(self):
        wikitext = (
            "Bestand:Original barnstar.png\n"
            "Bestand:writers_barnstar_hires.png\n"
        )
        result = detect_barnstars([wikitext])
        assert len(result) == MAX_BARNSTARS  # 2
        names = {r["name"] for r in result}
        assert "Ster van verdienste" in names
        assert "Schrijversster" in names

    def test_multiple_pages_combined(self):
        page1 = "Bestand:Original barnstar.png"
        page2 = "Bestand:writers_barnstar_hires.png"
        result = detect_barnstars([page1, page2])
        assert len(result) == 2

    def test_result_has_required_keys(self):
        result = detect_barnstars(["[[Bestand:Original barnstar.png]]"])
        for item in result:
            assert "filename" in item
            assert "name" in item

    def test_image_prefix_detected(self):
        result = detect_barnstars(["[[Image:Original barnstar.png]]"])
        assert len(result) == 1

    def test_none_in_list_does_not_raise(self):
        result = detect_barnstars([None, "Bestand:Original barnstar.png", None])
        assert len(result) == 1

    def test_fragment_stripped(self):
        result = detect_barnstars(["Bestand:Original barnstar.png#anchor"])
        assert len(result) == 1
        assert "#" not in result[0]["filename"]
