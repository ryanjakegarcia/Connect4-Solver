#include <algorithm>
#include <cassert>
#include <chrono>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <unordered_map>
#include <utility>
#include <vector>

#include "../src/Position.hpp"

struct Entry {
    bool is_comment = false;
    std::string raw;
    std::string seq;
    std::string best;
    std::string score;
};

static volatile std::sig_atomic_t g_stop_requested = 0;

static void handleSignal(int) {
    g_stop_requested = 1;
}

static void parseEntryLine(const std::string &line, Entry &e) {
    if (line.empty() || line[0] == '#') {
        e.is_comment = true;
        e.raw = line;
        return;
    }

    std::string a, b, c;
    std::size_t i = 0;
    auto nextToken = [&](std::string &out) -> bool {
        while (i < line.size() && std::isspace(static_cast<unsigned char>(line[i]))) i++;
        if (i >= line.size()) return false;
        std::size_t j = i;
        while (j < line.size() && !std::isspace(static_cast<unsigned char>(line[j]))) j++;
        out.assign(line.begin() + static_cast<long>(i), line.begin() + static_cast<long>(j));
        i = j;
        return true;
    };

    if (!nextToken(a) || !nextToken(b) || !nextToken(c)) {
        e.is_comment = true;
        e.raw = line;
        return;
    }

    e.is_comment = false;
    e.seq = a;
    e.best = b;
    e.score = c;
}

static std::string makeTmpPath(const char *prefix) {
    std::string pattern = std::string("/tmp/") + prefix + "XXXXXX";
    std::vector<char> buf(pattern.begin(), pattern.end());
    buf.push_back('\0');
    int fd = mkstemp(buf.data());
    if (fd >= 0) close(fd);
    return std::string(buf.data());
}

// Runs a batch of seq? queries through one solver process.
// Returns per-sequence best move in order where available, else -1.
static void solveBestMovesViaBatch(const std::vector<std::string> &seqs,
                                   const std::string &solver_bin,
                                   int batch_timeout_sec,
                                   std::vector<int> &best_moves,
                                   int &solved_count,
                                   int &timeout_count,
                                   int &error_count) {
    best_moves.assign(seqs.size(), -1);
    solved_count = 0;
    timeout_count = 0;
    error_count = 0;
    if (seqs.empty()) return;

    const std::string in_path = makeTmpPath("c4_batch_in_");
    const std::string out_path = makeTmpPath("c4_batch_out_");

    {
        std::ofstream in_file(in_path, std::ios::trunc);
        for (const auto &s : seqs) in_file << s << "?\n";
    }

    const int batch_timeout = std::max(1, batch_timeout_sec);
    const std::string cmd = "timeout " + std::to_string(batch_timeout) + "s " +
                            solver_bin + " < " + in_path + " > " + out_path +
                            " 2>/dev/null";
    const int status = std::system(cmd.c_str());

    std::ifstream out_file(out_path);
    std::string line;
    int out_idx = 0;
    while (std::getline(out_file, line) && out_idx < static_cast<int>(seqs.size())) {
        if (line.size() == 1 && line[0] >= '1' && line[0] <= '7') {
            best_moves[out_idx] = line[0] - '0';
            solved_count++;
        }
        out_idx++;
    }

    if (status == -1) {
        for (std::size_t i = out_idx; i < seqs.size(); i++) error_count++;
    } else if (WIFEXITED(status)) {
        const int code = WEXITSTATUS(status);
        if (code == 124) {
            for (std::size_t i = out_idx; i < seqs.size(); i++) timeout_count++;
        } else if (code != 0) {
            for (std::size_t i = out_idx; i < seqs.size(); i++) error_count++;
        } else {
            for (std::size_t i = out_idx; i < seqs.size(); i++) error_count++;
        }
    } else {
        for (std::size_t i = out_idx; i < seqs.size(); i++) error_count++;
    }

    std::remove(in_path.c_str());
    std::remove(out_path.c_str());
}

int main(int argc, char **argv) {
    std::string book_path = "data/opening_book.txt";
    std::string move_cache_path = ".opening_book_moves.tsv";
    std::string solver_bin = "./solver";
    int timeout_sec = 120;
    int batch_timeout_sec = -1;
    int log_every = 100;
    int save_every = 50;
    int batch_size = 64;
    int max_cases = 0;
    int max_minutes = 0;
    bool omit_zero = false;

    int positional = 0;
    for (int i = 1; i < argc; i++) {
        const std::string arg = argv[i];
        if (arg == "--book" && i + 1 < argc) {
            book_path = argv[++i];
        } else if (arg == "--move-cache" && i + 1 < argc) {
            move_cache_path = argv[++i];
        } else if (arg == "--solver" && i + 1 < argc) {
            solver_bin = argv[++i];
        } else if (arg == "--timeout-sec" && i + 1 < argc) {
            timeout_sec = std::stoi(argv[++i]);
        } else if (arg == "--log-every" && i + 1 < argc) {
            log_every = std::stoi(argv[++i]);
        } else if (arg == "--save-every" && i + 1 < argc) {
            save_every = std::stoi(argv[++i]);
        } else if (arg == "--batch-size" && i + 1 < argc) {
            batch_size = std::stoi(argv[++i]);
        } else if (arg == "--batch-timeout-sec" && i + 1 < argc) {
            batch_timeout_sec = std::stoi(argv[++i]);
        } else if (arg == "--max-cases" && i + 1 < argc) {
            max_cases = std::stoi(argv[++i]);
        } else if (arg == "--max-minutes" && i + 1 < argc) {
            max_minutes = std::stoi(argv[++i]);
        } else if (arg == "--omit-zero") {
            omit_zero = true;
        } else if (arg == "-h" || arg == "--help") {
            std::cout
                << "Usage: ./backfill_opening_book_moves [options] [book_file] [move_cache] [solver]\n"
                << "Options:\n"
                << "  --book FILE         Opening book path (default: data/opening_book.txt)\n"
                << "  --move-cache FILE   Move cache path (default: .opening_book_moves.tsv)\n"
                << "  --solver PATH|none  Solver binary for len-5 fallback (default: ./solver)\n"
                << "  --timeout-sec N     Timeout for solver fallback (default: 120)\n"
                << "  --batch-size N      Number of seq? queries per solver batch (default: 64)\n"
                << "  --batch-timeout-sec N  Timeout for each solver batch (default: --timeout-sec)\n"
                << "  --log-every N       Progress logging period (default: 100)\n"
                << "  --save-every N      Persist progress every N newly derived moves (default: 50)\n"
                << "  --max-cases N       Max unresolved positions to attempt (default: 0 = no limit)\n"
                << "  --max-minutes N     Max wall-clock minutes (default: 0 = no limit)\n"
                << "  --omit-zero         Omit zero counters in progress logs\n";
            return 0;
        } else if (!arg.empty() && arg[0] == '-') {
            std::cerr << "Error: unknown argument: " << arg << "\n";
            return 1;
        } else {
            if (positional == 0) book_path = arg;
            else if (positional == 1) move_cache_path = arg;
            else if (positional == 2) solver_bin = arg;
            positional++;
        }
    }

    if (log_every <= 0) {
        std::cerr << "Error: --log-every must be > 0\n";
        return 1;
    }
    if (save_every <= 0) {
        std::cerr << "Error: --save-every must be > 0\n";
        return 1;
    }
    if (batch_size <= 0) {
        std::cerr << "Error: --batch-size must be > 0\n";
        return 1;
    }
    if (batch_timeout_sec == -1) {
        batch_timeout_sec = timeout_sec;
    }
    if (batch_timeout_sec <= 0) {
        std::cerr << "Error: --batch-timeout-sec must be > 0\n";
        return 1;
    }
    if (max_cases < 0) {
        std::cerr << "Error: --max-cases must be >= 0\n";
        return 1;
    }
    if (max_minutes < 0) {
        std::cerr << "Error: --max-minutes must be >= 0\n";
        return 1;
    }
    if (timeout_sec <= 0) {
        std::cerr << "Error: --timeout-sec must be > 0\n";
        return 1;
    }

    std::signal(SIGINT, handleSignal);
    std::signal(SIGTERM, handleSignal);
    std::signal(SIGTSTP, handleSignal);

    std::ifstream in(book_path);
    if (!in.is_open()) {
        std::cerr << "Error: cannot open " << book_path << "\n";
        return 1;
    }

    std::vector<Entry> entries;
    entries.reserve(20000);
    std::unordered_map<std::string, int> score_by_seq;

    std::string line;
    while (std::getline(in, line)) {
        Entry e;
        parseEntryLine(line, e);
        entries.push_back(e);
        if (!e.is_comment && e.score != "-") {
            score_by_seq[e.seq] = std::stoi(e.score);
        }
    }

    int derived = 0;
    int derived_from_scores = 0;
    int solver_attempts = 0;
    int solver_solved = 0;
    int solver_timeouts = 0;
    int solver_errors = 0;
    int queued = 0;
    int completed = 0;
    std::vector<std::pair<std::string, int>> derived_moves;
    derived_moves.reserve(4096);
    std::vector<int> pending_solver_idx;
    pending_solver_idx.reserve(static_cast<std::size_t>(batch_size));

    static const int colOrder[Position::WIDTH] = {3, 2, 4, 1, 5, 0, 6};

    const auto start = std::chrono::steady_clock::now();

    std::size_t cache_written_count = 0;
    int last_persisted_derived = 0;

    auto persistProgress = [&](bool force) {
        if (!force && derived - last_persisted_derived < save_every) return;

        const std::string tmp_book_path = book_path + ".tmp_checkpoint";
        {
            std::ofstream out(tmp_book_path, std::ios::trunc);
            if (!out.is_open()) {
                std::cerr << "Warning: failed to open checkpoint file for book write\n";
                return;
            }
            for (const auto &ent : entries) {
                if (ent.is_comment) out << ent.raw << "\n";
                else out << ent.seq << ' ' << ent.best << ' ' << ent.score << "\n";
            }
        }

        // Atomic replace of the book file.
        if (std::rename(tmp_book_path.c_str(), book_path.c_str()) != 0) {
            std::remove(tmp_book_path.c_str());
            std::cerr << "Warning: failed to replace book file during checkpoint\n";
            return;
        }

        std::ofstream move_cache(move_cache_path, std::ios::app);
        if (move_cache.is_open()) {
            for (std::size_t i = cache_written_count; i < derived_moves.size(); i++) {
                move_cache << derived_moves[i].first << '\t' << derived_moves[i].second << "\n";
            }
            cache_written_count = derived_moves.size();
        }

        last_persisted_derived = derived;
    };

    auto flushSolverBatch = [&]() {
        if (pending_solver_idx.empty() || solver_bin == "none") return;

        std::vector<std::string> seqs;
        seqs.reserve(pending_solver_idx.size());
        for (int idx : pending_solver_idx) seqs.push_back(entries[idx].seq);

        std::vector<int> best_moves;
        int solved_now = 0;
        int timeouts_now = 0;
        int errors_now = 0;

        std::cout << "batch: size=" << seqs.size()
              << " timeout_s=" << batch_timeout_sec
              << " first_seq=" << seqs.front() << "\n";

        solveBestMovesViaBatch(seqs, solver_bin, batch_timeout_sec, best_moves,
                               solved_now, timeouts_now, errors_now);

        std::cout << "batch_done: solved=" << solved_now
              << " timed_out=" << timeouts_now
              << " errors=" << errors_now << "\n";

        const int batch_count = static_cast<int>(pending_solver_idx.size());
        solver_attempts += batch_count;
        solver_solved += solved_now;
        solver_timeouts += timeouts_now;
        solver_errors += errors_now;
        completed += batch_count;

        for (std::size_t i = 0; i < pending_solver_idx.size(); i++) {
            if (i >= best_moves.size()) break;
            if (best_moves[i] < 1 || best_moves[i] > 7) continue;
            Entry &e = entries[pending_solver_idx[i]];
            e.best = std::to_string(best_moves[i]);
            derived_moves.emplace_back(e.seq, best_moves[i]);
            derived++;
        }

        pending_solver_idx.clear();
        persistProgress(true);
    };

    for (auto &e : entries) {
        if (g_stop_requested) {
            std::cerr << "Stop requested by signal, saving progress...\n";
            break;
        }
        if (e.is_comment) continue;
        if (e.best != "-") continue;

        if (max_cases > 0 && queued >= max_cases) {
            std::cout << "Reached --max-cases=" << max_cases << "\n";
            break;
        }

        if (max_minutes > 0) {
            const auto now = std::chrono::steady_clock::now();
            const auto elapsed = std::chrono::duration_cast<std::chrono::minutes>(now - start).count();
            if (elapsed >= max_minutes) {
                std::cout << "Reached --max-minutes=" << max_minutes << "\n";
                break;
            }
        }

        queued++;

        Position p;
        if (p.play(e.seq) != e.seq.size()) continue;

        int best_col = -1;
        int best_value = -1000000;
        bool can_resolve = true;

        // Preserve immediate tactical wins first.
        if (p.canWinNext()) {
            for (int i = 0; i < Position::WIDTH; i++) {
                const int col = colOrder[i];
                if (p.canPlay(col) && p.isWinningMove(col)) {
                    best_col = col;
                    break;
                }
            }
            if (best_col == -1) can_resolve = false;
        } else {
            for (int i = 0; i < Position::WIDTH; i++) {
                const int col = colOrder[i];
                if (!p.canPlay(col)) continue;

                std::string child = e.seq;
                child.push_back(static_cast<char>('1' + col));

                auto it = score_by_seq.find(child);
                if (it == score_by_seq.end()) {
                    can_resolve = false;
                    break;
                }

                const int value = -it->second;
                if (best_col == -1 || value > best_value) {
                    best_value = value;
                    best_col = col;
                }
            }
        }

        if (!can_resolve || best_col == -1) {
            // For leaf layer in this book (len-5), derive exact best move via batched solver queries.
            if (e.seq.size() == 5 && solver_bin != "none") {
                pending_solver_idx.push_back(static_cast<int>(&e - &entries[0]));
                if (static_cast<int>(pending_solver_idx.size()) >= batch_size) {
                    flushSolverBatch();
                }
            }
        } else {
            e.best = std::to_string(best_col + 1);
            derived_moves.emplace_back(e.seq, best_col + 1);
            derived++;
            derived_from_scores++;
        }

        if (queued % log_every == 0) {
            const auto now = std::chrono::steady_clock::now();
            const auto elapsed =
                std::chrono::duration_cast<std::chrono::seconds>(now - start).count();

            std::vector<std::string> parts;
            parts.push_back("queued=" + std::to_string(queued));
            auto addField = [&](const std::string &name, int value) {
                if (!omit_zero || value != 0) {
                    parts.push_back(name + "=" + std::to_string(value));
                }
            };

            addField("completed", completed);
            addField("derived", derived);
            addField("derived_from_scores", derived_from_scores);
            addField("solver_attempts", solver_attempts);
            addField("solver_solved", solver_solved);
            addField("solver_timeouts", solver_timeouts);
            addField("solver_errors", solver_errors);
            parts.push_back("seq=" + e.seq);
            parts.push_back("elapsed_s=" + std::to_string(elapsed));

            std::cout << "progress:";
            for (const auto &pstr : parts) std::cout << " " << pstr;
            std::cout << "\n";
        }

        persistProgress(false);
    }

    flushSolverBatch();
    persistProgress(true);

    int unresolved_moves = 0;
    int resolved_moves = 0;
    for (const auto &e : entries) {
        if (e.is_comment) continue;
        if (e.best == "-") unresolved_moves++;
        else resolved_moves++;
    }

    std::cout << "Best-move backfill complete: derived=" << derived
              << " derived_from_scores=" << derived_from_scores
              << " solver_attempts=" << solver_attempts
              << " solver_solved=" << solver_solved
              << " solver_timeouts=" << solver_timeouts
              << " solver_errors=" << solver_errors
              << " queued_positions=" << queued
              << " completed_positions=" << completed
              << " resolved_moves=" << resolved_moves
              << " unresolved_moves=" << unresolved_moves << "\n";

    return 0;
}
