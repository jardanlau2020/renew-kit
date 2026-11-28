#!/usr/bin/env python3
"""Freshness Watch - read-only liveness check for renewal repos.

Why this exists: a repo can have Actions fully stopped while its output keeps
being updated by something else (an external host pushing with a PAT). The run
list then looks 'stalled' while the service is fine, or worse: the external
pusher stops and nobody hears about it. Judging by run history alone misses
both cases.

Verdict per repo:
  LIVE      last schedule run within expected window
  FROZEN    no recent schedule run, but commits still fresh -> external pusher
  STALLED   no recent schedule run and commits stale -> nobody is running it
  NO-SCHED  repo has no schedule runs at all

Read-only: GET requests only. Exit code 1 if any FROZEN / STALLED / stale.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

OWNER = os.environ.get("WATCH_OWNER", "jardanlau2020")
REPOS = [r.strip() for r in os.environ.get("WATCH_REPOS", "freesub").split(",") if r.strip()]
FRESH_MAX_H = float(os.environ.get("FRESH_MAX_H", "24"))
SCHED_MAX_H = float(os.environ.get("SCHED_MAX_H", "36"))
TOKEN = os.environ.get("WATCH_TOKEN", "").strip()

API = "https://api.github.com"


def get(path: str):
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "freshness-watch")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception as e:  # noqa: BLE001 - report, never crash the whole run
        print(f"  ! request failed {path}: {e}")
        return None, -1


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def hours_since(ts: datetime | None, now: datetime) -> float | None:
    return None if ts is None else round((now - ts).total_seconds() / 3600.0, 1)


def check(repo: str, now: datetime) -> dict:
    meta, code = get(f"/repos/{OWNER}/{repo}")
    if meta is None:
        return {"repo": repo, "verdict": "ERROR", "detail": f"repo metadata HTTP {code}", "bad": True}

    runs, rcode = get(f"/repos/{OWNER}/{repo}/actions/runs?per_page=30")
    last_sched = None
    last_sched_concl = "-"
    if runs:
        for run in runs.get("workflow_runs", []):
            if run.get("event") == "schedule":
                last_sched = parse_ts(run.get("created_at"))
                last_sched_concl = run.get("conclusion") or "?"
                break

    commits, ccode = get(f"/repos/{OWNER}/{repo}/commits?per_page=1")
    last_commit = None
    if commits:
        try:
            last_commit = parse_ts(commits[0]["commit"]["committer"]["date"])
        except (KeyError, IndexError, TypeError):
            last_commit = parse_ts(meta.get("pushed_at"))

    sched_age = hours_since(last_sched, now)
    commit_age = hours_since(last_commit, now)

    if last_sched is None:
        verdict = "NO-SCHED" if rcode == 200 else "ERROR"
        bad = verdict == "ERROR"
        detail = "no event=schedule run in last 30 runs" if rcode == 200 else f"runs HTTP {rcode}"
    elif sched_age is not None and sched_age <= SCHED_MAX_H:
        verdict, bad = "LIVE", False
        detail = f"last schedule {sched_age}h ago ({last_sched_concl})"
    elif commit_age is not None and commit_age <= FRESH_MAX_H:
        verdict, bad = "FROZEN", True
        detail = f"schedule dead {sched_age}h, but commit {commit_age}h ago -> external pusher"
    else:
        verdict, bad = "STALLED", True
        detail = f"schedule dead {sched_age}h, commit {commit_age}h ago -> nothing is running it"

    return {
        "repo": repo,
        "verdict": verdict,
        "detail": detail,
        "sched_age": sched_age,
        "commit_age": commit_age,
        "last_sched": last_sched.strftime("%Y-%m-%d %H:%MZ") if last_sched else "-",
        "last_commit": last_commit.strftime("%Y-%m-%d %H:%MZ") if last_commit else "-",
        "bad": bad,
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    print(f"# Freshness Watch  (now {now.strftime('%Y-%m-%d %H:%M:%SZ')})")
    print(f"# thresholds: schedule_max={SCHED_MAX_H}h  commit_fresh_max={FRESH_MAX_H}h")
    print(f"# auth: {'token' if TOKEN else 'anonymous (60 req/h limit)'}")
    print()

    rows = [check(repo, now) for repo in REPOS]

    print("| repo | verdict | schedule | last schedule | last commit | detail |")
    print("|---|---|---|---|---|---|")
    for row in rows:
        if row["verdict"] == "ERROR":
            print(f"| {row['repo']} | ERROR | - | - | - | {row['detail']} |")
            continue
        print(
            f"| {row['repo']} | {row['verdict']} | {row['sched_age']}h ago | "
            f"{row['last_sched']} | {row['last_commit']} | {row['detail']} |"
        )

    bad = [r for r in rows if r["bad"]]
    print()
    if bad:
        print(f"RESULT: {len(bad)} repo(s) need attention: " + ", ".join(r["repo"] for r in bad))
        return 1
    print(f"RESULT: all {len(rows)} repo(s) healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())