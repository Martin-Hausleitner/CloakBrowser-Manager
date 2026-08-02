# PhoneFit idempotent — local Vite + Manager proof

Date: 2026-08-02
UI: branch Vite `http://127.0.0.1:5190` (this SHA)
API: Manager `http://127.0.0.1:18115`
Script: `scripts/phonefit_idempotent_retap_proof.py` (re-runnable)

## Outcome: **PASS**

### Profile
```json
{
  "id": "a8b99a1f-bd77-4249-917f-0ad681ea5519",
  "name": "VCVM Mobile Demo",
  "status": "running",
  "screen_width": 390,
  "screen_height": 844
}
```

### Checks
- [PASS] fullscreen control present/open
- [PASS] viewport panel open
- [PASS] Phone fit available
- [PASS] first Phone fit settled
- [PASS] first Phone fit size 390x844
- [PASS] re-tap settled
- [PASS] re-tap no restart claim
- [PASS] re-tap size still 390x844
- [PASS] re-tap no update/stop/launch traffic
- [PASS] re-tap single canvas (content)
- [PASS] screenshot written

Machine-readable: `docs/reports/PHONEFIT-IDEMPOTENT-LOCAL-PROOF-2026-08-02.json`

Screenshot: `docs/evidence/phonefit-idempotent-local-retap-2026-08-02.png`
SHA-256: `bae3257260d6a5336a787907e8a59916cc870c543794ee1411bb85d2b9a862ea`

![PhoneFit re-tap](../evidence/phonefit-idempotent-local-retap-2026-08-02.png)

### Re-run
```bash
# terminal 1
cd frontend && CLOAK_API_PROXY_TARGET=http://127.0.0.1:18115 npm run dev -- --host 127.0.0.1 --port 5190
# terminal 2
set -a; source /path/to/.env.vcvm; set +a
python3 scripts/phonefit_idempotent_retap_proof.py
```
