---
id: demo-cache
name: Cache
template: jargon-default
fields:
  alias: ""
  synonymy: buffer (loosely)
  polysemy: in hardware, also the fast memory inside a CPU (L1/L2/L3)
tags:
- Concept
- Performance
attachments: []
created: 1720000300.0
updated: 1720000300.0
history: []
---

**Store a result you already computed or fetched**, so next time you can grab it directly instead of redoing the work or re-querying.

The cost is that the stored data can go **stale** — so the hard part isn't building a cache, it's deciding when to invalidate it. As the saying goes:

> There are only two hard things in computer science: cache invalidation and naming things.

This project's `SQLite` search index is exactly this kind of cache: if it breaks, delete it and rebuild — the `.md` files are the source of truth.
