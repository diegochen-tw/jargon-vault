---
id: demo-code-sql-duplicates
name: Find duplicate emails in SQL
template: code-snippet
fields:
  language: SQL
  dependencies: ""
tags:
- Programming
- SQL
attachments: []
created: 1720000800.0
updated: 1720000800.0
history: []
---

Uses `GROUP BY` to bucket rows with the same email, then `HAVING COUNT(*) > 1` to keep only the ones that appear more than once. Select `COUNT(*)` too if you want to see how many duplicates each has.

```sql
SELECT email, COUNT(*) AS n
FROM users
GROUP BY email
HAVING COUNT(*) > 1
ORDER BY n DESC;
```
