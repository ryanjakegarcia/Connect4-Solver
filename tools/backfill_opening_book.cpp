#include <algorithm>
#include <cassert>
#include <fstream>
#include <iostream>
#include <string>
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

static bool parseEntryLine(const std::string &line, Entry &e) {
    if (line.empty() || line[0] == '#') {
        e.is_comment = true;
        e.raw = line;
        return true;
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
        return true;
    }

    e.is_comment = false;
    e.seq = a;
    e.best = b;
    e.score = c;
    return true;
}

int main(int argc, char **argv) {
    const std::string book_path = (argc > 1) ? argv[1] : "data/opening_book.txt";
    const std::string cache_path = (argc > 2) ? argv[2] : ".opening_book_scores.tsv";

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

    std::vector<int> idx;
    idx.reserve(entries.size());
    for (int i = 0; i < static_cast<int>(entries.size()); i++) {
        if (!entries[i].is_comment) idx.push_back(i);
    }

    std::sort(idx.begin(), idx.end(), [&](int lhs, int rhs) {
        const auto &a = entries[lhs];
        const auto &b = entries[rhs];
        if (a.seq.size() != b.seq.size()) return a.seq.size() > b.seq.size();
        return a.seq < b.seq;
    });

    std::vector<std::pair<std::string, int>> derived;
    derived.reserve(4096);

    bool changed = true;
    int pass = 0;
    while (changed) {
        changed = false;
        pass++;

        for (int k : idx) {
            Entry &e = entries[k];
            if (e.score != "-") continue;

            Position p;
            if (p.play(e.seq) != e.seq.size()) continue;

            int resolved_score = 0;
            bool resolvable = false;

            if (p.canWinNext()) {
                resolved_score = (Position::WIDTH * Position::HEIGHT + 1 - p.moveCount()) / 2;
                resolvable = true;
            } else {
                bool all_children_known = true;
                int best_value = -1000000;

                for (int col = 0; col < Position::WIDTH; col++) {
                    if (!p.canPlay(col)) continue;

                    std::string child = e.seq;
                    child.push_back(static_cast<char>('1' + col));

                    auto it = score_by_seq.find(child);
                    if (it == score_by_seq.end()) {
                        all_children_known = false;
                        break;
                    }

                    const int value = -it->second;
                    if (value > best_value) best_value = value;
                }

                if (all_children_known) {
                    resolved_score = best_value;
                    resolvable = true;
                }
            }

            if (resolvable) {
                e.score = std::to_string(resolved_score);
                score_by_seq[e.seq] = resolved_score;
                derived.emplace_back(e.seq, resolved_score);
                changed = true;
            }
        }
    }

    std::ofstream out(book_path, std::ios::trunc);
    if (!out.is_open()) {
        std::cerr << "Error: cannot write " << book_path << "\n";
        return 1;
    }

    for (const auto &e : entries) {
        if (e.is_comment) {
            out << e.raw << "\n";
        } else {
            out << e.seq << ' ' << e.best << ' ' << e.score << "\n";
        }
    }

    std::ofstream cache(cache_path, std::ios::app);
    if (cache.is_open()) {
        for (const auto &kv : derived) {
            cache << kv.first << '\t' << kv.second << "\n";
        }
    }

    int unresolved = 0;
    int resolved = 0;
    for (const auto &e : entries) {
        if (e.is_comment) continue;
        if (e.score == "-") unresolved++;
        else resolved++;
    }

    std::cout << "Backfill complete: derived=" << derived.size()
              << " resolved=" << resolved
              << " unresolved=" << unresolved
              << " passes=" << pass << "\n";

    return 0;
}
