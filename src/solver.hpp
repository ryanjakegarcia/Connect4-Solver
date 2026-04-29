#ifndef SOLVER_HPP
#define SOLVER_HPP

#include "Position.hpp"
#include "TranspositionTable.hpp"

constexpr unsigned int log2(unsigned int n)
{
    return n <= 1 ? 0 : log2(n / 2) + 1;
}

class Solver{
private:
    uint64_t nodeCount;
    
    int columnOrder[Position::WIDTH];
    
    TranspositionTable<Position::WIDTH * (Position::HEIGHT + 1),
                      log2(Position::MAX_SCORE - Position::MIN_SCORE + 1) + 2,
                      23> table;

    int negamax(const Position &curPos, int alpha, int beta);

public:
    int solve(const Position &P, bool weak = false);

    uint64_t getNodeCount(){
        return nodeCount;
    }

    void resetNodeCount(){
        nodeCount = 0;
    }

    void clearCache(){
        table.reset();
    }

    void reset(){
        resetNodeCount();
        clearCache();
    }

    Solver();
};

#endif
