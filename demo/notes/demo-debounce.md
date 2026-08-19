---
id: demo-debounce
name: debounce
template: jargon-default
fields:
  alias: input debouncing
  synonymy: ""
  polysemy: In electronics, suppressing contact bounce in a mechanical switch
tags:
- Concept
- Performance
attachments: []
created: 1720000400.0
updated: 1720000400.0
history: []
---

Literally "to remove the bounce." In programming it means: **wait until the user stops for a short moment before actually running**, so a rapid burst of triggers doesn't fire over and over.

The classic case is a search box — querying on every keystroke is wasteful, so `debounce` waits until you've paused for, say, 300 ms before sending the query.

> Let's **debounce** the search input so we don't hammer the server on every keystroke.
