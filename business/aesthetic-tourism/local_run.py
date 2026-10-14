"""Local rehearsal: registers a throwaway tenant on a LOCAL backend, sets it up as a Medical Tourism business, approves a consent wording,
imports site/ through the builder and publishes it. Nothing here touches production. Usage: python3 local_run.py http://localhost:8020"""
import json, sys, urllib.request, urllib.error
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8020").rstrip("/")
ROOT = Path(__file__).parent / "site"

def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + "/api/v1" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

s, j = call("POST", "/auth/register", {"organization_name": "Aesthetic Tourism", "full_name": "Local Owner", "email": "owner@aesthetictourism-local.example", "password": "local-rehearsal-1"})
print("register", s)
if s != 201:
    s, j = call("POST", "/auth/login", {"email": "owner@aesthetictourism-local.example", "password": "local-rehearsal-1"}); print("login", s)
tok = j["tokens"]["access_token"] if "tokens" in j else j["access_token"]
tenant = (j.get("user") or {}).get("tenant_id")
s, j2 = call("POST", "/business-setup/medical-tourism", {"business_name": "Aesthetic Tourism", "business_model": "both", "patient_markets": ["US"], "destinations": ["TR", "TH", "KR", "IN"], "procedures": ["Dental", "Hair transplant", "Aesthetic surgery"]}, tok)
print("mt setup", s, json.dumps(j2)[:200])
text = "I agree that Aesthetic Tourism may store my name, email address, the treatment I am interested in and the notes I enter on this form, and contact me about my quote request. I understand the treatment and notes may relate to my health. I can withdraw this at any time by contacting Aesthetic Tourism."
s, j3 = call("POST", "/consent-wordings", {"text": text}, tok); print("consent wording", s, j3.get("version"))
files = {str(p.relative_to(ROOT)): p.read_text() for p in ROOT.rglob("*") if p.is_file()}
s, j4 = call("POST", "/builder/projects/import", {"name": "Aesthetic Tourism site", "files": files}, tok); print("import", s, j4.get("validation", j4) if s != 201 else "ok", )
pid = j4["project"]["id"]; slug = j4["project"]["slug"]
s, j5 = call("POST", f"/builder/projects/{pid}/publish", {"acknowledge_examples": True}, tok); print("publish", s, json.dumps(j5)[:200])
print("SITE", f"{BASE}/api/v1/public/sites/{tenant}/{slug}/")
