"""
Central configuration. Every other module reads settings from here rather
than calling os.environ directly, so there's one place to change defaults
or add validation later.
"""
import os
from dotenv import load_dotenv

load_dotenv()

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "").strip()
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "").strip()

CROSSREF_BASE_URL = "https://api.crossref.org/works"
NCBI_EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_ID_CONVERTER_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

# Extended retrieval endpoints (title resolution + open-access fallbacks)
EUROPE_PMC_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL_BASE_URL = "https://api.unpaywall.org/v2"
OPENALEX_BASE_URL = "https://api.openalex.org/works"

# Shared HTTP settings
REQUEST_TIMEOUT_SECONDS = 15
MAX_RETRIES = 3

# LLM settings
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()  # set OPENAI_MODEL to switch models in production without editing code
LLM_MAX_RETRIES = 3
LLM_GUARD_REROLL_TEMPERATURE = 0.3  # the guard's one re-roll samples a fresh quote instead of repeating the deterministic temp-0 answer

# temperature=0 alone does NOT guarantee bit-identical output from the
# OpenAI API (backend batching / MoE routing can still vary the response).
# Passing a fixed seed makes the *first* (temp-0) call best-effort
# deterministic across runs -- OpenAI documents this as "mostly" but not
# 100% guaranteed, so treat it as a large reduction in flakiness, not a
# hard guarantee. The guard's re-roll deliberately samples at a different
# temperature to get a genuinely fresh attempt, so it intentionally does
# NOT reuse this seed.
LLM_SEED = int(os.getenv("LLM_SEED", "42"))


def require_contact_email() -> str:
    """
    Crossref and NCBI both expect a real contact identifier for polite-pool /
    higher rate-limit access. Fail loudly at call time rather than silently
    sending unidentified traffic that's more likely to get throttled.
    """
    if not CONTACT_EMAIL:
        raise RuntimeError(
            "CONTACT_EMAIL is not set. Add it to your .env file — "
            "Crossref and NCBI use it for polite-pool access."
        )
    return CONTACT_EMAIL