/*
 * SPDX-FileCopyrightText: 2024-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include "freertos/FreeRTOS.h"
#include "driver/gpio.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ========== GPIO Pin Definitions ========== */
#define IN1_PIN     (GPIO_NUM_28)   /**< Stepper motor IN1 control pin */
#define IN2_PIN     (GPIO_NUM_27)   /**< Stepper motor IN2 control pin */
#define IN3_PIN     (GPIO_NUM_26)   /**< Stepper motor IN3 control pin */
#define IN4_PIN     (GPIO_NUM_25)   /**< Stepper motor IN4 control pin */

/* ========== Speed Configuration (delay per step, unit: microseconds μs) ========== */
/**
 * @note Uses half-step driving mode for doubled precision and smoother motion
 * @note Smaller delay means faster speed
 */
#define STEPPER_SPEED_ULTRA_FAST    600     /**< Ultra fast mode: 600μs/step (0.6ms) */
#define STEPPER_SPEED_FAST          800     /**< Fast mode: 1000μs/step (1ms) */
#define STEPPER_SPEED_NORMAL        1500    /**< Normal mode: 1500μs/step (1.5ms) */
#define STEPPER_SPEED_SLOW          2000    /**< Slow mode: 2000μs/step (2ms) */

/* ========== Acceleration/Deceleration Configuration ========== */
#define STEPPER_START_DELAY_US      1500    /**< Start delay (slower, ensures smooth start) */
#define STEPPER_ACCEL_STEPS         30      /**< Acceleration steps */
#define STEPPER_DECEL_STEPS         30      /**< Deceleration steps */

/* ========== Stepper Motor Action Type Enumeration ========== */
/**
 * @brief Stepper motor predefined action types
 */
typedef enum {
    STEPPER_ACTION_SHAKE_HEAD,          /**< Shake head action (fixed amplitude and cycles) */
    STEPPER_ACTION_SHAKE_HEAD_DECAY,    /**< Gradually decaying shake head action */
    STEPPER_ACTION_LOOK_AROUND,         /**< Look around observation action */
    STEPPER_ACTION_BEAT_SWING,          /**< Follow drum beat swing motion */
    STEPPER_ACTION_CAT_NUZZLE,          /**< Cat nuzzling action */
    STEPPER_ACTION_MAX                  /**< Number of action types (for boundary check) */
} stepper_action_type_t;

/* ========== Function Declarations ========== */

/**
 * @brief Acquire exclusive ownership of the complete motor motion sequence
 *
 * @param timeout_ticks Maximum time to wait for ownership
 * @return ESP_OK on success, ESP_ERR_INVALID_STATE before motor initialization,
 *         or ESP_ERR_TIMEOUT if ownership could not be acquired
 *
 * @note The mutex is recursive so a compound action can safely call lower-level
 *       motion helpers while retaining ownership for the complete sequence.
 */
esp_err_t stepper_motor_motion_begin(TickType_t timeout_ticks);

/**
 * @brief Release ownership acquired by stepper_motor_motion_begin()
 */
void stepper_motor_motion_end(void);

/**
 * @brief Rotate by specified angle (with acceleration/deceleration)
 * 
 * @param angle Rotation angle, positive for right (clockwise), negative for left (counterclockwise)
 * @param target_delay_us Target speed delay (microseconds), will automatically accelerate from slow speed to this speed at start
 */
void stepper_rotate_angle_with_accel(float angle, int target_delay_us);

/**
 * @brief Shake head action function
 * 
 * @param amplitude Shake amplitude (one-sided angle), e.g., 30 means shake 30 degrees left and right
 * @param cycles Number of shake cycles, one complete left-right shake counts as 1
 * @param speed_us Shake speed (microsecond delay), recommend using STEPPER_SPEED_xxx macros
 */
void stepper_shake_head(float amplitude, int cycles, int speed_us);

/**
 * @brief Gradually decaying shake head action function
 * 
 * @param initial_amplitude Initial shake amplitude (one-sided angle), e.g., 30 means initially shake 30 degrees left and right
 * @param decay_rate Decay rate, range 0.0~1.0 for percentage decay, >1.0 for fixed angle decay
 *                   e.g., 0.8 means amplitude becomes 80% after each shake
 *                   e.g., 5.0 means decrease by 5 degrees each time
 * @param speed_us Shake speed (microsecond delay), recommend using STEPPER_SPEED_xxx macros
 */
void stepper_shake_head_decay(float initial_amplitude, float decay_rate, int speed_us);

/**
 * @brief Look around action function (with small amplitude scanning + random offset)
 * 
 * @param left_angle Main angle to turn left (positive value), e.g., 45 means turn left 45 degrees
 * @param right_angle Main angle to turn right (positive value), e.g., 45 means turn right 45 degrees
 * @param scan_angle Small amplitude scanning angle at left and right sides (positive value), e.g., 10 means scan 10 degrees left and right
 * @param pause_ms Pause time after each movement (milliseconds), simulating "observation" motion
 * @param large_speed_us Large movement speed (microsecond delay), used for main position rotation
 * @param small_speed_us Small scanning speed (microsecond delay), used for small range observation
 * 
 * @note Random offset is added to each rotation for more natural motion
 */
void stepper_look_around(float left_angle, float right_angle, float scan_angle, 
                         int pause_ms, int large_speed_us, int small_speed_us);

/**
 * @brief Follow drum beat swing function
 * 
 * @param angle Swing angle for each beat (positive value), e.g., 10 means swing 10 degrees left and right
 * @param speed_us Rotation speed (microsecond delay)
 * 
 * @note Each call to this function automatically switches direction (left-right-left-right...)
 */
void stepper_beat_swing(float angle, int speed_us);

/**
 * @brief Cat nuzzling action function (gently turn left and return to center, repeat several times)
 * 
 * @param angle Angle to turn left (positive value), e.g., 20 means turn left 20 degrees
 * @param cycles Number of nuzzles, each includes a complete "turn-return to center" motion
 * @param speed_us Rotation speed (microsecond delay), recommend using slower speed like STEPPER_SPEED_SLOW
 * 
 * @note Uses slow smooth acceleration/deceleration throughout for gentle feel
 */
void stepper_cat_nuzzle(float angle, int cycles, int speed_us);

/**
 * @brief Turn off all stepper motor coils (power off)
 * 
 * @note After calling this function, motor will no longer hold position and can be rotated by external force
 */
void stepper_motor_power_off(void);

/**
 * @brief Initialize stepper motor GPIO pins
 */
void stepper_motor_gpio_init(void);

#ifdef __cplusplus
}
#endif
