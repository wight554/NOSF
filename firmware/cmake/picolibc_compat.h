/* picolibc_compat.h
 *
 * Force-included before all translation units when building with clang + picolibc
 * (e.g. ATfE 22.x).  Fills two gaps between picolibc/clang and what the Pico SDK
 * expects from a GCC/newlib environment:
 *
 *  1. __printflike — newlib's sys/cdefs.h defines it; picolibc's does not.
 *     pico/platform/compiler.h includes sys/cdefs.h for GNU-compatible compilers
 *     and then uses __printflike unconditionally.
 *
 *  2. __wfe / __sev / __wfi — GCC provides these ARM wait/event intrinsics as
 *     implicit builtins; clang requires an explicit declaration via <arm_acle.h>.
 */
#pragma once

#ifndef __ASSEMBLER__

/* 1. __printflike
 * newlib's sys/cdefs.h defines this; picolibc's does not.
 * The Pico SDK's pico/platform/compiler.h includes sys/cdefs.h for
 * GNU-compatible compilers and then uses __printflike unconditionally. */
#ifndef __printflike
#define __printflike(a, b) __attribute__((__format__(__printf__, a, b)))
#endif

/* 2. ARM wait/event intrinsics (__wfe, __sev, __wfi)
 * Pico SDK's hardware/sync.h guards these with #if !__has_builtin(...).
 * Clang reports them as builtins (so the SDK skips its own definitions),
 * but C99+ still requires an explicit declaration before calling them.
 * We add forward declarations here; the compiler uses its builtin
 * implementations.  Do NOT include <arm_acle.h> — it redefines __nop,
 * __dmb, __dsb, __isb in a way that conflicts with hardware/sync.h. */
#if defined(__clang__) && defined(__ARM_ARCH)
#if __has_builtin(__wfe)
void __wfe(void);
#endif
#if __has_builtin(__sev)
void __sev(void);
#endif
#if __has_builtin(__wfi)
void __wfi(void);
#endif
#endif /* __clang__ && __ARM_ARCH */

#endif /* !__ASSEMBLER__ */
