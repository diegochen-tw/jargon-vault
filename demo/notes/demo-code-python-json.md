---
id: demo-code-python-json
name: Read a JSON config file in Python
template: code-snippet
fields:
  language: Python
  dependencies: ""
tags:
- Programming
- Python
attachments: []
created: 1720000600.0
updated: 1720000600.0
history: []
---

Uses the standard library `json` to read a file, with `encoding="utf-8"` so non-ASCII text isn't mangled. When the file is missing or malformed it returns a default, so callers don't have to wrap it in another try.

```python
import json
from pathlib import Path

def load_config(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
```
