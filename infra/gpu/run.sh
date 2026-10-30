#!/usr/bin/env bash
# Run a GPU job on a RunPod pod so that the pod can never outlive it.
#
#   nohup bash run.sh pod_verifier.py [args] > job.log 2>&1 &
#
# 1. The job runs under a wall-clock cap (MAX_JOB, default 2h).
# 2. The pod then waits for its results to be copied off. The copier signals it with
#    `touch /workspace/FETCHED`. The wait is at most FETCH_WAIT_MIN minutes (default 30).
# 3. Then the pod deletes itself, whether or not anyone is still watching.
#
# Why: on 2026-10-29 a finished pod idled for 13.7 hours after the controlling session ended,
# and spent the account's whole balance. Deleting the pod must not depend on a person or an
# agent being present.
set -u

timeout "${MAX_JOB:-2h}" python "$@"
echo "job exit status: $?"

for _ in $(seq 1 "${FETCH_WAIT_MIN:-30}"); do
  [ -f /workspace/FETCHED ] && break
  sleep 60
done

echo "deleting pod ${RUNPOD_POD_ID:-?}"
runpodctl remove pod "${RUNPOD_POD_ID}"
