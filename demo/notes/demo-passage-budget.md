---
id: demo-passage-budget
name: You are allowed to be down for a set amount of time, and that allowance is what buys you the right to ship
template: passage-decoder
fields:
  gist: The gap between 100% and your reliability target is a budget you get to spend on shipping
  domain: Site reliability engineering
  key_terms: SLO, error budget, [[idempotency]]
  source: Platform team onboarding deck
tags:
- Concept
- Reliability
attachments: []
created: 1720001000.0
updated: 1720001000.0
history: []
---

> We run the service against a 99.9% availability SLO, which leaves an error budget of roughly 43 minutes a month. While the budget is unspent, feature velocity governs the release cadence; once it is exhausted, the team freezes launches and spends the remainder of the window on reliability work.

**🌱 For beginners**

Think of a monthly allowance for being broken. You promise the service works almost always — but "almost" is written down as a number, and the leftover is money in an account. Every outage withdraws from it. Spend nothing and you can keep adding new things as fast as you like. Spend it all and you stop adding, and you go fix what keeps breaking until next month's allowance arrives.

**💬 In plain words**

The team commits to a target (99.9% of requests succeed). The remaining 0.1% — about 43 minutes across a month — is the *error budget*: the amount of failure the target already permits. It reframes an argument. Instead of "should we ship this risky change?", the question becomes "do we still have budget?" Nobody has to argue about whether a given release is safe in the abstract; the account balance decides who is allowed to move fast this month.

**🎓 For professionals**

The SLO fixes an availability objective over a rolling window; the error budget is its complement, `1 − SLO`, measured against the same window and the same event stream as the SLI. Burn rate — budget consumed per unit time — is the operational signal: a fast burn pages, a slow burn opens a ticket. The freeze on exhaustion is what makes the objective binding rather than aspirational, converting reliability from a value people say they hold into a scheduling constraint. It also implies the converse, which teams forget: a chronically unspent budget means the objective is set too loose, and the team is paying for reliability nobody asked for.
