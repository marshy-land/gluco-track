# Explorer 2 Handoff Report — Literature APIs & Citation Data Model Design

## 1. Observation

### 1.1 Reference Documents & System Boundaries
- **Reference Files Inspected**:
  - `c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\ORIGINAL_REQUEST.md`: Requirement R1 specifies programmatically querying scientific APIs (PubMed, OpenAlex) to find relevant medical literature and generate a customized research report suggesting dietary remedies tailored to specific data trends.
  - `c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\orchestrator\PROJECT.md`: Feature 3 ("R1 Literature Search Pipeline: Query PubMed E-utilities and OpenAlex APIs for peer-reviewed dietary interventions") and Feature 4 ("R1 Dietary Report Generator: Generate `dietary_remedies_report.md` with explicit data metrics, interventions, citations"). Interface dictates code placement in `literature_api.py` and `dietary_analysis.py`.
  - `c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\sub_orch_m1\SCOPE.md`: Deliverable 2 requires scientific API integration with PubMed and OpenAlex, robust offline caching/fallback mechanisms, and citation metadata extraction (title, authors, journal, year, PMID, DOI link).

### 1.2 Direct API Probing Observations
Live programmatic testing of PubMed (NCBI E-utilities) and OpenAlex APIs via Python `urllib` yielded the following concrete observations:
1. **PubMed ESearch Endpoint (`esearch.fcgi`)**:
   - Executed query: `term=(diabetes OR hyperglycemia OR "dawn phenomenon") AND (diet OR dietary OR fiber OR "glycemic index" OR cinnamon OR vinegar)&retmode=json&retmax=5`.
   - Result: HTTP 200 OK. Total hit count returned: `131,335`. Returned sample PMIDs: `['42547443', '42547437', '42547409', '42547104', '42547103']`.
2. **PubMed ESummary Endpoint (`esummary.fcgi`)**:
   - Executed query: `id=42547443,42547437&retmode=json`.
   - Result: Returns JSON object keyed by PMID (`result["42547443"]`). Contains fields: `title`, `source` (Journal), `pubdate`, `authors` (list of `{"name": "...", "authtype": "Author"}`), and `articleids` (list of `{"idtype": "doi"|"pubmed", "value": "..."}`).
3. **PubMed EFetch Endpoint (`efetch.fcgi`)**:
   - Executed query: `id=42547443&retmode=xml`.
   - Result: Returns XML payload `<PubmedArticleSet><PubmedArticle><MedlineCitation>...`. Abstracts are stored in `<Article><Abstract><AbstractText>` (with optional `Label` attributes for structured abstracts like `BACKGROUND`, `METHODS`, `RESULTS`, `CONCLUSION`).
4. **OpenAlex Works Endpoint (`https://api.openalex.org/works`)**:
   - Executed query: `search=dawn phenomenon dietary bedtime snack diabetes&per_page=3&select=id,doi,title,publication_year,cited_by_count,primary_location,authorships,abstract_inverted_index,ids&mailto=test@example.com`.
   - Result: HTTP 200 OK. Meta count: `75`.
   - Sample Hit #2: PMID `17712024`, DOI `https://doi.org/10.2337/dc07-1062`, Title `"Vinegar Ingestion at Bedtime Moderates Waking Glucose Concentrations in Adults With Well-Controlled Type 2 Diabetes"`, Journal `"Diabetes Care"`, Authors `['Andrea White', 'Carol S. Johnston']`.
   - Sample Hit #3: PMID `10205584`, DOI `https://doi.org/10.1046/j.1365-2796.1999.00432.x`, Title `"Bedtime uncooked cornstarch supplement prevents nocturnal hypoglycaemia in intensively treated type 1 diabetes subjects"`, Journal `"Journal of Internal Medicine"`.
   - Abstract Structure: OpenAlex stores abstracts as `abstract_inverted_index` mapping words to integer token position arrays, requiring algorithmic reconstruction.

---

## 2. Logic Chain

1. **API Endpoints & Request Specifications**:
   - **NCBI PubMed E-utilities**:
     - `ESearch`: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=<QUERY>&retmode=json&retmax=5&tool=GlucoTrack&email=<EMAIL>`
     - `ESummary`: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=<PMIDS>&retmode=json`
     - `EFetch`: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=<PMIDS>&retmode=xml`
     - Rate Limits: 3 req/sec default; 10 req/sec with `NCBI_API_KEY`.
   - **OpenAlex Works API**:
     - Endpoint: `https://api.openalex.org/works?search=<QUERY>&per_page=5&select=id,doi,title,publication_year,cited_by_count,primary_location,authorships,abstract_inverted_index,ids&mailto=<EMAIL>`
     - Rate Limits: Polite pool (with `mailto`): 10 req/sec, 100,000 req/day.
   - **Targeted Anomaly Queries**:
     - *Dawn Phenomenon*: `("dawn phenomenon"[tiab] OR "fasting hyperglycemia"[tiab]) AND (vinegar[tiab] OR "acetic acid"[tiab] OR "bedtime snack"[tiab] OR "uncooked cornstarch"[tiab] OR protein[tiab])`
     - *Postprandial Spikes*: `("postprandial hyperglycemia"[tiab] OR "postprandial glucose spike"[tiab]) AND ("dietary fiber"[tiab] OR "viscous fiber"[tiab] OR "protein preload"[tiab] OR cinnamon[tiab] OR berberine[tiab])`
     - *Nocturnal Hypos*: `("nocturnal hypoglycemia"[tiab] OR "nighttime hypoglycemia"[tiab]) AND ("uncooked cornstarch"[tiab] OR "bedtime snack"[tiab] OR "complex carbohydrates"[tiab])`
     - *Glycemic Variability*: `("glycemic variability"[tiab] OR "glucose fluctuations"[tiab]) AND ("mediterranean diet"[tiab] OR "low carbohydrate diet"[tiab] OR "high fiber"[tiab] OR "meal timing"[tiab])`

2. **Caching Strategy & Multi-Tier Fallback Architecture**:
   - **Local SQLite Cache**:
     - Database Table: `literature_cache(cache_key VARCHAR(64) PRIMARY KEY, api_name VARCHAR(32), query_text TEXT, anomaly_category VARCHAR(64), response_json TEXT, created_at TIMESTAMP, expires_at TIMESTAMP)`
     - Key Generation: `SHA-256(api_name + ":" + normalized_query)`
     - TTL Policy: 30 days (literature articles are static).
   - **Multi-Tier Fallback Strategy**:
     - *Tier 1*: Local Cache Lookup -> Return cached citations on hit.
     - *Tier 2*: Primary API (PubMed E-utilities) -> On success, save to cache and return.
     - *Tier 3*: Secondary API (OpenAlex Works) -> Triggered if PubMed returns 0 results, 429 rate limit, or HTTP errors.
     - *Tier 4*: Pre-Populated Offline Landmark Database -> Built-in dictionary of verified landmark studies for each anomaly category (e.g., White & Johnston 2007 for Dawn Phenomenon; Axelsen et al. 1999 for Nocturnal Hypos; Jenkins et al. 2015 for Postprandial Spikes; Giugliano et al. 2014 for Glycemic Variability). Guarantees zero failures even if offline or un-networked.
   - **Exponential Backoff**:
     - On HTTP 429/503: retry up to 3 times with wait time `2^attempt + jitter`.

3. **Citation Data Model**:
   - `Citation` Python dataclass / Pydantic model with attributes: `title`, `authors`, `journal`, `publication_year`, `pmid`, `doi`, `abstract`, `source_api`, `relevance_score`, `anomaly_category`, `remedy_summary`.
   - Properties `doi_url` (`https://doi.org/<doi>`) and `pubmed_url` (`https://pubmed.ncbi.nlm.nih.gov/<pmid>/`).
   - Methods: `to_markdown()` for report rendering, `to_dict()`, `from_pubmed_json()`, `from_openalex_json()`.

---

## 3. Caveats

- **API Rate Limits Without Keys**: Default NCBI rate limit is 3 requests/second. If running parallel queries without an `NCBI_API_KEY`, a 250ms sleep delay between requests or local caching is required to prevent HTTP 429.
- **XML vs JSON for PubMed Abstracts**: ESummary JSON does not provide the full abstract text; EFetch XML is required if full abstracts are needed. However, for citation metadata (Title, Authors, Journal, Year, PMID, DOI), ESummary JSON is sufficient and faster.
- **OpenAlex Inverted Index**: Abstract text requires dictionary sorting of `abstract_inverted_index` positions.

---

## 4. Conclusion

We recommend building `literature_api.py` around the `Citation` dataclass, implementing a 4-tier resilience architecture (Cache -> PubMed -> OpenAlex -> Offline Landmark DB). This guarantees fast, peer-reviewed, literature-backed dietary recommendations for all 4 glycemic anomaly types under any network condition.

---

## 5. Verification Method

### 5.1 Verification Commands
1. Test PubMed API retrieval:
   ```bash
   python -c "import urllib.request, json; resp=urllib.request.urlopen('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=dawn+phenomenon+vinegar&retmode=json'); print(json.loads(resp.read())['esearchresult']['idlist'])"
   ```
2. Test OpenAlex API retrieval & inverted index reconstruction:
   ```bash
   python -c "import urllib.request, json; resp=urllib.request.urlopen('https://api.openalex.org/works?search=nocturnal+hypoglycemia+cornstarch&per_page=1'); w=json.loads(resp.read())['results'][0]; print(w['title'], w['ids'].get('pmid'))"
   ```
3. Test suite verification (once implemented by implementer):
   ```bash
   pytest tests/test_literature_api.py
   ```

### 5.2 Invalidation Conditions
- Changes to NCBI E-utilities response schemas or mandatory parameters.
- Discontinuation of OpenAlex free polite pool access.
- Network requests timing out without fallback to Tier 4 offline landmark literature database.
