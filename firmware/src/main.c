#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>

#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"
#include "hardware/irq.h"
#include "hardware/pwm.h"
#include "hardware/clocks.h"

#include "u8g2.h"
#include "u8x8.h"

// ===================== PINOUT =====================
// Lane sensors / buffer
#define PIN_L1_IN     24
#define PIN_L1_OUT    25
#define PIN_L2_IN     22
#define PIN_L2_OUT    12
#define PIN_Y_SPLIT   2
#define PIN_BUF_LOW   6
#define PIN_BUF_HIGH  7

// Stepper outputs
#define PIN_M1_EN     8
#define PIN_M1_DIR    9
#define PIN_M1_STEP   10
#define PIN_M2_EN     14
#define PIN_M2_DIR    15
#define PIN_M2_STEP   16

#define M1_DIR_INVERT 0
#define M2_DIR_INVERT 1
#define EN_ACTIVE_LOW 1

// OLED
#define PIN_I2C_SDA     26
#define PIN_I2C_SCL     27
#define OLED_I2C_ADDR   0x3C
#define OLED_I2C_INST   i2c1
#define I2C_BAUDRATE    400000

// Encoder + buttons
#define PIN_ENC_A       28
#define PIN_ENC_B       4
#define PIN_BTN_BACK    3
#define PIN_BTN_CONFIRM 29

// Motion + runout
#define PIN_SFS_MOT     5
#define PIN_RUNOUT_OUT  18

// ===================== Tunables =====================
static int FEED_SPS = 5000;
static int REV_SPS  = 4000;
static int AUTO_SPS = 6000;

// Motion logic
static bool MOTION_FAULT_ENABLED = true;
static int  MOTION_TIMEOUT_MS    = 8000;
static int  MOTION_STARTUP_MS    = 10000;   // grace timer / bowden delay

// Runout cooldown
static int  RUNOUT_COOLDOWN_MS   = 12000;

// Auto behavior
static int  LOW_DELAY_MS         = 400;
static int  SWAP_COOLDOWN_MS     = 500;
static bool REQUIRE_Y_EMPTY_SWAP = true;

// Ramp
static int  RAMP_STEP_SPS        = 200;
static int  RAMP_TICK_MS         = 5;

// OLED robustness
static bool     oled_fault = false;
static uint32_t oled_fault_count = 0;
static uint32_t oled_retry_ms = 0;

// Helpers
static inline int clamp_i(int v, int lo, int hi){
    if(v < lo) return lo;
    if(v > hi) return hi;
    return v;
}

// ===================== Debounced digital input =====================
typedef struct {
    uint pin;
    bool stable;
    bool last_raw;
    absolute_time_t last_edge;
} din_t;

static inline void din_init(din_t *d, uint pin) {
    d->pin = pin;
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_IN);
    gpio_pull_up(pin);

    bool raw = gpio_get(pin);
    d->stable = raw;
    d->last_raw = raw;
    d->last_edge = get_absolute_time();
}

static inline void din_update(din_t *d) {
    absolute_time_t now = get_absolute_time();
    bool raw = gpio_get(d->pin);

    if(raw != d->last_raw) {
        d->last_raw = raw;
        d->last_edge = now;
    }

    if(raw != d->stable) {
        if(absolute_time_diff_us(d->last_edge, now) >= 10000) {
            d->stable = raw;
        }
    }
}

static inline bool on_al(const din_t *d) {
    return d->stable == 0;
}

// ===================== Runout =====================
static uint32_t g_runout_block_until = 0;

static inline void runout_init(void) {
    gpio_init(PIN_RUNOUT_OUT);
    gpio_set_dir(PIN_RUNOUT_OUT, GPIO_OUT);
    gpio_put(PIN_RUNOUT_OUT, 0);
}

static inline void runout_set(bool on) {
    gpio_put(PIN_RUNOUT_OUT, on ? 1 : 0);
}

// ===================== Motion =====================
static volatile uint32_t g_now_ms = 0;
static volatile uint32_t g_last_motion_ms = 0;
static volatile uint32_t g_motion_edges_irq = 0;

static uint32_t g_motion_edges_poll = 0;
static bool g_motion_prev_raw = true;

static void mot_irq(uint gpio, uint32_t events) {
    (void)gpio;
    (void)events;
    g_motion_edges_irq++;
    g_last_motion_ms = g_now_ms;
}

static void motion_init(void) {
    gpio_init(PIN_SFS_MOT);
    gpio_set_dir(PIN_SFS_MOT, GPIO_IN);
    gpio_pull_up(PIN_SFS_MOT);

    gpio_set_irq_enabled_with_callback(
        PIN_SFS_MOT,
        GPIO_IRQ_EDGE_RISE | GPIO_IRQ_EDGE_FALL,
        true,
        &mot_irq
    );

    g_motion_prev_raw = gpio_get(PIN_SFS_MOT);
}

static inline void motion_reset(void) {
    g_motion_edges_irq = 0;
    g_motion_edges_poll = 0;
    g_last_motion_ms = 0;
    g_motion_prev_raw = gpio_get(PIN_SFS_MOT);
}

// ===================== Stepper PWM =====================
typedef struct {
    uint en, dir, step;
    bool dir_invert;
    uint slice;
    uint chan;
} motor_t;

static void motor_init(motor_t *m, uint en, uint dir, uint step, bool dir_invert) {
    m->en = en;
    m->dir = dir;
    m->step = step;
    m->dir_invert = dir_invert;

    gpio_init(m->en);
    gpio_set_dir(m->en, GPIO_OUT);

    gpio_init(m->dir);
    gpio_set_dir(m->dir, GPIO_OUT);

    if(EN_ACTIVE_LOW) gpio_put(m->en, 1);
    else gpio_put(m->en, 0);

    gpio_put(m->dir, 0);

    gpio_set_function(m->step, GPIO_FUNC_PWM);
    m->slice = pwm_gpio_to_slice_num(m->step);
    m->chan  = pwm_gpio_to_channel(m->step);

    pwm_config cfg = pwm_get_default_config();
    pwm_init(m->slice, &cfg, false);
    pwm_set_enabled(m->slice, false);
}

static inline void motor_enable(motor_t *m, bool on) {
    if(EN_ACTIVE_LOW) gpio_put(m->en, on ? 0 : 1);
    else gpio_put(m->en, on ? 1 : 0);
}

static inline void motor_set_dir(motor_t *m, bool forward) {
    bool d = forward ^ m->dir_invert;
    gpio_put(m->dir, d ? 1 : 0);
}

static void motor_set_rate_sps(motor_t *m, int sps) {
    if(sps <= 0) {
        pwm_set_enabled(m->slice, false);
        return;
    }

    uint32_t sys = clock_get_hz(clk_sys);
    float target = (float)sps;

    float div = (float)sys / (target * 65535.0f);
    if(div < 1.0f) div = 1.0f;
    if(div > 255.0f) div = 255.0f;

    uint32_t wrap = (uint32_t)((float)sys / (div * target) - 1.0f);
    if(wrap < 10) wrap = 10;
    if(wrap > 65535) wrap = 65535;

    pwm_set_clkdiv(m->slice, div);
    pwm_set_wrap(m->slice, wrap);
    pwm_set_chan_level(m->slice, m->chan, (uint16_t)(wrap / 2));
    pwm_set_enabled(m->slice, true);
}

static inline void motor_stop(motor_t *m) {
    pwm_set_enabled(m->slice, false);
    motor_enable(m, false);
}

// ===================== OLED =====================
static u8g2_t g_u8g2;
static uint8_t g_i2c_buf[128];
static uint8_t g_i2c_len = 0;

static void oled_bus_recover(void) {
    i2c_deinit(OLED_I2C_INST);

    gpio_set_function(PIN_I2C_SDA, GPIO_FUNC_SIO);
    gpio_set_function(PIN_I2C_SCL, GPIO_FUNC_SIO);

    gpio_set_dir(PIN_I2C_SDA, GPIO_IN);
    gpio_set_dir(PIN_I2C_SCL, GPIO_OUT);
    gpio_put(PIN_I2C_SCL, 1);
    sleep_us(5);

    for(int i = 0; i < 9; i++) {
        gpio_put(PIN_I2C_SCL, 0);
        sleep_us(5);
        gpio_put(PIN_I2C_SCL, 1);
        sleep_us(5);
    }

    gpio_set_function(PIN_I2C_SDA, GPIO_FUNC_I2C);
    gpio_set_function(PIN_I2C_SCL, GPIO_FUNC_I2C);
    gpio_pull_up(PIN_I2C_SDA);
    gpio_pull_up(PIN_I2C_SCL);

    i2c_init(OLED_I2C_INST, I2C_BAUDRATE);
}

static bool oled_i2c_write(uint8_t addr7, const uint8_t *buf, size_t len) {
    int rc = i2c_write_timeout_us(OLED_I2C_INST, addr7, buf, len, false, 3000);
    if(rc < 0) {
        oled_fault = true;
        oled_fault_count++;
        oled_bus_recover();
        return false;
    }
    return true;
}

static uint8_t u8x8_byte_pico_i2c(u8x8_t *u8x8, uint8_t msg, uint8_t arg_int, void *arg_ptr) {
    switch (msg) {
        case U8X8_MSG_BYTE_INIT:
            return 1;

        case U8X8_MSG_BYTE_START_TRANSFER:
            g_i2c_len = 0;
            return 1;

        case U8X8_MSG_BYTE_SEND: {
            uint8_t *data = (uint8_t*)arg_ptr;
            while(arg_int--) {
                if(g_i2c_len >= sizeof(g_i2c_buf)) {
                    uint8_t addr7 = (u8x8_GetI2CAddress(u8x8) >> 1);
                    if(!oled_i2c_write(addr7, g_i2c_buf, g_i2c_len)) {
                        g_i2c_len = 0;
                        return 0;
                    }
                    g_i2c_len = 0;
                }
                g_i2c_buf[g_i2c_len++] = *data++;
            }
            return 1;
        }

        case U8X8_MSG_BYTE_END_TRANSFER: {
            uint8_t addr7 = (u8x8_GetI2CAddress(u8x8) >> 1);
            if(g_i2c_len) {
                if(!oled_i2c_write(addr7, g_i2c_buf, g_i2c_len)) {
                    g_i2c_len = 0;
                    return 0;
                }
            }
            g_i2c_len = 0;
            return 1;
        }

        default:
            return 0;
    }
}

static uint8_t u8x8_gpio_delay_pico(u8x8_t *u8x8, uint8_t msg, uint8_t arg_int, void *arg_ptr) {
    (void)u8x8;
    (void)arg_ptr;

    switch(msg) {
        case U8X8_MSG_DELAY_MILLI:
            sleep_ms(arg_int);
            return 1;
        case U8X8_MSG_DELAY_10MICRO:
            sleep_us(10 * arg_int);
            return 1;
        case U8X8_MSG_DELAY_100NANO:
            sleep_us(1);
            return 1;
        default:
            return 1;
    }
}

static bool oled_init(void) {
    oled_fault = false;
    g_i2c_len = 0;

    i2c_init(OLED_I2C_INST, I2C_BAUDRATE);
    gpio_set_function(PIN_I2C_SDA, GPIO_FUNC_I2C);
    gpio_set_function(PIN_I2C_SCL, GPIO_FUNC_I2C);
    gpio_pull_up(PIN_I2C_SDA);
    gpio_pull_up(PIN_I2C_SCL);

    u8g2_Setup_sh1106_i2c_128x64_noname_f(
        &g_u8g2,
        U8G2_R0,
        u8x8_byte_pico_i2c,
        u8x8_gpio_delay_pico
    );
    u8x8_SetI2CAddress(&g_u8g2.u8x8, (OLED_I2C_ADDR << 1));

    u8g2_InitDisplay(&g_u8g2);
    if(oled_fault) return false;

    u8g2_SetPowerSave(&g_u8g2, 0);
    if(oled_fault) return false;

    u8g2_ClearBuffer(&g_u8g2);
    u8g2_SendBuffer(&g_u8g2);

    return !oled_fault;
}

// ===================== Encoder + buttons =====================
#define ENC_STEP_MS   8
#define ENC_DIR_HYST  4
#define CONFIRM_LONGPRESS_MS 450

static uint8_t enc_prev_ab = 0;
static int8_t enc_accum = 0;
static int8_t enc_last_dir = 0;
static uint32_t enc_last_emit_ms = 0;

static const int8_t enc_table[16] = {
    0, -1,  1,  0,
    1,  0,  0, -1,
   -1,  0,  0,  1,
    0,  1, -1,  0
};

static bool confirm_down = false;
static bool confirm_long = false;
static uint32_t confirm_t0 = 0;

typedef enum {
    EVT_NONE=0, EVT_CW, EVT_CCW,
    EVT_CONFIRM, EVT_CONFIRM_LONG_START, EVT_CONFIRM_LONG_END,
    EVT_BACK_DOWN, EVT_BACK_UP
} evt_t;

static void input_init(void){
    gpio_init(PIN_ENC_A); gpio_set_dir(PIN_ENC_A, GPIO_IN); gpio_pull_up(PIN_ENC_A);
    gpio_init(PIN_ENC_B); gpio_set_dir(PIN_ENC_B, GPIO_IN); gpio_pull_up(PIN_ENC_B);
    gpio_init(PIN_BTN_BACK); gpio_set_dir(PIN_BTN_BACK, GPIO_IN); gpio_pull_up(PIN_BTN_BACK);
    gpio_init(PIN_BTN_CONFIRM); gpio_set_dir(PIN_BTN_CONFIRM, GPIO_IN); gpio_pull_up(PIN_BTN_CONFIRM);

    uint8_t a = gpio_get(PIN_ENC_A) ? 1 : 0;
    uint8_t b = gpio_get(PIN_ENC_B) ? 1 : 0;
    enc_prev_ab = (a << 1) | b;
}

static evt_t input_poll(uint32_t now_ms){
    uint8_t a = gpio_get(PIN_ENC_A) ? 1 : 0;
    uint8_t b = gpio_get(PIN_ENC_B) ? 1 : 0;
    uint8_t ab = (a << 1) | b;

    uint8_t idx = (enc_prev_ab << 2) | ab;
    int8_t delta = enc_table[idx];
    enc_prev_ab = ab;

    if(delta){
        int8_t dir = (delta > 0) ? 1 : -1;
        if(enc_last_dir && dir != enc_last_dir) enc_accum = 0;
        enc_last_dir = dir;
        enc_accum += dir;

        if((now_ms - enc_last_emit_ms) >= ENC_STEP_MS){
            if(enc_accum >= ENC_DIR_HYST){
                enc_accum -= ENC_DIR_HYST;
                enc_last_emit_ms = now_ms;
                return EVT_CW;
            }
            if(enc_accum <= -ENC_DIR_HYST){
                enc_accum += ENC_DIR_HYST;
                enc_last_emit_ms = now_ms;
                return EVT_CCW;
            }
        }
    }

    static bool back_prev = false;
    bool back = (gpio_get(PIN_BTN_BACK) == 0);
    if(back && !back_prev){
        back_prev = true;
        return EVT_BACK_DOWN;
    }
    if(!back && back_prev){
        back_prev = false;
        return EVT_BACK_UP;
    }

    bool down = (gpio_get(PIN_BTN_CONFIRM) == 0);
    if(down && !confirm_down){
        confirm_down = true;
        confirm_long = false;
        confirm_t0 = now_ms;
    } else if(!down && confirm_down){
        confirm_down = false;
        if(confirm_long){
            confirm_long = false;
            return EVT_CONFIRM_LONG_END;
        }
        if((now_ms - confirm_t0) < CONFIRM_LONGPRESS_MS) return EVT_CONFIRM;
    } else if(down && confirm_down && !confirm_long){
        if((now_ms - confirm_t0) >= CONFIRM_LONGPRESS_MS){
            confirm_long = true;
            return EVT_CONFIRM_LONG_START;
        }
    }

    return EVT_NONE;
}

// ===================== UI state =====================
typedef enum {
    SCR_HOME=0,
    SCR_MENU=1,
    SCR_SETTINGS=2,
    SCR_SETTINGS_EDIT=3,
    SCR_MANUAL=4,
    SCR_ERROR=5,
    SCR_DEBUG=6
} screen_t;

static screen_t screen = SCR_HOME;

static bool error_active = false;
static const char *error_msg = "";

static int main_idx = 0;
static int manual_idx = 0;

typedef enum { MAN_FEED=0, MAN_REV=1 } man_action_t;
static int manual_lane = 1;
static man_action_t manual_action = MAN_FEED;
static bool manual_running = false;
static int manual_sps = 0;

// settings hierarchy
typedef enum {
    SETCAT_SPEEDS = 0,
    SETCAT_MOTION = 1,
    SETCAT_SAFETY = 2,
    SETCAT_COUNT
} settings_cat_t;

static int settings_cat_idx = 0;
static int settings_item_idx = 0;
static bool settings_value_edit = false;

static const char* main_label(int i){
    switch(i){
        case 0: return "Settings";
        case 1: return "Manual";
        case 2: return "Exit";
        default: return "?";
    }
}

static const char* settings_cat_label(int i){
    switch(i){
        case 0: return "Speeds";
        case 1: return "Motion";
        case 2: return "Safety";
        default: return "?";
    }
}

static int settings_item_count(int cat){
    switch(cat){
        case SETCAT_SPEEDS: return 3;
        case SETCAT_MOTION: return 3;
        case SETCAT_SAFETY: return 1;
        default: return 0;
    }
}

static const char* settings_item_label(int cat, int i){
    switch(cat){
        case SETCAT_SPEEDS:
            switch(i){
                case 0: return "Feed sps";
                case 1: return "Rev  sps";
                case 2: return "Auto sps";
            }
            break;
        case SETCAT_MOTION:
            switch(i){
                case 0: return "Motion ms";
                case 1: return "Startup ms";
                case 2: return "Motion fault";
            }
            break;
        case SETCAT_SAFETY:
            switch(i){
                case 0: return "Runout cool";
            }
            break;
    }
    return "?";
}

static const char* current_settings_item_label(int i){
    return settings_item_label(settings_cat_idx, i);
}

static void settings_item_value_str(int cat, int i, char *out, size_t out_sz){
    switch(cat){
        case SETCAT_SPEEDS:
            if(i == 0) snprintf(out, out_sz, "%d", FEED_SPS);
            else if(i == 1) snprintf(out, out_sz, "%d", REV_SPS);
            else if(i == 2) snprintf(out, out_sz, "%d", AUTO_SPS);
            else snprintf(out, out_sz, "");
            break;

        case SETCAT_MOTION:
            if(i == 0) snprintf(out, out_sz, "%d", MOTION_TIMEOUT_MS);
            else if(i == 1) snprintf(out, out_sz, "%d", MOTION_STARTUP_MS);
            else if(i == 2) snprintf(out, out_sz, "%s", MOTION_FAULT_ENABLED ? "ON" : "OFF");
            else snprintf(out, out_sz, "");
            break;

        case SETCAT_SAFETY:
            if(i == 0) snprintf(out, out_sz, "%d", RUNOUT_COOLDOWN_MS);
            else snprintf(out, out_sz, "");
            break;

        default:
            snprintf(out, out_sz, "");
            break;
    }
}

static bool settings_item_is_bool(int cat, int i){
    return (cat == SETCAT_MOTION && i == 2);
}

static void settings_adjust(int cat, int i, int dir){
    switch(cat){
        case SETCAT_SPEEDS:
            if(i == 0) FEED_SPS = clamp_i(FEED_SPS + dir * 200, 200, 30000);
            if(i == 1) REV_SPS  = clamp_i(REV_SPS  + dir * 200, 200, 30000);
            if(i == 2) AUTO_SPS = clamp_i(AUTO_SPS + dir * 200, 200, 30000);
            break;

        case SETCAT_MOTION:
            if(i == 0) MOTION_TIMEOUT_MS = clamp_i(MOTION_TIMEOUT_MS + dir * 100, 100, 5000);
            if(i == 1) MOTION_STARTUP_MS = clamp_i(MOTION_STARTUP_MS + dir * 500, 0, 30000);
            if(i == 2) MOTION_FAULT_ENABLED = !MOTION_FAULT_ENABLED;
            break;

        case SETCAT_SAFETY:
            if(i == 0) RUNOUT_COOLDOWN_MS = clamp_i(RUNOUT_COOLDOWN_MS + dir * 1000, 1000, 60000);
            break;
    }
}

static const char* manual_label(int i){
    switch(i){
        case 0: return "Lane";
        case 1: return "Action";
        case 2: return "Run/Stop";
        default: return "?";
    }
}

static const char* action_str(man_action_t a){
    return (a == MAN_FEED) ? "FEED" : "REV";
}

// ===================== NightOwl lane core =====================
typedef enum {
    TASK_IDLE=0,
    TASK_AUTOLOAD=1,
    TASK_FEED=2,
    TASK_MANUAL=3
} task_t;

typedef struct {
    din_t in_sw;
    din_t out_sw;
    motor_t m;
    task_t task;
    uint32_t autoload_deadline_ms;
} lane_t;

static inline bool lane_in_present(lane_t *L){ return on_al(&L->in_sw); }
static inline bool lane_out_present(lane_t *L){ return on_al(&L->out_sw); }

static void lane_setup(lane_t *L, uint pin_in, uint pin_out, motor_t m){
    din_init(&L->in_sw, pin_in);
    din_init(&L->out_sw, pin_out);
    L->m = m;
    L->task = TASK_IDLE;
    L->autoload_deadline_ms = 0;
}

static void lane_stop(lane_t *L){
    L->task = TASK_IDLE;
    motor_stop(&L->m);
}

static void lane_start(lane_t *L, task_t t, int sps, bool forward, uint32_t now_ms, int autoload_timeout_ms){
    L->task = t;
    if(t == TASK_AUTOLOAD){
        L->autoload_deadline_ms = now_ms + (uint32_t)autoload_timeout_ms;
    }

    motor_enable(&L->m, true);
    motor_set_dir(&L->m, forward);
    motor_set_rate_sps(&L->m, sps);
}

static void lane_tick(lane_t *L, uint32_t now_ms){
    if(L->task == TASK_AUTOLOAD){
        if(lane_out_present(L) || (int32_t)(now_ms - L->autoload_deadline_ms) >= 0){
            lane_stop(L);
        }
    }
}

static inline lane_t* lane_ptr(int lane, lane_t *L1, lane_t *L2){
    return (lane == 1) ? L1 : L2;
}

static inline int other_lane(int lane){
    return (lane == 1) ? 2 : 1;
}

// auto feed / swap state
static bool feeding_latched = false;
static uint32_t low_since_ms = 0;
static bool swap_armed = false;
static uint32_t swap_block_until_ms = 0;

// Y debounce / hysteresis
static bool y_state = false;
static uint32_t y_change_ms = 0;

// ramp
static int ramp_target = 0;
static int ramp_current = 0;
static uint32_t ramp_last_ms = 0;

// active lane
static int active_lane = 1;

// motion timing
static uint32_t motion_started_ms = 0;

static void stop_all(lane_t *L1, lane_t *L2){
    lane_stop(L1);
    lane_stop(L2);
    feeding_latched = false;
    swap_armed = false;
    low_since_ms = 0;
    ramp_target = 0;
    ramp_current = 0;
    motion_started_ms = 0;
}

// ===================== UI draw helpers =====================
static void draw_dot(int x, int y, bool on){
    if(on) u8g2_DrawDisc(&g_u8g2, x, y, 2, U8G2_DRAW_ALL);
    else   u8g2_DrawCircle(&g_u8g2, x, y, 2, U8G2_DRAW_ALL);
}

static const char* home_state_str(void){
    if(error_active) return "ERROR";
    if(manual_running) return "MANUAL";
    if(feeding_latched) return "AUTO FEED";
    return "IDLE";
}

static void draw_error(void){
    u8g2_ClearBuffer(&g_u8g2);
    u8g2_SetFont(&g_u8g2, u8g2_font_6x10_tf);
    u8g2_DrawStr(&g_u8g2, 0, 10, "ERROR");
    u8g2_DrawHLine(&g_u8g2, 0, 12, 128);
    u8g2_DrawStr(&g_u8g2, 0, 30, error_msg);
    u8g2_DrawStr(&g_u8g2, 0, 50, "RUNOUT -> pause");
    u8g2_DrawStr(&g_u8g2, 0, 62, "BACK clears");
    u8g2_SendBuffer(&g_u8g2);
}

static void draw_list(const char *title, int count, int sel, const char* (*label)(int), const char *right){
    u8g2_ClearBuffer(&g_u8g2);
    u8g2_SetFont(&g_u8g2, u8g2_font_6x10_tf);
    u8g2_DrawStr(&g_u8g2, 0, 10, title);
    u8g2_DrawHLine(&g_u8g2, 0, 12, 128);
    if(right) u8g2_DrawStr(&g_u8g2, 82, 10, right);

    for(int i=0;i<count;i++){
        int y = 24 + i * 10;
        if(i == sel){
            u8g2_DrawBox(&g_u8g2, 0, y - 9, 128, 10);
            u8g2_SetDrawColor(&g_u8g2, 0);
            u8g2_DrawStr(&g_u8g2, 2, y, label(i));
            u8g2_SetDrawColor(&g_u8g2, 1);
        } else {
            u8g2_DrawStr(&g_u8g2, 2, y, label(i));
        }
    }
    u8g2_SendBuffer(&g_u8g2);
}

static void draw_settings_items(void){
    char right[24];
    if(settings_value_edit) snprintf(right, sizeof(right), "EDIT");
    else settings_item_value_str(settings_cat_idx, settings_item_idx, right, sizeof(right));

    draw_list(settings_cat_label(settings_cat_idx),
              settings_item_count(settings_cat_idx),
              settings_item_idx,
              current_settings_item_label,
              right);
}

static void draw_manual(void){
    u8g2_ClearBuffer(&g_u8g2);
    u8g2_SetFont(&g_u8g2, u8g2_font_6x10_tf);
    u8g2_DrawStr(&g_u8g2, 0, 10, "Manual");
    u8g2_DrawHLine(&g_u8g2, 0, 12, 128);

    for(int i=0;i<3;i++){
        int y = 26 + i * 12;
        if(i == manual_idx){
            u8g2_DrawBox(&g_u8g2, 0, y - 9, 128, 12);
            u8g2_SetDrawColor(&g_u8g2, 0);
            u8g2_DrawStr(&g_u8g2, 2, y, manual_label(i));
            u8g2_SetDrawColor(&g_u8g2, 1);
        } else {
            u8g2_DrawStr(&g_u8g2, 2, y, manual_label(i));
        }
    }

    char r0[16], r1[16], r2[16];
    snprintf(r0, sizeof(r0), "L%d", manual_lane);
    snprintf(r1, sizeof(r1), "%s", action_str(manual_action));
    snprintf(r2, sizeof(r2), "%s", manual_running ? "RUN" : "STOP");

    u8g2_DrawStr(&g_u8g2, 92, 26, r0);
    u8g2_DrawStr(&g_u8g2, 92, 38, r1);
    u8g2_DrawStr(&g_u8g2, 92, 50, r2);

    if(manual_running){
        char sp[24];
        snprintf(sp, sizeof(sp), "SPS:%d", manual_sps);
        u8g2_DrawStr(&g_u8g2, 0, 62, sp);
        u8g2_DrawStr(&g_u8g2, 72, 62, "BACK=STOP");
    } else {
        u8g2_DrawStr(&g_u8g2, 0, 62, "CONF toggles / BACK exit");
    }

    u8g2_SendBuffer(&g_u8g2);
}

static void draw_home(bool buffer_low,
                      bool buffer_high,
                      bool y_present,
                      bool motion_ok,
                      lane_t *L1,
                      lane_t *L2)
{
    u8g2_ClearBuffer(&g_u8g2);

    u8g2_SetFont(&g_u8g2, u8g2_font_6x10_tf);
    u8g2_DrawStr(&g_u8g2, 0, 10, "NightOwl");

    char hdr[12];
    snprintf(hdr, sizeof(hdr), "L%d", active_lane);
    u8g2_DrawStr(&g_u8g2, 104, 10, hdr);
    u8g2_DrawHLine(&g_u8g2, 0, 12, 128);

    u8g2_SetFont(&g_u8g2, u8g2_font_7x13B_tf);
    u8g2_DrawStr(&g_u8g2, 0, 28, home_state_str());

    u8g2_SetFont(&g_u8g2, u8g2_font_6x10_tf);
    char sp[20];
    snprintf(sp, sizeof(sp), "%d sps", FEED_SPS);
    u8g2_DrawStr(&g_u8g2, 0, 40, sp);

    u8g2_DrawStr(&g_u8g2, 0, 52, "L1");
    draw_dot(18, 49, lane_in_present(L1));
    draw_dot(26, 49, lane_out_present(L1));

    if(active_lane == 1) u8g2_DrawStr(&g_u8g2, 34, 52, "<");

    u8g2_DrawStr(&g_u8g2, 52, 52, "L2");
    draw_dot(70, 49, lane_in_present(L2));
    draw_dot(78, 49, lane_out_present(L2));

    if(active_lane == 2) u8g2_DrawStr(&g_u8g2, 86, 52, "<");

    u8g2_DrawStr(&g_u8g2, 0, 63, "BUF");
    draw_dot(24, 60, buffer_low);
    draw_dot(32, 60, buffer_high);

    u8g2_DrawStr(&g_u8g2, 52, 63, "Y");
    draw_dot(64, 60, y_present);

    if(motion_ok) u8g2_DrawStr(&g_u8g2, 84, 63, "MOT OK");
    else          u8g2_DrawStr(&g_u8g2, 84, 63, "MOT NO");

    u8g2_SendBuffer(&g_u8g2);
}

static void draw_debug(bool buffer_low,
                       bool buffer_high,
                       bool y_present,
                       bool motion_ok,
                       bool raw_motion,
                       bool l1_in,
                       bool l2_in)
{
    u8g2_ClearBuffer(&g_u8g2);
    u8g2_SetFont(&g_u8g2, u8g2_font_6x10_tf);

    u8g2_DrawStr(&g_u8g2, 0, 10, "Debug");
    u8g2_DrawHLine(&g_u8g2, 0, 12, 128);

    char s[32];

    snprintf(s, sizeof(s), "SWP:%d LAT:%d ACT:L%d",
             swap_armed ? 1 : 0,
             feeding_latched ? 1 : 0,
             active_lane);
    u8g2_DrawStr(&g_u8g2, 0, 24, s);

    snprintf(s, sizeof(s), "L1:%d L2:%d Y:%d B:%d/%d",
             l1_in ? 1 : 0,
             l2_in ? 1 : 0,
             y_present ? 1 : 0,
             buffer_low ? 1 : 0,
             buffer_high ? 1 : 0);
    u8g2_DrawStr(&g_u8g2, 0, 36, s);

    snprintf(s, sizeof(s), "M:%s R:%d I:%lu",
             motion_ok ? "OK" : "NO",
             raw_motion ? 1 : 0,
             (unsigned long)g_motion_edges_irq);
    u8g2_DrawStr(&g_u8g2, 0, 48, s);

    snprintf(s, sizeof(s), "P:%lu OF:%lu",
             (unsigned long)g_motion_edges_poll,
             (unsigned long)oled_fault_count);
    u8g2_DrawStr(&g_u8g2, 0, 60, s);

    u8g2_SendBuffer(&g_u8g2);
}

// ===================== MAIN =====================
int main(void){
    stdio_init_all();
    sleep_ms(200);

    input_init();
    oled_init();
    runout_init();
    motion_init();

    din_t y_split, buf_low, buf_high;
    din_init(&y_split, PIN_Y_SPLIT);
    din_init(&buf_low, PIN_BUF_LOW);
    din_init(&buf_high, PIN_BUF_HIGH);

    motor_t m1, m2;
    motor_init(&m1, PIN_M1_EN, PIN_M1_DIR, PIN_M1_STEP, M1_DIR_INVERT);
    motor_init(&m2, PIN_M2_EN, PIN_M2_DIR, PIN_M2_STEP, M2_DIR_INVERT);

    lane_t L1, L2;
    lane_setup(&L1, PIN_L1_IN, PIN_L1_OUT, m1);
    lane_setup(&L2, PIN_L2_IN, PIN_L2_OUT, m2);

    absolute_time_t last_ui = get_absolute_time();
    absolute_time_t last_poll = get_absolute_time();

    bool motion_was_moving = false;

    while(true){
        absolute_time_t now = get_absolute_time();
        uint32_t now_ms = to_ms_since_boot(now);
        g_now_ms = now_ms;

        if(oled_fault && (int32_t)(now_ms - oled_retry_ms) >= 0){
            oled_retry_ms = now_ms + 2000;
            oled_init();
        }

        din_update(&y_split);
        din_update(&buf_low);
        din_update(&buf_high);
        din_update(&L1.in_sw); din_update(&L1.out_sw);
        din_update(&L2.in_sw); din_update(&L2.out_sw);

        bool y_raw = on_al(&y_split);
        if(y_raw != y_state){
            if((now_ms - y_change_ms) > 30){
                y_state = y_raw;
                y_change_ms = now_ms;
            }
        }
        bool y_present = y_state;
        bool y_empty   = !y_state;

        bool buffer_low  = on_al(&buf_low);
        bool buffer_high = on_al(&buf_high);

        bool l1_in = lane_in_present(&L1);
        bool l2_in = lane_in_present(&L2);

        if(absolute_time_diff_us(last_poll, now) > 1000){
            last_poll = now;
            bool raw = gpio_get(PIN_SFS_MOT);
            if(raw != g_motion_prev_raw){
                g_motion_prev_raw = raw;
                g_motion_edges_poll++;
            }
        }

        evt_t ev = input_poll(now_ms);

        if(error_active){
            if(ev == EVT_BACK_DOWN){
                error_active = false;
                error_msg = "";
                runout_set(false);
                motion_reset();
                stop_all(&L1, &L2);
                screen = SCR_HOME;
            }
        } else {

            if(screen == SCR_MANUAL && manual_running){
                lane_t *LM = lane_ptr(manual_lane, &L1, &L2);

                if(ev == EVT_CW){
                    manual_sps = clamp_i(manual_sps + 200, 200, 30000);
                    motor_set_rate_sps(&LM->m, manual_sps);
                } else if(ev == EVT_CCW){
                    manual_sps = clamp_i(manual_sps - 200, 200, 30000);
                    motor_set_rate_sps(&LM->m, manual_sps);
                } else if(ev == EVT_BACK_DOWN){
                    manual_running = false;
                    lane_stop(LM);
                }
            } else {

                if(screen == SCR_HOME){
                    if(ev == EVT_CONFIRM) screen = SCR_MENU;
                    if(ev == EVT_BACK_DOWN) screen = SCR_DEBUG;

                    if(ev == EVT_CW)  FEED_SPS = clamp_i(FEED_SPS + 200, 200, 30000);
                    if(ev == EVT_CCW) FEED_SPS = clamp_i(FEED_SPS - 200, 200, 30000);
                }
                else if(screen == SCR_DEBUG){
                    if(ev == EVT_BACK_DOWN || ev == EVT_CONFIRM){
                        screen = SCR_HOME;
                    }
                }
                else if(screen == SCR_MENU){
                    if(ev == EVT_CW){ main_idx++; if(main_idx > 2) main_idx = 2; }
                    if(ev == EVT_CCW){ main_idx--; if(main_idx < 0) main_idx = 0; }
                    if(ev == EVT_BACK_DOWN) screen = SCR_HOME;
                    if(ev == EVT_CONFIRM){
                        if(main_idx == 0){
                            screen = SCR_SETTINGS;
                            settings_cat_idx = 0;
                        }
                        else if(main_idx == 1){
                            screen = SCR_MANUAL;
                            manual_idx = 0;
                        }
                        else screen = SCR_HOME;
                    }
                }
                else if(screen == SCR_SETTINGS){
                    if(ev == EVT_CW){
                        settings_cat_idx++;
                        if(settings_cat_idx >= SETCAT_COUNT) settings_cat_idx = SETCAT_COUNT - 1;
                    }
                    if(ev == EVT_CCW){
                        settings_cat_idx--;
                        if(settings_cat_idx < 0) settings_cat_idx = 0;
                    }
                    if(ev == EVT_BACK_DOWN) screen = SCR_MENU;
                    if(ev == EVT_CONFIRM){
                        settings_item_idx = 0;
                        settings_value_edit = false;
                        screen = SCR_SETTINGS_EDIT;
                    }
                }
                else if(screen == SCR_SETTINGS_EDIT){
                    int item_count = settings_item_count(settings_cat_idx);

                    if(!settings_value_edit){
                        if(ev == EVT_CW){
                            settings_item_idx++;
                            if(settings_item_idx >= item_count) settings_item_idx = item_count - 1;
                        }
                        if(ev == EVT_CCW){
                            settings_item_idx--;
                            if(settings_item_idx < 0) settings_item_idx = 0;
                        }
                        if(ev == EVT_BACK_DOWN){
                            screen = SCR_SETTINGS;
                        }
                        if(ev == EVT_CONFIRM){
                            if(settings_item_is_bool(settings_cat_idx, settings_item_idx)){
                                settings_adjust(settings_cat_idx, settings_item_idx, 1);
                            } else {
                                settings_value_edit = true;
                            }
                        }
                    } else {
                        if(ev == EVT_CW){
                            settings_adjust(settings_cat_idx, settings_item_idx, +1);
                        }
                        if(ev == EVT_CCW){
                            settings_adjust(settings_cat_idx, settings_item_idx, -1);
                        }
                        if(ev == EVT_CONFIRM || ev == EVT_BACK_DOWN){
                            settings_value_edit = false;
                        }
                    }
                }
                else if(screen == SCR_MANUAL){
                    if(ev == EVT_CW){ manual_idx++; if(manual_idx > 2) manual_idx = 2; }
                    if(ev == EVT_CCW){ manual_idx--; if(manual_idx < 0) manual_idx = 0; }
                    if(ev == EVT_BACK_DOWN) screen = SCR_MENU;

                    if(ev == EVT_CONFIRM){
                        if(manual_idx == 0){
                            manual_lane = (manual_lane == 1) ? 2 : 1;
                        } else if(manual_idx == 1){
                            manual_action = (manual_action == MAN_FEED) ? MAN_REV : MAN_FEED;
                        } else if(manual_idx == 2){
                            lane_t *LM = lane_ptr(manual_lane, &L1, &L2);
                            if(!manual_running){
                                stop_all(&L1, &L2);

                                manual_running = true;
                                manual_sps = (manual_action == MAN_FEED) ? FEED_SPS : REV_SPS;

                                lane_start(LM, TASK_MANUAL, manual_sps, (manual_action == MAN_FEED), now_ms, 0);

                                motion_started_ms = now_ms;
                                motion_reset();
                            } else {
                                manual_running = false;
                                lane_stop(LM);
                            }
                        }
                    }
                }
            }

            if(!manual_running){

                if(lane_in_present(&L1) && !lane_out_present(&L1) && L1.task == TASK_IDLE){
                    lane_start(&L1, TASK_AUTOLOAD, AUTO_SPS, true, now_ms, 6000);
                    motion_started_ms = now_ms;
                    motion_reset();
                }
                if(lane_in_present(&L2) && !lane_out_present(&L2) && L2.task == TASK_IDLE){
                    lane_start(&L2, TASK_AUTOLOAD, AUTO_SPS, true, now_ms, 6000);
                    motion_started_ms = now_ms;
                    motion_reset();
                }

                if(buffer_low){
                    if(low_since_ms == 0) low_since_ms = now_ms;
                } else {
                    low_since_ms = 0;
                }

                if(buffer_high){
                    feeding_latched = false;
                    low_since_ms = 0;
                }

                if(!feeding_latched){
                    if(!buffer_high &&
                       low_since_ms != 0 &&
                       (int32_t)(now_ms - low_since_ms) >= LOW_DELAY_MS){
                        feeding_latched = true;
                        swap_armed = false;
                        motion_started_ms = now_ms;
                        motion_reset();
                    }
                }

                lane_t *A = lane_ptr(active_lane, &L1, &L2);
                lane_t *B = lane_ptr(other_lane(active_lane), &L1, &L2);

                bool a_in = lane_in_present(A);
                bool b_in = lane_in_present(B);

                if(feeding_latched && !a_in){
                    swap_armed = true;
                }

                bool cooldown_ok = (int32_t)(now_ms - swap_block_until_ms) >= 0;
                bool y_ok = (!REQUIRE_Y_EMPTY_SWAP) || y_empty;

                if(swap_armed && cooldown_ok && y_ok && b_in){
                    if(A->task == TASK_FEED) lane_stop(A);
                    if(B->task == TASK_FEED) lane_stop(B);

                    active_lane = other_lane(active_lane);
                    swap_armed = false;
                    swap_block_until_ms = now_ms + (uint32_t)SWAP_COOLDOWN_MS;

                    motion_started_ms = now_ms;
                    motion_reset();
                }

                A = lane_ptr(active_lane, &L1, &L2);

                if(feeding_latched){
                    if(A->task != TASK_FEED){
                        lane_start(A, TASK_FEED, 1, true, now_ms, 0);
                        ramp_current = 0;
                        ramp_target = FEED_SPS;
                        motion_started_ms = now_ms;
                        motion_reset();
                    } else {
                        ramp_target = FEED_SPS;
                    }

                    lane_t *O = lane_ptr(other_lane(active_lane), &L1, &L2);
                    if(O->task == TASK_FEED) lane_stop(O);
                } else {
                    if(L1.task == TASK_FEED) lane_stop(&L1);
                    if(L2.task == TASK_FEED) lane_stop(&L2);
                    ramp_target = 0;
                }

                lane_tick(&L1, now_ms);
                lane_tick(&L2, now_ms);
            }
        }

        if((int32_t)(now_ms - ramp_last_ms) >= RAMP_TICK_MS){
            ramp_last_ms = now_ms;

            if(ramp_current < ramp_target) ramp_current += RAMP_STEP_SPS;
            if(ramp_current > ramp_target) ramp_current = ramp_target;

            if(ramp_target == 0 && ramp_current > 0){
                ramp_current -= (RAMP_STEP_SPS * 2);
                if(ramp_current < 0) ramp_current = 0;
            }

            lane_t *A = lane_ptr(active_lane, &L1, &L2);
            if(A->task == TASK_FEED){
                motor_set_rate_sps(&A->m, ramp_current);
                motor_enable(&A->m, ramp_current > 0);
                motor_set_dir(&A->m, true);
            }
        }

        bool feeding_now = (L1.task == TASK_FEED) || (L2.task == TASK_FEED);

        bool moving_now = false;
        if(manual_running) moving_now = true;
        if(L1.task == TASK_AUTOLOAD || L1.task == TASK_FEED) moving_now = true;
        if(L2.task == TASK_AUTOLOAD || L2.task == TASK_FEED) moving_now = true;

        if(moving_now && !motion_was_moving){
            motion_reset();
            motion_started_ms = now_ms;
        }
        motion_was_moving = moving_now;

        bool motion_ok = true;
        if(moving_now){
            uint32_t run_ms = now_ms - motion_started_ms;

            if((int32_t)run_ms <= MOTION_STARTUP_MS){
                motion_ok = true;
            } else {
                uint32_t lm = g_last_motion_ms;
                if(lm == 0) {
                    motion_ok = false;
                } else {
                    uint32_t age = now_ms - lm;
                    if((int32_t)age > MOTION_TIMEOUT_MS) motion_ok = false;
                }
            }
        }

        bool filament_present = l1_in || l2_in;
        bool both_lanes_empty = !l1_in && !l2_in;

        bool no_motion_fault =
            feeding_now &&
            filament_present &&
            !motion_ok;

        bool empty_fault = both_lanes_empty;

        if(!error_active && (empty_fault || (MOTION_FAULT_ENABLED && no_motion_fault))){
            if(now_ms > g_runout_block_until){
                error_active = true;

                if(empty_fault) error_msg = "No filament L1/L2";
                else error_msg = "Filament not moving";

                runout_set(true);
                g_runout_block_until = now_ms + (uint32_t)RUNOUT_COOLDOWN_MS;
                stop_all(&L1, &L2);
                screen = SCR_ERROR;
            }
        }

        if(!oled_fault && absolute_time_diff_us(last_ui, now) > 80000){
            last_ui = now;

            bool raw_motion = gpio_get(PIN_SFS_MOT);

            if(error_active){
                draw_error();
            }
            else if(screen == SCR_HOME){
                draw_home(buffer_low, buffer_high, y_present, motion_ok, &L1, &L2);
            }
            else if(screen == SCR_DEBUG){
                draw_debug(buffer_low, buffer_high, y_present, motion_ok, raw_motion, l1_in, l2_in);
            }
            else if(screen == SCR_MENU){
                draw_list("Menu", 3, main_idx, main_label, NULL);
            }
            else if(screen == SCR_SETTINGS){
                draw_list("Settings", SETCAT_COUNT, settings_cat_idx, settings_cat_label, NULL);
            }
            else if(screen == SCR_SETTINGS_EDIT){
                draw_settings_items();
            }
            else if(screen == SCR_MANUAL){
                draw_manual();
            }

            if(oled_fault){
                oled_retry_ms = now_ms + 2000;
            }
        }

        tight_loop_contents();
    }
}
