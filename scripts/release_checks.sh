# Helpers sourced by .github/workflows/daily-release.yml (needs GH_TOKEN,
# GITHUB_REPOSITORY and REQUIRED_CHECKS in the environment).

# checks_state SHA -> prints success | pending | failure:<names>
checks_state() {
  local failed missing line n
  failed=""; missing=""
  IFS='|' read -ra names <<< "$REQUIRED_CHECKS"
  for n in "${names[@]}"; do
    # Ask for each check by name: the hourly scheduled workflows attach their own
    # runs to main's head, and an unfiltered first page loses the CI runs after a few days.
    # A name can still have several runs (one per push or scheduled suite), so judge the
    # newest one; app 15368 is GitHub Actions, so another app's same-named check never counts.
    line=$(gh api -X GET "repos/$GITHUB_REPOSITORY/commits/$1/check-runs" -f check_name="$n" -f app_id=15368 \
      -f per_page=100 --jq '[.check_runs[]] | max_by(.id) // empty | "\(.name)\t\(.status)\t\(.conclusion)"')
    if [ -z "$line" ] || [ "$(cut -f2 <<< "$line")" != "completed" ]; then missing="$missing $n;"
    elif [ "$(cut -f3 <<< "$line")" != "success" ]; then failed="$failed $n;"; fi
  done
  if [ -n "$failed" ]; then echo "failure:$failed"; elif [ -n "$missing" ]; then echo pending; else echo success; fi
}

# wait_checks SHA -> returns 0 when all required checks passed
wait_checks() {
  local s
  for _ in $(seq 1 50); do
    s=$(checks_state "$1")
    case "$s" in
      success) return 0 ;;
      failure:*) echo "::error::Required checks failed on $1:${s#failure:}"; return 1 ;;
    esac
    sleep 30
  done
  echo "::error::Timed out waiting for required checks on $1"; return 1
}
