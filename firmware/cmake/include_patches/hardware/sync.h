/* hardware/sync.h patch — injected via -I${project}/firmware/cmake/include_patches
 *
 * Fixes remove_volatile_cast / remove_volatile_cast_no_barrier for clang 22.x.
 *
 * Root cause: in clang 22.x a compound statement expression ({ ... }) evaluates
 * to void when _Pragma() is the last statement, rather than to the value of the
 * preceding expression.  The Pico SDK's clang branch ends both macros with
 * _Pragma("clang diagnostic pop"), making them return void and breaking every
 * caller that passes the result to a typed parameter.
 *
 * Fix: capture the cast result in a local variable before the pop pragma and
 * return the variable as the final expression so the type is preserved.
 */
#include_next <hardware/sync.h>

#if defined(__clang__)
#undef remove_volatile_cast_no_barrier
#undef remove_volatile_cast

#define remove_volatile_cast_no_barrier(t, x) \
    __extension__({ \
        _Pragma("clang diagnostic push") \
        _Pragma("clang diagnostic ignored \"-Wcast-qual\"") \
        t _rvcast_res = (t)(x); \
        _Pragma("clang diagnostic pop") \
        _rvcast_res; \
    })

#define remove_volatile_cast(t, x) \
    __extension__({ \
        __compiler_memory_barrier(); \
        _Pragma("clang diagnostic push") \
        _Pragma("clang diagnostic ignored \"-Wcast-qual\"") \
        t _rvcast_res = (t)(x); \
        _Pragma("clang diagnostic pop") \
        _rvcast_res; \
    })
#endif /* __clang__ */
