/*
 * SPDX-FileCopyrightText: 2024-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ========== UART Configuration ========== */
#define UART_ECHO_TXD               (GPIO_NUM_29)   /**< UART TX pin */
#define UART_ECHO_RXD               (GPIO_NUM_8)    /**< UART RX pin */
#define UART_ECHO_RTS               (-1)            /**< UART RTS pin (unused) */
#define UART_ECHO_CTS               (-1)            /**< UART CTS pin (unused) */

#define ECHO_UART_PORT_NUM          (1)             /**< UART port number */
#define ECHO_UART_BAUD_RATE         (115200)        /**< Baud rate */
#define UART_CMD_TASK_STACK_SIZE    (1024 * 3)      /**< Command receive task stack size */
#define BUF_SIZE                    (1024)          /**< Receive buffer size */

/* ========== Frame Format Definitions ========== */
#define UART_FRAME_HEAD_H           (0xAA)          /**< Frame header high byte */
#define UART_FRAME_HEAD_L           (0x55)          /**< Frame header low byte */
#define UART_FRAME_HEADER_LEN       (2)             /**< Frame header length */
#define UART_FRAME_MIN_LEN          (5)             /**< Minimum frame length: header+length+command+checksum */

/* ========== Command Code Definitions ========== */
#define CMD_BASE_HEARTBEAT          (0x00)          /**< Official link heartbeat (value 0x0001 = alive) */
#define CMD_BASE_ANGLE_CONTROL      (0x01)          /**< Base angle control */
#define CMD_BASE_ACTION_CONTROL     (0x02)          /**< Base action control */
#define CMD_MAGNETIC_SWITCH_EVENT   (0x03)          /**< Magnetic slide switch event */
#define CMD_BASE_HOME_CONTROL       (0x04)          /**< Re-run mechanical homing */
#define CMD_BASE_MECHANICAL_STATUS  (0x05)          /**< Mechanical homing/motion status */
#define CMD_MAGNETIC_SENSOR_SAMPLE  (0x06)          /**< Low-rate raw magnetic sensor sample */
#define CMD_BASE_SOUND_BEARING      (0x07)          /**< Relative DOA bearing, 90 = ahead */
/* Keep the old name source-compatible, but its wire value is now 0x05. */
#define CMD_BASE_STATUS             CMD_BASE_MECHANICAL_STATUS
#define CMD_ACTION_COMPLETE         (0x02)          /**< Specific action execution complete */

/* ========== Magnetic Switch Special Command Codes ========== */
#define MAG_SWITCH_CMD_RECALIBRATE  (0x0010)        /**< Recalibrate magnetic switch */
#define MAG_SWITCH_CALIB_START      (0x0081)        /**< Start calibration */
#define MAG_SWITCH_CALIB_FIRST_DONE (0x0082)        /**< Second position calibration done, proceed to third action */
#define MAG_SWITCH_CALIB_COMPLETE   (0x0083)        /**< Calibration complete */
/* Legacy VoCat profiles used 0x0011..0x0013 for these notifications. */
#define MAG_SWITCH_CALIB_START_LEGACY      (0x0011)
#define MAG_SWITCH_CALIB_FIRST_DONE_LEGACY (0x0012)
#define MAG_SWITCH_CALIB_COMPLETE_LEGACY   (0x0013)

/* ========== Action Complete Status Code ========== */
#define ACTION_COMPLETE_CODE        (0x0010)        /**< Action complete code (16) */

/* ========== Base Status Codes ========== */
#define BASE_STATUS_HOMING          (0x0000)        /**< Mechanical homing is running */
#define BASE_STATUS_READY           (0x0001)        /**< Homing complete; absolute angle is known */
#define BASE_STATUS_HOMING_FAILED   (0x0002)        /**< Homing failed; motion remains blocked */
#define BASE_HOME_REQUEST           (0x0001)        /**< Re-run mechanical homing */
#define BASE_HEARTBEAT_ALIVE        (0x0001)        /**< CMD_BASE_HEARTBEAT value */

#define BASE_STATUS_TASK_STACK_SIZE (1024 * 2)
#define BASE_STATUS_INTERVAL_MS     (500)

typedef esp_err_t (*control_serial_home_request_cb_t)(void *context);

/* ========== Function Declarations ========== */

/**
 * @brief Initialize serial control module
 * 
 * @return esp_err_t ESP_OK indicates success
 * 
 * @note Initializes UART and creates command receive task
 */
esp_err_t control_serial_init(void);

/**
 * @brief Publish the current homing state and gate motor commands
 *
 * @param status BASE_STATUS_HOMING, BASE_STATUS_READY, or
 *               BASE_STATUS_HOMING_FAILED
 *
 * @note Only READY allows angle and predefined-action commands. The status is
 *       emitted on CMD_BASE_MECHANICAL_STATUS (0x05); the independent
 *       CMD_BASE_HEARTBEAT (0x00, value 0x0001) is emitted as the link
 *       heartbeat.
 */
void control_serial_set_base_status(uint16_t status);

/**
 * @brief Register the application callback used by CMD_BASE_HOME_CONTROL
 *
 * The callback should schedule homing and return quickly. It is invoked from
 * the UART receive task after a checksum-valid request is decoded.
 */
void control_serial_set_home_request_callback(control_serial_home_request_cb_t callback,
                                              void *context);

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
esp_err_t control_serial_send_magnetic_switch_event(uint16_t event);

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
 * [7]:   Checksum (command code + data bytes sum)
 */
esp_err_t control_serial_send_action_complete(void);

/**
 * @brief Compatibility hook retained for the legacy bell/iphone/accessory
 *        examples.
 *
 * The old implementation periodically emitted a synthetic ``attached``
 * frame.  Command 0x00 is now the base homing/status heartbeat, so emitting
 * that legacy frame would be misinterpreted by the body firmware.  The
 * profile-specific magnetic switch task is the sole source of sensor events;
 * this function therefore intentionally does nothing.
 */
void control_serial_start_magnetic_detect_task(void);

/**
 * @brief Send calibration step notification
 * 
 * @param step_code Step code (MAG_SWITCH_CALIB_FIRST_DONE or MAG_SWITCH_CALIB_COMPLETE)
 * @return esp_err_t ESP_OK indicates success
 * 
 * Packet format example (second position complete):
 * AA 55 00 03 03 00 82 85
 * [0-1]: Header (0xAA 0x55)
 * [2-3]: Data length (0x00 0x03 = 3 bytes)
 * [4]:   Command code (0x03 = magnetic switch event)
 * [5-6]: Step code (0x00 0x82 = second position complete)
 * [7]:   Checksum (0x03 + 0x00 + 0x82 = 0x85)
 */
esp_err_t control_serial_send_magnetic_switch_calibration_step(uint16_t step_code);

typedef enum {
    MAGNETIC_SENSOR_KIND_UNKNOWN = 0,
    MAGNETIC_SENSOR_KIND_BMM150 = 1,
    MAGNETIC_SENSOR_KIND_QMC6309 = 2,
    MAGNETIC_SENSOR_KIND_SS49E = 3,
} control_serial_magnetic_sensor_kind_t;

/**
 * @brief Send one checksum-protected magnetic sensor sample.
 *
 * The three signed values are X/Y/Z for a digital magnetometer. For the
 * single-axis SS49E profile they are ADC raw, calibrated millivolts (or -1
 * when ADC calibration is unavailable), and zero. Samples are deliberately
 * separate from classified switch events on command 0x03.
 */
esp_err_t control_serial_send_magnetic_sensor_sample(
    control_serial_magnetic_sensor_kind_t sensor_kind,
    int16_t value_a,
    int16_t value_b,
    int16_t value_c
);

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
esp_err_t control_serial_print_magnetometer_data(int16_t x, int16_t y, int16_t z);

#ifdef __cplusplus
}
#endif
