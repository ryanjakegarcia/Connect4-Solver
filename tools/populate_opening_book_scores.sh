#!/usr/bin/env bash
set -euo pipefail

book_file="data/opening_book.txt"
solver_bin="./solver"
timeout_sec=2
max_cases=0
max_minutes=0
cache_file=".opening_book_scores.tsv"
move_cache_file=".opening_book_moves.tsv"
log_every=100
fill_moves_mode="none"
omit_zero_log_fields=0

die() {
  echo "Error: $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./populate_opening_book_scores.sh [options]

Populate exact scores in data/opening_book.txt with safeguards.

Options:
  --book FILE          Opening book path (default: data/opening_book.txt)
  --solver PATH        Solver binary path (default: ./solver)
  --timeout-sec N      Timeout per position in seconds (default: 2)
  --max-cases N        Max unresolved positions to attempt this run (default: 0 = no limit)
  --max-minutes N      Max wall-clock minutes for this run (default: 0 = no limit)
  --cache FILE         Score cache path (default: .opening_book_scores.tsv)
  --move-cache FILE    Best-move cache path (default: .opening_book_moves.tsv)
  --fill-moves MODE    Move fill policy: none|len5|all (default: none)
  --log-every N        Progress log period (default: 100)
  --omit-zero          Omit zero-valued counters in progress logs
  -h, --help           Show this help

Notes:
- Caches store solved scores/moves so reruns resume naturally.
- Timed-out/error positions are left unchanged and can be retried later.
- Each solved score/move is written immediately to cache and book (atomic per update).

Examples:
  ./populate_opening_book_scores.sh --timeout-sec 1 --max-cases 500
  ./populate_opening_book_scores.sh --timeout-sec 3 --max-minutes 15
  ./populate_opening_book_scores.sh --fill-moves len5 --timeout-sec 10 --max-cases 200
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --book)
      [[ $# -ge 2 ]] || die "missing value for --book"
      book_file="$2"
      shift 2
      ;;
    --solver)
      [[ $# -ge 2 ]] || die "missing value for --solver"
      solver_bin="$2"
      shift 2
      ;;
    --timeout-sec)
      [[ $# -ge 2 ]] || die "missing value for --timeout-sec"
      timeout_sec="$2"
      shift 2
      ;;
    --max-cases)
      [[ $# -ge 2 ]] || die "missing value for --max-cases"
      max_cases="$2"
      shift 2
      ;;
    --max-minutes)
      [[ $# -ge 2 ]] || die "missing value for --max-minutes"
      max_minutes="$2"
      shift 2
      ;;
    --cache)
      [[ $# -ge 2 ]] || die "missing value for --cache"
      cache_file="$2"
      shift 2
      ;;
    --move-cache)
      [[ $# -ge 2 ]] || die "missing value for --move-cache"
      move_cache_file="$2"
      shift 2
      ;;
    --fill-moves)
      [[ $# -ge 2 ]] || die "missing value for --fill-moves"
      fill_moves_mode="$2"
      shift 2
      ;;
    --log-every)
      [[ $# -ge 2 ]] || die "missing value for --log-every"
      log_every="$2"
      shift 2
      ;;
    --omit-zero)
      omit_zero_log_fields=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -f "$book_file" ]] || die "book file not found: $book_file"
[[ -x "$solver_bin" ]] || die "solver binary missing or not executable: $solver_bin"
command -v timeout >/dev/null 2>&1 || die "'timeout' command not found"

[[ "$timeout_sec" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "--timeout-sec must be numeric"
[[ "$max_cases" =~ ^[0-9]+$ ]] || die "--max-cases must be a non-negative integer"
[[ "$max_minutes" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "--max-minutes must be numeric"
[[ "$log_every" =~ ^[0-9]+$ ]] || die "--log-every must be a positive integer"
[[ "$log_every" -gt 0 ]] || die "--log-every must be > 0"
[[ "$fill_moves_mode" =~ ^(none|len5|all)$ ]] || die "--fill-moves must be one of: none, len5, all"

mkdir -p "$(dirname "$cache_file")"
touch "$cache_file"
mkdir -p "$(dirname "$move_cache_file")"
touch "$move_cache_file"

tmp_candidates="$(mktemp)"
tmp_solver_out="$(mktemp)"
tmp_book_update="$(mktemp)"
trap 'rm -f "$tmp_candidates" "$tmp_solver_out" "$tmp_book_update"' EXIT

persist_score() {
  local seq="$1"
  local score="$2"

  # Cache is append-only for fast resume.
  printf "%s\t%s\n" "$seq" "$score" >> "$cache_file"

  # Atomically update this one sequence in the book.
  awk -v target="$seq" -v resolved="$score" '
    /^#/ {print; next}
    NF >= 3 {
      if ($1 == target && $3 == "-") {
        $3 = resolved;
      }
      print $1, $2, $3;
      next;
    }
    {print}
  ' "$book_file" > "$tmp_book_update"
  mv "$tmp_book_update" "$book_file"
}

persist_best_move() {
  local seq="$1"
  local best_move="$2"

  # Cache is append-only for fast resume.
  printf "%s\t%s\n" "$seq" "$best_move" >> "$move_cache_file"

  # Atomically update this one sequence in the book.
  awk -v target="$seq" -v resolved="$best_move" '
    /^#/ {print; next}
    NF >= 3 {
      if ($1 == target && $2 == "-") {
        $2 = resolved;
      }
      print $1, $2, $3;
      next;
    }
    {print}
  ' "$book_file" > "$tmp_book_update"
  mv "$tmp_book_update" "$book_file"
}

# Candidate unresolved sequences where score is unresolved, or move is unresolved
# according to --fill-moves mode.
# Priority order:
# 1) immediate parent sequence already has a resolved score
# 2) longer sequence first
# 3) lexical tie-breaker
awk '
  /^#/ {next}
  function need_move(seq, best, mode) {
    if (mode == "none") return 0;
    if (mode == "all") return best == "-";
    if (mode == "len5") return (best == "-" && length(seq) == 5);
    return 0;
  }
  NF >= 3 {
    seq=$1;
    best=$2;
    score=$3;
    best_by_seq[seq]=best;
    score_by_seq[seq]=score;
    seq_list[++n]=seq;
  }
  END {
    for(i=1; i<=n; i++) {
      seq = seq_list[i];
      need_score = (score_by_seq[seq] == "-");
      need_best = need_move(seq, best_by_seq[seq], mode);
      if(!need_score && !need_best) continue;

      has_solved_parent = 0;
      if(length(seq) > 1) {
        parent = substr(seq, 1, length(seq) - 1);
        if((parent in score_by_seq) && score_by_seq[parent] != "-") has_solved_parent = 1;
      }

      print has_solved_parent, length(seq), seq;
    }
  }
' mode="$fill_moves_mode" "$book_file" \
  | sort -k1,1nr -k2,2nr -k3,3 \
  | awk '{print $3}' > "$tmp_candidates"

start_epoch="$(date +%s)"
max_seconds=0
if awk -v m="$max_minutes" 'BEGIN{exit !(m>0)}'; then
  max_seconds="$(awk -v m="$max_minutes" 'BEGIN{printf "%d", m*60}')"
fi

attempted=0
score_solved=0
score_timeouts=0
score_errors=0
move_solved=0
move_timeouts=0
move_errors=0

while IFS= read -r seq; do
  [[ -n "$seq" ]] || continue

  # Check current book state (book is updated incrementally).
  fields="$(awk -v s="$seq" 'NF>=3 && $1==s {print $2" "$3; exit}' "$book_file")"
  [[ -n "$fields" ]] || continue
  best_now="${fields%% *}"
  score_now="${fields##* }"
  if [[ "$best_now" != "-" && "$score_now" != "-" ]]; then
    continue
  fi

  if [[ "$max_cases" -gt 0 && "$attempted" -ge "$max_cases" ]]; then
    echo "Reached --max-cases=$max_cases"
    break
  fi

  if [[ "$max_seconds" -gt 0 ]]; then
    now="$(date +%s)"
    elapsed=$((now - start_epoch))
    if [[ "$elapsed" -ge "$max_seconds" ]]; then
      echo "Reached --max-minutes=$max_minutes"
      break
    fi
  fi

  attempted=$((attempted + 1))

  should_solve_move=0
  if [[ "$fill_moves_mode" == "all" && "$best_now" == "-" ]]; then
    should_solve_move=1
  elif [[ "$fill_moves_mode" == "len5" && "$best_now" == "-" && ${#seq} -eq 5 ]]; then
    should_solve_move=1
  fi

  # Best move solve (query mode) if enabled and missing.
  if [[ "$should_solve_move" -eq 1 ]]; then
    if timeout "${timeout_sec}s" "$solver_bin" <<< "${seq}?" > "$tmp_solver_out" 2>/dev/null; then
      best_move="$(awk 'NF>=1 {print $1; exit}' "$tmp_solver_out")"
      if [[ -n "$best_move" && "$best_move" =~ ^[1-7]$ ]]; then
        persist_best_move "$seq" "$best_move"
        move_solved=$((move_solved + 1))
      else
        move_errors=$((move_errors + 1))
      fi
    else
      rc=$?
      if [[ "$rc" -eq 124 ]]; then
        move_timeouts=$((move_timeouts + 1))
      else
        move_errors=$((move_errors + 1))
      fi
    fi
  fi

  # Exact score solve if missing.
  if [[ "$score_now" == "-" ]]; then
    if timeout "${timeout_sec}s" "$solver_bin" <<< "$seq" > "$tmp_solver_out" 2>/dev/null; then
      score="$(awk 'NF>=2 {print $2; exit}' "$tmp_solver_out")"
      if [[ -n "$score" && "$score" =~ ^-?[0-9]+$ ]]; then
        persist_score "$seq" "$score"
        score_solved=$((score_solved + 1))
      else
        score_errors=$((score_errors + 1))
      fi
    else
      rc=$?
      if [[ "$rc" -eq 124 ]]; then
        score_timeouts=$((score_timeouts + 1))
      else
        score_errors=$((score_errors + 1))
      fi
    fi
  fi

  if (( attempted % log_every == 0 )); then
    now_log="$(date +%s)"
    elapsed_log=$((now_log - start_epoch))

    # Keep attempted first and elapsed last for easy visual scanning.
    log_parts=("attempted=$attempted")
    if [[ "$omit_zero_log_fields" -eq 1 ]]; then
      [[ "$move_solved" -ne 0 ]] && log_parts+=("move_solved=$move_solved")
      [[ "$move_timeouts" -ne 0 ]] && log_parts+=("move_timeouts=$move_timeouts")
      [[ "$move_errors" -ne 0 ]] && log_parts+=("move_errors=$move_errors")
      [[ "$score_solved" -ne 0 ]] && log_parts+=("score_solved=$score_solved")
      [[ "$score_timeouts" -ne 0 ]] && log_parts+=("score_timeouts=$score_timeouts")
      [[ "$score_errors" -ne 0 ]] && log_parts+=("score_errors=$score_errors")
    else
      log_parts+=("move_solved=$move_solved")
      log_parts+=("move_timeouts=$move_timeouts")
      log_parts+=("move_errors=$move_errors")
      log_parts+=("score_solved=$score_solved")
      log_parts+=("score_timeouts=$score_timeouts")
      log_parts+=("score_errors=$score_errors")
    fi
    log_parts+=("elapsed_s=$elapsed_log")
    echo "progress: ${log_parts[*]}"
  fi
done < "$tmp_candidates"

filled_moves_now="$(awk '!/^#/ && NF>=3 && $2 != "-" {c++} END{print c+0}' "$book_file")"
unresolved_moves_now="$(awk '!/^#/ && NF>=3 && $2 == "-" {c++} END{print c+0}' "$book_file")"
filled_now="$(awk '!/^#/ && NF>=3 && $3 != "-" {c++} END{print c+0}' "$book_file")"
unresolved_now="$(awk '!/^#/ && NF>=3 && $3 == "-" {c++} END{print c+0}' "$book_file")"

echo "Done."
echo "attempted_positions=$attempted"
echo "fill_moves_mode=$fill_moves_mode"
echo "run_moves_solved=$move_solved run_move_timeouts=$move_timeouts run_move_errors=$move_errors"
echo "run_scores_solved=$score_solved run_score_timeouts=$score_timeouts run_score_errors=$score_errors"
echo "book_filled_moves=$filled_moves_now book_unresolved_moves=$unresolved_moves_now"
echo "book_filled_scores=$filled_now book_unresolved_scores=$unresolved_now"
echo "cache_file=$cache_file"
echo "move_cache_file=$move_cache_file"
