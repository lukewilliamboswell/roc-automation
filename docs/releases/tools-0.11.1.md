# roc-automation tools 0.11.1

Release and nightly suite callers can select case deadlines up to 300000 milliseconds, matching five-minute native application specifications. Both `RP_CASE_TIMEOUT_MS` and `case-timeout-ms` retain their 10000-millisecond defaults. The existing managed command runner preserves partial output and terminates descendants when a selected deadline expires.

Native regressions verify the prior 120000-millisecond setting, the new upper bound, rejected out-of-range values and short live-case interruption. Compiler, prerequisite, output and overall job budgets retain their separate limits.
