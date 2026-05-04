cmake_minimum_required(VERSION 3.13)
include(pico_sdk_import.cmake)

project(nosf_controller C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(GEN_SCRIPT ${CMAKE_CURRENT_LIST_DIR}/../scripts/gen_motor_config.py)
set(USER_CONFIG_INI ${CMAKE_CURRENT_LIST_DIR}/../config.ini)
set(TUNE_H ${CMAKE_CURRENT_LIST_DIR}/include/tune.h)

add_custom_command(
    OUTPUT  ${TUNE_H}
    COMMAND ${Python3_EXECUTABLE} ${GEN_SCRIPT} ${USER_CONFIG_INI} ${TUNE_H}
    DEPENDS ${USER_CONFIG_INI} ${GEN_SCRIPT}
    COMMENT "Generating tune.h from config.ini"
)
add_custom_target(motor_config DEPENDS ${TUNE_H})

add_executable(nosf_controller
  src/main.c
  src/tmc2209.c
  src/neopixel.c
)

target_include_directories(nosf_controller PRIVATE
  ${CMAKE_CURRENT_LIST_DIR}/include
)

pico_generate_pio_header(nosf_controller
  ${CMAKE_CURRENT_LIST_DIR}/third_party/ws2812.pio
)

add_dependencies(nosf_controller motor_config)

target_link_libraries(nosf_controller
  pico_stdlib
  hardware_gpio
  hardware_irq
  hardware_pwm
  hardware_clocks
  hardware_flash
  hardware_sync
  hardware_pio
  hardware_adc
)

pico_enable_stdio_usb(nosf_controller 1)
pico_enable_stdio_uart(nosf_controller 0)

pico_add_extra_outputs(nosf_controller)
