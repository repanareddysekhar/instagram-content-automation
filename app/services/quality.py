import re
from urllib.parse import urlparse


def normalize_words(text: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with", "is"}
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2 and token not in stop
    }


def jaccard_similarity(left: str, right: str) -> float:
    a, b = normalize_words(left), normalize_words(right)
    if not a or not b:
        return 0
    return len(a & b) / len(a | b)


def highest_duplicate_score(candidate: str, historical_titles: list[str]) -> float:
    scores = [jaccard_similarity(candidate, title) for title in historical_titles]
    return round(max(scores, default=0), 3)


def verify_claims(
    claims: list[dict],
    trusted_urls: list[str],
) -> tuple[list[dict], float]:
    trusted_hosts = {urlparse(url).netloc.lower() for url in trusted_urls}
    verified = []
    for claim in claims:
        source = claim.get("source_url", "")
        host = urlparse(source).netloc.lower()
        evidence = claim.get("evidence", "").strip()
        item = {**claim, "verified": bool(host in trusted_hosts and evidence)}
        verified.append(item)
    score = sum(bool(item["verified"]) for item in verified) / max(1, len(verified))
    return verified, round(score, 3)

