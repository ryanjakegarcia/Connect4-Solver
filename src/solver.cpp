#include <cassert>
#include <climits>
#include <array>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <unordered_map>
#include "solver.hpp"
#include "MoverSorter.hpp"


unsigned long long nodeCount;
int columnOrder[Position::WIDTH];
TranspositionTable<Position::WIDTH * (Position::HEIGHT + 1),
                log2(Position::MAX_SCORE - Position::MIN_SCORE + 1) + 1,
                23> table;

struct OpeningBook {
    std::unordered_map<uint64_t, int> exactScoreByCanonicalKey;
    std::unordered_map<uint64_t, int> weakScoreByCanonicalKey;
    std::unordered_map<uint64_t, int> bestMoveByKey;
    bool loaded = false;
};

static OpeningBook g_openingBook;
static const char *OPENING_BOOK_PATH = "data/opening_book.txt";

static int mirrorCol(int col) {
    return Position::WIDTH - 1 - col;
}

static void addBookBestMove(const Position &P, int best_col) {
    g_openingBook.bestMoveByKey[P.key()] = best_col;
    g_openingBook.bestMoveByKey[P.mirroredKey()] = mirrorCol(best_col);
}

static int classifyWeakScore(int score) {
    return (score > 0) - (score < 0);
}

static bool loadOpeningBook(const char *path) {
    std::ifstream in(path);
    if(!in.is_open()) return false;

    std::string line;
    int loaded_scores = 0;
    int loaded_moves = 0;
    while(std::getline(in, line)) {
        if(line.empty() || line[0] == '#') continue;

        std::istringstream iss(line);
        std::string seq;
        std::string best_col_tok;
        std::string score_tok;
        if(!(iss >> seq)) continue;

        Position P;
        if(P.play(seq) != seq.size()) continue;

        // Format: <sequence> [best_col_1_based|-> [exact_score|-]
        if(iss >> best_col_tok) {
            try {
                if(best_col_tok != "-") {
                    int best_col = std::stoi(best_col_tok) - 1;
                    if(best_col >= 0 && best_col < Position::WIDTH && P.canPlay(best_col)) {
                        addBookBestMove(P, best_col);
                        loaded_moves++;
                    }
                }
            } catch(const std::exception &) {
                // Ignore malformed best-move tokens.
            }

            if(iss >> score_tok) {
                try {
                    if(score_tok != "-") {
                        int score = std::stoi(score_tok);
                        g_openingBook.exactScoreByCanonicalKey[P.canonicalKey()] = score;
                        g_openingBook.weakScoreByCanonicalKey[P.canonicalKey()] = classifyWeakScore(score);
                        loaded_scores++;
                    }
                } catch(const std::exception &) {
                    // Ignore malformed score tokens.
                }
            }
        }
    }

    g_openingBook.loaded = true;
    std::cerr << "Opening book loaded from '" << path << "' (moves="
              << loaded_moves << ", scores=" << loaded_scores << ")" << std::endl;
    return true;
}

static bool getOpeningBookBestMove(const Position &P, int &best_col) {
    const auto it = g_openingBook.bestMoveByKey.find(P.key());
    if(it == g_openingBook.bestMoveByKey.end()) return false;
    if(!P.canPlay(it->second)) return false;
    best_col = it->second;
    return true;
}

static bool getOpeningBookExactScore(const Position &P, int &score) {
    const auto it = g_openingBook.exactScoreByCanonicalKey.find(P.canonicalKey());
    if(it == g_openingBook.exactScoreByCanonicalKey.end()) return false;
    score = it->second;
    return true;
}

static bool getOpeningBookWeakScore(const Position &P, int &score) {
    const auto it = g_openingBook.weakScoreByCanonicalKey.find(P.canonicalKey());
    if(it == g_openingBook.weakScoreByCanonicalKey.end()) return false;
    score = it->second;
    return true;
}

/**
 * Recursively score connect 4 position using negamax variant of alpha-beta algorithm.
 * @param: alpha < beta, a score window within which we are evaluating the position.
 * @return the exact score, an upper or lower bound score depending on the case:
 * - if true score of position <= alpah then true score <= return value <= alpha
 * - if true score of position >= beta then beta <= return value <= true score
 * - if alpha <= true score <= beta then return value = true score
 */
int Solver::negamax(const Position &curPos, int alpha, int beta){
    assert(alpha < beta);
    assert(!curPos.canWinNext());

    nodeCount++;

    uint64_t possible = curPos.possibleNonLosingMoves();
    if(possible == 0) return -(Position::WIDTH * Position::HEIGHT - curPos.moveCount()) / 2;

    if(curPos.moveCount() >= Position::WIDTH * Position::HEIGHT - 2) return 0;

    int min = -(Position::WIDTH * Position::HEIGHT - 2 - curPos.moveCount()) / 2;
    if(alpha < min){
        alpha = min;
        if(alpha >= beta) return alpha;
    }

    int max = (Position::WIDTH * Position::HEIGHT - 1 - curPos.moveCount()) / 2;
    if(beta > max){
        beta = max;
        if(alpha >= beta) return beta;
    }

    const uint64_t key = curPos.canonicalKey();
    if(int val = table.get(key)){
        if(val > Position::MAX_SCORE - Position::MIN_SCORE + 1){
            min = val + 2 * Position::MIN_SCORE - Position::MAX_SCORE - 2;
            if(alpha < min){
                alpha = min;
                if(alpha >= beta) return alpha;
            }
        }
        else{
            max = val + Position::MIN_SCORE - 1;
            if(beta > max){
                beta = max;
                if(alpha >= beta) return beta;
            }
        }
    }

    MoveSorter moves;
    const bool is_symmetric = (curPos.key() == curPos.mirroredKey());
    const int middle_col = Position::WIDTH / 2;

    for(int i = Position::WIDTH; i--; ){
        const int col = columnOrder[i];
        if(is_symmetric && col > middle_col) continue;
        if(uint64_t move = possible & Position::column_mask(col))
            moves.add(move, curPos.scoreMove(move));
    }

    bool first_move = true;
    while(uint64_t next = moves.getNext()){
        Position newPos(curPos);
        newPos.play(next);

        int score;
        if(first_move){
            score = -negamax(newPos, -beta, -alpha);
            first_move = false;
        }
        else{
            // PVS: narrow null-window search first; re-search only if it improves alpha.
            score = -negamax(newPos, -alpha - 1, -alpha);
            if(score > alpha && score < beta)
                score = -negamax(newPos, -beta, -alpha);
        }

        if(score >= beta){
            table.put(key, score + Position::MAX_SCORE - 2 * Position::MIN_SCORE + 2);
            return score;
        }
        if(score > alpha) alpha = score;
    }

    table.put(key, alpha - Position::MIN_SCORE + 1);
    return alpha;
}

int Solver::solve(const Position &P, bool weak)
{
    if(P.canWinNext())
        return (Position::WIDTH * Position::HEIGHT + 1 - P.moveCount()) / 2;
    int min = -(Position::WIDTH * Position::HEIGHT - P.moveCount()) / 2;
    int max = (Position::WIDTH * Position::HEIGHT + 1 - P.moveCount()) / 2;

    if(weak){
        min = -1;
        max = 1;
    }

    while(min < max){
        int med = min + (max - min) / 2;
        if(med <= 0 && min / 2 < med) med = min / 2;
        else if(med >= 0 && max / 2 > med) med = max / 2;
        int r = negamax(P, med, med + 1);
        if(r <= med) max = r;
        else min = r;
    }
    return min;
}

Solver::Solver() : nodeCount{0} {
    reset();
    for(int i = 0; i < Position::WIDTH; i++)
        columnOrder[i] = Position::WIDTH / 2 + (1 - 2 * (i % 2)) * (i + 1) / 2;
}
#include <sys/time.h>
unsigned long long getTimeMicrosec(){
    timeval NOW;
    gettimeofday(&NOW, NULL);
    return NOW.tv_sec*1000000LL + NOW.tv_usec;
}

// Center-out column evaluation order (0-based)
static const int COL_ORDER[Position::WIDTH] = {3, 2, 4, 1, 5, 0, 6};

enum class GameStatus {
    Ongoing,
    WinP1,
    WinP2,
    Draw,
    Invalid,
};

static bool isConnect4(
    const std::array<std::array<int, Position::WIDTH>, Position::HEIGHT> &grid,
    int row,
    int col,
    int player
) {
    static const int DIRS[4][2] = {
        {1, 0}, {0, 1}, {1, 1}, {1, -1}
    };

    for(const auto &d : DIRS) {
        int count = 1;
        for(int sign : {-1, 1}) {
            int r = row + sign * d[1];
            int c = col + sign * d[0];
            while(r >= 0 && r < Position::HEIGHT && c >= 0 && c < Position::WIDTH && grid[r][c] == player) {
                count++;
                r += sign * d[1];
                c += sign * d[0];
            }
        }
        if(count >= 4) return true;
    }
    return false;
}

static GameStatus analyzeSequenceStatus(const std::string &seq) {
    std::array<std::array<int, Position::WIDTH>, Position::HEIGHT> grid{};
    std::array<int, Position::WIDTH> heights{};

    int player = 1;
    bool game_over = false;
    GameStatus terminal_status = GameStatus::Ongoing;
    int total = 0;

    for(char ch : seq) {
        if(ch < '1' || ch > '7') return GameStatus::Invalid;
        const int col = ch - '1';
        if(game_over) return GameStatus::Invalid;
        if(heights[col] >= Position::HEIGHT) return GameStatus::Invalid;

        const int row = heights[col]++;
        grid[row][col] = player;
        total++;

        if(isConnect4(grid, row, col, player)) {
            game_over = true;
            terminal_status = player == 1 ? GameStatus::WinP1 : GameStatus::WinP2;
        }

        if(total == Position::WIDTH * Position::HEIGHT) {
            game_over = true;
            if(terminal_status == GameStatus::Ongoing)
                terminal_status = GameStatus::Draw;
        }

        player = 3 - player;
    }
    return game_over ? terminal_status : GameStatus::Ongoing;
}

static const char *statusToString(GameStatus s) {
    switch(s) {
        case GameStatus::Ongoing: return "ongoing";
        case GameStatus::WinP1: return "win1";
        case GameStatus::WinP2: return "win2";
        case GameStatus::Draw: return "draw";
        case GameStatus::Invalid: return "invalid";
    }
    return "invalid";
}

int main(int argc, char** argv){
    Solver solver;
    bool weak = false;
    if(argc > 1 && argv[1][0] == '-' && argv[1][1] == 'w') weak = true;

    if(!g_openingBook.loaded)
        loadOpeningBook(OPENING_BOOK_PATH);

    std::string line;

    for(int l = 1; std::getline(std::cin, line); l++){
        // A line ending with '?' is a "best move" query.
        // Return the 1-based column number of the best move to play.
        bool find_best_move = !line.empty() && line.back() == '?';
        if(find_best_move) line.pop_back();

        // A line ending with '!' is a status query.
        // Return one of: ongoing, win1, win2, draw, invalid.
        bool query_status = !find_best_move && !line.empty() && line.back() == '!';
        if(query_status) line.pop_back();

        if(query_status) {
            std::cout << statusToString(analyzeSequenceStatus(line)) << std::endl;
            continue;
        }

        Position P;
        if(P.play(line) != line.size()){
            std::cerr << "Line " << l << ": Invalid move " << (P.moveCount() + 1)
                      << " \"" << line << "\"" << std::endl;
            if(find_best_move) std::cout << -1;
        }
        else if(find_best_move){
            int best_col = -1;

            // Opening-book fast path: on an empty board, always open in center.
            if(P.moveCount() == 0){
                best_col = Position::WIDTH / 2;
            }

            // --- Step 1: Check for immediate winning move (always, regardless of threshold) ---
            if(best_col == -1 && P.canWinNext()){
                for(int i = 0; i < Position::WIDTH; i++){
                    int col = COL_ORDER[i];
                    if(P.canPlay(col) && P.isWinningMove(col)){
                        best_col = col;
                        break;
                    }
                }
            }

            // --- Step 2: Use opening-book best move when available ---
            if(best_col == -1)
                getOpeningBookBestMove(P, best_col);

            if(best_col == -1){
                // Step 3 fallback: full negamax for best-move selection.
                int best_score = INT_MIN;
                solver.resetNodeCount();
                for(int i = 0; i < Position::WIDTH; i++){
                    int col = COL_ORDER[i];
                    if(!P.canPlay(col)) continue;
                    if(P.isWinningMove(col)){  // should be caught above, but be safe
                        best_col = col;
                        break;
                    }
                    Position child(P);
                    child.playCol(col);
                    // Negative score: best for us = worst for opponent
                    int score = -solver.solve(child, weak);
                    if(score > best_score){
                        best_score = score;
                        best_col = col;
                    }
                }
            }

            // Fallback (should not be reached)
            if(best_col == -1){
                for(int i = 0; i < Position::WIDTH; i++){
                    int col = COL_ORDER[i];
                    if(P.canPlay(col)){ best_col = col; break; }
                }
            }

            std::cout << best_col + 1; // 1-based output
        }
        else{
            // Original score-only mode, but keep the cache warm across queries.
            solver.resetNodeCount();
            unsigned long long start_time = getTimeMicrosec();
            int score;
            if(weak) {
                if(!getOpeningBookWeakScore(P, score))
                    score = solver.solve(P, true);
            }
            else {
                if(!getOpeningBookExactScore(P, score))
                    score = solver.solve(P, false);
            }
            unsigned long long end_time = getTimeMicrosec();
            std::cout << line << " " << score << " " << solver.getNodeCount() << " " << (end_time - start_time);
        }
        std::cout << std::endl;
    }
}

