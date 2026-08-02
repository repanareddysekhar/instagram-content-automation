from app.services.quality import (
    highest_duplicate_score,
    jaccard_similarity,
    verify_claims,
)


def test_duplicate_score_detects_same_topic() -> None:
    candidate = "OpenAI launches a smaller model for production AI"
    historical = [
        "A practical guide to cloud security",
        "OpenAI launches smaller AI model for production",
    ]
    assert highest_duplicate_score(candidate, historical) > 0.7


def test_unrelated_titles_are_not_duplicates() -> None:
    assert jaccard_similarity(
        "New database indexing technique",
        "Why design systems improve product consistency",
    ) < 0.2


def test_claims_require_trusted_host_and_evidence() -> None:
    claims, score = verify_claims(
        [
            {
                "text": "Supported",
                "source_url": "https://research.example.com/post",
                "evidence": "The paper reports the result.",
            },
            {
                "text": "Unsupported",
                "source_url": "https://random.example.net/post",
                "evidence": "",
            },
        ],
        ["https://research.example.com/post"],
    )
    assert claims[0]["verified"] is True
    assert claims[1]["verified"] is False
    assert score == 0.5

