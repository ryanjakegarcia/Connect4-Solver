#ifndef MOVE_SORTER_HPP
#define MOVE_SORTER_HPP

#include "Position.hpp"

class MoveSorter{
public:
    /**
     * Add a move in the container with its score.
     * You cannot add more than Position::WIDTH moves
     */
    void add(uint64_t move, int score)
    {
        int pos = size++;
        for(; pos && entries[pos - 1].score > score; --pos) entries[pos] = entries[pos - 1];
        entries[pos].move = move;
        entries[pos].score = score;
    }

    /**
     * Get next move
     * @return next remaining move with max score and remove it from the container.
     * If no more moves are available return 0
     */
    uint64_t getNext()
    {
        if(size)
            return entries[--size].move;
        else
            return 0;
    }

    /**
     * Set size = 0, allows old data to be overwritten. ("empties container")
     */
    void reset()
    {
        size = 0;
    }

    MoveSorter(): size{0}
    {
    }
private:
    unsigned int size;
    struct { uint64_t move; int score; } entries[Position::WIDTH];
};

#endif
