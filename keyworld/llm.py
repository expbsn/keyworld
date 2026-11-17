"""Sprachmodell fuer Einschaetzung und Dialog. Zurzeit DeepSeek.

OpenAI-kompatibles Format, direkt ueber requests -- keine weitere
Abhaengigkeit. Jeder Aufruf wird mit den von der API gemeldeten Tokens
abgerechnet, nicht geschaetzt; die Schaetzung dient nur der Anzeige vor
dem Klick.

Preise laut api-docs.deepseek.com (Stand November 2026), je 1 Mio. Tokens
zur Stosszeit. Ausserhalb der Stosszeit gilt die Haelfte.
"""
import json
import time
from datetime import datetime, timezone

import requests

from .config import Config

# Modell: (Eingabe Cache-Treffer, Eingabe sonst, Ausgabe) in USD je 1 Mio.
PREISE = {
    "deepseek-v4-pro": (0.044, 1.32, 3.96),
    "deepseek-flash": (0.006, 0.30, 1.20),
}
# Stosszeit in UTC, Montag bis Freitag. Chinesische Feiertage sind laut
# DeepSeek ebenfalls guenstig -- die werden hier nicht erkannt, die Rechnung
# faellt dann also hoechstens zu hoch aus, nie zu niedrig.
STOSSZEIT_STUNDEN = {1, 2, 3, 6, 7, 8, 9}


class LLMFehler(RuntimeError):
    pass


def ist_stosszeit(ts=None):
    t = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return t.weekday() < 5 and t.hour in STOSSZEIT_STUNDEN


def kosten(model, usage, ts=None):
    """USD fuer einen Aufruf aus dem usage-Block der Antwort."""
    hit_p, miss_p, out_p = PREISE.get(model, PREISE["deepseek-v4-pro"])
    faktor = 1.0 if ist_stosszeit(ts) else 0.5
    hit = usage.get("prompt_cache_hit_tokens") or 0
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = max(0, (usage.get("prompt_tokens") or 0) - hit)
    out = usage.get("completion_tokens") or 0
    return faktor * (hit * hit_p + miss * miss_p + out * out_p) / 1e6


def schaetzung(model, eingabe_tokens, ausgabe_tokens, ts=None):
    """Obergrenze vor dem Klick: alles als Cache-Fehlschlag, zur Stosszeit
    gerechnet, wenn gerade Stosszeit ist."""
    return kosten(model, {"prompt_cache_miss_tokens": eingabe_tokens,
                          "completion_tokens": ausgabe_tokens}, ts)


def tokens_ungefaehr(text):
    """Grob: deutsche Texte und JSON liegen bei etwa 3,5 Zeichen je Token."""
    return int(len(text) / 3.5) + 1


class DeepSeek:
    name = "deepseek"

    def __init__(self, api_key=None, base_url=None, timeout=240):
        self.api_key = api_key or Config.deepseek_api_key
        self.base_url = (base_url or Config.deepseek_base_url).rstrip("/")
        self.timeout = timeout

    def chat(self, messages, model=None, json_mode=False, denken=True,
             aufwand="high", max_tokens=8000):
        """-> (text, usage, model). Wirft LLMFehler mit lesbarer Meldung."""
        if not self.api_key:
            raise LLMFehler("DEEPSEEK_API_KEY fehlt in .env")
        model = model or Config.llm_model
        body = {"model": model, "messages": messages, "max_tokens": max_tokens,
                "thinking": {"type": "enabled" if denken else "disabled"}}
        if denken:
            body["reasoning_effort"] = aufwand
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            r = requests.post("%s/chat/completions" % self.base_url,
                              headers={"Authorization": "Bearer %s" % self.api_key,
                                       "Content-Type": "application/json"},
                              data=json.dumps(body), timeout=self.timeout)
        except requests.RequestException as e:
            raise LLMFehler("DeepSeek nicht erreichbar: %s" % type(e).__name__)
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message") or r.text[:200]
            except ValueError:
                msg = r.text[:200]
            if r.status_code == 402:
                msg = "Guthaben bei DeepSeek aufgebraucht"
            raise LLMFehler("DeepSeek %d: %s" % (r.status_code, msg))
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content") or "").strip()
        return text, data.get("usage") or {}, data.get("model") or model
