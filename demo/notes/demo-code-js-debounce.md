---
id: demo-code-js-debounce
name: A debounce function in JavaScript
template: code-snippet
fields:
  language: JavaScript
  dependencies: ""
tags:
- Programming
- JavaScript
- Performance
attachments: []
created: 1720000700.0
updated: 1720000700.0
history: []
---

Returns a wrapped function: every call resets the timer, so `fn` only runs **after calls stop for `wait` milliseconds**. Handy for search boxes, window resize, and scroll events.

```javascript
function debounce(fn, wait = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

// Usage: only query 300ms after typing stops
searchInput.addEventListener('input', debounce(runSearch, 300));
```
