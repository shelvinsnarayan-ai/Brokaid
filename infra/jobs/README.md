# Bounded job runner

Every scheduled job in brokAId runs through `python -m app.jobs.run <job_name>`.
The runner:

1. Acquires a Postgres advisory lock keyed to the job. If it can't, it
   exits `0` immediately (another instance is running).
2. Emits `JobRunStarted` under the ops tenant.
3. Runs the callable inside `asyncio.wait_for(max_runtime_sec)`.
4. Emits `JobRunCompleted { ok: true|false, error?: str }`.
5. Releases the lock.

There is **no in-process scheduler**. External infra (cron, k8s
CronJob, systemd timer) must call the CLI.

## Registered jobs (phase 3)

| Name           | Suggested schedule | Max runtime | Notes                                    |
|----------------|--------------------|-------------|------------------------------------------|
| `chain_verify` | `0 * * * *`        | 300 s       | Verifies every tenant's hash chain.      |
| `timer_sweep`  | `* * * * *`        |  25 s       | Fires `TimerFired` for ready wf waits.   |

## Example crontab

```
# m  h dom mon dow  command
  *  *  *  *  *   cd /app/services/api && python -m app.jobs.run timer_sweep  >> /var/log/brokaid/timer_sweep.log 2>&1
  0  *  *  *  *   cd /app/services/api && python -m app.jobs.run chain_verify >> /var/log/brokaid/chain_verify.log 2>&1
```

## Exit codes

| Code | Meaning                                                   |
|------|-----------------------------------------------------------|
|   0  | Ran to completion, OR another instance held the lock.     |
|   1  | Callable failed (timeout or exception).                   |
|   2  | Unknown job name (typo in schedule).                      |

## Registering a new job

```python
from app.jobs.runner import register

register(
    "my_job",
    my_async_callable,          # async def(pool) -> None
    schedule="*/5 * * * *",     # informational only
    advisory_lock_key=0xDEADBEEF,
    max_runtime_sec=60.0,
    description="What this job does.",
)
```

Ensure `advisory_lock_key` is unique across every registered job.
