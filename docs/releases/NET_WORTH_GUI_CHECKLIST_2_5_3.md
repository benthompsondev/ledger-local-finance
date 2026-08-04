# Net worth: manual GUI checks for the 2.5.3 release pass

The 2.5.3 correction pass was code and automated tests only. No GUI was
driven, no installer was built, and nothing was launched. These are the
checks that need a person in front of the app, to be run during the release
pass that packages 2.5.3.

Run them against a disposable database:

```powershell
$env:LEDGER_DATA_DIR = "$env:TEMP\ledger-gui-check"
```

Insights → Net worth.

## Edit loads the reading you clicked

This is the defect that had no visible symptom until you tried it: the form
was mounted inside a closed details element, so its fields were seeded once,
long before Edit existed as an action.

1. Record three months with clearly different figures.
2. Open **Every reading**, click **Edit** on the middle month.
3. The form shows that month and all four of its saved components, not the
   prefill and not the newest month.
4. Click **Edit** on a different month without closing the form. Every field
   changes to the new month's figures.
5. Click **Cancel**. The form returns to a new reading seeded from the
   prefill, with an empty note.
6. Start editing a month, change Cash but do not save, then press
   **Refresh** in the panel header. The half-finished edit is still there.

## A blank field cannot overwrite a balance

7. Edit a month and clear the Cash field entirely.
8. The running total is replaced by "Fill in all four figures" and the save
   button is disabled. It must not read $0.00 or save a zero.
9. Type `0` into Cash. The total returns and saving is allowed. Zero is a
   legitimate reading.

## Delete asks first

10. Click **Delete** on a reading. It asks, naming the month, and nothing is
    removed yet.
11. Click **Keep**. The reading is still there.
12. Click **Delete**, then **Remove**. The row goes, and the chart, the
    summary figures and the reading count all update immediately without a
    manual refresh.

## Periods say what they mean

13. Record only January and June of the same year.
14. **This month** and **Three months** both read "Not yet" with a reason,
    never **+$0.00**.
15. The badge at the top reads "since 2026-01", not "on 2026-01".
16. **All recorded** states the span in months and the number of readings,
    not "across 2 months".
17. **What moved** names both months and says the readings are five months
    apart.
18. Now record February as well. **This month** becomes a real figure
    comparing January with February... verify it names the right pair.

## A foreign-currency balance is excluded and said so

19. Add two accounts with balances, one CAD and one USD, then open the
    record form.
20. Cash shows only the CAD figure.
21. A calm panel names the USD account, its currency and its amount, and
    says the amount was left out because Northstar does not convert.
22. It is styled as information, not as an error.

## A backdated balance does not move the prefill backwards

23. Add a balance for an account dated this month.
24. Add a second balance for the *same* account dated six months ago.
25. Reopen the record form. The prefill still shows the newer figure.

## The legacy carry-forward stays deleted

Only relevant on a database that has an old quick estimate or snapshot in it.

26. Delete a reading marked "Carried over from an earlier estimate".
27. Close the app completely and reopen it. The reading is still gone.

## Nothing else regressed

28. Home, Plan, Insights and Coach all load, and Insights still reports the
    same analysis month it did before.
29. Import a small invented CSV and confirm the Add Data screen behaves as
    it did in 2.5.2.
