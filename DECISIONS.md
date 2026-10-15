# DECISIONS.md

1. **Indexes.** I create a unique `employees.emp_code` index and a unique compound `(emp_code, date)` attendance index. These enforce the two natural-key guarantees even under concurrency. Department/join-date supports headcount filtering; date/employee supports the attendance list sort; employee/date/status helps filtered employee queries; date/late-minutes/employee supports monthly late analysis. I would measure before adding more indexes because every index adds write and storage cost.

2. **Punch-in race.** Both requests may read the employee and calculate the same attendance date. They then attempt the insert. MongoDB's unique `(emp_code, date)` index allows only one insert; the other raises `DuplicateKeyError`, which becomes HTTP 409. A pre-check alone would not be safe.

3. **Ties.** `$rank` assigns equal totals the same rank and skips the next rank (1, 2, 2, 4). I filter on rank after ranking, so ties at the cutoff are all returned, ordered by employee code.

4. **Headcount.** The department summary starts from employees joined by the end of the month and looks up logs. Employees with no logs remain in the pipeline, so they count toward headcount but add no attendance metrics.

5. **100x data.** I would inspect explain plans and production query profiles, consider pre-aggregated daily summaries, and load-test indexes and pagination before changing architecture.
