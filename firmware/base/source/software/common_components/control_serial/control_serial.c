/*
 * SPDX-FileCopyrightText: 2024-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <stdatomic.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "stepper_motor.h"
#include "control_serial.h"
#include "magnetic_slide_switch.h"

static const char *TAG = "Control Serial";
static atomic_bool s_motion_ready = ATOMIC_VAR_INIT(false);
static atomic_uint_fast16_t s_base_status = ATOMIC_VAR_INIT(BASE_STATUS_HOMING);
static atomic_int s_base_absolute_angle_degrees = ATOMIC_VAR_INIT(90);
static control_serial_home_request_cb_t s_home_request_callback = NULL;
static void *s_home_request_context = NULL;
static SemaphoreHandle_t s_tx_mutex = NULL;

static esp_err_t control_serial_send_heartbeat(void);
static esp_err_t control_serial_send_mechanical_status(uint16_t status);

static esp_err_t control_serial_write_bytes(const uint8_t *data, size_t length)
{
    if (data == NULL || length == 0 || s_tx_mutex == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(s_tx_mutex, pdMS_TO_TICKS(250)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    const int sent = uart_write_bytes(ECHO_UART_PORT_NUM, data, length);
    const esp_err_t wait_result = sent == (int)length
                                  ? uart_wait_tx_done(ECHO_UART_PORT_NUM, pdMS_TO_TICKS(500))
                                  : ESP_FAIL;
    xSemaphoreGive(s_tx_mutex);
    return wait_result == ESP_OK && sent == (int)length ? ESP_OK : ESP_FAIL;
}

void control_serial_set_base_status(uint16_t status)
{
    if (status != BASE_STATUS_HOMING && status != BASE_STATUS_READY &&
        status != BASE_STATUS_HOMING_FAILED) {
        ESP_LOGW(TAG, "Ignoring invalid base status: 0x%04X", status);
        return;
    }

    atomic_store_explicit(&s_base_status, status, memory_order_release);
    const bool ready = status == BASE_STATUS_READY;
    atomic_store_explicit(&s_motion_ready, ready, memory_order_release);
    if (ready) {
        // A READY transition follows a successful mechanical home at 90 deg.
        atomic_store_explicit(&s_base_absolute_angle_degrees, 90, memory_order_release);
    }
    ESP_LOGI(TAG, "Base status=0x%04X; UART motor commands %s", status,
             ready ? "enabled" : "blocked");

    /* Publish transitions immediately so a short HOMING state cannot be
     * hidden between periodic status frames. During early UART setup the
     * mutex is still NULL; the status task publishes the initial state once
     * the driver is ready. */
    if (s_tx_mutex != NULL) {
        control_serial_send_mechanical_status(status);
    }
}

void control_serial_set_home_request_callback(control_serial_home_request_cb_t callback,
                                              void *context)
{
    s_home_request_context = context;
    s_home_request_callback = callback;
}

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
static void control_serial_log_tx_frame(const char *name, const uint8_t *frame, size_t length)
{
    char hex[65];
    size_t logged_length = length < ((sizeof(hex) - 1) / 2) ? length : ((sizeof(hex) - 1) / 2);
    for (size_t i = 0; i < logged_length; i++) {
        snprintf(&hex[i * 2], 3, "%02X", frame[i]);
    }
    hex[logged_length * 2] = '\0';

    ESP_LOGI(TAG, "[diag] uart_tx name=%s len=%u hex=%s%s", name,
             (unsigned)length, hex, logged_length < length ? "..." : "");
}
#endif

/*
 * Execute one already validated command frame.  Keeping command execution
 * separate from transport parsing lets the receive task retain split and
 * concatenated frames without duplicating motor or calibration semantics.
 */
static void control_serial_execute_frame(const uint8_t *frame, uint16_t data_len)
{
    if (frame == NULL || data_len == 0) {
        return;
    }

    const uint8_t cmd = frame[4];
    if ((cmd == CMD_BASE_ANGLE_CONTROL || cmd == CMD_BASE_ACTION_CONTROL ||
         cmd == CMD_BASE_SOUND_BEARING) &&
        !atomic_load_explicit(&s_motion_ready, memory_order_acquire)) {
        ESP_LOGW(TAG, "Ignoring motor command 0x%02X while base homing is incomplete", cmd);
        return;
    }

    if (cmd == CMD_BASE_SOUND_BEARING) {
        if (data_len != 3) return;
        const unsigned bearing = ((unsigned)frame[5] << 8) | frame[6];
        if (bearing > 180) return;
        static TickType_t last_move_tick;
        const TickType_t now = xTaskGetTickCount();
        if (last_move_tick && (now - last_move_tick) < pdMS_TO_TICKS(1200)) return;
        int delta = (int)bearing - 90;
        if (delta > -4 && delta < 4) return;
        // Reproduce official relative bearing semantics, with bounded travel.
        if (delta > 30) delta = 30;
        if (delta < -30) delta = -30;
        if (stepper_motor_motion_begin(pdMS_TO_TICKS(100)) != ESP_OK) return;
        if (!atomic_load_explicit(&s_motion_ready, memory_order_acquire)) {
            stepper_motor_motion_end();
            return;
        }
        const int current = atomic_load_explicit(&s_base_absolute_angle_degrees, memory_order_acquire);
        int target = current + delta;
        // +/-60 around the calibrated center leaves margin before the one
        // known mechanical limit (70 degrees from center).
        if (target < 30) target = 30;
        if (target > 150) target = 150;
        delta = target - current;
        if (current < 30 || current > 150 || delta == 0) {
            ESP_LOGW(TAG, "DOA motion held at software limit current=%d", current);
            stepper_motor_motion_end();
            return;
        }
        last_move_tick = now;
        ESP_LOGI(TAG, "DOA bearing=%u current=%d delta=%d target=%d step_us=1500",
                 bearing, current, delta, target);
        stepper_rotate_angle_with_accel((float)delta, 1500);
        vTaskDelay(pdMS_TO_TICKS(100));
        stepper_motor_power_off();
        atomic_store_explicit(&s_base_absolute_angle_degrees, target, memory_order_release);
        ESP_LOGI(TAG, "DOA steps issued; estimated angle=%d (no position encoder)", target);
        stepper_motor_motion_end();
        return;
    }

    if (cmd == CMD_BASE_ANGLE_CONTROL) {
        if (data_len != 3) {
            ESP_LOGW(TAG, "Invalid data length for angle control: %d (expected 3)", data_len);
            return;
        }

        const uint16_t target_angle = ((uint16_t)frame[5] << 8) | frame[6];
        if (target_angle > 180) {
            ESP_LOGW(TAG, "Received angle %d out of range [0, 180], current %.1f",
                     target_angle,
                     (float)atomic_load_explicit(
                         &s_base_absolute_angle_degrees, memory_order_acquire
                     ));
            return;
        }

        /* The wire value is an absolute target in the 0..180 degree frame.
         * Computing a delta from the tracked position prevents a repeated
         * sound-source update from applying the same rotation over and over.
         */
        const int current_angle = atomic_load_explicit(
                                      &s_base_absolute_angle_degrees, memory_order_acquire
                                  );
        const float diff_angle = (float)target_angle - (float)current_angle;
        if (diff_angle > 180.0f || diff_angle < -180.0f) {
            ESP_LOGW(TAG, "Angle delta %.1f is outside the safe range", diff_angle);
            return;
        }

        ESP_LOGI(TAG,
                 "Received target angle: %d, current %.1f, delta %.1f",
                 target_angle, (float)current_angle, diff_angle);

        if (stepper_motor_motion_begin(portMAX_DELAY) != ESP_OK) {
            ESP_LOGE(TAG, "Failed to acquire motor for angle command");
            return;
        }

        // Use the normal ramp speed for the loaded rotating head.  The ultra
        // fast 600us setting can stall the geared stepper and only produce a
        // faint buzz, especially for larger DOA corrections.
        stepper_rotate_angle_with_accel(diff_angle, STEPPER_SPEED_NORMAL);
        vTaskDelay(pdMS_TO_TICKS(100));
        stepper_motor_power_off();
        atomic_store_explicit(
            &s_base_absolute_angle_degrees, (int)target_angle, memory_order_release
        );
        ESP_LOGI(TAG, "Base rotated to absolute angle: %d degrees", target_angle);
        stepper_motor_motion_end();
        return;
    }

    if (cmd == CMD_BASE_ACTION_CONTROL) {
        if (data_len != 3) {
            ESP_LOGW(TAG, "Invalid data length for action control: %d (expected 3)", data_len);
            return;
        }

        const uint16_t action = ((uint16_t)frame[5] << 8) | frame[6];
        if (action >= STEPPER_ACTION_MAX) {
            ESP_LOGW(TAG, "Unknown action: %d", action);
            return;
        }

        if (stepper_motor_motion_begin(portMAX_DELAY) != ESP_OK) {
            ESP_LOGE(TAG, "Failed to acquire motor for action %d", action);
            return;
        }

        ESP_LOGI(TAG, "Received action value: %d", action);
        switch (action) {
        case STEPPER_ACTION_SHAKE_HEAD:
            ESP_LOGI(TAG, "Execute action: SHAKE_HEAD");
            stepper_shake_head(6.0, 2, 600);
            stepper_motor_power_off();
            control_serial_send_action_complete();
            break;
        case STEPPER_ACTION_SHAKE_HEAD_DECAY:
            ESP_LOGI(TAG, "Execute action: SHAKE_HEAD_DECAY");
            stepper_shake_head_decay(20.0, 0.8, 800);
            stepper_motor_power_off();
            control_serial_send_action_complete();
            break;
        case STEPPER_ACTION_LOOK_AROUND:
            ESP_LOGI(TAG, "Execute action: LOOK_AROUND");
            stepper_look_around(35.0, 35.0, 10.0, 600, 600, 800);
            stepper_motor_power_off();
            control_serial_send_action_complete();
            break;
        case STEPPER_ACTION_BEAT_SWING:
            ESP_LOGI(TAG, "Execute action: BEAT_SWING (drum beat)");
            stepper_beat_swing(20.0, 800);
            stepper_motor_power_off();
            control_serial_send_action_complete();
            break;
        case STEPPER_ACTION_CAT_NUZZLE:
            ESP_LOGI(TAG, "Execute action: CAT_NUZZLE");
            stepper_cat_nuzzle(20, 3, 1500);
            stepper_motor_power_off();
            control_serial_send_action_complete();
            break;
        default:
            ESP_LOGW(TAG, "Unknown action: %d", action);
            break;
        }
        stepper_motor_motion_end();
        return;
    }

    if (cmd == CMD_MAGNETIC_SWITCH_EVENT) {
        if (data_len != 3) {
            ESP_LOGW(TAG, "Invalid data length for magnetic switch command: %d (expected 3)",
                     data_len);
            return;
        }

        const uint16_t magnetic_command = ((uint16_t)frame[5] << 8) | frame[6];
        if (magnetic_command == MAG_SWITCH_CMD_RECALIBRATE) {
            ESP_LOGI(TAG, "Magnetic switch recalibration command received");
            magnetic_slide_switch_start_recalibration();
            ESP_LOGI(TAG, "Recalibration started, please follow the prompts");
        } else {
            ESP_LOGW(TAG, "Unknown magnetic switch command: 0x%04X", magnetic_command);
        }
        return;
    }

    if (cmd == CMD_BASE_HOME_CONTROL) {
        if (data_len != 3) {
            ESP_LOGW(TAG, "Invalid data length for home control: %d (expected 3)", data_len);
            return;
        }

        const uint16_t request = ((uint16_t)frame[5] << 8) | frame[6];
        if (request != BASE_HOME_REQUEST || s_home_request_callback == NULL) {
            ESP_LOGW(TAG, "Home request unavailable or invalid: 0x%04X", request);
            return;
        }

        const esp_err_t result = s_home_request_callback(s_home_request_context);
        if (result != ESP_OK) {
            ESP_LOGW(TAG, "Home request rejected: %s", esp_err_to_name(result));
        }
        return;
    }

    ESP_LOGW(TAG, "Unknown command code: 0x%02X, data_len: %d", cmd, data_len);
}

/**
 * @brief UART command receive task
 * 
 * @param arg Task parameter (unused)
 * 
 * @note This task loops to receive UART data, parse commands and execute corresponding operations:
 *       - CMD_BASE_ANGLE_CONTROL: Control base rotation to specified angle
 *       - CMD_BASE_ACTION_CONTROL: Execute predefined actions
 *       - CMD_MAGNETIC_SWITCH_EVENT: Magnetic switch calibration command
 * @note Implements frame header synchronization to handle data misalignment
 */
/* Kept as a reference implementation while the streaming parser is being
 * validated on hardware.  The active task below uses the same command
 * semantics through control_serial_execute_frame(). */
static void uart_cmd_receive_task_legacy(void *arg)
{
    // Track absolute angle of the base (0-180 degrees, initial: 90 degrees)
    static float s_base_absolute_angle = 90.0f;
    
    // Configure temporary buffer for incoming data
    uint8_t *data = (uint8_t *) malloc(BUF_SIZE);
    if (data == NULL) {
        ESP_LOGE(TAG, "Failed to allocate UART buffer");
        vTaskDelete(NULL);
        return;
    }

    while (1) {
        // Read data from UART
        int len = uart_read_bytes(ECHO_UART_PORT_NUM, data, (BUF_SIZE - 1), 20 / portTICK_PERIOD_MS);
        if (len > 0) {
            // Frame header synchronization: search for 0xAA 0x55
            int frame_start = -1;
            for (int i = 0; i < len - 1; i++) {
                if (data[i] == UART_FRAME_HEAD_H && data[i + 1] == UART_FRAME_HEAD_L) {
                    frame_start = i;
                    break;
                }
            }
            
            // If frame header not found, discard data and continue
            if (frame_start == -1) {
                ESP_LOGD(TAG, "Frame header not found in %d bytes, discarding", len);
                continue;
            }
            
            // If frame header not at beginning, log warning
            if (frame_start > 0) {
                ESP_LOGW(TAG, "Frame header found at offset %d, discarded %d bytes", frame_start, frame_start);
            }
            
            // Check if we have enough data for minimum frame
            int remaining_len = len - frame_start;
            if (remaining_len < UART_FRAME_MIN_LEN) {
                ESP_LOGW(TAG, "Incomplete frame: only %d bytes after header", remaining_len);
                continue;
            }
            
            // Point to the start of the frame
            uint8_t *frame = data + frame_start;
            
            // Get data length from frame
            uint16_t data_len = (frame[2] << 8) | frame[3];
            
            // Validate data length
            if (data_len == 0 || data_len > BUF_SIZE - UART_FRAME_HEADER_LEN - 2 - 1) {
                ESP_LOGW(TAG, "Invalid data length: %d", data_len);
                continue;
            }
            
            // Check if we have complete frame
            /* AA 55 + two-byte length + payload + checksum. */
            int expected_frame_len = UART_FRAME_HEADER_LEN + 2 + data_len + 1;
            if (remaining_len < expected_frame_len) {
                ESP_LOGW(TAG, "Incomplete frame: expected %d bytes, got %d", expected_frame_len, remaining_len);
                continue;
            }
            
            // Get command code and data length
            uint8_t cmd = frame[4];
            
            // Calculate checksum for the entire data payload (command code + data bytes)
            uint8_t calc_checksum = 0;
            for (int i = 4; i < 4 + data_len; i++) {
                calc_checksum += frame[i];
            }
            
            // Get received checksum (last byte of frame)
            uint8_t received_checksum = frame[4 + data_len];
            
            // Verify checksum FIRST before parsing any data
            if (calc_checksum != received_checksum) {
                ESP_LOGW(TAG, "Checksum verification failed: calc=0x%02X, recv=0x%02X, cmd=0x%02X", 
                         calc_checksum, received_checksum, cmd);
                continue;  // Discard this frame and continue
            }
            
            // Checksum verified, now safe to parse and execute commands
            
            // Parse data according to command type
            if (cmd == CMD_BASE_ANGLE_CONTROL) {  // Base angle control
                if (data_len != 3) {  // Should be: cmd(1) + angle(2) = 3 bytes
                    ESP_LOGW(TAG, "Invalid data length for angle control: %d (expected 3)", data_len);
                    continue;
                }
                
                // Get data value (combine two bytes) - sound source angle from head (0-180 degrees)
                // Value is relative to initial position (90 degrees = facing head directly)
                uint16_t value = (frame[5] << 8) | frame[6];
                
                // Check if received angle is within valid range (0-180 degrees)
                if (value > 180) {
                    ESP_LOGW(TAG, "Received angle %d out of range [0, 180], ignoring rotation. Current absolute angle: %.1f", 
                             value, s_base_absolute_angle);
                    continue;
                }
                
                // Calculate rotation angle relative to initial position (90 degrees)
                // diff_angle = value - 90 means:
                //   - value = 90: diff_angle = 0 (no rotation, facing head)
                //   - value = 120: diff_angle = 30 (rotate right 30 degrees)
                //   - value = 60: diff_angle = -30 (rotate left 30 degrees)
                float diff_angle = (float)value - 90.0f;
                
                // Calculate new absolute angle after rotation
                float new_absolute_angle = s_base_absolute_angle + diff_angle;
                
                // Check if new absolute angle would be out of range (0-180 degrees)
                if (new_absolute_angle < 0.0f || new_absolute_angle > 180.0f) {
                    ESP_LOGW(TAG, "Rotation would result in angle %.1f out of range [0, 180], ignoring. Current: %.1f, received: %d, diff: %.1f", 
                             new_absolute_angle, s_base_absolute_angle, value, diff_angle);
                    continue;
                }
                
                ESP_LOGI(TAG, "Received sound source angle: %d, current absolute angle: %.1f, diff_angle: %.1f, new absolute angle: %.1f", 
                         value, s_base_absolute_angle, diff_angle, new_absolute_angle);
                
                // Execute angle control (rotate by diff_angle degrees)
                stepper_rotate_angle_with_accel((int16_t)diff_angle, STEPPER_SPEED_ULTRA_FAST);
                vTaskDelay(pdMS_TO_TICKS(100));
                stepper_motor_power_off();
                
                // Update absolute angle after successful rotation
                s_base_absolute_angle = new_absolute_angle;
                ESP_LOGI(TAG, "Base rotated to absolute angle: %.1f degrees", s_base_absolute_angle);
            }
            else if (cmd == CMD_BASE_ACTION_CONTROL) {  // Base action control
                if (data_len != 3) {  // Should be: cmd(1) + action(2) = 3 bytes
                    ESP_LOGW(TAG, "Invalid data length for action control: %d (expected 3)", data_len);
                    continue;
                }
                
                // Get action value (combine two bytes)
                uint16_t action = (frame[5] << 8) | frame[6];
                ESP_LOGI(TAG, "Received action value: %d", action);
                
                // Execute different predefined actions based on action value
                switch (action) {
                    case STEPPER_ACTION_SHAKE_HEAD:
                        ESP_LOGI(TAG, "Execute action: SHAKE_HEAD");
                        stepper_shake_head(6.0, 2, 600);
                        stepper_motor_power_off();
                        control_serial_send_action_complete();  // Send action complete notification
                        break;
                        
                    case STEPPER_ACTION_SHAKE_HEAD_DECAY:
                        ESP_LOGI(TAG, "Execute action: SHAKE_HEAD_DECAY");
                        stepper_shake_head_decay(20.0, 0.8, 800);
                        stepper_motor_power_off();
                        control_serial_send_action_complete();  // Send action complete notification
                        break;
                        
                    case STEPPER_ACTION_LOOK_AROUND:
                        ESP_LOGI(TAG, "Execute action: LOOK_AROUND");
                        stepper_look_around(35.0, 35.0, 10.0, 600, 600, 800);
                        stepper_motor_power_off();
                        control_serial_send_action_complete();  // Send action complete notification
                        break;
                        
                    case STEPPER_ACTION_BEAT_SWING:  // action = 3: drum beat
                        ESP_LOGI(TAG, "Execute action: BEAT_SWING (drum beat)");
                        stepper_beat_swing(20.0, 800);
                        stepper_motor_power_off();
                        control_serial_send_action_complete();  // Send action complete notification
                        break;
                        
                    case STEPPER_ACTION_CAT_NUZZLE:
                        ESP_LOGI(TAG, "Execute action: CAT_NUZZLE");
                        stepper_cat_nuzzle(20, 3, 1500);
                        stepper_motor_power_off();
                        control_serial_send_action_complete();  // Send action complete notification
                        break;
                        
                    default:
                        ESP_LOGW(TAG, "Unknown action: %d", action);
                        break;
                }
            }
            else if (cmd == CMD_MAGNETIC_SWITCH_EVENT) {  // Magnetic switch command
                if (data_len != 3) {  // Should be: cmd(1) + mag_cmd(2) = 3 bytes
                    ESP_LOGW(TAG, "Invalid data length for magnetic switch command: %d (expected 3)", data_len);
                    continue;
                }
                
                // Get command data (combine two bytes)
                uint16_t mag_cmd = (frame[5] << 8) | frame[6];
                
                // Check if it's recalibration command
                if (mag_cmd == MAG_SWITCH_CMD_RECALIBRATE) {
                    ESP_LOGI(TAG, "Magnetic switch recalibration command received");
                    magnetic_slide_switch_start_recalibration();
                    ESP_LOGI(TAG, "Recalibration started, please follow the prompts");
                } else {
                    ESP_LOGW(TAG, "Unknown magnetic switch command: 0x%04X", mag_cmd);
                }
            }
            else {
                ESP_LOGW(TAG, "Unknown command code: 0x%02X, data_len: %d", cmd, data_len);
            }
        }
    }
}

/*
 * Receive task with a persistent buffer.  UART delivery boundaries are not
 * protocol boundaries: a frame may be split across reads or several frames
 * may arrive together.  This loop consumes only complete, checksum-valid
 * frames and keeps the remainder for the next read.
 */
static void uart_cmd_receive_task_streaming(void *arg)
{
    (void)arg;
    uint8_t *data = (uint8_t *)malloc(BUF_SIZE);
    if (data == NULL) {
        ESP_LOGE(TAG, "Failed to allocate UART buffer");
        vTaskDelete(NULL);
        return;
    }

    size_t buffered = 0;
    while (1) {
        if (buffered >= BUF_SIZE) {
            ESP_LOGW(TAG, "UART parser buffer full; dropping buffered noise");
            buffered = 0;
        }

        const size_t capacity = BUF_SIZE - buffered;
        const int received = uart_read_bytes(
            ECHO_UART_PORT_NUM, data + buffered, capacity,
            20 / portTICK_PERIOD_MS
        );
        if (received > 0) {
            buffered += (size_t)received;
        }

        while (buffered >= UART_FRAME_HEADER_LEN) {
            size_t frame_start = 0;
            bool found_header = false;
            for (size_t index = 0; index + 1 < buffered; ++index) {
                if (data[index] == UART_FRAME_HEAD_H && data[index + 1] == UART_FRAME_HEAD_L) {
                    frame_start = index;
                    found_header = true;
                    break;
                }
            }

            if (!found_header) {
                /* Preserve a trailing AA, since the following read may
                 * provide the 55 byte of a split header. */
                if (data[buffered - 1] == UART_FRAME_HEAD_H) {
                    data[0] = UART_FRAME_HEAD_H;
                    buffered = 1;
                } else {
                    buffered = 0;
                }
                break;
            }

            if (frame_start > 0) {
                memmove(data, data + frame_start, buffered - frame_start);
                buffered -= frame_start;
            }

            if (buffered < UART_FRAME_MIN_LEN) {
                break;
            }

            const uint16_t data_len = ((uint16_t)data[2] << 8) | data[3];
            /* A frame is AA 55 + uint16 length + payload + checksum.  The
             * length field counts payload bytes (command + data), so leave
             * five bytes of framing/checksum overhead in the receive buffer. */
            if (data_len == 0 || data_len > BUF_SIZE - UART_FRAME_HEADER_LEN - 2 - 1) {
                ESP_LOGW(TAG, "Invalid data length: %u", (unsigned)data_len);
                /* Drop one byte only; an AA inside the malformed header may
                 * begin a valid frame. */
                memmove(data, data + 1, buffered - 1);
                --buffered;
                continue;
            }

            const size_t expected_frame_len = UART_FRAME_HEADER_LEN + 2 + data_len + 1;
            if (buffered < expected_frame_len) {
                /* Keep the incomplete frame intact for the next read. */
                break;
            }

            uint8_t calculated_checksum = 0;
            for (size_t index = 4; index < 4 + data_len; ++index) {
                calculated_checksum = (uint8_t)(calculated_checksum + data[index]);
            }
            const uint8_t received_checksum = data[4 + data_len];
            if (calculated_checksum != received_checksum) {
                ESP_LOGW(TAG,
                         "Checksum verification failed: calc=0x%02X recv=0x%02X cmd=0x%02X",
                         calculated_checksum, received_checksum, data[4]);
                /* Resynchronise at the next byte while retaining any AA55
                 * sequence that follows the corrupted frame. */
                memmove(data, data + 1, buffered - 1);
                --buffered;
                continue;
            }

            control_serial_execute_frame(data, data_len);
            memmove(data, data + expected_frame_len, buffered - expected_frame_len);
            buffered -= expected_frame_len;
        }
    }
}

static esp_err_t control_serial_send_heartbeat(void)
{
    const uint8_t tx_buffer[8] = {
        UART_FRAME_HEAD_H,
        UART_FRAME_HEAD_L,
        0x00,
        0x03,
        CMD_BASE_HEARTBEAT,
        (uint8_t)(BASE_HEARTBEAT_ALIVE >> 8),
        (uint8_t)(BASE_HEARTBEAT_ALIVE & 0xFF),
        (uint8_t)(CMD_BASE_HEARTBEAT +
                  (uint8_t)(BASE_HEARTBEAT_ALIVE >> 8) +
                  (uint8_t)(BASE_HEARTBEAT_ALIVE & 0xFF)),
    };

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    control_serial_log_tx_frame("heartbeat", tx_buffer, sizeof(tx_buffer));
#endif

    const esp_err_t result = control_serial_write_bytes(tx_buffer, sizeof(tx_buffer));
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Failed to send heartbeat: %s", esp_err_to_name(result));
    }
    return result;
}

static esp_err_t control_serial_send_mechanical_status(uint16_t status)
{
    const uint8_t tx_buffer[8] = {
        UART_FRAME_HEAD_H,
        UART_FRAME_HEAD_L,
        0x00,
        0x03,
        CMD_BASE_MECHANICAL_STATUS,
        (uint8_t)(status >> 8),
        (uint8_t)(status & 0xFF),
        (uint8_t)(CMD_BASE_MECHANICAL_STATUS + (uint8_t)(status >> 8) +
                  (uint8_t)(status & 0xFF)),
    };

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    control_serial_log_tx_frame("mechanical_status", tx_buffer, sizeof(tx_buffer));
#endif

    const esp_err_t result = control_serial_write_bytes(tx_buffer, sizeof(tx_buffer));
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Failed to send mechanical status: %s", esp_err_to_name(result));
    }
    return result;
}

static void base_status_task(void *arg)
{
    (void)arg;
    while (1) {
        const uint16_t status = (uint16_t)atomic_load_explicit(
                                    &s_base_status, memory_order_acquire
                                );
        control_serial_send_heartbeat();
        control_serial_send_mechanical_status(status);
        vTaskDelay(pdMS_TO_TICKS(BASE_STATUS_INTERVAL_MS));
    }
}

/**
 * @brief Initialize serial control module
 * 
 * @return esp_err_t ESP_OK indicates success
 * 
 * @note Configures UART parameters and creates command receive task
 */
esp_err_t control_serial_init(void)
{
    control_serial_set_base_status(BASE_STATUS_HOMING);

    if (s_tx_mutex == NULL) {
        s_tx_mutex = xSemaphoreCreateMutex();
        if (s_tx_mutex == NULL) {
            ESP_LOGE(TAG, "Failed to create UART TX mutex");
            return ESP_ERR_NO_MEM;
        }
    }

    /* Configure UART driver parameters, communication pins and install driver */
    uart_config_t uart_config = {
        .baud_rate = ECHO_UART_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    int intr_alloc_flags = 0;

    ESP_ERROR_CHECK(uart_driver_install(ECHO_UART_PORT_NUM, BUF_SIZE * 2, 0, 0, NULL, intr_alloc_flags));
    ESP_ERROR_CHECK(uart_param_config(ECHO_UART_PORT_NUM, &uart_config));
    ESP_ERROR_CHECK(uart_set_pin(ECHO_UART_PORT_NUM, UART_ECHO_TXD, UART_ECHO_RXD, UART_ECHO_RTS, UART_ECHO_CTS));

    BaseType_t created = xTaskCreate(
                             uart_cmd_receive_task_streaming, "uart_cmd_receive_task",
                             UART_CMD_TASK_STACK_SIZE, NULL, 12, NULL
                         );
    if (created != pdPASS) {
        ESP_LOGE(TAG, "Failed to create UART receive task");
        uart_driver_delete(ECHO_UART_PORT_NUM);
        vSemaphoreDelete(s_tx_mutex);
        s_tx_mutex = NULL;
        return ESP_ERR_NO_MEM;
    }

    created = xTaskCreate(
                  base_status_task, "base_status_task",
                  BASE_STATUS_TASK_STACK_SIZE, NULL, 5, NULL
              );
    if (created != pdPASS) {
        ESP_LOGE(TAG, "Failed to create base status task");
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

/**
 * @brief Send magnetic slide switch event notification
 * 
 * @param event Event type (magnetic_slide_switch_event_t)
 * @return esp_err_t ESP_OK indicates success
 * 
 * Packet format:
 * AA 55 00 03 03 00 01 04
 * [0-1]: Header (0xAA 0x55)
 * [2-3]: Data length (0x00 0x03 = 3 bytes)
 * [4]:   Command code (0x03 = magnetic slide switch event)
 * [5-6]: Event data (0x00 0x01 = SLIDE_DOWN)
 * [7]:   Checksum (command code + data bytes sum)
 */
esp_err_t control_serial_send_magnetic_switch_event(uint16_t event)
{
    uint8_t tx_buffer[8];
    
    // Frame header
    tx_buffer[0] = UART_FRAME_HEAD_H;  // 0xAA
    tx_buffer[1] = UART_FRAME_HEAD_L;  // 0x55
    
    // Data length (command code 1 byte + data 2 bytes = 3 bytes)
    tx_buffer[2] = 0x00;
    tx_buffer[3] = 0x03;
    
    // Command code
    tx_buffer[4] = CMD_MAGNETIC_SWITCH_EVENT;  // 0x03
    
    // Event data (big-endian, high byte first)
    tx_buffer[5] = (event >> 8) & 0xFF;  // High byte
    tx_buffer[6] = event & 0xFF;          // Low byte
    
    // Checksum (command code + data high byte + data low byte)
    tx_buffer[7] = tx_buffer[4] + tx_buffer[5] + tx_buffer[6];

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    control_serial_log_tx_frame("magnetic_switch_event", tx_buffer, sizeof(tx_buffer));
#endif
    
    // Send through UART
    const esp_err_t write_result = control_serial_write_bytes(tx_buffer, sizeof(tx_buffer));

    if (write_result == ESP_OK) {
        return ESP_OK;
    } else {
        ESP_LOGE(TAG, "Failed to send magnetic switch event: %s",
                 esp_err_to_name(write_result));
        return ESP_FAIL;
    }
}

/**
 * @brief Send specific action execution complete notification
 * 
 * @return esp_err_t ESP_OK indicates success
 * 
 * Packet format:
 * AA 55 00 03 02 00 10 12
 * [0-1]: Header (0xAA 0x55)
 * [2-3]: Data length (0x00 0x03 = 3 bytes)
 * [4]:   Command code (0x02 = action execution complete)
 * [5-6]: Complete code (0x00 0x10 = 16)
 * [7]:   Checksum (command code + data bytes sum = 0x02 + 0x00 + 0x10 = 0x12)
 */
esp_err_t control_serial_send_action_complete(void)
{
    uint8_t tx_buffer[8];
    
    // Frame header
    tx_buffer[0] = UART_FRAME_HEAD_H;  // 0xAA
    tx_buffer[1] = UART_FRAME_HEAD_L;  // 0x55
    
    // Data length (command code 1 byte + data 2 bytes = 3 bytes)
    tx_buffer[2] = 0x00;
    tx_buffer[3] = 0x03;
    
    // Command code
    tx_buffer[4] = CMD_ACTION_COMPLETE;  // 0x02
    
    // Complete code (big-endian, high byte first)
    tx_buffer[5] = (ACTION_COMPLETE_CODE >> 8) & 0xFF;  // 0x00
    tx_buffer[6] = ACTION_COMPLETE_CODE & 0xFF;          // 0x10
    
    // Checksum (command code + data high byte + data low byte)
    tx_buffer[7] = tx_buffer[4] + tx_buffer[5] + tx_buffer[6];

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    control_serial_log_tx_frame("action_complete", tx_buffer, sizeof(tx_buffer));
#endif
    
    // Send through UART
    const esp_err_t write_result = control_serial_write_bytes(tx_buffer, sizeof(tx_buffer));

    if (write_result == ESP_OK) {
        ESP_LOGI(TAG, "Action complete notification sent");
        return ESP_OK;
    } else {
        ESP_LOGE(TAG, "Failed to send action complete: %s",
                 esp_err_to_name(write_result));
        return ESP_FAIL;
    }
}

void control_serial_start_magnetic_detect_task(void)
{
    /* Kept solely so the legacy example profiles continue to compile.  Do
     * not recreate the former synthetic attachment task: frame command 0x00
     * is now reserved for the real base status heartbeat. */
    ESP_LOGW(TAG, "Legacy magnetic attachment task is disabled; use the profile sensor task");
}

/**
 * @brief Send magnetic switch calibration step notification
 * 
 * @param step_code Step code (0x0011 or 0x0012)
 * @return esp_err_t ESP_OK indicates success
 * 
 * Packet format example (second position complete):
 * AA 55 00 03 03 00 11 14
 * [0-1]: Header (0xAA 0x55)
 * [2-3]: Data length (0x00 0x03 = 3 bytes)
 * [4]:   Command code (0x03 = magnetic switch event)
 * [5-6]: Step code (0x00 0x11 = second position complete)
 * [7]:   Checksum (0x03 + 0x00 + 0x11 = 0x14)
 * 
 * Packet format example (calibration complete):
 * AA 55 00 03 03 00 12 15
 */
esp_err_t control_serial_send_magnetic_switch_calibration_step(uint16_t step_code)
{
    uint8_t tx_buffer[8];
    
    // Frame header
    tx_buffer[0] = UART_FRAME_HEAD_H;  // 0xAA
    tx_buffer[1] = UART_FRAME_HEAD_L;  // 0x55
    
    // Data length (command code 1 byte + data 2 bytes = 3 bytes)
    tx_buffer[2] = 0x00;
    tx_buffer[3] = 0x03;
    
    // Command code
    tx_buffer[4] = CMD_MAGNETIC_SWITCH_EVENT;  // 0x03
    
    // Step code (big-endian, high byte first)
    tx_buffer[5] = (step_code >> 8) & 0xFF;
    tx_buffer[6] = step_code & 0xFF;
    
    // Checksum (command code + data high byte + data low byte)
    tx_buffer[7] = tx_buffer[4] + tx_buffer[5] + tx_buffer[6];

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    control_serial_log_tx_frame("calibration_step", tx_buffer, sizeof(tx_buffer));
#endif
    
    // Send through UART
    const esp_err_t write_result = control_serial_write_bytes(tx_buffer, sizeof(tx_buffer));

    if (write_result == ESP_OK) {
        ESP_LOGI(TAG, "Calibration step notification sent: 0x%04X", step_code);
        return ESP_OK;
    } else {
        ESP_LOGE(TAG, "Failed to send calibration step: %s",
                 esp_err_to_name(write_result));
        return ESP_FAIL;
    }
}

esp_err_t control_serial_send_magnetic_sensor_sample(
    control_serial_magnetic_sensor_kind_t sensor_kind,
    int16_t value_a,
    int16_t value_b,
    int16_t value_c
)
{
    /* Payload: command + sensor kind + three signed big-endian int16 values. */
    uint8_t tx_buffer[13] = {
        UART_FRAME_HEAD_H,
        UART_FRAME_HEAD_L,
        0x00,
        0x08,
        CMD_MAGNETIC_SENSOR_SAMPLE,
        (uint8_t)sensor_kind,
        (uint8_t)(((uint16_t)value_a) >> 8),
        (uint8_t)((uint16_t)value_a & 0xFF),
        (uint8_t)(((uint16_t)value_b) >> 8),
        (uint8_t)((uint16_t)value_b & 0xFF),
        (uint8_t)(((uint16_t)value_c) >> 8),
        (uint8_t)((uint16_t)value_c & 0xFF),
        0,
    };
    for (size_t index = 4; index < sizeof(tx_buffer) - 1; ++index) {
        tx_buffer[sizeof(tx_buffer) - 1] += tx_buffer[index];
    }

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    control_serial_log_tx_frame("magnetic_sensor_sample", tx_buffer, sizeof(tx_buffer));
#endif
    return control_serial_write_bytes(tx_buffer, sizeof(tx_buffer));
}

/**
 * @brief Print magnetometer sensor data via UART
 * 
 * @param x X-axis magnetic field value
 * @param y Y-axis magnetic field value
 * @param z Z-axis magnetic field value
 * @return esp_err_t ESP_OK indicates success
 * 
 * @note Output format: "X: 100, Y: 100, Z: 100\r\n"
 * @note Sends data directly through UART without frame header
 */
esp_err_t control_serial_print_magnetometer_data(int16_t x, int16_t y, int16_t z)
{
    // Format string: "X: 100, Y: 100, Z: 100\r\n"
    // Maximum length: "X: -32768, Y: -32768, Z: -32768\r\n" = 35 bytes
    char tx_buffer[50];
    int len = snprintf(tx_buffer, sizeof(tx_buffer), "%d,%d,%d\r\n", x, y, z);
    
    if (len < 0 || len >= sizeof(tx_buffer)) {
        ESP_LOGE(TAG, "Failed to format magnetometer data string");
        return ESP_FAIL;
    }
    
    // Send through UART
    const esp_err_t write_result = control_serial_write_bytes(
                                       (const uint8_t *)tx_buffer, (size_t)len
                                   );

    if (write_result == ESP_OK) {
        return ESP_OK;
    } else {
        ESP_LOGE(TAG, "Failed to send magnetometer data: %s",
                 esp_err_to_name(write_result));
        return ESP_FAIL;
    }
}
