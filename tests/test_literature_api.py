"""
Unit tests for literature_api.py (Milestone M1 / Requirement R1)

Tests:
  - Citation dataclass properties (pubmed_url, doi_url, formatters)
  - URL structure compliance (https://pubmed.ncbi.nlm.nih.gov/<PMID>/ and https://doi.org/<DOI>)
  - 4-Tier Resilience Strategy:
      Tier 1: In-memory / SQLite Cache
      Tier 2: PubMed API (mocked)
      Tier 3: OpenAlex API (mocked)
      Tier 4: Offline Landmark Database
"""

import os
import pytest
from unittest.mock import patch, MagicMock

import literature_api
from literature_api import Citation, fetch_literature_for_anomaly, fetch_literature_for_anomalies


@pytest.fixture(autouse=True)
def reset_cache_state(tmp_path):
    """
    Autouse fixture executing before and after every test in this module.
    Redirects SQLite cache to a fresh temporary file and resets in-memory cache.
    Guarantees 100% test isolation and zero cross-test state leakage.
    """
    temp_db = str(tmp_path / "test_literature_cache.db")
    literature_api.set_db_cache_file(temp_db)
    literature_api.clear_cache()
    yield
    literature_api.clear_cache()


def test_citation_data_model_and_urls():
    citation = Citation(
        title="Test Article Title",
        authors=["Author A", "Author B"],
        journal="Journal of Diabetes",
        year=2024,
        pmid="12345678",
        doi="10.1000/xyz123",
        summary="Summary text.",
        anomaly_category="postprandial_spike"
    )

    # Verify URL properties
    assert citation.pubmed_url == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert citation.doi_url == "https://doi.org/10.1000/xyz123"

    # Verify Markdown formatters
    assert citation.format_pmid_link() == "[12345678](https://pubmed.ncbi.nlm.nih.gov/12345678/)"
    assert citation.format_doi_link() == "[10.1000/xyz123](https://doi.org/10.1000/xyz123)"


def test_citation_url_formatting_with_prefixed_doi():
    citation = Citation(
        title="Test Article Title",
        authors=["Author A"],
        journal="Journal of Diabetes",
        year=2024,
        pmid="999999",
        doi="https://doi.org/10.1000/prefixed",
        summary="Summary text.",
        anomaly_category="dawn_phenomenon"
    )

    assert citation.doi_url == "https://doi.org/10.1000/prefixed"
    assert citation.format_doi_link() == "[10.1000/prefixed](https://doi.org/10.1000/prefixed)"


def test_citation_missing_identifiers():
    citation = Citation(
        title="No Identifiers Study",
        authors=["Author A"],
        journal="Journal of Diabetes",
        year=2020,
        pmid=None,
        doi=None,
        summary="Summary text.",
        anomaly_category="nocturnal_hypo"
    )

    assert citation.pubmed_url is None
    assert citation.doi_url is None
    assert citation.format_pmid_link() == "N/A"
    assert citation.format_doi_link() == "N/A"


def test_tier_4_offline_landmark_database():
    """Verify Tier 4 offline landmark database returns valid citations when network is disabled."""
    categories = ["postprandial_spike", "dawn_phenomenon", "nocturnal_hypo", "high_glycemic_variability"]
    
    for cat in categories:
        citations = fetch_literature_for_anomaly(cat, use_network=False)
        assert len(citations) > 0
        for c in citations:
            assert isinstance(c, Citation)
            assert c.title != ""
            assert c.pubmed_url is not None or c.doi_url is not None
            if c.pmid:
                assert c.pubmed_url.startswith("https://pubmed.ncbi.nlm.nih.gov/")
            if c.doi:
                assert c.doi_url.startswith("https://doi.org/")


def test_tier_1_caching_mechanism():
    """Verify Tier 1 cache returns cached citations on subsequent requests."""
    cache_key = "test_category_cache:default"
    literature_api._IN_MEMORY_CACHE.clear()

    # Seed in-memory cache
    test_citation = Citation(
        title="Cached Landmark Study",
        authors=["Cached Author"],
        journal="Cache Journal",
        year=2025,
        pmid="11111111",
        doi="10.1111/cache",
        summary="Cached summary.",
        anomaly_category="postprandial_spike"
    )
    literature_api._IN_MEMORY_CACHE[cache_key] = [test_citation]

    results = fetch_literature_for_anomaly("test_category_cache", use_network=True)
    assert len(results) == 1
    assert results[0].title == "Cached Landmark Study"
    assert results[0].pmid == "11111111"


@patch("literature_api.query_pubmed_api")
def test_tier_2_pubmed_api_fallback(mock_pubmed):
    """Verify Tier 2 PubMed API is called when cache misses."""
    literature_api.clear_cache()
    
    mock_citation = Citation(
        title="PubMed Study",
        authors=["PubMed Author"],
        journal="PubMed Journal",
        year=2024,
        pmid="22222222",
        doi="10.2222/pubmed",
        summary="PubMed summary.",
        anomaly_category="dawn_phenomenon"
    )
    mock_pubmed.return_value = [mock_citation]

    results = fetch_literature_for_anomaly("dawn_phenomenon_test_t2", use_network=True)
    assert len(results) == 1
    assert results[0].title == "PubMed Study"
    mock_pubmed.assert_called_once()


@patch("literature_api.query_pubmed_api")
@patch("literature_api.query_openalex_api")
def test_tier_3_openalex_fallback(mock_openalex, mock_pubmed):
    """Verify Tier 3 OpenAlex API is called if PubMed returns empty results."""
    literature_api.clear_cache()
    mock_pubmed.return_value = []  # PubMed returns nothing

    mock_citation = Citation(
        title="OpenAlex Study",
        authors=["OpenAlex Author"],
        journal="OpenAlex Journal",
        year=2023,
        pmid="33333333",
        doi="10.3333/openalex",
        summary="OpenAlex summary.",
        anomaly_category="nocturnal_hypo"
    )
    mock_openalex.return_value = [mock_citation]

    results = fetch_literature_for_anomaly("nocturnal_hypo_test_t3", use_network=True)
    assert len(results) == 1
    assert results[0].title == "OpenAlex Study"
    mock_pubmed.assert_called_once()
    mock_openalex.assert_called_once()


def test_fetch_literature_for_anomalies_bulk():
    """Verify bulk fetching for multiple anomaly categories."""
    cats = ["postprandial_spike", "nocturnal_hypo"]
    res = fetch_literature_for_anomalies(cats, use_network=False)
    assert "postprandial_spike" in res
    assert "nocturnal_hypo" in res
    assert len(res["postprandial_spike"]) > 0
    assert len(res["nocturnal_hypo"]) > 0
