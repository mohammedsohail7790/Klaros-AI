"""Deterministic chunking (app/services/knowledge_retrieval_service.py::chunk_text)."""

from app.services.knowledge_retrieval_service import chunk_text


def test_empty_content_produces_no_chunks() -> None:
    assert chunk_text("", chunk_size=100, overlap=20) == []
    assert chunk_text("   ", chunk_size=100, overlap=20) == []


def test_short_content_fits_in_one_chunk() -> None:
    content = "The quick brown fox jumps over the lazy dog."
    chunks = chunk_text(content, chunk_size=200, overlap=20)
    assert len(chunks) == 1
    assert chunks[0] == content


def test_long_content_splits_into_multiple_chunks_within_size() -> None:
    content = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(content, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 100 + len("word499")  # allow one word's slack over the boundary


def test_chunking_is_deterministic_across_repeated_calls() -> None:
    content = " ".join(f"token{i}" for i in range(300))
    first = chunk_text(content, chunk_size=150, overlap=30)
    second = chunk_text(content, chunk_size=150, overlap=30)
    assert first == second


def test_chunks_overlap_by_roughly_the_configured_amount() -> None:
    content = " ".join(f"w{i}" for i in range(200))
    chunks = chunk_text(content, chunk_size=100, overlap=30)
    assert len(chunks) >= 2
    # The end of chunk N should share at least one word with the start of chunk N+1.
    first_words = set(chunks[0].split())
    second_words = chunks[1].split()
    assert any(w in first_words for w in second_words[:5])


def test_all_original_words_are_preserved_across_chunks() -> None:
    words = [f"unique{i}" for i in range(300)]
    content = " ".join(words)
    chunks = chunk_text(content, chunk_size=120, overlap=25)
    covered = set()
    for c in chunks:
        covered.update(c.split())
    assert set(words) <= covered


def test_overlap_greater_than_or_equal_to_chunk_size_still_terminates() -> None:
    content = " ".join(f"x{i}" for i in range(100))
    # Pathological config: must never hang or loop forever.
    chunks = chunk_text(content, chunk_size=20, overlap=1000)
    assert len(chunks) > 0


def test_stable_ordering_matches_source_order() -> None:
    words = [f"seq{i}" for i in range(50)]
    content = " ".join(words)
    chunks = chunk_text(content, chunk_size=40, overlap=5)
    # First word of the content must appear in the first chunk.
    assert words[0] in chunks[0]
    # Last word of the content must appear in the last chunk.
    assert words[-1] in chunks[-1]
