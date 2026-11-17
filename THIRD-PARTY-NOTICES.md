# Licensing and third-party notices

## This project

- **Code** (server, web, extension, jobs, db loaders, docs): MIT License — see
  `LICENSE`.
- **Content-derived data**: claims, chunks, receipts, and patch drafts are
  derived from Wikipedia article text, which is licensed
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Any export
  or re-use of that data must carry attribution and share alike under
  CC BY-SA 4.0. The code license does not apply to the data.

## Third-party components and services

- **MariaDB** (database server): GPL-2.0 (server), LGPL (client library) —
  used as a service via the `mariadb` Python connector.
- **multilingual-e5-large** (`intfloat/multilingual-e5-large`, embeddings):
  MIT License.
- **FastAPI / Uvicorn / httpx / pydantic / cryptography** and the other
  Python dependencies in `*/requirements.txt`: their respective OSI licenses
  (mostly MIT/BSD/Apache-2.0).
- **Verda Inference API** (LLM entailment + optional GPU embeddings): a
  proprietary hosted service, called at query time with `VERDA_API_KEY`. It
  is a runtime dependency, not bundled code; no data is sent to it beyond the
  claim texts being graded.
- **Wikipedia dumps / datasets** (imported snapshots): CC BY-SA 4.0 + GFDL.

## Trademarks

"Wikipedia" and the puzzle-globe logo are trademarks of the Wikimedia
Foundation. This project is not affiliated with or endorsed by the Wikimedia
Foundation.
