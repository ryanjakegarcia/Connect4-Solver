#ifndef TRANSPOSITION_TABLE_HPP
#define TRANSPOSITION_TABLE_HPP

#include <cstring>
#include <cassert>
#include <type_traits>

constexpr uint64_t med(uint64_t min, uint64_t max){
    return (min + max) / 2;
}

constexpr bool has_factor(uint64_t n, uint64_t min, uint64_t max){
    return min * min > n ? false :
        min + 1 >= max ? n % min == 0 :
        has_factor(n, min, med(min, max)) || has_factor(n, med(min, max), max);
}

constexpr uint64_t next_prime(uint64_t n){
    return has_factor(n, 2, n) ? next_prime(n + 1) : n;
}

/**
 * Transposition Table
 */
template<unsigned int key_size, unsigned int value_size, unsigned int log_size>
class TranspositionTable{
private:
    static_assert(key_size   <= 64, "key_size is too large");
    static_assert(value_size <= 64, "value_size is too large");
    static_assert(log_size   <= 64, "log_size is too large");

    template<int S> using uint_t = 
        typename std::conditional<S <= 8, uint_least8_t,
        typename std::conditional<S <= 16, uint_least16_t,
        typename std::conditional<S <= 32, uint_least32_t, uint_least64_t>::type >::type >::type;

    typedef uint_t<key_size - log_size> key_t;
    typedef uint_t<value_size> value_t;

    static const size_t size = next_prime(1 << log_size);

    key_t *K;
    value_t *V;
    
    size_t index(uint64_t key) const {
        return key % size;
    }

public:
    TranspositionTable(){
        K = new key_t[size];
        V = new value_t[size];
        reset();
    }

    ~TranspositionTable(){
        delete[] K;
        delete[] V;
    }

    /**
     * Empty the table.
     */
    void reset(){
        memset(K, 0, size * sizeof(key_t));
        memset(V, 0, size * sizeof(value_t));
    }

    /**
     * Store a value for a given key
     * @param key: must be less than key_size bits.
     * @param value: must be less than value_size bits. null (0) value is used to encode missing data.
     */
    void put(uint64_t key, value_t value){
        assert(key >> key_size == 0);
        assert(value >> value_size == 0);
        size_t pos = index(key);
        K[pos] = key;
        V[pos] = value;
    }

    /**
     * Get the value of a key
     * @param key: must be less than key_size bits
     * @return value_size bits value associated with the key if present, 0 otherwise
     */
    value_t get(uint64_t key) const {
        assert(key >> key_size == 0);
        size_t pos = index(key);
        if(K[pos] == (key_t)key) return V[pos];
        else return 0;
    }
};

#endif
