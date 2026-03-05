cmake_minimum_required(VERSION 3.13)
include(pico_sdk_import.cmake)

project(nightowl_controller C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

set(U8G2_DIR ${CMAKE_CURRENT_LIST_DIR}/third_party/u8g2/csrc)
file(GLOB U8G2_SRC
  ${U8G2_DIR}/u8g2_*.c
  ${U8G2_DIR}/u8x8_*.c
)

add_executable(nightowl_controller
  src/main.c
  ${U8G2_SRC}
)

target_include_directories(nightowl_controller PRIVATE
  ${U8G2_DIR}
  ${CMAKE_CURRENT_LIST_DIR}/include
)

target_link_libraries(nightowl_controller
  pico_stdlib
  hardware_gpio
  hardware_i2c
  hardware_irq
  hardware_pwm
  hardware_clocks
)

pico_enable_stdio_usb(nightowl_controller 0)
pico_enable_stdio_uart(nightowl_controller 0)

pico_add_extra_outputs(nightowl_controller)
