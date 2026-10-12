/*
 * SPDX-FileCopyrightText: 2024-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_random.h"
#include "stepper_motor.h"

static const char *TAG = "Stepper Motor";
static SemaphoreHandle_t s_motion_mutex = NULL;

/* ========== Step Sequence Definitions ========== */

/**
 * @brief 8-beat half-step sequence - Clockwise (forward rotation)
 * @note Alternates single-phase and dual-phase excitation for smoother motion and higher precision
 */
static const int step_sequence_cw[8][4] = {
    {1, 0, 0, 0},  // Step 1: IN1 (single phase)
    {1, 1, 0, 0},  // Step 2: IN1+IN2 (dual phase)
    {0, 1, 0, 0},  // Step 3: IN2 (single phase)
    {0, 1, 1, 0},  // Step 4: IN2+IN3 (dual phase)
    {0, 0, 1, 0},  // Step 5: IN3 (single phase)
    {0, 0, 1, 1},  // Step 6: IN3+IN4 (dual phase)
    {0, 0, 0, 1},  // Step 7: IN4 (single phase)
    {1, 0, 0, 1}   // Step 8: IN4+IN1 (dual phase)
};

/**
 * @brief 8-beat half-step sequence - Counterclockwise (reverse rotation)
 * @note Alternates single-phase and dual-phase excitation for smoother motion and higher precision
 */
static const int step_sequence_ccw[8][4] = {
    {1, 0, 0, 1},  // Step 1: IN4+IN1 (dual phase)
    {0, 0, 0, 1},  // Step 2: IN4 (single phase)
    {0, 0, 1, 1},  // Step 3: IN3+IN4 (dual phase)
    {0, 0, 1, 0},  // Step 4: IN3 (single phase)
    {0, 1, 1, 0},  // Step 5: IN2+IN3 (dual phase)
    {0, 1, 0, 0},  // Step 6: IN2 (single phase)
    {1, 1, 0, 0},  // Step 7: IN1+IN2 (dual phase)
    {1, 0, 0, 0}   // Step 8: IN1 (single phase)
};

/* ========== Private Functions ========== */

/**
 * @brief Set stepper motor pin states
 * 
 * @param in1 IN1 pin state (0 or 1)
 * @param in2 IN2 pin state (0 or 1)
 * @param in3 IN3 pin state (0 or 1)
 * @param in4 IN4 pin state (0 or 1)
 */
static void set_motor_pins(int in1, int in2, int in3, int in4)
{
    gpio_set_level(IN1_PIN, in1);
    gpio_set_level(IN2_PIN, in2);
    gpio_set_level(IN3_PIN, in3);
    gpio_set_level(IN4_PIN, in4);
}

/**
 * @brief Stepper motor clockwise one step
 * 
 * @param step Current step number (will be auto modulo 8)
 */
static void stepper_step_cw(int step)
{
    step = step % 8;  // Ensure step is in range 0-7 (8 steps in half-step mode)
    set_motor_pins(step_sequence_cw[step][0], 
                   step_sequence_cw[step][1], 
                   step_sequence_cw[step][2], 
                   step_sequence_cw[step][3]);
}

/**
 * @brief Stepper motor counterclockwise one step
 * 
 * @param step Current step number (will be auto modulo 8)
 */
static void stepper_step_ccw(int step)
{
    step = step % 8;  // Ensure step is in range 0-7 (8 steps in half-step mode)
    set_motor_pins(step_sequence_ccw[step][0], 
                   step_sequence_ccw[step][1], 
                   step_sequence_ccw[step][2], 
                   step_sequence_ccw[step][3]);
}

/**
 * @brief Microsecond precision delay function
 * 
 * @param delay_us Delay time (microseconds)
 */
static inline void precise_delay_us(int delay_us)
{
    if (delay_us > 0) {
        esp_rom_delay_us(delay_us);
    }
}

/*
 * Build a profile that is valid even for very short moves.  The original
 * fixed 30/30-step profile can produce a zero-sized acceleration or
 * deceleration phase for one- and two-step moves, which then divides by zero
 * in the interpolation below.  Short moves do not need a full ramp, but they
 * still need deterministic timing.
 */
static void stepper_profile_for_steps(int steps, int *accel_steps, int *decel_steps)
{
    if (steps <= 1) {
        *accel_steps = 0;
        *decel_steps = 0;
        return;
    }

    int accel = STEPPER_ACCEL_STEPS;
    int decel = STEPPER_DECEL_STEPS;
    if (accel + decel > steps) {
        /* Keep both phases non-empty when at least two steps are available. */
        accel = steps / 2;
        decel = steps - accel;
        if (accel < 1) {
            accel = 1;
            decel = steps - 1;
        }
        if (decel < 1) {
            decel = 1;
            accel = steps - 1;
        }
    }

    *accel_steps = accel;
    *decel_steps = decel;
}

/* ========== Public Function Implementations ========== */

esp_err_t stepper_motor_motion_begin(TickType_t timeout_ticks)
{
    if (s_motion_mutex == NULL) {
        ESP_LOGE(TAG, "Motion mutex is not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (xSemaphoreTakeRecursive(s_motion_mutex, timeout_ticks) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    return ESP_OK;
}

void stepper_motor_motion_end(void)
{
    if (s_motion_mutex == NULL || xSemaphoreGiveRecursive(s_motion_mutex) != pdTRUE) {
        ESP_LOGE(TAG, "Failed to release motion mutex");
    }
}

/**
 * @brief Clockwise rotation with acceleration/deceleration
 * 
 * @param steps Number of rotation steps
 * @param target_delay_us Target speed delay (microseconds)
 * 
 * @note Uses linear acceleration/deceleration algorithm, divided into acceleration, constant speed, and deceleration phases
 */
static void stepper_rotate_cw_with_accel(int steps, int target_delay_us)
{
    int start_delay_us = STEPPER_START_DELAY_US;
    int accel_steps;
    int decel_steps;
    stepper_profile_for_steps(steps, &accel_steps, &decel_steps);
    
    int constant_steps = steps - accel_steps - decel_steps;
    int current_delay_us;
    
    ESP_LOGD(TAG, "Accel-Decel profile: accel=%d, constant=%d, decel=%d steps", 
             accel_steps, constant_steps, decel_steps);
    
    // Acceleration phase
    for (int i = 0; i < accel_steps; i++) {
        // Linear interpolation: gradually decrease from start_delay to target_delay
        current_delay_us = start_delay_us - (start_delay_us - target_delay_us) * i / accel_steps;
        stepper_step_cw(i);
        precise_delay_us(current_delay_us);
    }
    
    // Constant speed phase
    for (int i = accel_steps; i < accel_steps + constant_steps; i++) {
        stepper_step_cw(i);
        precise_delay_us(target_delay_us);
    }
    
    // Deceleration phase
    for (int i = accel_steps + constant_steps; i < steps; i++) {
        // Linear interpolation: gradually increase from target_delay to start_delay
        int decel_progress = i - (accel_steps + constant_steps);
        current_delay_us = target_delay_us + (start_delay_us - target_delay_us) * decel_progress / decel_steps;
        stepper_step_cw(i);
        precise_delay_us(current_delay_us);
    }
}

/**
 * @brief Counterclockwise rotation with acceleration/deceleration
 * 
 * @param steps Number of rotation steps
 * @param target_delay_us Target speed delay (microseconds)
 * 
 * @note Uses linear acceleration/deceleration algorithm, divided into acceleration, constant speed, and deceleration phases
 */
static void stepper_rotate_ccw_with_accel(int steps, int target_delay_us)
{
    int start_delay_us = STEPPER_START_DELAY_US;
    int accel_steps;
    int decel_steps;
    stepper_profile_for_steps(steps, &accel_steps, &decel_steps);
    
    int constant_steps = steps - accel_steps - decel_steps;
    int current_delay_us;
    
    ESP_LOGD(TAG, "Accel-Decel profile: accel=%d, constant=%d, decel=%d steps", 
             accel_steps, constant_steps, decel_steps);
    
    // Acceleration phase
    for (int i = 0; i < accel_steps; i++) {
        current_delay_us = start_delay_us - (start_delay_us - target_delay_us) * i / accel_steps;
        stepper_step_ccw(i);
        precise_delay_us(current_delay_us);
    }
    
    // Constant speed phase
    for (int i = accel_steps; i < accel_steps + constant_steps; i++) {
        stepper_step_ccw(i);
        precise_delay_us(target_delay_us);
    }
    
    // Deceleration phase
    for (int i = accel_steps + constant_steps; i < steps; i++) {
        int decel_progress = i - (accel_steps + constant_steps);
        current_delay_us = target_delay_us + (start_delay_us - target_delay_us) * decel_progress / decel_steps;
        stepper_step_ccw(i);
        precise_delay_us(current_delay_us);
    }
}

/**
 * @brief Rotate by specified angle (with acceleration/deceleration)
 * 
 * @param angle Rotation angle, positive for right (clockwise), negative for left (counterclockwise)
 * @param target_delay_us Target speed delay (microseconds), will automatically accelerate from slow speed to this speed at start
 * 
 * @note Half-step mode: 4128 steps = 360 degrees
 * @note Uses linear acceleration/deceleration algorithm to ensure smooth start and stop
 */
void stepper_rotate_angle_with_accel(float angle, int target_delay_us)
{
    // Half-step mode: 4128 steps = 360 degrees
    const float step_count = angle * 4128.0f / 360.0f;
    int steps = step_count >= 0.0f ? (int)(step_count + 0.5f) : (int)(step_count - 0.5f);

    if (target_delay_us <= 0) {
        ESP_LOGW(TAG, "Invalid target delay %d us; using start delay %d us",
                 target_delay_us, STEPPER_START_DELAY_US);
        target_delay_us = STEPPER_START_DELAY_US;
    }
    
    if (steps == 0) {
        ESP_LOGI(TAG, "Angle too small, no rotation needed");
        return;
    }
    
    if (steps > 0) {
        ESP_LOGD(TAG, "Rotating %.1f° CW with acceleration (%d steps, target %dus/step)", 
                 angle, steps, target_delay_us);
        stepper_rotate_cw_with_accel(steps, target_delay_us);
    } else {
        ESP_LOGD(TAG, "Rotating %.1f° CCW with acceleration (%d steps, target %dus/step)", 
                 angle, -steps, target_delay_us);
        stepper_rotate_ccw_with_accel(-steps, target_delay_us);
    }
}

/**
 * @brief Shake head action function
 * 
 * @param amplitude Shake amplitude (one-sided angle), e.g., 30 means shake 30 degrees left and right
 * @param cycles Number of shake cycles, one complete left-right shake counts as 1
 * @param speed_us Shake speed (microsecond delay), recommend using STEPPER_SPEED_xxx macros
 * 
 * Motion sequence (example with amplitude=30, cycles=2):
 * 1. Turn left to -30° → pause
 * 2. Turn right to +30° → pause → turn left to -30° → pause  (cycle 1)
 * 3. Turn right to +30° → pause → turn left to -30° → pause  (cycle 2)
 * 4. Return to center 0°
 */
void stepper_shake_head(float amplitude, int cycles, int speed_us)
{
    if (amplitude <= 0 || cycles <= 0) {
        ESP_LOGW(TAG, "Invalid shake head parameters: amplitude=%.1f, cycles=%d", amplitude, cycles);
        return;
    }
    
    ESP_LOGD(TAG, "Shake head started: amplitude=%.1f°, cycles=%d, speed=%dus/step", 
             amplitude, cycles, speed_us);
    
    // Step 1: Turn left from center to start position
    ESP_LOGD(TAG, "Moving to left start position (%.1f°)", -amplitude);
    stepper_rotate_angle_with_accel(-amplitude, speed_us);
    vTaskDelay(pdMS_TO_TICKS(10));  // Brief pause
    
    // Shake cycles
    for (int i = 0; i < cycles; i++) {
        ESP_LOGD(TAG, "Shake cycle %d/%d", i + 1, cycles);
        
        // Turn right (from left to right, rotate 2*amplitude degrees)
        stepper_rotate_angle_with_accel(2 * amplitude, speed_us);
        vTaskDelay(pdMS_TO_TICKS(10));  // Brief pause
        
        // Turn left (from right to left, rotate 2*amplitude degrees)
        stepper_rotate_angle_with_accel(-2 * amplitude, speed_us);
        vTaskDelay(pdMS_TO_TICKS(10));  // Brief pause
    }
    
    // Last step: Return from left to center position
    ESP_LOGD(TAG, "Returning to center position");
    stepper_rotate_angle_with_accel(amplitude, speed_us);
}

/**
 * @brief Gradually decaying shake head action function
 * 
 * @param initial_amplitude Initial shake amplitude (one-sided angle), e.g., 30 means initially shake 30 degrees left and right
 * @param decay_rate Decay rate
 *                   - Range 0.0~1.0: Percentage decay, e.g., 0.8 means amplitude becomes 80% each time
 *                   - >1.0: Fixed angle decay, e.g., 5.0 means decrease by 5 degrees each time
 * @param speed_us Shake speed (microsecond delay), recommend using STEPPER_SPEED_xxx macros
 * 
 * @note Shake amplitude gradually decreases until below minimum threshold (5 degrees) then stops
 * @note Speed automatically decreases when amplitude is small to avoid jitter
 */
void stepper_shake_head_decay(float initial_amplitude, float decay_rate, int speed_us)
{
    if (initial_amplitude <= 0) {
        ESP_LOGW(TAG, "Invalid amplitude: %.1f", initial_amplitude);
        return;
    }
    
    if (decay_rate <= 0) {
        ESP_LOGW(TAG, "Invalid decay rate: %.3f", decay_rate);
        return;
    }
    
    ESP_LOGD(TAG, "Decay shake head started: initial_amplitude=%.1f°, decay_rate=%.3f, speed=%dus/step", 
             initial_amplitude, decay_rate, speed_us);
    
    float current_amplitude = initial_amplitude;
    float min_amplitude = 5.0;  // Minimum amplitude threshold (degrees), stop shaking below this to avoid small angle jitter
    float smooth_threshold = 8.0;  // Smooth threshold (degrees), reduce speed below this
    int cycle_count = 0;
    bool use_percentage_decay = (decay_rate > 0.0 && decay_rate < 1.0);  // Determine if percentage decay or fixed value decay
    float current_position = 0.0;  // Track current position relative to center
    
    // Step 1: Turn left from center to start position
    ESP_LOGD(TAG, "Moving to left start position (%.1f°)", -current_amplitude);
    stepper_rotate_angle_with_accel(-current_amplitude, speed_us);
    current_position = -current_amplitude;
    vTaskDelay(pdMS_TO_TICKS(10));  // Brief pause
    
    // Shake loop, until amplitude below threshold
    while (current_amplitude >= min_amplitude) {
        cycle_count++;
        
        // Dynamically adjust speed based on current amplitude, slower speed for smaller amplitude to avoid jitter
        int current_speed_us = speed_us;
        if (current_amplitude < smooth_threshold) {
            // Linear interpolation: speed factor increases from 1.0 to 1.3 (max 30% slowdown) as amplitude decreases from smooth_threshold to min_amplitude
            float speed_factor = 1.0 + 0.3 * (smooth_threshold - current_amplitude) / (smooth_threshold - min_amplitude);
            current_speed_us = (int)(speed_us * speed_factor);
            ESP_LOGD(TAG, "Decay shake cycle %d: amplitude=%.1f°, speed adjusted to %dus/step (factor=%.2f)", 
                     cycle_count, current_amplitude, current_speed_us, speed_factor);
        } else {
            ESP_LOGD(TAG, "Decay shake cycle %d: amplitude=%.1f°", cycle_count, current_amplitude);
        }
        
        // Turn right (from left to right, rotate 2*current_amplitude degrees)
        stepper_rotate_angle_with_accel(2 * current_amplitude, current_speed_us);
        current_position += 2 * current_amplitude;
        vTaskDelay(pdMS_TO_TICKS(15));  // Brief pause, longer when amplitude is small
        
        // Calculate next amplitude
        float next_amplitude;
        if (use_percentage_decay) {
            // Percentage decay mode: amplitude = current amplitude × decay rate
            next_amplitude = current_amplitude * decay_rate;
            ESP_LOGD(TAG, "Percentage decay: next amplitude=%.1f° (%.1f%%)", 
                     next_amplitude, decay_rate * 100);
        } else {
            // Fixed value decay mode: amplitude = current amplitude - decay value
            next_amplitude = current_amplitude - decay_rate;
            ESP_LOGD(TAG, "Fixed decay: next amplitude=%.1f° (-%.1f°)", 
                     next_amplitude, decay_rate);
        }
        
        // Check if should continue shaking
        if (next_amplitude < min_amplitude) {
            ESP_LOGD(TAG, "Next amplitude too small (%.1f° < %.1f°), stopping at position %.1f°", 
                     next_amplitude, min_amplitude, current_position);
            break;
        }
        
        current_amplitude = next_amplitude;
        
        // Dynamically adjust speed based on current amplitude
        current_speed_us = speed_us;
        if (current_amplitude < smooth_threshold) {
            float speed_factor = 1.0 + 0.3 * (smooth_threshold - current_amplitude) / (smooth_threshold - min_amplitude);
            current_speed_us = (int)(speed_us * speed_factor);
        }
        
        // Turn left (from right to left, rotate 2*current_amplitude degrees)
        stepper_rotate_angle_with_accel(-2 * current_amplitude, current_speed_us);
        current_position -= 2 * current_amplitude;
        vTaskDelay(pdMS_TO_TICKS(15));  // Brief pause
        
        // Calculate next amplitude again (symmetric decay)
        if (use_percentage_decay) {
            next_amplitude = current_amplitude * decay_rate;
        } else {
            next_amplitude = current_amplitude - decay_rate;
        }
        
        // Check next amplitude
        if (next_amplitude < min_amplitude) {
            ESP_LOGD(TAG, "Next amplitude too small (%.1f° < %.1f°), stopping at position %.1f°", 
                     next_amplitude, min_amplitude, current_position);
            break;
        }
        
        current_amplitude = next_amplitude;
    }
    
    // Last step: Return to center position
    ESP_LOGD(TAG, "Returning to center from position %.1f°", current_position);
    
    // Use slightly slower speed when returning to center to ensure smoothness
    float abs_position = (current_position > 0) ? current_position : -current_position;
    int return_speed_us = speed_us;
    if (abs_position < smooth_threshold) {
        // If remaining angle is small, slightly reduce return speed (max 20% slowdown)
        float speed_factor = 1.2;
        return_speed_us = (int)(speed_us * speed_factor);
        ESP_LOGD(TAG, "Using slower speed for return: %dus/step", return_speed_us);
    }
    
    stepper_rotate_angle_with_accel(-current_position, return_speed_us);
    
    ESP_LOGD(TAG, "Decay shake head completed: total cycles=%d", cycle_count);
}

/**
 * @brief Generate random angle offset (within ±max_offset range)
 * 
 * @param max_offset Maximum offset (degrees)
 * @return float Random offset angle, range [-max_offset, +max_offset]
 * 
 * @note Used to add randomness to motions for more natural movement
 */
static float get_random_angle_offset(float max_offset)
{
    if (max_offset <= 0) {
        return 0;
    }
    
    // Generate random number between 0 and 1
    uint32_t random_value = esp_random();
    float normalized = (float)(random_value % 10000) / 10000.0;  // 0.0 ~ 1.0
    
    // Convert to range -max_offset to +max_offset
    float offset = (normalized * 2.0 - 1.0) * max_offset;
    
    return offset;
}

/**
 * @brief Look around action function (with small amplitude scanning + random offset)
 * 
 * @param left_angle Main angle to turn left (positive value), e.g., 45 means turn left 45 degrees
 * @param right_angle Main angle to turn right (positive value), e.g., 45 means turn right 45 degrees
 * @param scan_angle Small amplitude scanning angle at left and right sides (positive value), e.g., 10 means scan 10 degrees left and right
 * @param pause_ms Pause time after each movement (milliseconds), simulating "observation" motion
 * @param large_speed_us Large movement speed (microsecond delay), used for main position rotation
 * @param small_speed_us Small scanning speed (microsecond delay), used for small range observation, recommend slower than large_speed_us
 * 
 * @note Random offset is added to each rotation for more natural motion
 * @note Large movement: random offset ±10°
 * @note Small scanning: random offset ±10°
 * 
 * Motion sequence (example with left_angle=45, scan_angle=10, actual values will have random offset):
 * 1. Turn left to -45°±10° (large speed) → pause
 * 2. Scan left to -55°±10° (small speed) → pause
 * 3. Scan right to -35°±10° (small speed) → pause
 * 4. Turn right to +45°±10° (large speed) → pause
 * 5. Scan right to +55°±10° (small speed) → pause
 * 6. Scan left to +35°±10° (small speed) → pause
 * 7. Return to center 0° (large speed)
 */
void stepper_look_around(float left_angle, float right_angle, float scan_angle, int pause_ms, int large_speed_us, int small_speed_us)
{
    if (left_angle < 0 || right_angle < 0 || scan_angle < 0) {
        ESP_LOGW(TAG, "Invalid angles: left=%.1f, right=%.1f, scan=%.1f (should be positive)", 
                 left_angle, right_angle, scan_angle);
        return;
    }
    
    if (pause_ms < 0) {
        pause_ms = 0;
    }
    
    ESP_LOGD(TAG, "Look around started: left=%.1f°, right=%.1f°, scan=%.1f°, pause=%dms, large_speed=%dus, small_speed=%dus", 
             left_angle, right_angle, scan_angle, pause_ms, large_speed_us, small_speed_us);
    
    // Accumulated position tracking (relative to starting center position)
    float accumulated_position = 0.0;
    
    // ========== Phase 1: Look Left ==========
    if (left_angle > 0) {
        
        // 1. Turn left to main position (large amplitude, fast) + random offset
        float left_offset = get_random_angle_offset(10.0);  // ±10 degrees random
        float actual_left_angle = left_angle + left_offset;
        stepper_rotate_angle_with_accel(-actual_left_angle, large_speed_us);
        accumulated_position -= actual_left_angle;  // Track accumulated position
        vTaskDelay(pdMS_TO_TICKS(pause_ms));
        
        // 2. Scan left with small amplitude (slow speed) + random offset
        if (scan_angle > 0) {
            float scan_left_offset = get_random_angle_offset(10.0);  // ±10 degrees random
            float actual_scan_left = scan_angle + scan_left_offset;
            
            // Ensure small amplitude rotation is not less than 5 degrees
            if (actual_scan_left < 5.0) {
                actual_scan_left = 5.0;
                scan_left_offset = actual_scan_left - scan_angle;
            }

            stepper_rotate_angle_with_accel(-actual_scan_left, small_speed_us);
            accumulated_position -= actual_scan_left;  // Track accumulated position
            vTaskDelay(pdMS_TO_TICKS(pause_ms));
            
            // 3. Scan right directly with small amplitude (slow speed, don't return to main position) + random offset
            float scan_right_offset = get_random_angle_offset(10.0);  // ±10 degrees random
            float actual_scan_range = 2 * scan_angle + scan_left_offset - scan_right_offset;
            
            // Ensure small amplitude rotation is not less than 5 degrees
            if (actual_scan_range < 5.0) {
                actual_scan_range = 5.0;
            }
            
            stepper_rotate_angle_with_accel(actual_scan_range, small_speed_us);
            accumulated_position += actual_scan_range;  // Track accumulated position
            vTaskDelay(pdMS_TO_TICKS(pause_ms));
        }
    }
    
    // ========== Phase 2: Look Right ==========
    if (right_angle > 0) {
        // 4. Turn from current position to right main position (fast) + random offset
        float right_offset = get_random_angle_offset(10.0);  // ±10 degrees random
        float actual_right_angle = right_angle + right_offset;
        
        // Calculate rotation angle: turn from current accumulated position to right side
        float turn_angle = actual_right_angle - accumulated_position;
        stepper_rotate_angle_with_accel(turn_angle, large_speed_us);
        accumulated_position += turn_angle;  // Add actual rotated angle to avoid precision errors
        vTaskDelay(pdMS_TO_TICKS(pause_ms));
        
        // 5. Scan right with small amplitude (slow speed) + random offset
        if (scan_angle > 0) {
            float scan_right2_offset = get_random_angle_offset(10.0);  // ±10 degrees random
            float actual_scan_right = scan_angle + scan_right2_offset;
            
            // Ensure small amplitude rotation is not less than 5 degrees
            if (actual_scan_right < 5.0) {
                actual_scan_right = 5.0;
                scan_right2_offset = actual_scan_right - scan_angle;
            }

            stepper_rotate_angle_with_accel(actual_scan_right, small_speed_us);
            accumulated_position += actual_scan_right;  // Track accumulated position
            vTaskDelay(pdMS_TO_TICKS(pause_ms));
            
            // 6. Scan left directly with small amplitude (slow speed, don't return to main position) + random offset
            float scan_left2_offset = get_random_angle_offset(10.0);  // ±10 degrees random
            float actual_scan_range2 = 2 * scan_angle + scan_right2_offset - scan_left2_offset;
            
            // Ensure small amplitude rotation is not less than 5 degrees
            if (actual_scan_range2 < 5.0) {
                actual_scan_range2 = 5.0;
            }
            
            stepper_rotate_angle_with_accel(-actual_scan_range2, small_speed_us);
            accumulated_position -= actual_scan_range2;  // Track accumulated position
            vTaskDelay(pdMS_TO_TICKS(pause_ms));
        }
    }
    
    // ========== Phase 3: Return to Center ==========
    
    // Use accumulated position to return directly to center (tolerance: 0.5 degrees)
    float abs_position = (accumulated_position > 0) ? accumulated_position : -accumulated_position;
    if (abs_position > 0.5) {
        stepper_rotate_angle_with_accel(-accumulated_position, large_speed_us);
    } else {
        ESP_LOGD(TAG, "Already near center (offset=%.2f°)", accumulated_position);
    }
    
    ESP_LOGD(TAG, "Look around completed");
}

/**
 * @brief Follow drum beat swing function
 * 
 * @param angle Swing angle for each beat (positive value), e.g., 10 means swing 10 degrees left and right
 * @param speed_us Rotation speed (microsecond delay)
 * 
 * @note Each call to this function automatically switches direction (using static variable to track state)
 * @note Call sequence: left → right → left → right ...
 * 
 * Typical usage: Call this function when music beat is detected, motor will swing left and right following the beat
 */
void stepper_beat_swing(float angle, int speed_us)
{
    // Static variable to track current swing direction, false=left, true=right
    static bool swing_direction = false;
    
    if (angle <= 0) {
        ESP_LOGW(TAG, "Invalid angle: %.1f (should be positive)", angle);
        return;
    }
    
    if (swing_direction) {
        // Turn right
        stepper_rotate_angle_with_accel(angle, speed_us);
    } else {
        // Turn left
        stepper_rotate_angle_with_accel(-angle, speed_us);
    }
    
    // Switch direction
    swing_direction = !swing_direction;
}

/**
 * @brief Cat nuzzling action function (gently turn left and return to center, repeat several times)
 * 
 * @param angle Angle to turn left (positive value), e.g., 20 means turn left 20 degrees
 * @param cycles Number of nuzzles, each includes a complete "turn-return to center" motion
 * @param speed_us Rotation speed (microsecond delay), recommend using slower speed like STEPPER_SPEED_SLOW for gentle feel
 * 
 * Motion sequence (example with angle=20, cycles=3):
 * 1. Turn left to -20° (slow) → pause 100ms → return to center 0° (slow) → pause 50ms
 * 2. Turn left to -20° (slow) → pause 100ms → return to center 0° (slow) → pause 50ms
 * 3. Turn left to -20° (slow) → pause 100ms → return to center 0° (slow)
 * 
 * @note Uses slow smooth acceleration/deceleration throughout for gentle feel
 * @note Brief pause after each turn to simulate cat nuzzling contact feel
 */
void stepper_cat_nuzzle(float angle, int cycles, int speed_us)
{
    if (angle <= 0) {
        ESP_LOGW(TAG, "Invalid angle: %.1f (should be positive)", angle);
        return;
    }
    
    if (cycles <= 0) {
        ESP_LOGW(TAG, "Invalid cycles: %d (should be positive)", cycles);
        return;
    }
    
    ESP_LOGD(TAG, "Cat nuzzle started: angle=%.1f°, cycles=%d, speed=%dus/step", 
             angle, cycles, speed_us);
    
    // Execute nuzzle cycles
    for (int i = 0; i < cycles; i++) {
        ESP_LOGD(TAG, "Nuzzle cycle %d/%d", i + 1, cycles);
        
        // Slowly turn left
        stepper_rotate_angle_with_accel(-angle, speed_us);
        vTaskDelay(pdMS_TO_TICKS(100));  // Pause 100ms to simulate nuzzling contact feel
        
        // Slowly return to center
        stepper_rotate_angle_with_accel(angle, speed_us);
        
        // Add brief pause between cycles (not after last cycle)
        if (i < cycles - 1) {
            vTaskDelay(pdMS_TO_TICKS(50));  // Brief pause after each nuzzle, prepare for next one
        }
    }
    
    ESP_LOGD(TAG, "Cat nuzzle completed");
}

/**
 * @brief Turn off all stepper motor coils (power off)
 * 
 * @note After calling this function, motor will no longer hold position and can be rotated by external force
 * @note Saves power consumption and avoids motor heating from prolonged energization
 */
void stepper_motor_power_off(void)
{
    set_motor_pins(0, 0, 0, 0);
}

/**
 * @brief Initialize stepper motor GPIO pins
 * 
 * @note Configures IN1-IN4 pins as output mode, initial state is low level
 */
void stepper_motor_gpio_init(void)
{
    if (s_motion_mutex == NULL) {
        s_motion_mutex = xSemaphoreCreateRecursiveMutex();
        if (s_motion_mutex == NULL) {
            ESP_LOGE(TAG, "Failed to create motion mutex");
            return;
        }
    }

    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << IN1_PIN) | (1ULL << IN2_PIN) | (1ULL << IN3_PIN) | (1ULL << IN4_PIN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    
    gpio_config(&io_conf);
    
    // Set all pins to low level
    gpio_set_level(IN1_PIN, 0);
    gpio_set_level(IN2_PIN, 0);
    gpio_set_level(IN3_PIN, 0);
    gpio_set_level(IN4_PIN, 0);
    
    ESP_LOGI(TAG, "GPIO initialization completed, IN1-IN4 pins set as output mode with default low level");
}
