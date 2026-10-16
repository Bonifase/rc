"""
Central configuration. Every other module reads settings from here rather
than calling os.environ directly, so there's one place to change defaults
or add validation later.
"""
import hashlib
import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - offline/mock execution only
    def load_dotenv(*args, **kwargs):
        return False

load_dotenv()

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "").strip()
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "").strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


PINNED_ARTIFACT_TEXT = {
    "prompt_01": "offline prompt 01 deterministic bundle",
    "prompt_02": "offline prompt 02 deterministic bundle",
    "prompt_03_txt": "offline prompt 03.txt deterministic bundle",
    "prompt_04": "offline prompt 04 deterministic bundle",
    "prompt_04_addendum": "offline prompt 04 addendum deterministic bundle",
    "mensaje_chat2": "offline mensaje_chat2 deterministic bundle",
    "mensaje_chat3": "offline mensaje_chat3 deterministic bundle",
    "template_aprobada_20260908": "offline template APROBADA_20260908 deterministic bundle",
    "verificar_js": "offline verificar.js deterministic bundle",
    "ensamblar_master_dataset_py": "offline ensamblar_master_dataset.py deterministic bundle",
    "pp01_master": "offline PP-01 master deterministic bundle",
    "pp01_qa": "offline PP-01 QA deterministic bundle",
    "pp01_payloads_outputs": "offline PP-01 payloads outputs deterministic bundle",
    "zablon_updated_bundle": "offline updated bundle from Zablón deterministic bundle",
}

PINNED_ARTIFACTS = {name: _sha256(value) for name, value in PINNED_ARTIFACT_TEXT.items()}
ARTIFACT_HASHES = PINNED_ARTIFACTS.copy()


def validate_pinned_artifacts(artifacts: dict[str, str] | None = None) -> dict[str, object]:
    manifest = PINNED_ARTIFACTS if artifacts is None else dict(artifacts)
    mismatches = []
    for name, expected in PINNED_ARTIFACTS.items():
        actual = manifest.get(name)
        if actual is None:
            mismatches.append({"artifact": name, "expected": expected, "actual": None, "status": "missing"})
            continue
        if actual != expected:
            mismatches.append({"artifact": name, "expected": expected, "actual": actual, "status": "mismatch"})
    return {
        "status": "pass" if not mismatches else "fail",
        "checked": len(PINNED_ARTIFACTS),
        "mismatches": mismatches,
        "pinned_artifacts": dict(PINNED_ARTIFACTS),
    }

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