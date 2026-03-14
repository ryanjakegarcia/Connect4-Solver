#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

book_file="data/opening_book.txt"
solver_bin="./solver"
move_cache_file=".opening_book_moves.tsv"
workers=8
timeout_sec=120
seq_len=5
chunk_size=128
work_dir=".parallel_move_backfill"
log_every=100
omit_zero=0
max_minutes=0
stop_grace_sec=5

stop_requested=0
stop_epoch=0
force_kill_sent=0
last_stop_log=0
worker_pids=()

request_stop() {
  if [[ "$stop_requested" -eq 1 ]]; then
    return
  fi

  # Ignore repeated interrupts while we shut down workers and finalize.
  trap '' INT TERM

  stop_requested=1
  stop_epoch="$(date +%s)"
  echo "Stop requested. Signaling workers and finalizing partial results..." >&2
  for pid in "${worker_pids[@]:-}"; do
    kill -TERM "$pid" 2>/dev/null || true
    # Also signal direct children (e.g., timeout/solver) in case shell trap delayed exit.
    pkill -TERM -P "$pid" 2>/dev/null || true
  done
}

trap request_stop INT TERM

usage() {
  cat <<'EOF'
Usage: ./parallel_backfill_opening_book_moves.sh [options]

Parallel-safe best-move backfill for data/opening_book.txt.
It does NOT let multiple workers write data/opening_book.txt directly.
Workers claim small chunks dynamically, write per-worker outputs, then results are merged and applied atomically.

Options:
  --book FILE         Opening book path (default: data/opening_book.txt)
  --solver PATH       Solver binary (default: ./solver)
  --move-cache FILE   Move cache file to append merged results (default: .opening_book_moves.tsv)
  --workers N         Number of parallel workers (default: 8)
  --timeout-sec N     Per-sequence timeout in each worker (default: 120)
  --seq-len N         Sequence length to target (default: 5)
  --chunk-size N      Sequences per claimable chunk (default: 128)
  --work-dir DIR      Working directory for shards/temp files (default: .parallel_move_backfill)
  --log-every N       Progress log period per worker (default: 100)
  --max-minutes N     Max wall-clock minutes for the whole run (default: 0 = no limit)
  --omit-zero         Omit zero-valued counters in progress logs
  -h, --help          Show this help
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
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
    --move-cache)
      [[ $# -ge 2 ]] || die "missing value for --move-cache"
      move_cache_file="$2"
      shift 2
      ;;
    --workers)
      [[ $# -ge 2 ]] || die "missing value for --workers"
      workers="$2"
      shift 2
      ;;
    --timeout-sec)
      [[ $# -ge 2 ]] || die "missing value for --timeout-sec"
      timeout_sec="$2"
      shift 2
      ;;
    --seq-len)
      [[ $# -ge 2 ]] || die "missing value for --seq-len"
      seq_len="$2"
      shift 2
      ;;
    --chunk-size)
      [[ $# -ge 2 ]] || die "missing value for --chunk-size"
      chunk_size="$2"
      shift 2
      ;;
    --work-dir)
      [[ $# -ge 2 ]] || die "missing value for --work-dir"
      work_dir="$2"
      shift 2
      ;;
    --log-every)
      [[ $# -ge 2 ]] || die "missing value for --log-every"
      log_every="$2"
      shift 2
      ;;
    --max-minutes)
      [[ $# -ge 2 ]] || die "missing value for --max-minutes"
      max_minutes="$2"
      shift 2
      ;;
    --omit-zero)
      omit_zero=1
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
command -v split >/dev/null 2>&1 || die "'split' command not found"

[[ "$workers" =~ ^[0-9]+$ ]] || die "--workers must be a positive integer"
[[ "$workers" -gt 0 ]] || die "--workers must be > 0"
[[ "$timeout_sec" =~ ^[0-9]+$ ]] || die "--timeout-sec must be a positive integer"
[[ "$timeout_sec" -gt 0 ]] || die "--timeout-sec must be > 0"
[[ "$seq_len" =~ ^[0-9]+$ ]] || die "--seq-len must be a positive integer"
[[ "$seq_len" -gt 0 ]] || die "--seq-len must be > 0"
[[ "$chunk_size" =~ ^[0-9]+$ ]] || die "--chunk-size must be a positive integer"
[[ "$chunk_size" -gt 0 ]] || die "--chunk-size must be > 0"
[[ "$log_every" =~ ^[0-9]+$ ]] || die "--log-every must be a positive integer"
[[ "$log_every" -gt 0 ]] || die "--log-every must be > 0"
[[ "$max_minutes" =~ ^[0-9]+$ ]] || die "--max-minutes must be a non-negative integer"

mkdir -p "$work_dir"

unresolved_file="$work_dir/unresolved_len${seq_len}.txt"
chunks_dir="$work_dir/chunks_len${seq_len}"
merged_file="$work_dir/moves_merged.tsv"
book_tmp="$book_file.tmp_parallel_apply"

# 1) Gather unresolved sequences for target length.
awk -v n="$seq_len" '!/^#/ && NF>=3 && length($1)==n && $2=="-" {print $1}' "$book_file" > "$unresolved_file"

total_unresolved=$(awk 'END{print NR+0}' "$unresolved_file")
if [[ "$total_unresolved" -eq 0 ]]; then
  echo "No unresolved best moves found for len=$seq_len"
  exit 0
fi

echo "Unresolved len-$seq_len sequences: $total_unresolved"

# 2) Build claimable chunks for dynamic worker scheduling.
rm -rf "$chunks_dir"
mkdir -p "$chunks_dir"
rm -f "$work_dir"/worker_*.moves
split -l "$chunk_size" -d --additional-suffix=.txt "$unresolved_file" "$chunks_dir/pending_"
total_chunks=$(find "$chunks_dir" -maxdepth 1 -type f -name 'pending_*' | wc -l)
echo "Prepared $total_chunks chunks (chunk_size=$chunk_size)."

# 3) Run workers in parallel; each claims pending chunks until empty.
echo "Running $workers workers (timeout ${timeout_sec}s per sequence, dynamic chunks)..."
start_epoch="$(date +%s)"
for ((w=0; w<workers; w++)); do
  (
    # Workers should terminate on TERM/INT from parent stop handling.
    trap - INT TERM

    worker="worker_$(printf '%02d' "$w")"
    out="$work_dir/${worker}.moves"
    : > "$out"
    attempted=0
    solved=0
    timeouts=0
    errors=0
    chunks_done=0
    idle_polls=0
    max_idle_polls=25

    while :; do
      chunk_file=""
      for candidate in "$chunks_dir"/pending_*; do
        [[ -e "$candidate" ]] || break
        claimed="$chunks_dir/claimed_${worker}_$(basename "$candidate")"
        if mv "$candidate" "$claimed" 2>/dev/null; then
          chunk_file="$claimed"
          break
        fi
      done

      if [[ -z "$chunk_file" ]]; then
        # Keep polling briefly so workers do not exit on a transient empty scan.
        if [[ "$idle_polls" -lt "$max_idle_polls" ]]; then
          idle_polls=$((idle_polls + 1))
          sleep 0.2 || true
          continue
        fi
        break
      fi

      idle_polls=0

      while IFS= read -r seq; do
        [[ -n "$seq" ]] || continue
        attempted=$((attempted + 1))

        if out_text="$(timeout "${timeout_sec}s" "$solver_bin" <<< "${seq}?" 2>/dev/null)"; then
          move="$(printf "%s\n" "$out_text" | tail -n 1)"
          rc=0
        else
          rc=$?
          move=""
        fi

        if [[ "$rc" -eq 0 && "$move" =~ ^[1-7]$ ]]; then
          printf "%s\t%s\n" "$seq" "$move" >> "$out"
          solved=$((solved + 1))
        else
          printf "%s\t-\n" "$seq" >> "$out"
          if [[ "$rc" -eq 124 ]]; then
            timeouts=$((timeouts + 1))
          else
            errors=$((errors + 1))
          fi
        fi

        if (( attempted % log_every == 0 )); then
          now="$(date +%s)"
          elapsed_s=$((now - start_epoch))
          parts=("worker=${worker}" "attempted=${attempted}" "chunks_done=${chunks_done}")
          add_field() {
            local name="$1"
            local value="$2"
            if [[ "$omit_zero" -eq 0 || "$value" -ne 0 ]]; then
              parts+=("${name}=${value}")
            fi
          }
          add_field "solved" "$solved"
          add_field "timeouts" "$timeouts"
          add_field "errors" "$errors"
          parts+=("seq=${seq}" "elapsed_s=${elapsed_s}")
          echo "progress: ${parts[*]}"
        fi
      done < "$chunk_file"

      chunks_done=$((chunks_done + 1))
      rm -f "$chunk_file"
    done

    now="$(date +%s)"
    elapsed_s=$((now - start_epoch))
    summary=("worker=${worker}" "attempted=${attempted}" "chunks_done=${chunks_done}")
    if [[ "$omit_zero" -eq 0 || "$solved" -ne 0 ]]; then summary+=("solved=${solved}"); fi
    if [[ "$omit_zero" -eq 0 || "$timeouts" -ne 0 ]]; then summary+=("timeouts=${timeouts}"); fi
    if [[ "$omit_zero" -eq 0 || "$errors" -ne 0 ]]; then summary+=("errors=${errors}"); fi
    summary+=("elapsed_s=${elapsed_s}")
    echo "worker_done: ${summary[*]}"
  ) &
  worker_pids+=("$!")
done

# Wait loop with optional max runtime enforcement.
while :; do
  active=0
  active_count=0
  for pid in "${worker_pids[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      active=1
      active_count=$((active_count + 1))
    fi
  done
  [[ "$active" -eq 0 ]] && break

  if [[ "$stop_requested" -eq 1 ]]; then
    now="$(date +%s)"

    if [[ $((now - last_stop_log)) -ge 2 ]]; then
      echo "Waiting for workers to stop... active_workers=${active_count}" >&2
      last_stop_log="$now"
    fi

    if [[ "$force_kill_sent" -eq 0 && $((now - stop_epoch)) -ge "$stop_grace_sec" ]]; then
      echo "Workers still active after ${stop_grace_sec}s; sending SIGKILL..." >&2
      for pid in "${worker_pids[@]:-}"; do
        kill -KILL "$pid" 2>/dev/null || true
        pkill -KILL -P "$pid" 2>/dev/null || true
      done
      force_kill_sent=1
    fi
  fi

  if [[ "$stop_requested" -eq 0 && "$max_minutes" -gt 0 ]]; then
    now="$(date +%s)"
    elapsed_s=$((now - start_epoch))
    max_s=$((max_minutes * 60))
    if [[ "$elapsed_s" -ge "$max_s" ]]; then
      echo "Reached --max-minutes=$max_minutes. Stopping workers..." >&2
      request_stop
    fi
  fi

  # Ctrl+C can interrupt sleep; under `set -e` that would abort before finalize.
  sleep 1 || true
done

# Reap workers.
for pid in "${worker_pids[@]:-}"; do
  wait "$pid" 2>/dev/null || true
done

if [[ "$stop_requested" -eq 1 ]]; then
  echo "Interrupted: merging partial worker outputs." >&2
fi

# After workers are stopped, finalize atomically even if user presses Ctrl+C again.
trap '' INT TERM
echo "Finalizing: collecting worker outputs..."

# 4) Merge successful results and dedupe by sequence.
move_files=("$work_dir"/worker_*.moves)
if [[ "${#move_files[@]}" -eq 0 ]]; then
  echo "No worker output files found; nothing to merge."
  exit 0
fi

cat "${move_files[@]}" \
  | awk '$2 ~ /^[1-7]$/ && !seen[$1]++ {print $1"\t"$2}' > "$merged_file"

resolved_now=$(awk 'END{print NR+0}' "$merged_file")
echo "Resolved in this run: $resolved_now"

if [[ "$resolved_now" -eq 0 ]]; then
  echo "No moves resolved; opening book not modified."
  exit 0
fi

# 5) Apply merged moves atomically to book.
echo "Finalizing: applying merged moves to book..."
awk -v merged="$merged_file" '
  BEGIN {
    while((getline < merged) > 0) {
      if(NF >= 2) m[$1] = $2;
    }
    close(merged);
  }
  /^#/ {print; next}
  NF >= 3 {
    if($2 == "-" && ($1 in m)) $2 = m[$1];
    print $1, $2, $3;
    next;
  }
  {print}
' "$book_file" > "$book_tmp"
mv "$book_tmp" "$book_file"

# 6) Append merged moves to move cache.
echo "Finalizing: appending merged moves to cache..."
mkdir -p "$(dirname "$move_cache_file")"
touch "$move_cache_file"
cat "$merged_file" >> "$move_cache_file"

remaining=$(awk -v n="$seq_len" '!/^#/ && NF>=3 && length($1)==n && $2=="-" {c++} END{print c+0}' "$book_file")
echo "Done. Remaining unresolved len-$seq_len moves: $remaining"
echo "Merged moves file: $merged_file"
