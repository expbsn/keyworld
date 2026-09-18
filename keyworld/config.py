"""Konfiguration aus .env / Umgebung. Keine Fremdabhaengigkeit."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"


def _load_dotenv(path=None):
    path = Path(path or ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


class Config:
    dataforseo_login = os.environ.get("DATAFORSEO_LOGIN", "")
    dataforseo_password = os.environ.get("DATAFORSEO_PASSWORD", "")
    location_code = int(os.environ.get("KEYWORLD_LOCATION_CODE", 2276))
    language_code = os.environ.get("KEYWORLD_LANGUAGE_CODE", "de")
    run_budget_usd = float(os.environ.get("KEYWORLD_RUN_BUDGET_USD", 2.0))
    cache_ttl_days = int(os.environ.get("KEYWORLD_CACHE_TTL_DAYS", 30))
    db_path = DATA_DIR / "keyworld.db"

    @classmethod
    def has_dataforseo(cls):
        return bool(cls.dataforseo_login and cls.dataforseo_password)

    @classmethod
    def market(cls):
        return "%s/%s" % (cls.location_code, cls.language_code)


DATA_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)
