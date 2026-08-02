# PhoneFit idempotent — local Vite + Manager proof

Date: 2026-08-02
UI: branch Vite `http://127.0.0.1:5190` (this SHA)
API: Manager `http://127.0.0.1:18115`

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
- [PASS] mobile workspace ready
- [PASS] fullscreen control present
- [PASS] fullscreen dialog open
- [PASS] viewport panel open
- [PASS] 390x844 profile available
- [PASS] first Phone fit settled
- [PASS] first Phone fit size 390x844
- [PASS] re-tap settled
- [PASS] re-tap no restart claim
- [PASS] re-tap size still 390x844
- [PASS] re-tap no update/stop/launch traffic
- [PASS] re-tap single canvas (content)
- [PASS] screenshot written

Screenshot: `docs/evidence/phonefit-idempotent-local-retap-2026-08-02.png`
SHA-256: `bae3257260d6a5336a787907e8a59916cc870c543794ee1411bb85d2b9a862ea`

![PhoneFit re-tap](../evidence/phonefit-idempotent-local-retap-2026-08-02.png)
