# pico_arm_cortex_m0plus_clang_atfe.cmake
#
# Cortex-M0+ clang toolchain for:
#   ATfE (Arm Toolchain for Embedded) 22.x  — macOS and Linux
#   LLVM Embedded Toolchain for Arm (older) — macOS and Linux
#   Debian/Raspberry Pi OS system clang + libnewlib-arm-none-eabi
#
# Compiler discovery order:
#   1. PICO_TOOLCHAIN_PATH env var (bin/ subdirectory)
#   2. System PATH
#
# For ATfE on macOS: add the ATfE bin dir to PATH, or set:
#   export PICO_TOOLCHAIN_PATH=/path/to/ATfE/bin
#
# For Raspberry Pi:
#   sudo apt install clang llvm llvm-dev libnewlib-arm-none-eabi
#
# Usage:
#   cmake -S firmware -B build_clang -G Ninja \
#     -DPICO_SDK_PATH=... \
#     -DCMAKE_TOOLCHAIN_FILE=cmake/pico_arm_cortex_m0plus_clang_atfe.cmake

set(CMAKE_SYSTEM_PROCESSOR cortex-m0plus)
set(CMAKE_SYSTEM_NAME PICO)

# Persist PICO_TOOLCHAIN_PATH across try_compile invocations (CMake relaunches
# toolchain files in sub-processes that don't inherit cache variables).
if (NOT "${PICO_TOOLCHAIN_PATH}" STREQUAL "")
    set(ENV{PICO_TOOLCHAIN_PATH} "${PICO_TOOLCHAIN_PATH}")
endif ()

# --- Compiler discovery ---
# Try PICO_TOOLCHAIN_PATH/bin first, then fall through to system PATH.
find_program(PICO_COMPILER_CC NAMES clang
    PATHS "$ENV{PICO_TOOLCHAIN_PATH}"
    PATH_SUFFIXES bin
    NO_DEFAULT_PATH)
if (NOT PICO_COMPILER_CC)
    find_program(PICO_COMPILER_CC NAMES clang)
endif ()
if (NOT PICO_COMPILER_CC)
    message(FATAL_ERROR
        "clang not found.\n"
        "  macOS: add ATfE bin to PATH or set PICO_TOOLCHAIN_PATH\n"
        "  Raspberry Pi: sudo apt install clang")
endif ()

find_program(PICO_COMPILER_CXX NAMES clang++
    PATHS "$ENV{PICO_TOOLCHAIN_PATH}"
    PATH_SUFFIXES bin
    NO_DEFAULT_PATH)
if (NOT PICO_COMPILER_CXX)
    find_program(PICO_COMPILER_CXX NAMES clang++)
endif ()

set(PICO_COMPILER_ASM "${PICO_COMPILER_CC}" CACHE INTERNAL "")

find_program(PICO_OBJCOPY NAMES llvm-objcopy
    PATHS "$ENV{PICO_TOOLCHAIN_PATH}"
    PATH_SUFFIXES bin
    NO_DEFAULT_PATH)
if (NOT PICO_OBJCOPY)
    find_program(PICO_OBJCOPY NAMES llvm-objcopy)
endif ()
if (NOT PICO_OBJCOPY)
    message(FATAL_ERROR "llvm-objcopy not found alongside clang.")
endif ()

find_program(PICO_OBJDUMP NAMES llvm-objdump
    PATHS "$ENV{PICO_TOOLCHAIN_PATH}"
    PATH_SUFFIXES bin
    NO_DEFAULT_PATH)
if (NOT PICO_OBJDUMP)
    find_program(PICO_OBJDUMP NAMES llvm-objdump)
endif ()

set(CMAKE_C_COMPILER   "${PICO_COMPILER_CC}"  CACHE FILEPATH "C compiler")
set(CMAKE_CXX_COMPILER "${PICO_COMPILER_CXX}" CACHE FILEPATH "C++ compiler")
set(CMAKE_ASM_COMPILER "${PICO_COMPILER_ASM}" CACHE FILEPATH "ASM compiler")
set(CMAKE_OBJCOPY      "${PICO_OBJCOPY}"       CACHE FILEPATH "")
set(CMAKE_OBJDUMP      "${PICO_OBJDUMP}"       CACHE FILEPATH "")

# --- Sysroot and target triple detection ---
# Derive toolchain root from the found clang binary.
get_filename_component(_clang_dir "${PICO_COMPILER_CC}" DIRECTORY)
get_filename_component(_toolchain_root "${_clang_dir}" DIRECTORY)

# ATfE 22.x layout:  lib/clang-runtimes/
#   multilib.yaml        — selects the right arch sub-directory at link time
#   arm-none-eabi/       — common headers (include/stdio.h, include/picolibc.h)
#     armv6m_soft_nofp_size/lib/  — actual runtime libs chosen by multilib
#
# For ATfE the sysroot must point to lib/clang-runtimes/ (the parent), so the
# linker can find multilib.yaml and resolve the correct lib sub-directory.
# The target triple must be thumbv6m-unknown-none-eabi (exact form in multilib.yaml).
#
# Older LLVM Embedded Toolchain for Arm, and Debian/Raspberry Pi OS newlib,
# use an arch-specific sysroot directory that contains include/ and lib/ directly.
# These use armv6m-none-eabi as the target triple.

set(_pico_target_triple "armv6m-none-eabi")
set(_pico_extra_flags "")

if (EXISTS "${_toolchain_root}/lib/clang-runtimes/multilib.yaml")
    # ATfE-style: sysroot = clang-runtimes parent; multilib.yaml handles lib selection.
    set(PICO_COMPILER_SYSROOT "${_toolchain_root}/lib/clang-runtimes"
        CACHE PATH "Clang ARM sysroot" FORCE)
    # thumbv6m-unknown-none-eabi is the exact triple that matches the armv6m
    # entries in ATfE's multilib.yaml; armv6m-none-eabi does not trigger multilib.
    set(_pico_target_triple "thumbv6m-unknown-none-eabi")
    # -mfpu=none is required by the multilib.yaml flag-matching rules.
    set(_pico_extra_flags "-mfpu=none")
else ()
    # Fallback: probe for an arch-specific sysroot directory (older LLVM embedded
    # toolchain or Debian/Ubuntu/Raspberry Pi OS libnewlib-arm-none-eabi).
    foreach(_candidate IN ITEMS
            "${_toolchain_root}/lib/clang-runtimes/armv6m_soft_nofp_size"
            "${_toolchain_root}/lib/clang-runtimes/armv6m_soft_nofp"
            "${_toolchain_root}/lib/clang-runtimes/armv6m-unknown-none-eabi"
            "/usr/lib/arm-none-eabi"
            "/usr/arm-none-eabi")
        if (EXISTS "${_candidate}/include/stdio.h")
            set(PICO_COMPILER_SYSROOT "${_candidate}" CACHE PATH "Clang ARM sysroot" FORCE)
            break()
        endif ()
    endforeach()

    # Fallback: Check for system clang + newlib (e.g. Raspberry Pi OS / Debian)
    if (NOT PICO_COMPILER_SYSROOT)
        set(_pico_target_triple "armv6m-none-eabi")
        if (EXISTS "/usr/lib/arm-none-eabi")
            set(PICO_COMPILER_SYSROOT "/usr/lib/arm-none-eabi")
        elseif (EXISTS "/usr/arm-none-eabi")
            set(PICO_COMPILER_SYSROOT "/usr/arm-none-eabi")
        endif()
        
    endif()
endif()

if (NOT PICO_COMPILER_SYSROOT)
    message(FATAL_ERROR
        "ARM clang sysroot not found.\n"
        "  Expected near: ${_toolchain_root}/lib/clang-runtimes/\n"
        "  Raspberry Pi: sudo apt install libnewlib-arm-none-eabi")
endif ()

message(STATUS "ARM clang sysroot: ${PICO_COMPILER_SYSROOT}")
message(STATUS "ARM clang target:  ${_pico_target_triple}")

# Detect picolibc — ATfE 22.x ships picolibc; Debian ships newlib (default).
# With ATfE (parent sysroot), picolibc.h lives in arm-none-eabi/include/.
# With older arch-specific sysroots, it lives in include/ directly.
if (NOT PICO_CLIB)
    foreach(_picolibc_candidate IN ITEMS
            "${PICO_COMPILER_SYSROOT}/arm-none-eabi/include/picolibc.h"
            "${PICO_COMPILER_SYSROOT}/include/picolibc.h")
        if (EXISTS "${_picolibc_candidate}")
            message(STATUS "ARM clang: picolibc detected")
            set(PICO_CLIB "picolibc" CACHE INTERNAL "")
            break()
        endif ()
    endforeach()
endif ()



# --- Compiler flags ---
set(_common_flags "--target=${_pico_target_triple} -mfloat-abi=soft -march=armv6m ${_pico_extra_flags} --sysroot=${PICO_COMPILER_SYSROOT} -fno-exceptions -fno-rtti")

# --- Linker-only flags ---
set(_pico_extra_link_flags "")

# On Linux/Pi, Clang often needs help finding the C++ machine-specific headers
# (like bits/c++config.h) and the GCC runtime (libgcc.a).
if (NOT PICO_COMPILER_SYSROOT_IS_ATFE AND PICO_COMPILER_SYSROOT)
    # 1. Fix C++ headers (Compiler flags)
    file(GLOB _cpp_headers "/usr/lib/arm-none-eabi/include/c++/*")
    if (_cpp_headers)
        list(GET _cpp_headers 0 _cpp_base)
        if (EXISTS "${_cpp_base}/arm-none-eabi")
            string(APPEND _common_flags " -isystem ${_cpp_base}/arm-none-eabi")
        endif()
    endif()

    # 2. Find library multilib paths
    execute_process(COMMAND arm-none-eabi-gcc -mcpu=cortex-m0plus -print-libgcc-file-name
                    OUTPUT_VARIABLE _libgcc_file
                    OUTPUT_STRIP_TRAILING_WHITESPACE
                    ERROR_QUIET)
    if (NOT _libgcc_file OR NOT EXISTS "${_libgcc_file}")
        # Fallback to broad filesystem search in common locations
        file(GLOB_RECURSE _libgcc_candidates 
             "/usr/lib/gcc/arm-none-eabi/*/thumb/v6-m/nofp/libgcc.a"
             "/usr/lib/gcc/arm-none-eabi/*/armv6-m/*/libgcc.a"
             "/usr/lib/gcc/arm-none-eabi/*/libgcc.a")
        if (_libgcc_candidates)
            list(GET _libgcc_candidates 0 _libgcc_file)
        endif()
    endif()

    if (_libgcc_file AND EXISTS "${_libgcc_file}")
        get_filename_component(_libgcc_dir "${_libgcc_file}" DIRECTORY)
        message(STATUS "ARM clang: found libgcc at ${_libgcc_dir}")
        set(_pico_extra_link_flags "-L${_libgcc_dir} -lgcc")
        
        # Also try to find the corresponding libc.a multilib directory in the sysroot
        # Usually /usr/lib/arm-none-eabi/lib/thumb/v6-m/nofp/
        set(_multilib_suffix "")
        if (_libgcc_dir MATCHES "thumb/v6-m/nofp")
            set(_multilib_suffix "thumb/v6-m/nofp")
        elseif (_libgcc_dir MATCHES "armv6-m")
            set(_multilib_suffix "armv6-m")
        endif()
        
        if (_multilib_suffix)
            set(_libc_dir "${PICO_COMPILER_SYSROOT}/lib/${_multilib_suffix}")
            if (EXISTS "${_libc_dir}/libc.a")
                message(STATUS "ARM clang: found libc at ${_libc_dir}")
                string(APPEND _pico_extra_link_flags " -L${_libc_dir}")
            endif()
        endif()
    else()
        message(STATUS "ARM clang: libgcc.a not found via arm-none-eabi-gcc or glob.")
    endif()
endif()

# Inject include_patches/ before the SDK include search path so that our
# hardware/sync.h wrapper (which uses #include_next) is found first.  The
# wrapper fixes remove_volatile_cast / remove_volatile_cast_no_barrier for
# clang 22.x where _Pragma at end of ({ }) evaluates to void.
set(_patches_dir "${CMAKE_CURRENT_LIST_DIR}/include_patches")
set(_common_flags "${_common_flags} -I${_patches_dir}")

# picolibc omits __printflike from sys/cdefs.h; the Pico SDK's compiler.h
# includes sys/cdefs.h for GNU-compatible compilers and then uses __printflike
# unconditionally.  Force-include a shim to fill the gap.
if (PICO_CLIB STREQUAL "picolibc")
    set(_compat_header "${CMAKE_CURRENT_LIST_DIR}/picolibc_compat.h")
    set(_common_flags "${_common_flags} -include ${_compat_header}")
endif ()

foreach(LANG IN ITEMS C CXX ASM)
    set(CMAKE_${LANG}_OUTPUT_EXTENSION    .o)
    set(CMAKE_${LANG}_FLAGS_INIT          "${_common_flags}")
    set(CMAKE_${LANG}_FLAGS_MINSIZEREL_INIT "-Oz -DNDEBUG")
    set(CMAKE_${LANG}_FLAGS_DEBUG_INIT    "-Og")
    set(CMAKE_${LANG}_FLAGS_RELEASE_INIT  "-g")
    set(CMAKE_${LANG}_LINK_FLAGS          "-Wl,--build-id=none")
endforeach()

# Clang drives the linker:
#   --target / --sysroot : required so the linker finds the correct libraries.
#   -nostartfiles: picolibc's crt0.o (which would be auto-linked from the
#                 sysroot) references picolibc-specific linker symbols like
#                 __stack and __arm32_tls_tcb_offset that the Pico SDK's
#                 memmap_default.ld does not define.  The Pico SDK provides
#                 its own startup code (pico_crt0/crt0.S), so suppress the
#                 default startup files from the C library.
set(_link_flags "-Qunused-arguments --target=${_pico_target_triple} --sysroot=${PICO_COMPILER_SYSROOT} -nostartfiles -nostdlib++ ${_pico_extra_link_flags}")
foreach(TYPE IN ITEMS EXE SHARED MODULE)
    set(CMAKE_${TYPE}_LINKER_FLAGS_INIT "${_link_flags}")
endforeach()

# try_compile needs -nostdlib to avoid undefined _exit symbols.
get_property(_in_try_compile GLOBAL PROPERTY IN_TRY_COMPILE)
if (_in_try_compile)
    foreach(LANG IN ITEMS C CXX ASM)
        string(APPEND CMAKE_${LANG}_LINK_FLAGS " -nostdlib")
    endforeach()
endif ()

# Clang ASM compile-object form expected by the SDK.
set(CMAKE_ASM_COMPILE_OBJECT
    "<CMAKE_ASM_COMPILER> <DEFINES> <INCLUDES> <FLAGS> -o <OBJECT> -c <SOURCE>")
set(CMAKE_INCLUDE_FLAG_ASM "-I")

# Cross-compilation root — tell CMake's find_* not to search host paths.
set(CMAKE_FIND_ROOT_PATH "${_toolchain_root}")
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)

# Propagate PICO_CLIB into try_compile sub-projects.
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES PICO_CLIB)
