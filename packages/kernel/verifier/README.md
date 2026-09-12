# brokAId — offline trAIl verifier

Pure-Node script. Reads an export JSON from `/api/trail/export`,
recomputes each event hash and prev_hash link, exits 0 on success or
1 on the first failure with the offending seq.

```
node packages/kernel/verifier/verify.js path/to/export.json
```

Depends on the `canonicalize` npm package (RFC 8785 JCS). The
equivalent in-process Python verifier lives at
`services/api/app/kernel/verifier.py` and produces identical results.
