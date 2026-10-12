/*
 * SPDX-FileCopyrightText: 2024-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdio.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_app_format.h"
#include "esp_ota_ops.h"
#include "iot_button.h"
#include "nvs_flash.h"
#include "control_serial.h"
#include "stepper_motor.h"
#include "magnetic_slide_switch.h"

static const char *TAG = "ESP-VoCat Rotating Base";

#define BASE_ANGLE_LIMIT_SWITCH_GPIO (GPIO_NUM_1)
#define BOOT_BUTTON_GPIO             (GPIO_NUM_9)
#define BASE_CALIBRATION_TASK_STACK_SIZE    (1024 * 3)

/*
 * The released base has one electrical limit switch and an open-loop
 * stepper.  Keep the seek direction and the mechanical assumptions in one
 * place so a real installation can be tuned without changing the state
 * machine below.  Negative angles are the direction used by the original
 * firmware to seek the limit; positive angles move away from that limit.
 */
#define BASE_HOMING_SEEK_DIRECTION       (-1.0f)
#define BASE_HOMING_STEP_DEGREES         (1.0f)
#define BASE_HOMING_RELEASE_DEGREES      (10.0f)
#define BASE_HOMING_CENTER_OFFSET_DEGREES (70.0f) /* magnitude, away from the limit */
#define BASE_HOMING_MAX_TRAVEL_DEGREES   (210.0f)
#define BASE_HOMING_TIMEOUT_MS           (5000)
#define BASE_HOMING_SWITCH_SETTLE_MS     (50)

static SemaphoreHandle_t s_limit_switch_semaphore = NULL;
static button_handle_t s_limit_switch_button = NULL;
static atomic_bool s_homing_active = ATOMIC_VAR_INIT(false);

static bool base_limit_switch_pressed(void)
{
    return gpio_get_level(BASE_ANGLE_LIMIT_SWITCH_GPIO) == 0;
}

static void base_homing_fail(const char *reason)
{
    ESP_LOGE(TAG, "Homing failed: %s", reason);
    stepper_motor_power_off();
    stepper_motor_motion_end();
    control_serial_set_base_status(BASE_STATUS_HOMING_FAILED);
    atomic_store_explicit(&s_homing_active, false, memory_order_release);
    ESP_LOGE(TAG, "Motor commands remain blocked because the physical angle is unknown");
}

/**
 * @brief Base angle limit switch event callback function
 * 
 * @param arg Button handle
 * @param data Button event data
 */
static void base_angle_limit_switch_event_cb(void *arg, void *data)
{
    button_event_t event = iot_button_get_event(arg);
    if (event == BUTTON_PRESS_DOWN && s_limit_switch_semaphore != NULL) {
        // iot_button callbacks run in task context, not an interrupt handler.
        xSemaphoreGive(s_limit_switch_semaphore);
    }
}

/**
 * @brief Boot button event callback function
 * 
 * @param arg Button handle
 * @param data Button event data
 */
static void boot_button_event_cb(void *arg, void *data)
{
    button_event_t event = iot_button_get_event(arg);
    if (event == BUTTON_LONG_PRESS_START) {
        ESP_LOGI(TAG, "Boot button long press detected, starting recalibration...");
        magnetic_slide_switch_start_recalibration();
    }
}

/**
 * @brief Initialize base angle limit switch
 */
static esp_err_t base_angle_limit_switch_init(void)
{
    if (s_limit_switch_button != NULL) {
        return ESP_OK;
    }

    button_config_t cfg = {
        .type = BUTTON_TYPE_GPIO,
        .long_press_time = CONFIG_BUTTON_LONG_PRESS_TIME_MS,
        .short_press_time = CONFIG_BUTTON_SHORT_PRESS_TIME_MS,
        .gpio_button_config = {
            .gpio_num = BASE_ANGLE_LIMIT_SWITCH_GPIO,
            .active_level = 0,
        },
    };
    button_handle_t button = iot_button_create(&cfg);
    if (button == NULL) {
        return ESP_ERR_NO_MEM;
    }

    esp_err_t ret = iot_button_register_cb(button, BUTTON_PRESS_DOWN,
                                           base_angle_limit_switch_event_cb, NULL);
    if (ret != ESP_OK) {
        iot_button_delete(button);
        return ret;
    }

    s_limit_switch_button = button;
    return ESP_OK;
}

/**
 * @brief Initialize boot button
 */
static void boot_button_init(void)
{
    button_handle_t boot_btn = NULL;

    button_config_t boot_cfg = {
        .type = BUTTON_TYPE_GPIO,
        .long_press_time = CONFIG_BUTTON_LONG_PRESS_TIME_MS,  // Use default long press time (usually 5000ms)
        .short_press_time = CONFIG_BUTTON_SHORT_PRESS_TIME_MS,
        .gpio_button_config = {
            .gpio_num = BOOT_BUTTON_GPIO,
            .active_level = 0,  // Active low
        },
    };
    boot_btn = iot_button_create(&boot_cfg);
    assert(boot_btn != NULL);
    iot_button_register_cb(boot_btn, BUTTON_LONG_PRESS_START, boot_button_event_cb, NULL);
    
    ESP_LOGI(TAG, "Boot button initialized on GPIO%d (long press to recalibrate)", BOOT_BUTTON_GPIO);
}

/**
 * @brief Base calibration task
 * 
 * @param arg Task parameter (unused)
 * 
 * @note Validates a possibly-active switch before seeking the limit.  This is
 *       important because a stuck-low GPIO must not be treated as a valid
 *       home and followed by the configured center offset move.
 * @note The single-switch hardware cannot measure both mechanical ends.  The
 *       center is therefore a configured offset from the verified limit.
 */
static void base_calibration_task(void *arg)
{
    (void)arg;
    ESP_LOGI(TAG, "Base calibration task started");
    control_serial_set_base_status(BASE_STATUS_HOMING);

    while (xSemaphoreTake(s_limit_switch_semaphore, 0) == pdTRUE) {
        // Discard notifications received before the homing sequence starts.
    }

    if (stepper_motor_motion_begin(portMAX_DELAY) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to acquire motor for base calibration");
        control_serial_set_base_status(BASE_STATUS_HOMING_FAILED);
        atomic_store_explicit(&s_homing_active, false, memory_order_release);
        vTaskDelete(NULL);
        return;
    }

    const TickType_t start_time = xTaskGetTickCount();
    const TickType_t timeout_ticks = pdMS_TO_TICKS(BASE_HOMING_TIMEOUT_MS);
    const bool initially_pressed = base_limit_switch_pressed();
    ESP_LOGI(TAG, "Homing limit GPIO%d initial level=%d (pressed=%s)",
             BASE_ANGLE_LIMIT_SWITCH_GPIO,
             gpio_get_level(BASE_ANGLE_LIMIT_SWITCH_GPIO),
             initially_pressed ? "yes" : "no");

    /* Drop notifications generated before the homing state machine began. */
    while (xSemaphoreTake(s_limit_switch_semaphore, 0) == pdTRUE) {
    }

    /* A valid pressed-at-boot state must first be released.  If the level
     * never changes, the input is stuck, miswired, or the switch is still
     * mechanically held; in all three cases a blind center move is unsafe. */
    if (initially_pressed) {
        ESP_LOGW(TAG,
                 "Limit is already active; releasing it by %.1f degrees before seeking",
                 BASE_HOMING_RELEASE_DEGREES);
        const float release_step = -BASE_HOMING_SEEK_DIRECTION * BASE_HOMING_STEP_DEGREES;
        const int release_steps = (int)(BASE_HOMING_RELEASE_DEGREES /
                                        BASE_HOMING_STEP_DEGREES + 0.5f);
        for (int step = 0; step < release_steps; ++step) {
            stepper_rotate_angle_with_accel(release_step, STEPPER_SPEED_SLOW);
            if (!base_limit_switch_pressed()) {
                break;
            }
            if (xTaskGetTickCount() - start_time >= timeout_ticks) {
                base_homing_fail("timeout while releasing an active limit switch");
                vTaskDelete(NULL);
                return;
            }
        }
        if (base_limit_switch_pressed()) {
            base_homing_fail("limit switch stayed active during release; check GPIO1 wiring or mechanism");
            vTaskDelete(NULL);
            return;
        }
        ESP_LOGI(TAG, "Limit switch released; starting a fresh seek");
    }

    bool limit_pressed = false;
    float travelled_degrees = 0.0f;
    const int max_seek_steps = (int)(BASE_HOMING_MAX_TRAVEL_DEGREES /
                                     BASE_HOMING_STEP_DEGREES + 0.5f);
    for (int step = 0; step < max_seek_steps; ++step) {
        if (xTaskGetTickCount() - start_time >= timeout_ticks) {
            base_homing_fail("limit switch was not reached before timeout");
            vTaskDelete(NULL);
            return;
        }
        if (xSemaphoreTake(s_limit_switch_semaphore, 0) == pdTRUE ||
            base_limit_switch_pressed()) {
            limit_pressed = true;
            break;
        }

        stepper_rotate_angle_with_accel(BASE_HOMING_SEEK_DIRECTION *
                                        BASE_HOMING_STEP_DEGREES,
                                        STEPPER_SPEED_SLOW);
        travelled_degrees += BASE_HOMING_STEP_DEGREES;
    }

    if (!limit_pressed && base_limit_switch_pressed()) {
        limit_pressed = true;
    }
    if (!limit_pressed) {
        base_homing_fail("limit switch was not reached within the configured travel");
        vTaskDelete(NULL);
        return;
    }

    const float center_move_degrees =
        -BASE_HOMING_SEEK_DIRECTION * BASE_HOMING_CENTER_OFFSET_DEGREES;
    ESP_LOGI(TAG, "Limit switch pressed after approximately %.1f degrees; moving %.1f degrees away to center",
             travelled_degrees, center_move_degrees);
    vTaskDelay(pdMS_TO_TICKS(BASE_HOMING_SWITCH_SETTLE_MS));
    stepper_rotate_angle_with_accel(center_move_degrees,
                                    STEPPER_SPEED_NORMAL);
    stepper_motor_power_off();
    stepper_motor_motion_end();
    control_serial_set_base_status(BASE_STATUS_READY);
    atomic_store_explicit(&s_homing_active, false, memory_order_release);
    ESP_LOGI(TAG, "Base calibration completed");
    vTaskDelete(NULL);
}

static esp_err_t base_start_homing(void)
{
    bool expected = false;
    if (!atomic_compare_exchange_strong_explicit(
            &s_homing_active, &expected, true,
            memory_order_acq_rel, memory_order_acquire
        )) {
        return ESP_ERR_INVALID_STATE;
    }

    control_serial_set_base_status(BASE_STATUS_HOMING);
    const BaseType_t created = xTaskCreate(
                                   base_calibration_task, "base_calibration_task",
                                   BASE_CALIBRATION_TASK_STACK_SIZE, NULL, 10, NULL
                               );
    if (created != pdPASS) {
        atomic_store_explicit(&s_homing_active, false, memory_order_release);
        control_serial_set_base_status(BASE_STATUS_HOMING_FAILED);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

static esp_err_t base_home_request_callback(void *context)
{
    (void)context;
    ESP_LOGI(TAG, "UART re-home request received");
    return base_start_homing();
}

void app_main(void)
{
    // Initialize NVS (for saving calibration data)
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
#if CONFIG_DESKBOT_MAGNETIC_SAMPLES_ONLY
        ESP_LOGE(TAG, "NVS needs recovery; samples-only diagnostic will not erase it");
        return;
#else
        // NVS partition is full or version mismatch, erase and reinitialize
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
#endif
    }
    ESP_ERROR_CHECK(ret);
    ESP_LOGI(TAG, "NVS initialized");
    
    // Create binary semaphore for limit switch notification
    s_limit_switch_semaphore = xSemaphoreCreateBinary();
    if (s_limit_switch_semaphore == NULL) {
        ESP_LOGE(TAG, "Failed to create limit switch semaphore");
        return;
    }
    
    stepper_motor_gpio_init();
    ret = base_angle_limit_switch_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize base angle limit switch: %s", esp_err_to_name(ret));
        stepper_motor_power_off();
        return;
    }
    boot_button_init();  // Initialize Boot button
    ret = control_serial_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize control UART: %s", esp_err_to_name(ret));
        stepper_motor_power_off();
        return;
    }
    control_serial_set_home_request_callback(base_home_request_callback, NULL);

    ret = base_start_homing();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create base calibration task; motor commands remain blocked");
        stepper_motor_power_off();
        return;
    }
    magnetic_slide_switch_start();
}
