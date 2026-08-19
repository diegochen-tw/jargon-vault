---
id: demo-idempotency
name: Idempotency
template: jargon-default
fields:
  alias: Idempotence
  synonymy: ""
  polysemy: in math, a property where f(f(x)) = f(x)
tags:
- Concept
- Networking
attachments: []
created: 1720000200.0
updated: 1720000200.0
history: []
---

Doing the same operation once or many times **produces the same result**.

It matters a lot in networked services: if the user's connection is flaky and the same "payment" request goes out twice, an idempotent design guarantees they're only charged once. `GET`, `PUT`, and `DELETE` are usually designed to be idempotent; `POST` usually is not.

In practice you often use an {{orange:idempotency key}} so the server can recognize "this is the same request" and simply return the previous result on a retry.
