---
id: demo-rest
name: REST
template: jargon-default
fields:
  alias: Representational State Transfer
  synonymy: RESTful API
  polysemy: ""
tags:
- Concept
- Networking
attachments: []
created: 1720000100.0
updated: 1720000100.0
history: []
---

A **style** for designing Web APIs. The core idea: treat everything in your system as a "resource" with a fixed URL, then use HTTP methods to act on it.

- `GET /notes` — get the list
- `POST /notes` — create one
- `GET /notes/42` — get a single one
- `DELETE /notes/42` — delete it

The payoff is that it's {{green:intuitive, stateless, and cache-friendly}} — anyone can guess roughly what a call does just from the URL and the method.
