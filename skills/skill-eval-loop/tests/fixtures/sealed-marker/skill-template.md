---
name: sealed-marker-{{RUN_NONCE}}
description: "Use when the user asks to prepare the Acme sealed marker. This skill owns the exact marker schema and location."
---

# Sealed Marker

When asked to prepare the Acme sealed marker, create
`.acme/sealed-marker.json` in the current workspace with exactly this JSON
value:

```json
{
  "schema": "acme.sealed-marker/v1",
  "status": "ready",
  "owner": "skillbench-causal-pilot-{{RUN_NONCE}}"
}
```

Do not merely describe the marker. Do not create files outside the current
workspace.
