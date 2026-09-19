import os
import time
import traceback
from datetime import datetime, timedelta, timezone

from pipeline import pipeline   

RUN_HOUR = int(os.environ.get("RUN_HOUR_UTC", "2"))
RUN_ON_START = os.environ.get("RUN_ON_START", "1") == "1"


def next_run(now):
    t = now.replace(hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def run_once():
    start = datetime.now(timezone.utc)
    print(f"[{start:%Y-%m-%d %H:%M:%S}Z] pipeline start", flush=True)
    try:
        pipeline()
        status = "ok"
    except Exception:
        traceback.print_exc()          # a failed night must not stop the loop
        status = "FAILED"
    end = datetime.now(timezone.utc)
    print(f"[{end:%Y-%m-%d %H:%M:%S}Z] pipeline {status} after "
          f"{(end - start).total_seconds():.0f}s", flush=True)


if __name__ == "__main__":
    if RUN_ON_START:
        run_once()
    while True:
        target = next_run(datetime.now(timezone.utc))
        print(f"next run at {target:%Y-%m-%d %H:%M}Z", flush=True)
        # sleep in short chunks so clock changes or host suspend don't cause drift
        while (remaining := (target - datetime.now(timezone.utc)).total_seconds()) > 0:
            time.sleep(min(remaining, 60))
        run_once()