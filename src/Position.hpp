#ifndef POSITION_HPP
#define POSITION_HPP

#include <string>
#include <cstdint>

/**
 * A class storing a Connect 4 position.
 * Functions are relative to current player to play.
 * Positions containing alignments are not supported by this class.
 */

 /**
  * Generate a bitmask containing one for the bottom slot of each column
  * must be defined outside of the class definition to be available at compile time for bottom_mask
  * 
  * Bitboard to encode for representation
  * .  .  .  .  .  .  .
  * 5 12 19 26 33 40 47
  * 4 11 18 25 32 39 46
  * 3 10 17 24 31 38 45
  * 2  9 16 23 30 37 44
  * 1  8 15 22 29 36 43
  * 0  7 14 21 28 35 42
  * 
  * Position is stored as
 * - a bitboard "mask" with 1 on any color stones
 * - a bitboard "current_player" with 1 on stones of current player
 *
 * "current_player" bitboard can be transformed into a compact and non ambiguous key
 * by adding an extra bit on top of the last non empty cell of each column.
 * This allow to identify all the empty cells whithout needing "mask" bitboard
 *
 * current_player "x" = 1, opponent "o" = 0
 * board     position  mask      key       bottom
 *           0000000   0000000   0000000   0000000
 * .......   0000000   0000000   0001000   0000000
 * ...o...   0000000   0001000   0010000   0000000
 * ..xx...   0011000   0011000   0011000   0000000
 * ..ox...   0001000   0011000   0001100   0000000
 * ..oox..   0000100   0011100   0000110   0000000
 * ..oxxo.   0001100   0011110   1101101   1111111
 *
 * current_player "o" = 1, opponent "x" = 0
 * board     position  mask      key       bottom
 *           0000000   0000000   0001000   0000000
 * ...x...   0000000   0001000   0000000   0000000
 * ...o...   0001000   0001000   0011000   0000000
 * ..xx...   0000000   0011000   0000000   0000000
 * ..ox...   0010000   0011000   0010100   0000000
 * ..oox..   0011000   0011100   0011010   0000000
 * ..oxxo.   0010010   0011110   1110011   1111111
 *
 * key is an unique representation of a board key = position + mask + bottom
 * in practice, as bottom is constant, key = position + mask is also a
 * non-ambigous representation of the position.
  */
constexpr static uint64_t bottom(int width, int height){
  return width == 0 ? 0 : bottom(width - 1, height) | 1LL << (width - 1) * (height + 1); //fills 0, 7, 14, 21, 28, 35, 42 with 1s
}

class Position{
public:
  static const int WIDTH = 7;
  static const int HEIGHT = 6;
  static const int MIN_SCORE = -(WIDTH * HEIGHT) / 2 + 3;
  static const int MAX_SCORE = (WIDTH * HEIGHT + 1) / 2 - 3;

  static_assert(WIDTH < 10, "Board's width must be less than 10");
  static_assert(WIDTH * (HEIGHT + 1) <= 64, "Board does not fit in 64bits bitboard");

  /**
   * Plays a possible move given as a single-bit bitmap.
   * @param move: bitmask of the cell to play.
   */
  void play(uint64_t move)
  {
    current_position ^= mask;
    mask |= move;
    moves++;
  }

  /**
   * Plays a sequence of successive played columns, mainly used to initialize a board.
   * @param seq: a sequence of digits corresponding to the 1-based index of the column played.
   * @return number of played moves. Processing will stop at first invalid move that can be:
   *            - invalid character (non-digit, or digit >= WIDTH)
   *            - playing a column that is already full
   *            - playing a column that makes an alignment  (we only solve non).
   *         Caller can check if the move sequence was valid by comparing the number of
   *         processed moves to the length of the sequence.
   */
  unsigned int play(std::string seq)
  {
    for(unsigned int i = 0; i < seq.size(); i++){
      int col = seq[i] - '1';
      if(col < 0 || col >= Position::WIDTH || !canPlay(col) || isWinningMove(col)) return i; //invalid move
      playCol(col);
    }
    return seq.size();
  }

  /**
   * Indicates whether the current player wins by playing a given column.
   * This function should never be called on a non-playable column.
   * @param col: 0-based index of a playable column.
   * @return true if current player makes an alignment by playing the corresponding column col.
   */
  bool canWinNext() const
  {
    return winning_position() & possible();
  }

  /**
   * @return number of moves played from the beginning of the game.
   */
  int moveCount() const
  {
    return moves;
  }

  /**
   * @return a compact representation of a position on WIDTH * (HEIGHT + 1) bits
   */
  uint64_t key() const
  {
    return current_position + mask;
  }

  /**
   * @return key() mirrored across the vertical axis.
   */
  uint64_t mirroredKey() const
  {
    const uint64_t k = key();
    uint64_t mirrored = 0;
    constexpr int STRIDE = HEIGHT + 1;
    constexpr uint64_t CHUNK_MASK = (UINT64_C(1) << STRIDE) - 1;

    for(int col = 0; col < WIDTH; col++){
      uint64_t chunk = (k >> (col * STRIDE)) & CHUNK_MASK;
      mirrored |= chunk << ((WIDTH - 1 - col) * STRIDE);
    }
    return mirrored;
  }

  /**
   * @return canonical key shared by a position and its mirror image.
   */
  uint64_t canonicalKey() const
  {
    const uint64_t k = key();
    const uint64_t mk = mirroredKey();
    return (mk < k) ? mk : k;
  }

  /**
   * Return a bitmap of all the possible next moves that do not lose in one turn.
   * A losing move is a move leaving the possibility for the opponent to win directly.
   * 
   * Warning this function is intended to test position where you cannot win in one turn
   * If you have a winning move, this function can miss it and prefer to prevent the opponent
   * to make an alignment.
   */
  uint64_t possibleNonLosingMoves() const {
    assert(!canWinNext());
    uint64_t possible_mask = possible();
    uint64_t opponent_win = opponent_winning_position();
    uint64_t forced_moves = possible_mask & opponent_win;
    if(forced_moves){
      if(forced_moves & (forced_moves - 1))
        return 0;
      else possible_mask = forced_moves;
    }
    return possible_mask & ~(opponent_win >> 1);
  }

  /**
   * Score a possible move.
   * @param move: a possible move given in a bitmap format.
   * The score we are using is the number of winning spots
   * the current player has after playing the move.
   */
  int scoreMove(uint64_t move) const {
    return popcount(compute_winning_position(current_position | move, mask));
  }

  /**
   * Default constructor, build an empty position.
   */
  Position() : current_position{0}, mask{0}, moves{0} {}

  /**
   * Indicates whether a column is playable.
   * @param col: 0-based index of column to play
   * @return true if the column is playable, false if the column is already full.
   */
  bool canPlay(int col) const
  {
    return (mask & topMaskCol(col)) == 0;
  }

  /**
   * Plays a playable column.
   * This function should not be called on a non-playable column or a column making an alignment.
   *
   * @param col: 0-based index of a playable column.
   */
  void playCol(int col)
  {
    play((mask + bottomMaskCol(col)) & columnMask(col));
  }

  /**
   * Indicates whether the current player wins by playing a given column.
   * This function should never be called on a non-playable column.
   * @param col: 0-based index of a playable column.
   * @return true if current player makes an alignment by playing the corresponding column col.
   */
  bool isWinningMove(int col) const
  {
    return winning_position() & possible() & columnMask(col);
  }

  // return a bitmask 1 on all the cells of a given column
  static constexpr uint64_t columnMask(int col) {
    return ((UINT64_C(1) << HEIGHT)-1) << col*(HEIGHT+1);
  }

private:
  uint64_t current_position;
  uint64_t mask;
  unsigned int moves;

  /*
    * Return a bitmask of the possible winning positions for the current player
    */
  uint64_t winning_position() const {
    return compute_winning_position(current_position, mask);
  }

  /*
    * Return a bitmask of the possible winning positions for the opponent
    */
  uint64_t opponent_winning_position() const {
    return compute_winning_position(current_position ^ mask, mask);
  }

  uint64_t possible() const {
    return (mask + BOTTOM_MASK) & BOARD_MASK;
  }

  /**
   * counts number of bits set to one in a 64bit integer
   */
  static unsigned int popcount(uint64_t m){
    unsigned int c = 0;
    for(c = 0; m; c++) m &= m - 1;
    return c;
  }

  static uint64_t compute_winning_position(uint64_t position, uint64_t mask) {
    // vertical;
    uint64_t r = (position << 1) & (position << 2) & (position << 3);

    //horizontal
    uint64_t p = (position << (HEIGHT+1)) & (position << 2*(HEIGHT+1));
    r |= p & (position << 3*(HEIGHT+1));
    r |= p & (position >> (HEIGHT+1));
    p = (position >> (HEIGHT+1)) & (position >> 2*(HEIGHT+1));
    r |= p & (position << (HEIGHT+1));
    r |= p & (position >> 3*(HEIGHT+1));

    //diagonal 1
    p = (position << HEIGHT) & (position << 2*HEIGHT);
    r |= p & (position << 3*HEIGHT);
    r |= p & (position >> HEIGHT);
    p = (position >> HEIGHT) & (position >> 2*HEIGHT);
    r |= p & (position << HEIGHT);
    r |= p & (position >> 3*HEIGHT);

    //diagonal 2
    p = (position << (HEIGHT+2)) & (position << 2*(HEIGHT+2));
    r |= p & (position << 3*(HEIGHT + 2));
    r |= p & (position >> (HEIGHT + 2));
    p = (position >> (HEIGHT + 2)) & (position >> 2 * (HEIGHT + 2));
    r |= p & (position << (HEIGHT + 2));
    r |= p & (position >> 3*(HEIGHT+2));

    return r & (BOARD_MASK ^ mask);
  }

  // Static bitmaps

  const static uint64_t BOTTOM_MASK = bottom(WIDTH, HEIGHT);
  const static uint64_t BOARD_MASK = BOTTOM_MASK * ((1LL << HEIGHT)-1);

  static constexpr uint64_t topMaskCol(int col) {
    return UINT64_C(1) << ((HEIGHT - 1) + col*(HEIGHT+1));
  }

  static constexpr uint64_t bottomMaskCol(int col) {
    return UINT64_C(1) << col*(HEIGHT+1);
  }
};

#endif
