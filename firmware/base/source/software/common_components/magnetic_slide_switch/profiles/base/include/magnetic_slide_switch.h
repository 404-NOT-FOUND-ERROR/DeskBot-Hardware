/*
 * SPDX-FileCopyrightText: 2024-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include "sdkconfig.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ========== General Task Configuration ========== */
#define MAGNETIC_SENSOR_TASK_STACK_SIZE     (1024 * 3)  /**< Task stack size */

/* ========== Conditional Compilation by Sensor Type ========== */

#ifdef CONFIG_SENSOR_LINEAR_HALL
/* ---------- Linear Hall Sensor Configuration ---------- */
#define HALL_SENSOR_ADC_CHANNEL             (ADC_CHANNEL_3)         /**< ADC channel */
#define HALL_SENSOR_ADC_ATTEN               ADC_ATTEN_DB_12         /**< ADC attenuation */
#define HALL_SENSOR_VOLTAGE_THRESHOLD       (25)                    /**< Voltage change threshold (mV) */
#define HALL_SENSOR_INITIAL_STATE_THRESHOLD (1400)                  /**< Initial state determination threshold (mV) */
#define HALL_SENSOR_SAMPLE_PERIOD_MS        (50)                    /**< Sampling period (ms) */

#elif defined(CONFIG_SENSOR_MAGNETOMETER)
/* ---------- Geomagnetic Sensor I2C Configuration ---------- */
#define I2C_MASTER_SCL_IO   (GPIO_NUM_3)            /**< I2C SCL pin */
#define I2C_MASTER_SDA_IO   (GPIO_NUM_2)            /**< I2C SDA pin */
#define I2C_MASTER_NUM      I2C_NUM_0               /**< I2C port number */
#define I2C_MASTER_FREQ_HZ  400000                  /**< I2C clock frequency (Hz) */

#ifdef CONFIG_SENSOR_BMM150
    /* BMM150 magnetometer specific configuration */
    #define MAGNETOMETER_I2C_ADDR           (0x10)      /**< BMM150 I2C address */
    #define MAGNETOMETER_CHIP_ID            (0x32)      /**< BMM150 chip ID */

    /* Sliding window configuration */
    #define MAG_WINDOW_SIZE                     (10)         /**< Sliding window size */
    #define MAG_SAMPLE_PERIOD_MS                (5)        /**< Sampling period (ms) - smaller for faster response */
    #define MAG_STABLE_THRESHOLD                (5)         /**< Consecutive N same states to be considered stable */
    
    /* Single click detection configuration */
    #define MAG_CLICK_SLOPE_CHANGE_THRESHOLD     (3)         /**< Click detection: minimum slope direction change count to trigger click event */
    #define MAG_CLICK_STABLE_RANGE              (20)        /**< Click detection: stable range for initial value (data variation within this range is considered stable) */
    #define MAG_CLICK_DURATION_MAX_MS           (500)       /**< Click detection: maximum duration in milliseconds (to distinguish from slide actions) */
    #define MAG_CLICK_MIN_SLOPE                 (50)        /**< Click detection: minimum slope value to consider as a valid direction change (avoid noise) */
    #define MAG_CLICK_Y_DROP_THRESHOLD          (-1500)     /**< Click detection: if Y axis drops below this value, trigger click event immediately */
    #define MAG_CLICK_Y_RECOVER_THRESHOLD       (-1200)     /**< Click detection: Y axis recovery threshold (if Y recovers above this value after dropping, trigger click) */
    #define MAG_CLICK_Y_RECOVER_RANGE           (100)       /**< Click detection: Y axis recovery range (if Y recovers to within this range of initial value, trigger click) */

    /* State threshold configuration: center value ± offset */
    #define MAG_STATE_REMOVED_CENTER        (709)          /**< REMOVED state center value */
    #define MAG_STATE_REMOVED_OFFSET        (50)           /**< REMOVED state offset */
    
    #define MAG_STATE_UP_CENTER             (1383)          /**< UP state center value */
    #define MAG_STATE_UP_OFFSET             (50)           /**< UP state offset */
    
    #define MAG_STATE_DOWN_CENTER           (2047)          /**< DOWN state center value */
    #define MAG_STATE_DOWN_OFFSET           (50)           /**< DOWN state offset */
    
    /* Pairing mode detection configuration */
    #define MAG_PAIRING_DROP_THRESHOLD      (100)           /**< Pairing mode drop threshold: when average drops more than this from REMOVED center */
    
    /* Fish attached detection configuration */
    #define FISH_FROM_UP_MIN_DIFF           (120)           /**< Minimum increase from REMOVED when fish placed from UP */
    #define FISH_FROM_UP_MAX_DIFF           (200)           /**< Maximum increase from REMOVED when fish placed from UP */
    
    /* Automatic calibration configuration */
    #define CALIBRATION_STABILITY_TIME_MS    (500)          /**< Time (ms) for value to be stable before recording calibration point */
    #define CALIBRATION_VALUE_DIFF_THRESHOLD (100)          /**< Minimum difference between calibration values to consider them different */
    
#elif defined(CONFIG_SENSOR_QMC6309)
    /* QMC6309 magnetometer specific configuration */
    /* The QMC6309 documentation publishes 0x7C as the 8-bit write address.
     * ESP-IDF's native master API takes the 7-bit address. */
    #define QMC6309_I2C_WRITE_ADDR          (0x7C)
    #define MAGNETOMETER_I2C_ADDR           (QMC6309_I2C_WRITE_ADDR >> 1)
    #define MAGNETOMETER_CHIP_ID            (0x90)      /**< QMC6309 chip ID */

    /* Sliding window configuration */
    #define MAG_WINDOW_SIZE                     (5)         /**< Sliding window size */
    #define MAG_SAMPLE_PERIOD_MS                (2)         /**< Sampling period (ms) - smaller for faster response */
    #define MAG_STABLE_THRESHOLD                (10)        /**< Consecutive N same states to be considered stable */
    
    /* Single click detection configuration */
    #define MAG_CLICK_X_DROP_THRESHOLD          (1000)       /**< Click detection: mag_x drop threshold (mag_x will drop by this amount during click) */
    #define MAG_CLICK_DURATION_MAX_MS           (250)       /**< Click detection: maximum duration in milliseconds (to distinguish from slide actions) */

    /* State threshold configuration: center value ± offset */
    #define MAG_STATE_REMOVED_CENTER        (1096)          /**< REMOVED state center value */
    #define MAG_STATE_REMOVED_OFFSET        (100)           /**< REMOVED state offset */
    
    #define MAG_STATE_UP_CENTER             (1275)          /**< UP state center value */
    #define MAG_STATE_UP_OFFSET             (100)           /**< UP state offset */
    
    #define MAG_STATE_DOWN_CENTER           (2598)          /**< DOWN state center value */
    #define MAG_STATE_DOWN_OFFSET           (100)           /**< DOWN state offset */
    
    /* Pairing mode detection configuration */
    #define MAG_PAIRING_DROP_THRESHOLD      (150)           /**< Pairing mode drop threshold: when average drops more than this from REMOVED center */
    
    /* Fish attached detection configuration */
    #define FISH_FROM_UP_MIN_DIFF           (150)           /**< Minimum increase from REMOVED when fish placed from UP */
    #define FISH_FROM_UP_MAX_DIFF           (200)           /**< Maximum increase from REMOVED when fish placed from UP */
    
    /* Automatic calibration configuration */
    #define CALIBRATION_STABILITY_TIME_MS    (500)         /**< Time (ms) for value to be stable before recording calibration point */
    #define CALIBRATION_VALUE_DIFF_THRESHOLD (100)          /**< Minimum difference between calibration values to consider them different */
    
#endif  // CONFIG_SENSOR_BMM150 / CONFIG_SENSOR_QMC6309

#endif  // CONFIG_SENSOR_LINEAR_HALL / CONFIG_SENSOR_MAGNETOMETER

/* ========== Magnetic Slide Switch Event Types ========== */
/**
 * @brief Magnetic slide switch event enumeration
 */
typedef enum {
    MAGNETIC_SLIDE_SWITCH_EVENT_INIT = 0,                   /**< Power-on initial state, no action determined */
    MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_DOWN = 1,             /**< Slider moved from up to down */
    MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_UP = 2,               /**< Slider moved from down to up */
    MAGNETIC_SLIDE_SWITCH_EVENT_REMOVE_FROM_UP = 3,         /**< Slider removed from up position */
    MAGNETIC_SLIDE_SWITCH_EVENT_REMOVE_FROM_DOWN = 4,       /**< Slider removed from down position */
    MAGNETIC_SLIDE_SWITCH_EVENT_PLACE_FROM_UP = 5,          /**< Slider placed from up position */
    MAGNETIC_SLIDE_SWITCH_EVENT_PLACE_FROM_DOWN = 6,        /**< Slider placed from down position */
    MAGNETIC_SLIDE_SWITCH_EVENT_SINGLE_CLICK = 7,           /**< Single click event */
    MAGNETIC_SLIDE_SWITCH_EVENT_FISH_ATTACHED = 8,          /**< Fish attached event */
    MAGNETIC_SLIDE_SWITCH_EVENT_FISH_DETACHED = 9,          /**< Fish detached event */
    MAGNETIC_SLIDE_SWITCH_EVENT_PAIRING = 10,               /**< Pairing mode event */
    MAGNETIC_SLIDE_SWITCH_EVENT_PAIRING_CANCELLED = 11,     /**< Pairing cancelled event */
} magnetic_slide_switch_event_t;

/* ========== Function Declarations ========== */

/**
 * @brief Start magnetic slide switch task
 * 
 * @note Automatically selects Hall sensor or geomagnetic sensor based on configuration
 * @note After starting, will automatically calibrate (if no saved calibration data exists)
 */
void magnetic_slide_switch_start(void);

/**
 * @brief Get current magnetic slide switch event
 * 
 * @return magnetic_slide_switch_event_t Current event type
 */
magnetic_slide_switch_event_t magnetic_slide_switch_get_event(void);

/**
 * @brief Trigger recalibration (no restart needed, takes effect immediately)
 * 
 * @note This function sets recalibration flag, sensor task will automatically re-enter calibration mode in next loop
 * @note After calibration completes, will overwrite previously saved calibration data
 */
void magnetic_slide_switch_start_recalibration(void);

#ifdef __cplusplus
}
#endif
