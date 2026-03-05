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
#define PIN_L1_IN     24
#define PIN_L1_OUT    25
#define PIN_L2_IN     22
#define PIN_L2_OUT    12
#define PIN_Y_SPLIT   2
#define PIN_BUF_LOW   6
#define PIN_BUF_HIGH  7

#define PIN_M1_EN    8
#define PIN_M1_DIR   9
#define PIN_M1_STEP  10
#define PIN_M2_EN    14
#define PIN_M2_DIR   15
#define PIN_M2_STEP  16
#define M1_DIR_INVERT 0
#define M2_DIR_INVERT 1

#define EN_ACTIVE_LOW 1

#define PIN_I2C_SDA     26
#define PIN_I2C_SCL     27
#define OLED_I2C_ADDR   0x3C
#define OLED_I2C_INST   i2c1
#define I2C_BAUDRATE    400000

#define PIN_ENC_A       28
#define PIN_ENC_B       4
#define PIN_BTN_BACK    3
#define PIN_BTN_CONFIRM 29

#define PIN_SFS_MOT     5
#define PIN_RUNOUT_OUT  18

// ===================== Tunables =====================
static int FEED_SPS = 5000;
static int REV_SPS  = 4000;
static int AUTO_SPS = 6000;

static int MOTION_TIMEOUT_MS = 800;
// cooldown/startup: zolang na start géén error, zodat je filament kan doorvoeren tot de sensor iets ziet
static int MOTION_STARTUP_MAX_MS = 8000;
static bool MOTION_FAULT_ENABLED = false; // default OFF zoals je wilde

static inline int clamp_i(int v, int lo, int hi){ if(v<lo) return lo; if(v>hi) return hi; return v; }

// ===================== Runout (PC817) =====================
static inline void runout_init(void){
    gpio_init(PIN_RUNOUT_OUT);
    gpio_set_dir(PIN_RUNOUT_OUT, GPIO_OUT);
    gpio_put(PIN_RUNOUT_OUT, 0);
}
static inline void runout_set(bool on){ gpio_put(PIN_RUNOUT_OUT, on ? 1 : 0); }

// ===================== Motion =====================
static volatile uint32_t g_now_ms = 0;
static volatile uint32_t g_last_motion_ms = 0;
static volatile uint32_t g_motion_edges_irq = 0;

static uint32_t g_motion_edges_poll = 0;
static bool g_motion_prev_raw = true;

static void mot_irq(uint gpio, uint32_t events) {
    (void)gpio; (void)events;
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

// ===================== PWM Stepper =====================
typedef struct {
    uint en, dir, step;
    bool dir_invert;
    uint slice;
    uint chan;
} motor_t;

static void motor_init(motor_t *m, uint en, uint dir, uint step, bool dir_invert) {
    m->en=en; m->dir=dir; m->step=step; m->dir_invert=dir_invert;

    gpio_init(m->en);  gpio_set_dir(m->en,  GPIO_OUT);
    gpio_init(m->dir); gpio_set_dir(m->dir, GPIO_OUT);

    if (EN_ACTIVE_LOW) gpio_put(m->en, 1); else gpio_put(m->en, 0);
    gpio_put(m->dir, 0);

    gpio_set_function(m->step, GPIO_FUNC_PWM);
    m->slice = pwm_gpio_to_slice_num(m->step);
    m->chan  = pwm_gpio_to_channel(m->step);

    pwm_config cfg = pwm_get_default_config();
    pwm_init(m->slice, &cfg, false);
    pwm_set_enabled(m->slice, false);
}

static inline void motor_enable(motor_t *m, bool on) {
    if (EN_ACTIVE_LOW) gpio_put(m->en, on ? 0 : 1);
    else gpio_put(m->en, on ? 1 : 0);
}

static inline void motor_set_dir(motor_t *m, bool forward) {
    bool d = forward ^ m->dir_invert;
    gpio_put(m->dir, d ? 1 : 0);
}

static void motor_set_rate_sps(motor_t *m, int sps) {
    if (sps <= 0) { pwm_set_enabled(m->slice, false); return; }

    uint32_t sys = clock_get_hz(clk_sys);
    float target = (float)sps;

    float div = (float)sys / (target * 65535.0f);
    if (div < 1.0f) div = 1.0f;
    if (div > 255.0f) div = 255.0f;

    uint32_t wrap = (uint32_t)((float)sys / (div * target) - 1.0f);
    if (wrap < 10) wrap = 10;
    if (wrap > 65535) wrap = 65535;

    pwm_set_clkdiv(m->slice, div);
    pwm_set_wrap(m->slice, wrap);
    pwm_set_chan_level(m->slice, m->chan, (uint16_t)(wrap / 2));
    pwm_set_enabled(m->slice, true);
}

static inline void motor_stop(motor_t *m){
    pwm_set_enabled(m->slice, false);
    motor_enable(m, false);
}

// ===================== OLED (u8g2) =====================
static u8g2_t g_u8g2;
static uint8_t g_i2c_buf[128];
static uint8_t g_i2c_len = 0;

static uint8_t u8x8_byte_pico_i2c(u8x8_t *u8x8, uint8_t msg, uint8_t arg_int, void *arg_ptr) {
    switch (msg) {
        case U8X8_MSG_BYTE_INIT: return 1;
        case U8X8_MSG_BYTE_START_TRANSFER: g_i2c_len = 0; return 1;
        case U8X8_MSG_BYTE_SEND: {
            uint8_t *data = (uint8_t*)arg_ptr;
            while (arg_int--) {
                if (g_i2c_len >= sizeof(g_i2c_buf)) {
                    uint8_t addr7 = (u8x8_GetI2CAddress(u8x8) >> 1);
                    i2c_write_blocking(OLED_I2C_INST, addr7, g_i2c_buf, g_i2c_len, false);
                    g_i2c_len = 0;
                }
                g_i2c_buf[g_i2c_len++] = *data++;
            }
            return 1;
        }
        case U8X8_MSG_BYTE_END_TRANSFER: {
            uint8_t addr7 = (u8x8_GetI2CAddress(u8x8) >> 1);
            if (g_i2c_len) i2c_write_blocking(OLED_I2C_INST, addr7, g_i2c_buf, g_i2c_len, false);
            g_i2c_len = 0;
            return 1;
        }
        default: return 0;
    }
}
static uint8_t u8x8_gpio_delay_pico(u8x8_t *u8x8, uint8_t msg, uint8_t arg_int, void *arg_ptr) {
    (void)u8x8; (void)arg_ptr;
    switch (msg) {
        case U8X8_MSG_DELAY_MILLI: sleep_ms(arg_int); return 1;
        case U8X8_MSG_DELAY_10MICRO: sleep_us(10 * arg_int); return 1;
        case U8X8_MSG_DELAY_100NANO: sleep_us(1); return 1;
        default: return 1;
    }
}
static void oled_init(void) {
    i2c_init(OLED_I2C_INST, I2C_BAUDRATE);
    gpio_set_function(PIN_I2C_SDA, GPIO_FUNC_I2C);
    gpio_set_function(PIN_I2C_SCL, GPIO_FUNC_I2C);
    gpio_pull_up(PIN_I2C_SDA);
    gpio_pull_up(PIN_I2C_SCL);

    u8g2_Setup_sh1106_i2c_128x64_noname_f(&g_u8g2, U8G2_R0, u8x8_byte_pico_i2c, u8x8_gpio_delay_pico);
    u8x8_SetI2CAddress(&g_u8g2.u8x8, (OLED_I2C_ADDR << 1));
    u8g2_InitDisplay(&g_u8g2);
    u8g2_SetPowerSave(&g_u8g2, 0);
    u8g2_ClearBuffer(&g_u8g2);
    u8g2_SendBuffer(&g_u8g2);
}

// ===================== Encoder + Buttons (JOUW WERKENDE STUK) =====================
#define ENC_STEP_MS   4
#define ENC_DIR_HYST  1
#define CONFIRM_LONGPRESS_MS 450

static uint8_t enc_prev_ab=0;
static int8_t enc_accum=0;
static int8_t enc_last_dir=0;
static uint32_t enc_last_emit_ms=0;

static const int8_t enc_table[16] = {
    0, -1,  1, 0,
    1,  0,  0,-1,
   -1,  0,  0, 1,
    0,  1, -1, 0
};

static bool confirm_down=false, confirm_long=false;
static uint32_t confirm_t0=0;

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
    enc_prev_ab = (a<<1)|b;
}

static evt_t input_poll(uint32_t now_ms){
    uint8_t a = gpio_get(PIN_ENC_A) ? 1 : 0;
    uint8_t b = gpio_get(PIN_ENC_B) ? 1 : 0;
    uint8_t ab = (a<<1)|b;

    uint8_t idx = (enc_prev_ab<<2)|ab;
    int8_t delta = enc_table[idx];
    enc_prev_ab = ab;

    if(delta){
        int8_t dir = (delta>0)?1:-1;
        if(enc_last_dir && dir!=enc_last_dir) enc_accum=0;
        enc_last_dir=dir;
        enc_accum += dir;

        if((now_ms-enc_last_emit_ms) >= ENC_STEP_MS){
            if(enc_accum >= ENC_DIR_HYST){ enc_accum=0; enc_last_emit_ms=now_ms; return EVT_CW; }
            if(enc_accum <= -ENC_DIR_HYST){ enc_accum=0; enc_last_emit_ms=now_ms; return EVT_CCW; }
        }
    }

    static bool back_prev=false;
    bool back = (gpio_get(PIN_BTN_BACK)==0);
    if(back && !back_prev){ back_prev=true; return EVT_BACK_DOWN; }
    if(!back && back_prev){ back_prev=false; return EVT_BACK_UP; }

    bool down = (gpio_get(PIN_BTN_CONFIRM)==0);
    if(down && !confirm_down){
        confirm_down=true; confirm_long=false; confirm_t0=now_ms;
    } else if(!down && confirm_down){
        confirm_down=false;
        if(confirm_long){ confirm_long=false; return EVT_CONFIRM_LONG_END; }
        if((now_ms-confirm_t0) < CONFIRM_LONGPRESS_MS) return EVT_CONFIRM;
    } else if(down && confirm_down && !confirm_long){
        if((now_ms-confirm_t0) >= CONFIRM_LONGPRESS_MS){
            confirm_long=true; return EVT_CONFIRM_LONG_START;
        }
    }

    return EVT_NONE;
}

// ===================== UI =====================
typedef enum { SCR_HOME=0, SCR_MENU=1, SCR_SETTINGS=2, SCR_SETTINGS_EDIT=3, SCR_MANUAL=4, SCR_ERROR=5 } screen_t;
static screen_t screen = SCR_HOME;

static bool error_active=false;
static const char *error_msg="";

static int main_idx=0;
static int settings_idx=0;
static int manual_idx=0;

static int active_lane=1;

// manual state
typedef enum { MAN_OFF=0, MAN_FEED=1, MAN_REV=2 } man_action_t;
static int manual_lane = 1;
static man_action_t manual_action = MAN_FEED;
static bool manual_running = false;
static int manual_sps = 0;
static uint32_t manual_started_ms = 0;

// motion timing
static uint32_t motion_started_ms=0;
static bool motion_was_moving=false;

static const char* main_label(int i){
    switch(i){
        case 0: return "Settings";
        case 1: return "Manual";
        case 2: return "Exit";
        default: return "?";
    }
}
static const char* settings_label(int i){
    switch(i){
        case 0: return "Feed sps";
        case 1: return "Rev  sps";
        case 2: return "Auto sps";
        case 3: return "Motion ms";
        case 4: return "Cooldown ms";
        case 5: return "Motion fault";
        default: return "?";
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
    return (a==MAN_FEED) ? "FEED" : (a==MAN_REV) ? "REV" : "OFF";
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

static void draw_home(bool motion_ok){
    u8g2_ClearBuffer(&g_u8g2);
    u8g2_SetFont(&g_u8g2, u8g2_font_6x10_tf);

    u8g2_DrawStr(&g_u8g2, 0, 10, "NightOwl");
    u8g2_DrawHLine(&g_u8g2, 0, 12, 128);

    char s[64];
    snprintf(s, sizeof(s), "Lane: L%d", active_lane);
    u8g2_DrawStr(&g_u8g2, 0, 24, s);

    snprintf(s, sizeof(s), "Feed:%d Rev:%d Auto:%d", FEED_SPS, REV_SPS, AUTO_SPS);
    u8g2_DrawStr(&g_u8g2, 0, 36, s);

    bool raw = gpio_get(PIN_SFS_MOT);
    snprintf(s, sizeof(s), "Mot:%s RAW:%d I:%lu P:%lu",
             motion_ok?"OK":"NO", raw?1:0,
             (unsigned long)g_motion_edges_irq,
             (unsigned long)g_motion_edges_poll);
    u8g2_DrawStr(&g_u8g2, 0, 60, s);

    u8g2_DrawStr(&g_u8g2, 0, 50, "CONF=Menu");
    u8g2_SendBuffer(&g_u8g2);
}

static void draw_list(const char *title, int count, int sel, const char* (*label)(int), const char *right){
    u8g2_ClearBuffer(&g_u8g2);
    u8g2_SetFont(&g_u8g2, u8g2_font_6x10_tf);
    u8g2_DrawStr(&g_u8g2, 0, 10, title);
    u8g2_DrawHLine(&g_u8g2, 0, 12, 128);
    if(right) u8g2_DrawStr(&g_u8g2, 86, 10, right);

    for(int i=0;i<count;i++){
        int y = 24 + i*10;
        if(i==sel){
            u8g2_DrawBox(&g_u8g2, 0, y-9, 128, 10);
            u8g2_SetDrawColor(&g_u8g2, 0);
            u8g2_DrawStr(&g_u8g2, 2, y, label(i));
            u8g2_SetDrawColor(&g_u8g2, 1);
        } else {
            u8g2_DrawStr(&g_u8g2, 2, y, label(i));
        }
    }
    u8g2_SendBuffer(&g_u8g2);
}

static void draw_manual(void){
    u8g2_ClearBuffer(&g_u8g2);
    u8g2_SetFont(&g_u8g2, u8g2_font_6x10_tf);
    u8g2_DrawStr(&g_u8g2, 0, 10, "Manual");
    u8g2_DrawHLine(&g_u8g2, 0, 12, 128);

    for(int i=0;i<3;i++){
        int y = 26 + i*12;
        if(i==manual_idx){
            u8g2_DrawBox(&g_u8g2, 0, y-9, 128, 12);
            u8g2_SetDrawColor(&g_u8g2, 0);
            u8g2_DrawStr(&g_u8g2, 2, y, manual_label(i));
            u8g2_SetDrawColor(&g_u8g2, 1);
        } else {
            u8g2_DrawStr(&g_u8g2, 2, y, manual_label(i));
        }
    }

    char r0[16]; snprintf(r0,sizeof(r0),"L%d", manual_lane);
    char r1[16]; snprintf(r1,sizeof(r1),"%s", action_str(manual_action));
    char r2[16]; snprintf(r2,sizeof(r2),"%s", manual_running ? "RUN" : "STOP");
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

// ===================== MAIN =====================
int main(void){
    stdio_init_all();
    sleep_ms(200);

    input_init();
    oled_init();
    runout_init();
    motion_init();

    motor_t M1, M2;
    motor_init(&M1, PIN_M1_EN, PIN_M1_DIR, PIN_M1_STEP, M1_DIR_INVERT);
    motor_init(&M2, PIN_M2_EN, PIN_M2_DIR, PIN_M2_STEP, M2_DIR_INVERT);

    absolute_time_t last_ui=get_absolute_time();
    absolute_time_t last_poll=get_absolute_time();

    while(true){
        absolute_time_t now=get_absolute_time();
        uint32_t now_ms=to_ms_since_boot(now);
        g_now_ms = now_ms;

        // motion polling edges (1ms)
        if(absolute_time_diff_us(last_poll, now) > 1000){
            last_poll = now;
            bool raw = gpio_get(PIN_SFS_MOT);
            if(raw != g_motion_prev_raw){
                g_motion_prev_raw = raw;
                g_motion_edges_poll++;
            }
        }

        // input
        evt_t ev = input_poll(now_ms);

        motor_t *A = (manual_lane==1) ? &M1 : &M2;

        // error clear
        if(error_active){
            if(ev==EVT_BACK_DOWN){
                error_active=false;
                error_msg="";
                runout_set(false);
                motion_reset();
                manual_running=false;
                motor_stop(&M1); motor_stop(&M2);
                screen=SCR_HOME;
            }
        } else {
            // manual running: encoder changes speed, BACK stops
            if(screen==SCR_MANUAL && manual_running){
                if(ev==EVT_CW){
                    manual_sps = clamp_i(manual_sps+200, 200, 30000);
                    motor_set_rate_sps(A, manual_sps);
                } else if(ev==EVT_CCW){
                    manual_sps = clamp_i(manual_sps-200, 200, 30000);
                    motor_set_rate_sps(A, manual_sps);
                } else if(ev==EVT_BACK_DOWN){
                    manual_running=false;
                    motor_stop(A);
                }
            } else {
                // UI navigation
                if(screen==SCR_HOME){
                    if(ev==EVT_CONFIRM) screen=SCR_MENU;
                    // (optioneel) lane switch op encoder op home: maar jij vond dat gevoelig, dus laten we het weg.
                }
                else if(screen==SCR_MENU){
                    if(ev==EVT_CW){ main_idx++; if(main_idx>2) main_idx=2; }
                    if(ev==EVT_CCW){ main_idx--; if(main_idx<0) main_idx=0; }
                    if(ev==EVT_BACK_DOWN) screen=SCR_HOME;
                    if(ev==EVT_CONFIRM){
                        if(main_idx==0){ screen=SCR_SETTINGS; settings_idx=0; }
                        else if(main_idx==1){ screen=SCR_MANUAL; manual_idx=0; }
                        else screen=SCR_HOME;
                    }
                }
                else if(screen==SCR_SETTINGS){
                    if(ev==EVT_CW){ settings_idx++; if(settings_idx>5) settings_idx=5; }
                    if(ev==EVT_CCW){ settings_idx--; if(settings_idx<0) settings_idx=0; }
                    if(ev==EVT_BACK_DOWN) screen=SCR_MENU;
                    if(ev==EVT_CONFIRM) screen=SCR_SETTINGS_EDIT;
                }
                else if(screen==SCR_SETTINGS_EDIT){
                    if(ev==EVT_CW){
                        if(settings_idx==0) FEED_SPS = clamp_i(FEED_SPS+200, 200, 30000);
                        if(settings_idx==1) REV_SPS  = clamp_i(REV_SPS+200, 200, 30000);
                        if(settings_idx==2) AUTO_SPS = clamp_i(AUTO_SPS+200, 200, 30000);
                        if(settings_idx==3) MOTION_TIMEOUT_MS = clamp_i(MOTION_TIMEOUT_MS+100, 100, 5000);
                        if(settings_idx==4) MOTION_STARTUP_MAX_MS = clamp_i(MOTION_STARTUP_MAX_MS+500, 0, 30000);
                        if(settings_idx==5) MOTION_FAULT_ENABLED = !MOTION_FAULT_ENABLED;
                    }
                    if(ev==EVT_CCW){
                        if(settings_idx==0) FEED_SPS = clamp_i(FEED_SPS-200, 200, 30000);
                        if(settings_idx==1) REV_SPS  = clamp_i(REV_SPS-200, 200, 30000);
                        if(settings_idx==2) AUTO_SPS = clamp_i(AUTO_SPS-200, 200, 30000);
                        if(settings_idx==3) MOTION_TIMEOUT_MS = clamp_i(MOTION_TIMEOUT_MS-100, 100, 5000);
                        if(settings_idx==4) MOTION_STARTUP_MAX_MS = clamp_i(MOTION_STARTUP_MAX_MS-500, 0, 30000);
                        if(settings_idx==5) MOTION_FAULT_ENABLED = !MOTION_FAULT_ENABLED;
                    }
                    if(ev==EVT_CONFIRM || ev==EVT_BACK_DOWN) screen=SCR_SETTINGS;
                }
                else if(screen==SCR_MANUAL){
                    if(ev==EVT_CW){ manual_idx++; if(manual_idx>2) manual_idx=2; }
                    if(ev==EVT_CCW){ manual_idx--; if(manual_idx<0) manual_idx=0; }
                    if(ev==EVT_BACK_DOWN) screen=SCR_MENU;

                    if(ev==EVT_CONFIRM){
                        if(manual_idx==0){
                            manual_lane = (manual_lane==1)?2:1;
                        } else if(manual_idx==1){
                            manual_action = (manual_action==MAN_FEED)?MAN_REV:MAN_FEED;
                        } else if(manual_idx==2){
                            A = (manual_lane==1)? &M1 : &M2;
                            if(!manual_running){
                                manual_running=true;
                                manual_sps = (manual_action==MAN_FEED)? FEED_SPS : REV_SPS;
                                motor_enable(A, true);
                                motor_set_dir(A, manual_action==MAN_FEED);
                                motor_set_rate_sps(A, manual_sps);
                                motion_reset();
                                motion_started_ms = now_ms;
                                manual_started_ms = now_ms;
                            } else {
                                manual_running=false;
                                motor_stop(A);
                            }
                        }
                    }
                }
            }
        }

        // Motion logic for error (alleen als enabled + manual running)
        bool moving_now = manual_running;
        if(moving_now && !motion_was_moving){
            motion_reset();
            motion_started_ms = now_ms;
        }
        motion_was_moving = moving_now;

        bool motion_ok = true;
        if(moving_now){
            uint32_t run_ms = now_ms - motion_started_ms;
            if((int)run_ms <= MOTION_STARTUP_MAX_MS){
                motion_ok = true; // cooldown
            } else {
                uint32_t lm = g_last_motion_ms;
                if(lm == 0) motion_ok = false;
                else {
                    uint32_t age = now_ms - lm;
                    if((int)age > MOTION_TIMEOUT_MS) motion_ok = false;
                }
            }
        }

        if(!error_active && moving_now && MOTION_FAULT_ENABLED && !motion_ok){
            error_active=true;
            error_msg="No motion detected";
            runout_set(true);
            manual_running=false;
            motor_stop(&M1); motor_stop(&M2);
            screen=SCR_ERROR;
        }

        // UI refresh
        if(absolute_time_diff_us(last_ui, now) > 80000){
            last_ui = now;
            if(error_active) draw_error();
            else if(screen==SCR_HOME) draw_home(motion_ok);
            else if(screen==SCR_MENU) draw_list("Menu", 3, main_idx, main_label, NULL);
            else if(screen==SCR_SETTINGS){
                draw_list("Settings", 6, settings_idx, settings_label, NULL);
            }
            else if(screen==SCR_SETTINGS_EDIT){
                char v[24]={0};
                if(settings_idx==0) snprintf(v,sizeof(v),"%d",FEED_SPS);
                if(settings_idx==1) snprintf(v,sizeof(v),"%d",REV_SPS);
                if(settings_idx==2) snprintf(v,sizeof(v),"%d",AUTO_SPS);
                if(settings_idx==3) snprintf(v,sizeof(v),"%d",MOTION_TIMEOUT_MS);
                if(settings_idx==4) snprintf(v,sizeof(v),"%d",MOTION_STARTUP_MAX_MS);
                if(settings_idx==5) snprintf(v,sizeof(v),"%s",MOTION_FAULT_ENABLED?"ON":"OFF");
                draw_list("Edit", 6, settings_idx, settings_label, v);
            }
            else if(screen==SCR_MANUAL) draw_manual();
        }

        tight_loop_contents();
    }
}
