/*
 * SPDX-FileCopyrightText: 2024-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "bmm150.h"
#include "bmm150_defs.h"
#include "driver/i2c_master.h"
#include "magnetic_slide_switch.h"
#include "control_serial.h"

static const char *TAG = "Magnetic Slide Switch";

/* ========== NVS Storage Configuration ========== */
#define NVS_NAMESPACE       "mag_calib"     /**< NVS namespace */
#define NVS_KEY_REMOVED     "removed"       /**< REMOVED state center value key name */
#define NVS_KEY_UP          "up"            /**< UP state center value key name */
#define NVS_KEY_DOWN        "down"          /**< DOWN state center value key name */
#define NVS_KEY_CALIBRATED  "calibrated"    /**< Calibration flag key name */
/* Center values are sensor-specific.  Tag new records so a calibration made
 * with BMM150 cannot be silently reused by a QMC6309 build (or vice versa). */
#define NVS_KEY_FORMAT_VERSION "format_v"
#define NVS_KEY_SENSOR_ID      "sensor_id"
#define MAG_CALIBRATION_FORMAT_VERSION 2
#if CONFIG_SENSOR_QMC6309
#define MAG_CALIBRATION_SENSOR_ID 2
#elif CONFIG_SENSOR_BMM150
#define MAG_CALIBRATION_SENSOR_ID 1
#else
#define MAG_CALIBRATION_SENSOR_ID 0
#endif

/* ========== Global Variables ========== */
static magnetic_slide_switch_event_t s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_INIT;

#if CONFIG_SENSOR_LINEAR_HALL
    static int adc_raw[2][10];
    static int voltage[2][10];
    static int s_last_hall_voltage = 0;
    static bool s_first_hall_reading = true;
#endif

#if CONFIG_SENSOR_BMM150
static struct bmm150_dev s_bmm150 = { 0 };
static uint8_t s_bmm150_addr = BMM150_DEFAULT_I2C_ADDRESS;  /**< BMM150 default I2C address */
/* Used by the read-only identity probe when the selected address is absent. */
void bmm150_coines_deinit(void);
#endif

#ifdef CONFIG_SENSOR_MAGNETOMETER
/** Native ESP-IDF 5.5 I2C bus/device handles. */
static i2c_master_bus_handle_t s_i2c_bus = NULL;
static i2c_master_dev_handle_t s_i2c_dev = NULL;

#define MAGNETOMETER_I2C_TIMEOUT_MS (100)

static esp_err_t magnetometer_i2c_bus_init(void)
{
    if (s_i2c_bus != NULL) {
        return ESP_OK;
    }

    const i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_MASTER_NUM,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .scl_io_num = I2C_MASTER_SCL_IO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .intr_priority = 0,
        .trans_queue_depth = 0,
        .flags.enable_internal_pullup = true,
    };
    const esp_err_t ret = i2c_new_master_bus(&bus_config, &s_i2c_bus);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Native I2C bus init failed: %s", esp_err_to_name(ret));
        s_i2c_bus = NULL;
        return ret;
    }

    ESP_LOGI(TAG, "Native I2C master ready (port=%d SDA=%d SCL=%d clock=%dHz)",
             I2C_MASTER_NUM, I2C_MASTER_SDA_IO, I2C_MASTER_SCL_IO,
             I2C_MASTER_FREQ_HZ);
    return ESP_OK;
}

static esp_err_t magnetometer_i2c_device_add(uint8_t address,
                                              i2c_master_dev_handle_t *device)
{
    if (s_i2c_bus == NULL || device == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    const i2c_device_config_t device_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = address,
        .scl_speed_hz = I2C_MASTER_FREQ_HZ,
    };
    return i2c_master_bus_add_device(s_i2c_bus, &device_config, device);
}

static esp_err_t magnetometer_i2c_read(i2c_master_dev_handle_t device,
                                       uint8_t reg, uint8_t *data, size_t len)
{
    if (device == NULL || data == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    return i2c_master_transmit_receive(device, &reg, 1, data, len,
                                       MAGNETOMETER_I2C_TIMEOUT_MS);
}

static esp_err_t magnetometer_i2c_write(i2c_master_dev_handle_t device,
                                        uint8_t reg, const uint8_t *data,
                                        size_t len)
{
    if (device == NULL || data == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    i2c_master_transmit_multi_buffer_info_t buffers[] = {
        {.write_buffer = &reg, .buffer_size = 1},
        {.write_buffer = data, .buffer_size = len},
    };
    return i2c_master_multi_buffer_transmit(device, buffers,
                                            sizeof(buffers) / sizeof(buffers[0]),
                                            MAGNETOMETER_I2C_TIMEOUT_MS);
}

static void magnetometer_i2c_deinit(void)
{
    if (s_i2c_dev != NULL) {
        const esp_err_t ret = i2c_master_bus_rm_device(s_i2c_dev);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "Native I2C device removal failed: %s", esp_err_to_name(ret));
        }
        s_i2c_dev = NULL;
    }
    if (s_i2c_bus != NULL) {
        const esp_err_t ret = i2c_del_master_bus(s_i2c_bus);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "Native I2C bus removal failed: %s", esp_err_to_name(ret));
        }
        s_i2c_bus = NULL;
    }
}

/** Shared magnetometer data structure */
typedef struct {
    int16_t x;  /**< X-axis magnetic field value */
    int16_t y;  /**< Y-axis magnetic field value */
    int16_t z;  /**< Z-axis magnetic field value */
    bool valid; /**< Data validity flag */
} magnetometer_data_t;

static magnetometer_data_t s_mag_data = {0};  /**< Shared magnetometer data */
static SemaphoreHandle_t s_mag_data_mutex = NULL;  /**< Mutex for protecting shared data */
static SemaphoreHandle_t s_calibration_complete_sem = NULL;  /**< Semaphore to signal calibration completion */
/* Startup is two-phase: the reader first proves that the selected sensor is
 * present, then calibration/event tasks are allowed to run.  Keeping this
 * state separate from the calibration semaphore prevents a missing sensor
 * from leaving app_main blocked forever. */
static SemaphoreHandle_t s_sensor_init_complete_sem = NULL;
static atomic_bool s_magnetometer_init_failed = ATOMIC_VAR_INIT(false);
static atomic_bool s_magnetometer_stop_requested = ATOMIC_VAR_INIT(false);
static atomic_bool s_sensor_init_signaled = ATOMIC_VAR_INIT(false);
static atomic_bool s_magnetometer_ready = ATOMIC_VAR_INIT(false);

#define MAGNETOMETER_INIT_TIMEOUT_MS (3000)

static void magnetometer_signal_init_result(bool ready, esp_err_t reason)
{
    /* If startup already timed out, a late response must not resurrect a
     * reader that the caller has deliberately disabled. */
    if (ready && atomic_load_explicit(&s_magnetometer_stop_requested, memory_order_acquire)) {
        ready = false;
        reason = ESP_ERR_TIMEOUT;
    }
    atomic_store_explicit(&s_magnetometer_ready, ready, memory_order_release);
    atomic_store_explicit(&s_magnetometer_init_failed, !ready, memory_order_release);

    if (!ready) {
        ESP_LOGE(TAG, "Magnetometer unavailable; magnetic switch events disabled (%s)",
                 esp_err_to_name(reason));
    } else {
        ESP_LOGI(TAG, "Magnetometer identity verified; magnetic switch input enabled");
    }

    if (s_sensor_init_complete_sem != NULL &&
        !atomic_exchange_explicit(&s_sensor_init_signaled, true, memory_order_acq_rel)) {
        xSemaphoreGive(s_sensor_init_complete_sem);
    }
}

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
/* Probe both supported digital magnetometers before selecting the compile-time
 * driver.  This is read-only and gives the first hardware run an unambiguous
 * chip identity even when the selected driver is the wrong one. */
static bool diagnostic_probe_magnetometer(const char *sensor, uint8_t address,
                                          uint8_t id_register, uint8_t expected_id)
{
    if (s_i2c_bus == NULL) {
        ESP_LOGI(TAG, "[diag] probe sensor=%s i2c_addr=0x%02X bus=unavailable",
                 sensor, address);
        return false;
    }

    const esp_err_t ack_ret = i2c_master_probe(s_i2c_bus, address,
                                               MAGNETOMETER_I2C_TIMEOUT_MS);
    if (ack_ret != ESP_OK) {
        ESP_LOGI(TAG,
                 "[diag] probe sensor=%s i2c_addr=0x%02X ack=%s match=no",
                 sensor, address, esp_err_to_name(ack_ret));
        return false;
    }

    i2c_master_dev_handle_t probe = NULL;
    const esp_err_t add_ret = magnetometer_i2c_device_add(address, &probe);
    if (add_ret != ESP_OK) {
        ESP_LOGI(TAG,
                 "[diag] probe sensor=%s i2c_addr=0x%02X ack=yes add=%s match=unknown",
                 sensor, address, esp_err_to_name(add_ret));
        return false;
    }
    uint8_t chip_id = 0;
    const esp_err_t read_ret = magnetometer_i2c_read(probe, id_register, &chip_id, 1);
    const bool matched = read_ret == ESP_OK && chip_id == expected_id;
    ESP_LOGI(TAG,
             "[diag] probe sensor=%s i2c_addr=0x%02X ack=yes id_reg=0x%02X read=%s chip_id=0x%02X expected=0x%02X match=%s",
             sensor, address, id_register, esp_err_to_name(read_ret), chip_id,
             expected_id, matched ? "yes" : "no");
    (void)i2c_master_bus_rm_device(probe);
    return matched;
}

static void diagnostic_probe_magnetometers(void)
{
    const bool bmm150_found =
        diagnostic_probe_magnetometer("BMM150", 0x10, 0x40, 0x32);
    /* QMC6309's published 0x7C value is the 8-bit write address. */
    const bool qmc6309_found =
        diagnostic_probe_magnetometer("QMC6309", 0x3E, 0x00, 0x90);

    if (!bmm150_found && !qmc6309_found) {
        ESP_LOGW(TAG, "[diag] known magnetometer IDs not found; scanning ACK addresses");
        unsigned int ack_count = 0;
        for (uint8_t address = 0x01; address <= 0x7E; ++address) {
            if (i2c_master_probe(s_i2c_bus, address, 10) == ESP_OK) {
                ESP_LOGI(TAG, "[diag] i2c_ack address=0x%02X", address);
                ++ack_count;
            }
        }
        ESP_LOGI(TAG, "[diag] i2c_scan_complete ack_count=%u", ack_count);
    }
}
#endif
#endif

/* ========== Linear Hall Sensor Related Functions ========== */

#if CONFIG_SENSOR_LINEAR_HALL
/**
 * @brief ADC calibration initialization
 * 
 * @param unit ADC unit
 * @param channel ADC channel
 * @param atten ADC attenuation
 * @param out_handle Output calibration handle
 * @return true Calibration successful
 * @return false Calibration failed
 */
static bool example_adc_calibration_init(adc_unit_t unit, adc_channel_t channel, adc_atten_t atten, adc_cali_handle_t *out_handle)
{
    adc_cali_handle_t handle = NULL;
    esp_err_t ret = ESP_FAIL;
    bool calibrated = false;

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    if (!calibrated) {
        ESP_LOGI(TAG, "calibration scheme version is %s", "Curve Fitting");
        adc_cali_curve_fitting_config_t cali_config = {
            .unit_id = unit,
            .chan = channel,
            .atten = atten,
            .bitwidth = ADC_BITWIDTH_DEFAULT,
        };
        ret = adc_cali_create_scheme_curve_fitting(&cali_config, &handle);
        if (ret == ESP_OK) {
            calibrated = true;
        }
    }
#endif

#if ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    if (!calibrated) {
        ESP_LOGI(TAG, "calibration scheme version is %s", "Line Fitting");
        adc_cali_line_fitting_config_t cali_config = {
            .unit_id = unit,
            .atten = atten,
            .bitwidth = ADC_BITWIDTH_DEFAULT,
        };
        ret = adc_cali_create_scheme_line_fitting(&cali_config, &handle);
        if (ret == ESP_OK) {
            calibrated = true;
        }
    }
#endif

    *out_handle = handle;
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "Calibration Success");
    } else if (ret == ESP_ERR_NOT_SUPPORTED || !calibrated) {
        ESP_LOGW(TAG, "eFuse not burnt, skip software calibration");
    } else {
        ESP_LOGE(TAG, "Invalid arg or no memory");
    }

    return calibrated;
}

/**
 * @brief Hall sensor read task
 * 
 * @param arg Task parameter (unused)
 * 
 * @note Acquires Hall sensor voltage via ADC, determines slider movement events based on voltage changes
 * @note Sampling period defined by HALL_SENSOR_SAMPLE_PERIOD_MS
 */
static void hall_sensor_read_task(void *arg)
{
    ESP_LOGI(TAG, "Hall sensor read task started");
    //-------------ADC1 Init---------------//
    adc_oneshot_unit_handle_t adc1_handle;
    adc_oneshot_unit_init_cfg_t init_config1 = {
        .unit_id = ADC_UNIT_1,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_config1, &adc1_handle));

    //-------------ADC1 Config---------------//
    adc_oneshot_chan_cfg_t config = {
        .atten = HALL_SENSOR_ADC_ATTEN,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc1_handle, HALL_SENSOR_ADC_CHANNEL, &config));

    //-------------ADC1 Calibration Init---------------//
    adc_cali_handle_t adc1_cali_chan0_handle = NULL;
    bool do_calibration1_chan0 = example_adc_calibration_init(ADC_UNIT_1, HALL_SENSOR_ADC_CHANNEL, HALL_SENSOR_ADC_ATTEN, &adc1_cali_chan0_handle);
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    TickType_t last_diag_tick = 0;
#endif
    TickType_t last_telemetry_tick = 0;

    while (1) {
        ESP_ERROR_CHECK(adc_oneshot_read(adc1_handle, HALL_SENSOR_ADC_CHANNEL, &adc_raw[0][0]));
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
        int diag_voltage = -1;
        if (do_calibration1_chan0) {
            (void)adc_cali_raw_to_voltage(adc1_cali_chan0_handle, adc_raw[0][0], &diag_voltage);
        }
        const TickType_t now = xTaskGetTickCount();
        if (now - last_diag_tick >= pdMS_TO_TICKS(500)) {
            ESP_LOGI(TAG, "[diag] sample sensor=SS49E gpio=5 raw=%d voltage_mV=%d",
                     adc_raw[0][0], diag_voltage);
            last_diag_tick = now;
        }
#endif
        if (do_calibration1_chan0) {
            ESP_ERROR_CHECK(adc_cali_raw_to_voltage(adc1_cali_chan0_handle, adc_raw[0][0], &voltage[0][0]));
            
            // Check voltage changes to determine magnetic switch events
            if (!s_first_hall_reading) {  // Not first reading
                int voltage_change = voltage[0][0] - s_last_hall_voltage;
                
                // Voltage decreases and change exceeds threshold, slider moved down
                if (voltage_change < -HALL_SENSOR_VOLTAGE_THRESHOLD && s_slider_state != MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_DOWN) {
                    s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_DOWN;
                    ESP_LOGI(TAG, "Hall switch: SLIDE_DOWN - Voltage dropped by %d mV (from %d to %d)", 
                             -voltage_change, s_last_hall_voltage, voltage[0][0]);
                    control_serial_send_magnetic_switch_event(s_slider_state);
                }
                // Voltage increases and change exceeds threshold, slider moved up
                else if (voltage_change > HALL_SENSOR_VOLTAGE_THRESHOLD && s_slider_state == MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_DOWN) {
                    s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_UP;
                    ESP_LOGI(TAG, "Hall switch: SLIDE_UP - Voltage increased by %d mV (from %d to %d)", 
                             voltage_change, s_last_hall_voltage, voltage[0][0]);
                    control_serial_send_magnetic_switch_event(s_slider_state);
                }
            } else {
                s_first_hall_reading = false;
                
                // Determine initial state based on first reading voltage (initial state doesn't send event)
                if (voltage[0][0] < HALL_SENSOR_INITIAL_STATE_THRESHOLD) {
                    s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_DOWN;
                    ESP_LOGI(TAG, "First reading: Slider initially at DOWN position (voltage=%d < %d mV)", 
                             voltage[0][0], HALL_SENSOR_INITIAL_STATE_THRESHOLD);
                } else {
                    s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_UP;
                    ESP_LOGI(TAG, "First reading: Slider initially at UP position (voltage=%d >= %d mV)", 
                             voltage[0][0], HALL_SENSOR_INITIAL_STATE_THRESHOLD);
                }
            }
            
            // Update last voltage value
            s_last_hall_voltage = voltage[0][0];
            
            // ESP_LOGI(TAG, "Hall sensor voltage: %d mV", voltage[0][0]);
        }
        const TickType_t telemetry_now = xTaskGetTickCount();
        if (telemetry_now - last_telemetry_tick >= pdMS_TO_TICKS(100)) {
            const int sample_mv = do_calibration1_chan0 ? voltage[0][0] : -1;
            (void)control_serial_send_magnetic_sensor_sample(
                MAGNETIC_SENSOR_KIND_SS49E,
                (int16_t)adc_raw[0][0],
                (int16_t)sample_mv,
                0
            );
            last_telemetry_tick = telemetry_now;
        }
        vTaskDelay(pdMS_TO_TICKS(HALL_SENSOR_SAMPLE_PERIOD_MS));
    }
}
#endif  // CONFIG_SENSOR_LINEAR_HALL

/* ========== BMM150 Geomagnetic Sensor Related Functions ========== */

#ifdef CONFIG_SENSOR_BMM150

struct bmm150_settings settings = { 0 };

/**
 * @brief BMM150 I2C read function
 * 
 * @param reg_addr Register address
 * @param reg_data Read data buffer
 * @param length Read length
 * @param intf_ptr Interface pointer (device address)
 * @return BMM150_INTF_RET_TYPE Return status
 */
static BMM150_INTF_RET_TYPE bmm150_i2c_read(uint8_t reg_addr, uint8_t *reg_data, uint32_t length, void *intf_ptr)
{
    // Check if I2C device handle is valid
    if (s_i2c_dev == NULL) {
        ESP_LOGE(TAG, "I2C device not initialized");
        return BMM150_E_COM_FAIL;
    }

    esp_err_t ret = magnetometer_i2c_read(s_i2c_dev, reg_addr, reg_data, length);
    return (ret == ESP_OK) ? BMM150_INTF_RET_SUCCESS : BMM150_E_COM_FAIL;
}

/**
 * @brief BMM150 I2C write function
 * 
 * @param reg_addr Register address
 * @param reg_data Write data buffer
 * @param length Write length
 * @param intf_ptr Interface pointer (device address)
 * @return BMM150_INTF_RET_TYPE Return status
 */
static BMM150_INTF_RET_TYPE bmm150_i2c_write(uint8_t reg_addr, const uint8_t *reg_data, uint32_t length, void *intf_ptr)
{
    // Check if I2C device handle is valid
    if (s_i2c_dev == NULL) {
        ESP_LOGE(TAG, "I2C device not initialized");
        return BMM150_E_COM_FAIL;
    }

    esp_err_t ret = magnetometer_i2c_write(s_i2c_dev, reg_addr, reg_data, length);
    return (ret == ESP_OK) ? BMM150_INTF_RET_SUCCESS : BMM150_E_COM_FAIL;
}

/**
 * @brief BMM150 delay function
 * 
 * @param period Delay time (microseconds)
 * @param intf_ptr Interface pointer (unused)
 */
static void bmm150_delay(uint32_t period, void *intf_ptr)
{
    (void)intf_ptr;
    esp_rom_delay_us(period);
}

/**
 * @brief BMM150 interface initialization
 * 
 * @param dev BMM150 device structure pointer
 * @param device_addr I2C device address
 * @return int8_t BMM150_OK indicates success
 * 
 * @note Creates both I2C bus and device handles
 */
int8_t bmm150_interface_init(struct bmm150_dev *dev, uint8_t device_addr)
{    
    if (dev == NULL) {
        return BMM150_E_NULL_PTR;
    }
    
    const esp_err_t bus_ret = magnetometer_i2c_bus_init();
    if (bus_ret != ESP_OK) {
        return BMM150_E_COM_FAIL;
    }

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    diagnostic_probe_magnetometers();
#endif
    
    /* Create I2C device handle */
    const esp_err_t add_ret = magnetometer_i2c_device_add(device_addr, &s_i2c_dev);
    if (add_ret != ESP_OK) {
        ESP_LOGE(TAG, "I2C device create failed for address 0x%02X: %s",
                 device_addr, esp_err_to_name(add_ret));
        magnetometer_i2c_deinit();
        return BMM150_E_COM_FAIL;
    }

    /* In suspend mode the identity register may read as zero. This read is
     * diagnostic only: Bosch bmm150_init powers up, waits, then validates ID. */
    uint8_t chip_id = 0;
    const esp_err_t id_ret = magnetometer_i2c_read(s_i2c_dev,
                                                   BMM150_REG_CHIP_ID,
                                                   &chip_id, 1);
    if (id_ret != ESP_OK) {
        ESP_LOGE(TAG,
                 "BMM150 probe failed at 0x%02X: read=%s chip_id=0x%02X expected=0x%02X",
                 device_addr, esp_err_to_name(id_ret), chip_id, BMM150_CHIP_ID);
        bmm150_coines_deinit();
        return id_ret == ESP_OK ? BMM150_E_DEV_NOT_FOUND : BMM150_E_COM_FAIL;
    }
    ESP_LOGI(TAG, "BMM150 pre-wake ID at 0x%02X: 0x%02X (not an identity verdict)", device_addr, chip_id);
    
    ESP_LOGI(TAG, "I2C interface initialized (bus + device at 0x%02X)", device_addr);
    
    return BMM150_OK;
}

/**
 * @brief BMM150 interface deinitialization
 */
void bmm150_coines_deinit(void)
{
    magnetometer_i2c_deinit();
    ESP_LOGI(TAG, "Interface deinitialized");
}

/**
 * @brief BMM150 standalone initialization (try multiple addresses)
 * 
 * @return int8_t BMM150_OK indicates success
 * 
 * @note Will attempt to find BMM150 device from address list
 * @note After success, will be configured to regular mode (REGULAR mode)
 */
static int8_t bmm150_init_standalone(void)
{
    const uint8_t addr_candidates[] = { MAGNETOMETER_I2C_ADDR };
    int8_t rslt = BMM150_E_DEV_NOT_FOUND;

    for (size_t i = 0; i < sizeof(addr_candidates); ++i) {
        uint8_t addr = addr_candidates[i];

        rslt = bmm150_interface_init(&s_bmm150, addr);
        if (rslt != BMM150_OK) {
            ESP_LOGE(TAG, "Interface init failed for address 0x%02X", addr);
            continue;
        }

        s_bmm150_addr = addr;
        s_bmm150.intf  = BMM150_I2C_INTF;
        s_bmm150.read  = bmm150_i2c_read;
        s_bmm150.write = bmm150_i2c_write;
        s_bmm150.delay_us = bmm150_delay;
        s_bmm150.intf_ptr = (void *)&s_bmm150_addr;

        rslt = bmm150_init(&s_bmm150);
        ESP_LOGI(TAG, "Init at 0x%02X -> rslt=%d, chip_id=0x%02X",
                 addr, rslt, s_bmm150.chip_id);

        if (rslt != BMM150_OK || s_bmm150.chip_id != MAGNETOMETER_CHIP_ID) {
            if (rslt == BMM150_OK) {
                rslt = BMM150_E_DEV_NOT_FOUND;
            }
            bmm150_coines_deinit();
            continue;
        }

        /* ----------  关键修改开始  ---------- */
        /* 1. 软复位 + 等待 */
        if (rslt != BMM150_OK) {
            bmm150_soft_reset(&s_bmm150);
            s_bmm150.delay_us(BMM150_START_UP_TIME * 1000, s_bmm150.intf_ptr);
        }

        /* 2. 手动写寄存器：最小重复次数 → 最快转换时间 */
        uint8_t rep_xy = 0x00;   /* nXY = 1 */
        uint8_t rep_z  = 0x00;   /* nZ  = 1 */
        rslt = bmm150_set_regs(BMM150_REG_REP_XY, &rep_xy, 1, &s_bmm150);
        rslt |= bmm150_set_regs(BMM150_REG_REP_Z, &rep_z, 1, &s_bmm150);
        if (rslt != BMM150_OK) {
            ESP_LOGE(TAG, "Set repetition failed");
            bmm150_coines_deinit();
            continue;
        }

        /* 3. 切到 Forced 模式，其余保持默认 */
        settings.pwr_mode = BMM150_POWERMODE_FORCED;  /* 关键！ */
        rslt = bmm150_set_op_mode(&settings, &s_bmm150);
        if (rslt != BMM150_OK) {
            ESP_LOGE(TAG, "Set FORCED mode failed");
            bmm150_coines_deinit();
            continue;
        }

        /* ----------  关键修改结束  ---------- */

        ESP_LOGI(TAG, "BMM150 initialized in FORCED mode (300 Hz capable) at 0x%02X", addr);
        return BMM150_OK;
    }
    return rslt;
}

#elif defined(CONFIG_SENSOR_QMC6309)

/* ========== QMC6309 Geomagnetic Sensor Related Functions ========== */

/**
 * @brief QMC6309 interface initialization
 * 
 * @return esp_err_t ESP_OK indicates success
 */
static esp_err_t qmc6309_interface_init(void)
{
    const esp_err_t ret = magnetometer_i2c_bus_init();
    if (ret != ESP_OK) {
        return ret;
    }

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    diagnostic_probe_magnetometers();
#endif
    ESP_LOGI(TAG, "I2C interface initialized");
    return ESP_OK;
}

/**
 * @brief QMC6309 write register
 * 
 * @param reg Register address
 * @param val Value to write
 * @return esp_err_t ESP_OK indicates success
 */
static esp_err_t qmc_write_reg(uint8_t reg, uint8_t val)
{
    return magnetometer_i2c_write(s_i2c_dev, reg, &val, 1);
}

/**
 * @brief QMC6309 read register
 * 
 * @param reg Register address
 * @param data Read data buffer
 * @param len Read length
 * @return esp_err_t ESP_OK indicates success
 */
static esp_err_t qmc_read_reg(uint8_t reg, uint8_t *data, size_t len)
{
    return magnetometer_i2c_read(s_i2c_dev, reg, data, len);
}

/**
 * @brief QMC6309 initialization
 * 
 * @return esp_err_t ESP_OK indicates success
 * 
 * @note Configured as continuous mode, ±32G range, ODR=200Hz, OSR=8/8
 */
static esp_err_t qmc6309_init(void)
{
    // Create I2C device handle
    esp_err_t ret = magnetometer_i2c_device_add(MAGNETOMETER_I2C_ADDR,
                                                &s_i2c_dev);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create I2C device for QMC6309: %s",
                 esp_err_to_name(ret));
        return ret;
    }

    /* Identity read is deliberately performed before any configuration
     * writes.  A missing QMC6309 must return an error, never hit
     * ESP_ERROR_CHECK and reboot the whole base. */
    uint8_t chip_id = 0;
    ret = qmc_read_reg(0x00, &chip_id, 1);
    if (ret != ESP_OK || chip_id != MAGNETOMETER_CHIP_ID) {
        ESP_LOGE(TAG,
                 "QMC6309 probe failed at 0x%02X: read=%s chip_id=0x%02X expected=0x%02X",
                 MAGNETOMETER_I2C_ADDR, esp_err_to_name(ret), chip_id,
                 MAGNETOMETER_CHIP_ID);
        return ret == ESP_OK ? ESP_ERR_NOT_FOUND : ret;
    }
    ESP_LOGI(TAG, "QMC6309 probe OK at 0x%02X (chip_id=0x%02X)",
             MAGNETOMETER_I2C_ADDR, chip_id);

    /* Soft reset */
    ret = qmc_write_reg(0x0B, 0x80);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "QMC6309 soft-reset assert failed: %s", esp_err_to_name(ret));
        return ret;
    }
    vTaskDelay(pdMS_TO_TICKS(10));
    ret = qmc_write_reg(0x0B, 0x00);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "QMC6309 soft-reset release failed: %s", esp_err_to_name(ret));
        return ret;
    }
    vTaskDelay(pdMS_TO_TICKS(10));

    /* Continuous mode, ±32G, ODR=200Hz, OSR=8/8 */
    ret = qmc_write_reg(0x0B, 0x00);  // RNG=00
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "QMC6309 range configuration failed: %s", esp_err_to_name(ret));
        return ret;
    }
    ret = qmc_write_reg(0x0A, 0x63);  // MODE=11, OSR=8
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "QMC6309 mode configuration failed: %s", esp_err_to_name(ret));
        return ret;
    }
    
    ESP_LOGI(TAG, "QMC6309 initialized successfully");
    return ESP_OK;
}

/**
 * @brief QMC6309 read 3-axis raw data
 * 
 * @param x X-axis data
 * @param y Y-axis data
 * @param z Z-axis data
 * @return esp_err_t ESP_OK indicates success
 * 
 * @note Reads data after waiting for data ready
 */
static esp_err_t qmc6309_read_raw(int16_t *x, int16_t *y, int16_t *z)
{
    if (x == NULL || y == NULL || z == NULL || s_i2c_dev == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t status = 0;
    /* Wait for data ready, but bound the wait so a disconnected sensor cannot
     * pin the reader task forever. */
    bool data_ready = false;
    for (int attempt = 0; attempt < 20; ++attempt) {
        esp_err_t ret = qmc_read_reg(0x09, &status, 1);
        if (ret != ESP_OK) {
            return ret;
        }
        if ((status & 0x01) != 0) {
            data_ready = true;
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    if (!data_ready) {
        return ESP_ERR_TIMEOUT;
    }
    
    uint8_t data[6];
    esp_err_t ret = qmc_read_reg(0x01, data, 6);
    if (ret != ESP_OK) {
        return ret;
    }
    *x = (int16_t)(data[0] | (data[1] << 8));
    *y = (int16_t)(data[2] | (data[3] << 8));
    *z = (int16_t)(data[4] | (data[5] << 8));
    return ESP_OK;
}

/**
 * @brief QMC6309 interface deinitialization
 * 
 * @return esp_err_t ESP_OK indicates success
 */
static esp_err_t qmc6309_interface_deinit(void)
{
    magnetometer_i2c_deinit();
    ESP_LOGI(TAG, "QMC6309 interface deinitialized");
    return ESP_OK;
}
#endif  // CONFIG_SENSOR_BMM150 / CONFIG_SENSOR_QMC6309

#if CONFIG_SENSOR_MAGNETOMETER

/* ========== Geomagnetic Sensor State and Calibration Related ========== */

/**
 * @brief Magnetic field position enumeration
 */
typedef enum {
    MAG_POSITION_UNKNOWN = 0,   /**< Unknown position */
    MAG_POSITION_REMOVED,       /**< Removed */
    MAG_POSITION_UP,            /**< Up position */
    MAG_POSITION_DOWN           /**< Down position */
} mag_position_t;

/**
 * @brief Calibration state enumeration
 */
typedef enum {
    CALIBRATION_IDLE = 0,               /**< Idle state */
    CALIBRATION_DETECTING_FIRST,        /**< Detecting first stable position (2s stable) */
    CALIBRATION_WAITING_CHANGE_1,      /**< Waiting for change from first position */
    CALIBRATION_DETECTING_SECOND,       /**< Detecting second stable position (2s stable, diff > threshold) */
    CALIBRATION_WAITING_CHANGE_2,       /**< Waiting for change from second position */
    CALIBRATION_DETECTING_THIRD,        /**< Detecting third stable position (2s stable, diff > threshold) */
    CALIBRATION_COMPLETED               /**< Calibration completed */
} calibration_state_t;

/** Calibrated center values (global variables) */
static int16_t s_calibrated_removed_center = MAG_STATE_REMOVED_CENTER;
static int16_t s_calibrated_up_center = MAG_STATE_UP_CENTER;
static int16_t s_calibrated_down_center = MAG_STATE_DOWN_CENTER;

/** Recalibration flag (global variable) */
static volatile bool s_request_recalibration = false;

/**
 * @brief Save calibration data to NVS
 * 
 * @param removed_center REMOVED state center value
 * @param up_center UP state center value
 * @param down_center DOWN state center value
 * @return esp_err_t ESP_OK indicates success
 * 
 * @note Calibration data saved in Flash, survives power loss
 */
static esp_err_t save_calibration_to_nvs(int16_t removed_center, int16_t up_center, int16_t down_center)
{
    nvs_handle_t nvs_handle;
    esp_err_t err;
    
    // Open NVS
    err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error opening NVS handle: %s", esp_err_to_name(err));
        return err;
    }
    
    // Save three center values
    err = nvs_set_i16(nvs_handle, NVS_KEY_REMOVED, removed_center);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error saving removed_center: %s", esp_err_to_name(err));
        nvs_close(nvs_handle);
        return err;
    }
    
    err = nvs_set_i16(nvs_handle, NVS_KEY_UP, up_center);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error saving up_center: %s", esp_err_to_name(err));
        nvs_close(nvs_handle);
        return err;
    }
    
    err = nvs_set_i16(nvs_handle, NVS_KEY_DOWN, down_center);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error saving down_center: %s", esp_err_to_name(err));
        nvs_close(nvs_handle);
        return err;
    }

    err = nvs_set_u8(nvs_handle, NVS_KEY_FORMAT_VERSION,
                     MAG_CALIBRATION_FORMAT_VERSION);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error saving calibration format version: %s",
                 esp_err_to_name(err));
        nvs_close(nvs_handle);
        return err;
    }

    err = nvs_set_u8(nvs_handle, NVS_KEY_SENSOR_ID,
                     MAG_CALIBRATION_SENSOR_ID);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error saving calibration sensor id: %s",
                 esp_err_to_name(err));
        nvs_close(nvs_handle);
        return err;
    }
    
    // Set calibration flag
    err = nvs_set_u8(nvs_handle, NVS_KEY_CALIBRATED, 1);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error saving calibrated flag: %s", esp_err_to_name(err));
        nvs_close(nvs_handle);
        return err;
    }
    
    // Commit changes
    err = nvs_commit(nvs_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error committing NVS: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "Calibration data saved to NVS successfully");
    }
    
    // Close NVS
    nvs_close(nvs_handle);
    
    return err;
}

/**
 * @brief Load calibration data from NVS
 * 
 * @param removed_center Output parameter: REMOVED state center value
 * @param up_center Output parameter: UP state center value
 * @param down_center Output parameter: DOWN state center value
 * @return esp_err_t ESP_OK indicates read success, ESP_ERR_NVS_NOT_FOUND indicates not calibrated
 */
static esp_err_t load_calibration_from_nvs(int16_t *removed_center, int16_t *up_center, int16_t *down_center)
{
    nvs_handle_t nvs_handle;
    esp_err_t err;
    uint8_t calibrated = 0;
    uint8_t format_version = 0;
    uint8_t sensor_id = 0;
    
    // Open NVS
    err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &nvs_handle);
    if (err != ESP_OK) {
        if (err == ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGI(TAG, "No calibration data found, need to calibrate");
        } else {
            ESP_LOGE(TAG, "Error opening NVS handle: %s", esp_err_to_name(err));
        }
        return err;
    }
    
    // Check if already calibrated
    err = nvs_get_u8(nvs_handle, NVS_KEY_CALIBRATED, &calibrated);
    if (err != ESP_OK || calibrated != 1) {
        ESP_LOGI(TAG, "Device not calibrated yet");
        nvs_close(nvs_handle);
        return ESP_ERR_NVS_NOT_FOUND;
    }

    /* Legacy images stored only the three center values.  Missing or
     * mismatched metadata forces calibration while preserving every old NVS
     * key, so switching sensor builds cannot silently disable events. */
    esp_err_t format_err = nvs_get_u8(nvs_handle, NVS_KEY_FORMAT_VERSION,
                                      &format_version);
    esp_err_t sensor_err = nvs_get_u8(nvs_handle, NVS_KEY_SENSOR_ID,
                                      &sensor_id);
    if (format_err != ESP_OK || sensor_err != ESP_OK ||
        format_version != MAG_CALIBRATION_FORMAT_VERSION ||
        sensor_id != MAG_CALIBRATION_SENSOR_ID) {
        ESP_LOGW(TAG,
                 "Ignoring incompatible calibration (format=%u/%u sensor=%u/%u; recalibration required)",
                 format_version, MAG_CALIBRATION_FORMAT_VERSION,
                 sensor_id, MAG_CALIBRATION_SENSOR_ID);
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
        ESP_LOGI(TAG,
                 "[diag] calibration_ignored format_read=%s sensor_read=%s format=%u expected_format=%u sensor=%u expected_sensor=%u",
                 esp_err_to_name(format_err), esp_err_to_name(sensor_err),
                 format_version, MAG_CALIBRATION_FORMAT_VERSION,
                 sensor_id, MAG_CALIBRATION_SENSOR_ID);
#endif
        nvs_close(nvs_handle);
        return ESP_ERR_NVS_NOT_FOUND;
    }
    
    // Read three center values
    err = nvs_get_i16(nvs_handle, NVS_KEY_REMOVED, removed_center);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error reading removed_center: %s", esp_err_to_name(err));
        nvs_close(nvs_handle);
        return err;
    }
    
    err = nvs_get_i16(nvs_handle, NVS_KEY_UP, up_center);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error reading up_center: %s", esp_err_to_name(err));
        nvs_close(nvs_handle);
        return err;
    }
    
    err = nvs_get_i16(nvs_handle, NVS_KEY_DOWN, down_center);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error reading down_center: %s", esp_err_to_name(err));
        nvs_close(nvs_handle);
        return err;
    }
    
    // Close NVS
    nvs_close(nvs_handle);
    
    ESP_LOGI(TAG, "Calibration data loaded from NVS (sensor_id=%u, format=%u):",
             sensor_id, format_version);
    ESP_LOGI(TAG, "  REMOVED center: %d", *removed_center);
    ESP_LOGI(TAG, "  UP center: %d", *up_center);
    ESP_LOGI(TAG, "  DOWN center: %d", *down_center);
    
    return ESP_OK;
}

/**
 * @brief Initialize magnetometer sensor
 * 
 * @return esp_err_t ESP_OK indicates success, ESP_FAIL indicates failure
 * 
 * @note Initializes BMM150 or QMC6309 sensor based on configuration
 */
static esp_err_t magnetometer_sensor_init(void)
{
#ifdef CONFIG_SENSOR_BMM150
    /* bmm150_init_standalone will handle both interface and sensor initialization */
    int8_t rslt = bmm150_init_standalone();
    if (rslt != BMM150_OK) {
        ESP_LOGE(TAG, "BMM150 init failed: %d", rslt);
        return ESP_FAIL;
    }
    return ESP_OK;
#elif defined(CONFIG_SENSOR_QMC6309)
    if (qmc6309_interface_init() != ESP_OK) {
        ESP_LOGE(TAG, "QMC6309 interface init failed");
        return ESP_FAIL;
    }

    if (qmc6309_init() != ESP_OK) {
        ESP_LOGE(TAG, "QMC6309 init failed");
        qmc6309_interface_deinit();
        return ESP_FAIL;
    }
    return ESP_OK;
#else
    return ESP_FAIL;
#endif
}

/**
 * @brief Magnetometer data read task (shared data provider)
 * 
 * @param arg Task parameter (unused)
 * 
 * @note This task continuously reads magnetometer data and updates shared data structure
 * @note Other tasks (slide switch and rotary knob) can read from shared data
 */
static void magnetometer_data_read_task(void *arg)
{
    (void)arg;
    ESP_LOGI(TAG, "Magnetometer data read task started");

    if (atomic_load_explicit(&s_magnetometer_stop_requested, memory_order_acquire)) {
        magnetometer_signal_init_result(false, ESP_ERR_INVALID_STATE);
        goto cleanup;
    }
    
    // Initialize magnetometer sensor
    const esp_err_t sensor_init_ret = magnetometer_sensor_init();
    if (sensor_init_ret != ESP_OK) {
        ESP_LOGE(TAG, "Magnetometer sensor initialization failed: %s",
                 esp_err_to_name(sensor_init_ret));
        magnetometer_signal_init_result(false, sensor_init_ret);
        goto cleanup;
    }
    
    // Create mutex for protecting shared data
    s_mag_data_mutex = xSemaphoreCreateMutex();
    if (s_mag_data_mutex == NULL) {
        ESP_LOGE(TAG, "Failed to create mutex for magnetometer data");
        magnetometer_signal_init_result(false, ESP_ERR_NO_MEM);
        goto cleanup;
    }
    
    // Initialize shared data
    s_mag_data.x = 0;
    s_mag_data.y = 0;
    s_mag_data.z = 0;
    s_mag_data.valid = false;

    magnetometer_signal_init_result(true, ESP_OK);

    uint32_t consecutive_read_failures = 0;
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    TickType_t last_diag_tick = 0;
#endif
    TickType_t last_telemetry_tick = 0;
    
    while (!atomic_load_explicit(&s_magnetometer_stop_requested, memory_order_acquire)) {
        int8_t ret = ESP_FAIL;
        int16_t mag_x = 0, mag_y = 0, mag_z = 0;
        
#ifdef CONFIG_SENSOR_BMM150
        bmm150_set_op_mode(&settings, &s_bmm150);
        struct bmm150_mag_data mag = { 0 };
        ret = bmm150_read_mag_data(&mag, &s_bmm150);
        if (ret == BMM150_OK) {
            mag_x = mag.x;
            mag_y = mag.y;
            mag_z = mag.z;
        }
#elif defined(CONFIG_SENSOR_QMC6309)
        int16_t qmc_x, qmc_y, qmc_z;
        ret = qmc6309_read_raw(&qmc_x, &qmc_y, &qmc_z);
        if (ret == ESP_OK) {
            // QMC6309 data processing: store raw values, z-axis will be processed in slide switch task
            mag_x = qmc_x;
            mag_y = qmc_y;
            mag_z = -qmc_z / 10;  // Same processing as before for z-axis
        }
#endif
        
        // Update shared data with mutex protection
        if (ret == ESP_OK || ret == BMM150_OK) {
            if (xSemaphoreTake(s_mag_data_mutex, portMAX_DELAY) == pdTRUE) {
                s_mag_data.x = mag_x;
                s_mag_data.y = mag_y;
                s_mag_data.z = mag_z;
                s_mag_data.valid = true;
                xSemaphoreGive(s_mag_data_mutex);
            }
            consecutive_read_failures = 0;
            const TickType_t telemetry_now = xTaskGetTickCount();
            if (telemetry_now - last_telemetry_tick >= pdMS_TO_TICKS(100)) {
#if CONFIG_SENSOR_QMC6309
                const control_serial_magnetic_sensor_kind_t sensor_kind =
                    MAGNETIC_SENSOR_KIND_QMC6309;
#else
                const control_serial_magnetic_sensor_kind_t sensor_kind =
                    MAGNETIC_SENSOR_KIND_BMM150;
#endif
                (void)control_serial_send_magnetic_sensor_sample(
                    sensor_kind, mag_x, mag_y, mag_z
                );
                last_telemetry_tick = telemetry_now;
            }
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
            TickType_t now = xTaskGetTickCount();
            if (now - last_diag_tick >= pdMS_TO_TICKS(500)) {
#if CONFIG_SENSOR_QMC6309
                const char *sensor_name = "QMC6309";
#elif CONFIG_SENSOR_BMM150
                const char *sensor_name = "BMM150";
#else
                const char *sensor_name = "unknown";
#endif
                ESP_LOGI(TAG, "[diag] sample sensor=%s x=%d y=%d z=%d (processed units)",
                         sensor_name, mag_x, mag_y, mag_z);
                last_diag_tick = now;
            }
#endif
        } else {
            /* Invalidate the sample immediately.  Consumers must never turn
             * a stale value into a new magnetic event after the sensor drops
             * off the bus. */
            if (xSemaphoreTake(s_mag_data_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                s_mag_data.valid = false;
                xSemaphoreGive(s_mag_data_mutex);
            }
            ++consecutive_read_failures;
            if (consecutive_read_failures == 1 ||
                (consecutive_read_failures % 20U) == 0U) {
                ESP_LOGW(TAG, "Failed to read magnetometer data: %d (consecutive=%lu)",
                         ret, (unsigned long)consecutive_read_failures);
            }
        }
        
        vTaskDelay(pdMS_TO_TICKS(MAG_SAMPLE_PERIOD_MS));
    }

cleanup:
#ifdef CONFIG_SENSOR_BMM150
    bmm150_coines_deinit();
#elif defined(CONFIG_SENSOR_QMC6309)
    qmc6309_interface_deinit();
#endif
    if (s_mag_data_mutex != NULL) {
        if (xSemaphoreTake(s_mag_data_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            s_mag_data.valid = false;
            xSemaphoreGive(s_mag_data_mutex);
        }
        vSemaphoreDelete(s_mag_data_mutex);
        s_mag_data_mutex = NULL;
    }
    atomic_store_explicit(&s_magnetometer_ready, false, memory_order_release);
    if (!atomic_load_explicit(&s_magnetometer_init_failed, memory_order_acquire)) {
        atomic_store_explicit(&s_magnetometer_init_failed, true, memory_order_release);
    }
    ESP_LOGI(TAG, "Magnetometer data read task finished.");
    vTaskDelete(NULL);
}

/**
 * @brief Get magnetometer data from shared structure
 * 
 * @param x Output parameter: X-axis value
 * @param y Output parameter: Y-axis value
 * @param z Output parameter: Z-axis value
 * @return true Data is valid
 * @return false Data is invalid or mutex timeout
 */
static bool get_magnetometer_data(int16_t *x, int16_t *y, int16_t *z)
{
    if (s_mag_data_mutex == NULL) {
        return false;
    }
    
    if (xSemaphoreTake(s_mag_data_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
        bool valid = s_mag_data.valid;
        if (valid) {
            *x = s_mag_data.x;
            *y = s_mag_data.y;
            *z = s_mag_data.z;
        }
        xSemaphoreGive(s_mag_data_mutex);
        return valid;
    }
    
    return false;
}

/**
 * @brief Magnetometer calibration task
 * 
 * @param arg Task parameter (unused)
 * 
 * @note This task performs magnetometer calibration:
 *       1. Try to load calibration data from NVS
 *       2. If not found, perform automatic calibration (three positions)
 *       3. Save calibration data to NVS
 *       4. Signal completion via semaphore
 */
static void magnetometer_calibration_task(void *arg)
{
    ESP_LOGI(TAG, "Magnetometer calibration task started");
    
    // Wait for magnetometer data read task to initialize
    vTaskDelay(pdMS_TO_TICKS(100));
    
    // Sliding window buffer
    static int16_t s_window[MAG_WINDOW_SIZE] = {0};
    static uint8_t s_window_index = 0;
    static uint8_t s_window_filled = 0;  // Window fill count
    
    // Calibration related variables
    static calibration_state_t s_calibration_state = CALIBRATION_IDLE;
    static int16_t s_calibration_last_average = 0;
    static int16_t s_calibration_temp_values[3] = {0};  // Temporarily store calibration values
    static bool s_first_completion_signaled = false;  // Track if calibration completion has been signaled
    
    // Try to load calibration data from NVS
    esp_err_t load_err = load_calibration_from_nvs(
        &s_calibrated_removed_center,
        &s_calibrated_up_center,
        &s_calibrated_down_center
    );
    
    if (load_err == ESP_OK) {
        // Calibration data loaded successfully, skip calibration flow
        ESP_LOGI(TAG, "========== Using Saved Calibration Data ==========");
        s_calibration_state = CALIBRATION_COMPLETED;
        
        // Immediately signal calibration completion (no need to wait for sliding window)
        if (!s_first_completion_signaled && s_calibration_complete_sem != NULL) {
            xSemaphoreGive(s_calibration_complete_sem);
            s_first_completion_signaled = true;
        }
    } else {
        // No calibration data or load failed, start automatic calibration flow
        ESP_LOGI(TAG, "========== Automatic Magnetometer Calibration Started ==========");
        ESP_LOGI(TAG, "Detecting stable positions automatically...");
        s_calibration_state = CALIBRATION_DETECTING_FIRST;
    }
    
    while (1) {
        /* The data provider can terminate when the selected driver cannot
         * identify the physical sensor.  Do not leave the start routine
         * blocked forever on a semaphore that can never be given. */
        if (atomic_load_explicit(&s_magnetometer_init_failed, memory_order_acquire)) {
            ESP_LOGE(TAG, "Calibration aborted: magnetometer initialization failed");
            if (!s_first_completion_signaled && s_calibration_complete_sem != NULL) {
                xSemaphoreGive(s_calibration_complete_sem);
                s_first_completion_signaled = true;
            }
            vTaskDelete(NULL);
            return;
        }

        // Check if recalibration is needed
        if (s_request_recalibration && s_calibration_state == CALIBRATION_COMPLETED) {
            ESP_LOGI(TAG, "========== Re-calibration Requested ==========");
            ESP_LOGI(TAG, "Starting automatic recalibration...");
            control_serial_send_magnetic_switch_calibration_step(MAG_SWITCH_CALIB_START);
            
            // Reset calibration state
            s_calibration_state = CALIBRATION_DETECTING_FIRST;
            s_calibration_last_average = 0;
            s_calibration_temp_values[0] = 0;
            s_calibration_temp_values[1] = 0;
            s_calibration_temp_values[2] = 0;
            s_first_completion_signaled = false;  // Reset signal flag for recalibration
            
            // Reset window
            s_window_filled = 0;
            s_window_index = 0;
            
            // Clear request flag
            s_request_recalibration = false;
        }
        
        // Read z-axis data from shared structure
        int16_t mag_x, mag_y, mag_z;
        bool data_valid = get_magnetometer_data(&mag_x, &mag_y, &mag_z);
        
        if (!data_valid) {
            // Data not available yet, skip this iteration
            vTaskDelay(pdMS_TO_TICKS(MAG_SAMPLE_PERIOD_MS));
            continue;
        }
        
        // Use z-axis data for calibration
        int16_t s_mag_value = mag_z;
        
        // Process absolute value
        if (s_mag_value < 0) {
            s_mag_value = -s_mag_value;
        }
        
        // 1. Update sliding window
        s_window[s_window_index] = s_mag_value;
        s_window_index = (s_window_index + 1) % MAG_WINDOW_SIZE;
        if (s_window_filled < MAG_WINDOW_SIZE) {
            s_window_filled++;
        }
        
        // 2. Calculate sliding window average (start checking after window is filled)
        if (s_window_filled >= MAG_WINDOW_SIZE) {
            int32_t sum = 0;
            for (uint8_t i = 0; i < MAG_WINDOW_SIZE; i++) {
                sum += s_window[i];
            }
            int16_t average = sum / MAG_WINDOW_SIZE;
            
            // ========== Automatic Calibration State Machine ==========
            if (s_calibration_state != CALIBRATION_COMPLETED) {
                // Calculate stability time threshold in ticks
                const TickType_t stability_ticks = pdMS_TO_TICKS(CALIBRATION_STABILITY_TIME_MS);
                static TickType_t s_stable_start_time = 0;  // Time when value became stable
                static int16_t s_stable_value = 0;  // The stable value being monitored
                
                switch (s_calibration_state) {
                    case CALIBRATION_DETECTING_FIRST:
                    {
                        // Check if value is stable (change < threshold)
                        if (s_calibration_last_average == 0) {
                            // First reading, initialize
                            s_calibration_last_average = average;
                            s_stable_start_time = xTaskGetTickCount();
                            s_stable_value = average;
                        } else {
                            int16_t diff = abs(average - s_calibration_last_average);
                            
                            if (diff < 10) {  // Value is stable (within 10 units)
                                // Check if still the same stable value
                                if (abs(average - s_stable_value) < 10) {
                                    // Value is still stable, check if stable time >= 2 seconds
                                    TickType_t current_time = xTaskGetTickCount();
                                    TickType_t stable_duration = current_time - s_stable_start_time;
                                    
                                    if (stable_duration >= stability_ticks) {
                                        // Stable for required time, record first position
                                        s_calibration_temp_values[0] = average;
                                        ESP_LOGI(TAG, "First position calibrated: %d (stable for %lu ms)", 
                                                 average, (unsigned long)(stable_duration * portTICK_PERIOD_MS));
                                        
                                        // Enter next state: wait for change
                                        s_calibration_state = CALIBRATION_WAITING_CHANGE_1;
                                        s_calibration_last_average = 0;  // Reset for next detection
                                        s_stable_start_time = 0;
                                    }
                                } else {
                                    // Value changed but still stable, update stable value and time
                                    s_stable_value = average;
                                    s_stable_start_time = xTaskGetTickCount();
                                }
                            } else {
                                // Value changed significantly, reset stable tracking
                                s_stable_value = average;
                                s_stable_start_time = xTaskGetTickCount();
                            }
                            
                            s_calibration_last_average = average;
                        }
                        break;
                    }
                    
                    case CALIBRATION_WAITING_CHANGE_1:
                    {
                        // Wait for value to change significantly from first position
                        int16_t diff_from_first = abs(average - s_calibration_temp_values[0]);
                        
                        if (diff_from_first > CALIBRATION_VALUE_DIFF_THRESHOLD) {
                            // Value changed significantly, enter second position detection
                            ESP_LOGI(TAG, "Change detected from first position (diff=%d), detecting second position...", diff_from_first);
                            s_calibration_state = CALIBRATION_DETECTING_SECOND;
                            s_calibration_last_average = 0;  // Reset for next detection
                            s_stable_start_time = 0;
                        }
                        break;
                    }
                    
                    case CALIBRATION_DETECTING_SECOND:
                    {
                        // Check if value is stable and different from first position
                        int16_t diff_from_first = abs(average - s_calibration_temp_values[0]);
                        
                        if (diff_from_first <= CALIBRATION_VALUE_DIFF_THRESHOLD) {
                            // Too close to first position, reset
                            s_calibration_last_average = 0;
                            s_stable_start_time = 0;
                            break;
                        }
                        
                        // Check if value is stable
                        if (s_calibration_last_average == 0) {
                            // First reading, initialize
                            s_calibration_last_average = average;
                            s_stable_start_time = xTaskGetTickCount();
                            s_stable_value = average;
                        } else {
                            int16_t diff = abs(average - s_calibration_last_average);
                            
                            if (diff < 10) {  // Value is stable (within 10 units)
                                // Check if still the same stable value
                                if (abs(average - s_stable_value) < 10) {
                                    // Value is still stable, check if stable time >= 2 seconds
                                    TickType_t current_time = xTaskGetTickCount();
                                    TickType_t stable_duration = current_time - s_stable_start_time;
                                    
                                    if (stable_duration >= stability_ticks) {
                                        // Stable for required time and different from first, record second position
                                        s_calibration_temp_values[1] = average;
                                        ESP_LOGI(TAG, "Second position calibrated: %d (stable for %lu ms, diff from first=%d)", 
                                                 average, (unsigned long)(stable_duration * portTICK_PERIOD_MS), diff_from_first);
                                        
                                        // Send notification: second position calibration done (0x0012)
                                        control_serial_send_magnetic_switch_calibration_step(MAG_SWITCH_CALIB_FIRST_DONE);
                                        
                                        // Enter next state: wait for change
                                        s_calibration_state = CALIBRATION_WAITING_CHANGE_2;
                                        s_calibration_last_average = 0;  // Reset for next detection
                                        s_stable_start_time = 0;
                                    }
                                } else {
                                    // Value changed but still stable, update stable value and time
                                    s_stable_value = average;
                                    s_stable_start_time = xTaskGetTickCount();
                                }
                            } else {
                                // Value changed significantly, reset stable tracking
                                s_stable_value = average;
                                s_stable_start_time = xTaskGetTickCount();
                            }
                            
                            s_calibration_last_average = average;
                        }
                        break;
                    }
                    
                    case CALIBRATION_WAITING_CHANGE_2:
                    {
                        // Wait for value to change significantly from second position
                        int16_t diff_from_second = abs(average - s_calibration_temp_values[1]);
                        
                        if (diff_from_second > CALIBRATION_VALUE_DIFF_THRESHOLD) {
                            // Value changed significantly, enter third position detection
                            ESP_LOGI(TAG, "Change detected from second position (diff=%d), detecting third position...", diff_from_second);
                            s_calibration_state = CALIBRATION_DETECTING_THIRD;
                            s_calibration_last_average = 0;  // Reset for next detection
                            s_stable_start_time = 0;
                        }
                        break;
                    }
                    
                    case CALIBRATION_DETECTING_THIRD:
                    {
                        // Check if value is stable and different from both first and second positions
                        int16_t diff_from_first = abs(average - s_calibration_temp_values[0]);
                        int16_t diff_from_second = abs(average - s_calibration_temp_values[1]);
                        
                        if (diff_from_first <= CALIBRATION_VALUE_DIFF_THRESHOLD || 
                            diff_from_second <= CALIBRATION_VALUE_DIFF_THRESHOLD) {
                            // Too close to existing positions, reset
                            s_calibration_last_average = 0;
                            s_stable_start_time = 0;
                            break;
                        }
                        
                        // Check if value is stable
                        if (s_calibration_last_average == 0) {
                            // First reading, initialize
                            s_calibration_last_average = average;
                            s_stable_start_time = xTaskGetTickCount();
                            s_stable_value = average;
                        } else {
                            int16_t diff = abs(average - s_calibration_last_average);
                            
                            if (diff < 10) {  // Value is stable (within 10 units)
                                // Check if still the same stable value
                                if (abs(average - s_stable_value) < 10) {
                                    // Value is still stable, check if stable time >= 2 seconds
                                    TickType_t current_time = xTaskGetTickCount();
                                    TickType_t stable_duration = current_time - s_stable_start_time;
                                    
                                    if (stable_duration >= stability_ticks) {
                                        // Stable for required time and different from both positions, record third position
                                        s_calibration_temp_values[2] = average;
                                        ESP_LOGI(TAG, "Third position (REMOVED) calibrated: %d (stable for %lu ms, diff from first=%d, diff from second=%d)", 
                                                 average, (unsigned long)(stable_duration * portTICK_PERIOD_MS), 
                                                 diff_from_first, diff_from_second);
                                        
                                        // Now we have all three positions, re-sort by magnitude relationship
                                        // REMOVED must be minimum value, UP is middle value, DOWN is maximum value
                                        int16_t sorted_values[3];
                                        sorted_values[0] = s_calibration_temp_values[0];
                                        sorted_values[1] = s_calibration_temp_values[1];
                                        sorted_values[2] = s_calibration_temp_values[2];
                                        
                                        // Simple bubble sort
                                        for (int i = 0; i < 2; i++) {
                                            for (int j = 0; j < 2 - i; j++) {
                                                if (sorted_values[j] > sorted_values[j + 1]) {
                                                    int16_t temp = sorted_values[j];
                                                    sorted_values[j] = sorted_values[j + 1];
                                                    sorted_values[j + 1] = temp;
                                                }
                                            }
                                        }
                                        
                                        // Assign by magnitude: minimum=REMOVED, middle=UP, maximum=DOWN
                                        s_calibrated_removed_center = sorted_values[0];
                                        s_calibrated_up_center = sorted_values[1];
                                        s_calibrated_down_center = sorted_values[2];
                                        
                                        s_calibration_state = CALIBRATION_COMPLETED;
                                        
                                        ESP_LOGI(TAG, "========== Automatic Calibration Completed ==========");
                                        ESP_LOGI(TAG, "Calibrated values (sorted by magnitude):");
                                        ESP_LOGI(TAG, "  REMOVED center: %d (default: %d)", 
                                                 s_calibrated_removed_center, MAG_STATE_REMOVED_CENTER);
                                        ESP_LOGI(TAG, "  UP center: %d (default: %d)", 
                                                 s_calibrated_up_center, MAG_STATE_UP_CENTER);
                                        ESP_LOGI(TAG, "  DOWN center: %d (default: %d)", 
                                                 s_calibrated_down_center, MAG_STATE_DOWN_CENTER);
                                        
                                        // Save calibration data to NVS
                                        esp_err_t save_err = save_calibration_to_nvs(
                                            s_calibrated_removed_center,
                                            s_calibrated_up_center,
                                            s_calibrated_down_center
                                        );
                                        
                                        if (save_err == ESP_OK) {
                                            ESP_LOGI(TAG, "Calibration data saved to Flash successfully");
                                        } else {
                                            ESP_LOGW(TAG, "Failed to save calibration data to Flash: %s", 
                                                     esp_err_to_name(save_err));
                                        }
                                        
                                        ESP_LOGI(TAG, "Normal operation started.");
                                        
                                        // Send notification: calibration complete
                                        control_serial_send_magnetic_switch_calibration_step(MAG_SWITCH_CALIB_COMPLETE);
                                        
                                        // Signal calibration completion will be handled in the loop check below
                                    }
                                } else {
                                    // Value changed but still stable, update stable value and time
                                    s_stable_value = average;
                                    s_stable_start_time = xTaskGetTickCount();
                                }
                            } else {
                                // Value changed significantly, reset stable tracking
                                s_stable_value = average;
                                s_stable_start_time = xTaskGetTickCount();
                            }
                            
                            s_calibration_last_average = average;
                        }
                        break;
                    }
                    
                    default:
                        break;
                }
                
                // If calibration is completed (either loaded from NVS or finished), signal and wait for recalibration
                if (s_calibration_state == CALIBRATION_COMPLETED) {
                    // Signal calibration completion (only once, on first completion)
                    if (!s_first_completion_signaled && s_calibration_complete_sem != NULL) {
                        xSemaphoreGive(s_calibration_complete_sem);
                        s_first_completion_signaled = true;
                    }
                    // Wait for recalibration request (task stays alive to handle recalibration)
                    // The recalibration check at the beginning of the loop will handle it
                }
            } else {
                // Calibration already completed, signal and wait for recalibration
                if (!s_first_completion_signaled && s_calibration_complete_sem != NULL) {
                    xSemaphoreGive(s_calibration_complete_sem);
                    s_first_completion_signaled = true;
                }
                // Wait for recalibration request (task stays alive to handle recalibration)
            }
        }
        
        vTaskDelay(pdMS_TO_TICKS(MAG_SAMPLE_PERIOD_MS));
    }
}

/**
 * @brief Slide switch event detection task
 * 
 * @param arg Task parameter (unused)
 * 
 * @note This function implements the following:
 *       1. Use sliding window filter to smooth z-axis data
 *       2. Detect slider events based on state machine
 *       3. Single click detection (only in DOWN state)
 *       4. Send event notifications to serial port
 */
static void slide_switch_event_detect_task(void *arg)
{
    ESP_LOGI(TAG, "Slide switch event detection task started");

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    TickType_t last_diag_tick = 0;
#endif
    
    // Wait for magnetometer data read task and calibration task to initialize
    vTaskDelay(pdMS_TO_TICKS(100));
    
    // Sliding window buffer
    static int16_t s_window[MAG_WINDOW_SIZE] = {0};
    static uint8_t s_window_index = 0;
    static uint8_t s_window_filled = 0;  // Window fill count
    
    // State tracking variables (removed unused variables)

#ifdef CONFIG_SENSOR_BMM150
    // Single click detection - based on slope direction change count or Y axis drop
    typedef enum {
        CLICK_STATE_IDLE = 0,           // Idle state (waiting for click, monitoring stability)
        CLICK_STATE_DETECTING,          // Detecting state (counting slope direction changes)
        CLICK_STATE_Y_DROPPING,         // Y axis dropping state (Y < -2000)
        CLICK_STATE_Y_RECOVERING        // Y axis recovering state (Y rising back to initial value)
    } click_state_t;
    
    static click_state_t s_click_state = CLICK_STATE_IDLE;
    static int16_t s_prev_mag_x = 0;    // Previous X axis value for slope calculation
    static int16_t s_prev_mag_y = 0;    // Previous Y axis value for slope calculation
    static int8_t s_prev_slope_x = 0;   // Previous X axis slope direction (-1: down, 0: flat, 1: up)
    static int8_t s_prev_slope_y = 0;   // Previous Y axis slope direction (-1: down, 0: flat, 1: up)
    static uint8_t s_slope_change_count_x = 0;  // X axis slope direction change count
    static uint8_t s_slope_change_count_y = 0;  // Y axis slope direction change count
    static int16_t s_click_initial_x = 0;      // Initial X axis value when detection starts
    static int16_t s_click_initial_y = 0;      // Initial Y axis value when detection starts (for Y drop detection)
    static int16_t s_y_drop_min_value = 0;    // Minimum Y axis value during drop (lowest point reached)
    static bool s_y_drop_initialized = false; // Whether Y drop detection has been initialized
    static TickType_t s_click_start_time = 0;  // Click detection start time
    static bool s_click_initialized = false;   // Whether initial values have been set
    static bool s_y_drop_click_triggered = false;  // Whether Y drop click has been triggered (to prevent multiple triggers)
    static TickType_t s_y_drop_trigger_time = 0;  // Time when Y drop click was triggered (for debounce)
#elif defined(CONFIG_SENSOR_QMC6309)
    /* Upstream defines only MAG_CLICK_X_DROP_THRESHOLD for QMC6309, while
     * the click state machine below requires BMM150 Y-axis and slope
     * thresholds.  Do not invent uncalibrated QMC thresholds: position,
     * slide, placement and accessory detection remain active. */
    ESP_LOGW(TAG, "QMC6309 single-click gesture disabled until thresholds are calibrated; slide/accessory events remain enabled");
#endif
    
    while (1) {
        if (atomic_load_explicit(&s_magnetometer_init_failed, memory_order_acquire)) {
            ESP_LOGE(TAG, "Slide switch detection stopped: magnetometer initialization failed");
            vTaskDelete(NULL);
            return;
        }

        // Read z-axis data from shared structure
        int16_t mag_x, mag_y, mag_z;
        bool data_valid = get_magnetometer_data(&mag_x, &mag_y, &mag_z);
        // control_serial_print_magnetometer_data(mag_x, mag_y, mag_z);
        
        if (!data_valid) {
            // Data not available yet, skip this iteration
            vTaskDelay(pdMS_TO_TICKS(MAG_SAMPLE_PERIOD_MS));
            continue;
        }
        
        // Use z-axis data for slide switch detection
        int16_t s_mag_value = mag_z;
        
        // Process absolute value (same as before)
        if (s_mag_value < 0) {
            s_mag_value = -s_mag_value;
        }
        
        // Debug output (can be removed later)
        // ESP_LOGI(TAG, "Slide switch z-axis: %d", s_mag_value);
        
        {
            // 1. Update sliding window
            s_window[s_window_index] = s_mag_value;
            s_window_index = (s_window_index + 1) % MAG_WINDOW_SIZE;
            if (s_window_filled < MAG_WINDOW_SIZE) {
                s_window_filled++;
            }
            
            // 2. Calculate sliding window average (start checking after window is filled)
            if (s_window_filled >= MAG_WINDOW_SIZE) {
                int32_t sum = 0;
                for (uint8_t i = 0; i < MAG_WINDOW_SIZE; i++) {
                    sum += s_window[i];
                }
                int16_t average = sum / MAG_WINDOW_SIZE;
                
                // ========== Normal Operation Mode ==========
                
                // 3. Determine which center value the average is closest to
                
                // Calculate state ranges using calibrated center values (for pairing event detection)
                int16_t calibrated_removed_min = s_calibrated_removed_center - MAG_STATE_REMOVED_OFFSET;
                int16_t calibrated_removed_max = s_calibrated_removed_center + MAG_STATE_REMOVED_OFFSET;
                int16_t calibrated_up_min = s_calibrated_up_center - MAG_STATE_UP_OFFSET;
                int16_t calibrated_up_max = s_calibrated_up_center + MAG_STATE_UP_OFFSET;
                int16_t calibrated_down_min = s_calibrated_down_center - MAG_STATE_DOWN_OFFSET;
                int16_t calibrated_down_max = s_calibrated_down_center + MAG_STATE_DOWN_OFFSET;
                
                // ========== Initial Position Detection (Power-on) ==========
                // Detect initial slider position once after power-on
                // Only detect if average falls within UP or DOWN range (not REMOVED)
                static bool s_initial_position_detected = false;
                if (!s_initial_position_detected) {
                    // Check if average falls within UP range
                    if (average >= calibrated_up_min && average <= calibrated_up_max) {
                        // In UP position
                        s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_UP;
                        ESP_LOGI(TAG, "Initial position detected: SLIDE_UP (average: %d, UP range: [%d-%d])", 
                                 average, calibrated_up_min, calibrated_up_max);
                        control_serial_send_magnetic_switch_event(s_slider_state);
                        s_initial_position_detected = true;
                    }
                    // Check if average falls within DOWN range
                    else if (average >= calibrated_down_min && average <= calibrated_down_max) {
                        // In DOWN position
                        s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_DOWN;
                        ESP_LOGI(TAG, "Initial position detected: SLIDE_DOWN (average: %d, DOWN range: [%d-%d])", 
                                 average, calibrated_down_min, calibrated_down_max);
                        control_serial_send_magnetic_switch_event(s_slider_state);
                        s_initial_position_detected = true;
                    }
                    // If average is in REMOVED range or unknown, wait for next reading
                    // (Don't mark as detected, keep checking until we find UP or DOWN position)
                }

                // control_serial_print_magnetometer_data(0, 0, average);

                // Position detection (removed detected_position variable as it's not used)
                
                // Calculate distances to each center value (for event detection)
                int16_t dist_to_removed = abs(average - s_calibrated_removed_center);
                int16_t dist_to_up = abs(average - s_calibrated_up_center);
                int16_t dist_to_down = abs(average - s_calibrated_down_center);
                
                // Find the closest center value
                int16_t current_center = s_calibrated_removed_center;
                mag_position_t current_center_position = MAG_POSITION_REMOVED;
                int16_t min_dist = dist_to_removed;
                
                if (dist_to_up < min_dist) {
                    min_dist = dist_to_up;
                    current_center = s_calibrated_up_center;
                    current_center_position = MAG_POSITION_UP;
                }
                if (dist_to_down < min_dist) {
                    min_dist = dist_to_down;
                    current_center = s_calibrated_down_center;
                    current_center_position = MAG_POSITION_DOWN;
                }
                
                // Track last stable center value and position
                static int16_t s_last_stable_center = 0;
                static mag_position_t s_last_stable_position = MAG_POSITION_UNKNOWN;
                static int16_t s_candidate_center = 0;
                static mag_position_t s_candidate_position = MAG_POSITION_UNKNOWN;
                static uint8_t s_stable_count = 0;
                
                // Check if average is stable near current center (within threshold)
                const int16_t CENTER_STABILITY_THRESHOLD = MAG_STATE_REMOVED_OFFSET;  // Use same threshold as position detection
                bool is_near_center = (min_dist <= CENTER_STABILITY_THRESHOLD);
                
                if (is_near_center) {
                    // Average is near a center value
                    if (current_center == s_candidate_center && current_center_position == s_candidate_position) {
                        // Same candidate, accumulate stable count
                        s_stable_count++;
                        
                        // Check if stable enough to confirm position change
                        if (s_stable_count >= MAG_STABLE_THRESHOLD) {
                            // Position is stable, check if it's different from last stable position
                            if (s_last_stable_position != current_center_position && s_last_stable_position != MAG_POSITION_UNKNOWN) {
                                // Position changed, trigger event based on center value transition
                                magnetic_slide_switch_event_t event = MAGNETIC_SLIDE_SWITCH_EVENT_INIT;
                                
                                // Determine event based on center value transitions
                                if (s_last_stable_center == s_calibrated_down_center && 
                                    current_center == s_calibrated_up_center) {
                                    // Down -> Up: Slide up
                                    event = MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_UP;
                                    ESP_LOGI(TAG, "SLIDE_UP (average: %d -> %d)", s_last_stable_center, current_center);
                                }
                                else if (s_last_stable_center == s_calibrated_up_center && 
                                         current_center == s_calibrated_down_center) {
                                    // Up -> Down: Slide down
                                    event = MAGNETIC_SLIDE_SWITCH_EVENT_SLIDE_DOWN;
                                    ESP_LOGI(TAG, "SLIDE_DOWN (average: %d -> %d)", s_last_stable_center, current_center);
                                }
                                else if (s_last_stable_center == s_calibrated_up_center && 
                                         current_center == s_calibrated_removed_center) {
                                    // Up -> Removed: Remove from up
                                    event = MAGNETIC_SLIDE_SWITCH_EVENT_REMOVE_FROM_UP;
                                    ESP_LOGI(TAG, "REMOVE_FROM_UP (average: %d -> %d)", s_last_stable_center, current_center);
                                }
                                else if (s_last_stable_center == s_calibrated_removed_center && 
                                         current_center == s_calibrated_up_center) {
                                    // Removed -> Up: Place from up
                                    event = MAGNETIC_SLIDE_SWITCH_EVENT_PLACE_FROM_UP;
                                    ESP_LOGI(TAG, "PLACE_FROM_UP (average: %d -> %d)", s_last_stable_center, current_center);
                                }
                                else if (s_last_stable_center == s_calibrated_down_center && 
                                         current_center == s_calibrated_removed_center) {
                                    // Down -> Removed: Remove from down
                                    event = MAGNETIC_SLIDE_SWITCH_EVENT_REMOVE_FROM_DOWN;
                                    ESP_LOGI(TAG, "REMOVE_FROM_DOWN (average: %d -> %d)", s_last_stable_center, current_center);
                                }
                                else if (s_last_stable_center == s_calibrated_removed_center && 
                                         current_center == s_calibrated_down_center) {
                                    // Removed -> Down: Place from down
                                    event = MAGNETIC_SLIDE_SWITCH_EVENT_PLACE_FROM_DOWN;
                                    ESP_LOGI(TAG, "PLACE_FROM_DOWN (average: %d -> %d)", s_last_stable_center, current_center);
                                }
                                
                                // Send event if detected
                                if (event != MAGNETIC_SLIDE_SWITCH_EVENT_INIT) {
                                    s_slider_state = event;
                                    control_serial_send_magnetic_switch_event(event);
                                    
                                    // Enable "the fish is attached" detection after REMOVE_FROM_UP or REMOVE_FROM_DOWN event
                                    if (event == MAGNETIC_SLIDE_SWITCH_EVENT_REMOVE_FROM_UP || 
                                        event == MAGNETIC_SLIDE_SWITCH_EVENT_REMOVE_FROM_DOWN) {
                                        // Will be handled in fish detection section
                                    }
                                    // Disable "the fish is attached" detection after PLACE_FROM_UP or PLACE_FROM_DOWN event
                                    else if (event == MAGNETIC_SLIDE_SWITCH_EVENT_PLACE_FROM_UP || 
                                             event == MAGNETIC_SLIDE_SWITCH_EVENT_PLACE_FROM_DOWN) {
                                        // Will be handled in fish detection section
                                    }
                                }
                                
                                // Update last stable position
                                s_last_stable_center = current_center;
                                s_last_stable_position = current_center_position;
                            } else if (s_last_stable_position == MAG_POSITION_UNKNOWN) {
                                // First time detection, just record position
                                s_last_stable_center = current_center;
                                s_last_stable_position = current_center_position;
                            }
                            
                            // Reset stable count
                            s_stable_count = 0;
                        }
                    } else {
                        // New candidate, reset
                        s_candidate_center = current_center;
                        s_candidate_position = current_center_position;
                        s_stable_count = 1;
                    }
                } else {
                    // Not near any center, reset candidate
                    s_candidate_center = 0;
                    s_candidate_position = MAG_POSITION_UNKNOWN;
                    s_stable_count = 0;
                }

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
                TickType_t diag_now = xTaskGetTickCount();
                if (diag_now - last_diag_tick >= pdMS_TO_TICKS(500)) {
                    ESP_LOGI(TAG,
                             "[diag] detect avg=%d x=%d y=%d z=%d centers=%d/%d/%d min_dist=%d position=%d candidate=%d stable=%u",
                             average, mag_x, mag_y, mag_z,
                             s_calibrated_removed_center, s_calibrated_up_center,
                             s_calibrated_down_center, min_dist,
                             (int)s_last_stable_position, (int)s_candidate_position,
                             s_stable_count);
                    last_diag_tick = diag_now;
                }
#endif
                
#ifdef CONFIG_SENSOR_BMM150
                // 4. Single click detection - based on slope direction change count or Y axis drop threshold
                // Click event: 
                //   - X and Y axis data oscillate with multiple slope direction changes (>= 4)
                //   - OR Y axis drops below MAG_CLICK_Y_DROP_THRESHOLD (-2000) then recovers to initial value
                // Only detect click when last stable position is DOWN
                if (s_last_stable_position == MAG_POSITION_DOWN) {
                    // Check that Z axis is stable near s_calibrated_down_center
                    int16_t diff_z_from_down_center = abs(mag_z - s_calibrated_down_center);
                    
                    // Y axis drop detection state machine (separate from slope-based detection)
                    // This runs independently and has priority
                    if (diff_z_from_down_center <= MAG_STATE_DOWN_OFFSET) {
                        // Handle Y drop detection states
                        if (s_click_state == CLICK_STATE_Y_DROPPING || s_click_state == CLICK_STATE_Y_RECOVERING) {
                            // Already in Y drop detection, continue with that
                        } else {
                            // Not in Y drop detection, check if we should start
                            // Initialize Y axis initial value if not initialized
                            if (!s_y_drop_initialized) {
                                s_click_initial_y = mag_y;
                                s_y_drop_initialized = true;
                                s_y_drop_click_triggered = false;
                            } else {
                                // Update initial value if data is stable (within stable range)
                                int16_t diff_y = abs(mag_y - s_click_initial_y);
                                if (diff_y <= MAG_CLICK_STABLE_RANGE) {
                                    s_click_initial_y = mag_y;
                                    
                                    // Reset trigger flag only after sufficient time has passed since last trigger (debounce)
                                    if (s_y_drop_click_triggered) {
                                        TickType_t current_time = xTaskGetTickCount();
                                        TickType_t elapsed_ticks = current_time - s_y_drop_trigger_time;
                                        uint32_t elapsed_ms = elapsed_ticks * portTICK_PERIOD_MS;
                                        
                                        // Reset flag after debounce period (200ms)
                                        if (elapsed_ms >= 200) {
                                            s_y_drop_click_triggered = false;
                                        }
                                    }
                                }
                            }
                            
                            // Check if Y axis drops below threshold (only if not recently triggered)
                            if (mag_y < MAG_CLICK_Y_DROP_THRESHOLD && !s_y_drop_click_triggered) {
                                // Y axis dropped below threshold, enter dropping state
                                s_click_state = CLICK_STATE_Y_DROPPING;
                                s_click_start_time = xTaskGetTickCount();
                                s_y_drop_min_value = mag_y;  // Initialize minimum value
                                s_prev_mag_y = mag_y;  // Initialize previous value
                            }
                        }
                        
                        // Process Y drop detection states
                        switch (s_click_state) {
                        case CLICK_STATE_IDLE:
                        case CLICK_STATE_DETECTING:
                        {
                            // These states are handled above (initialization and threshold check)
                            break;
                        }
                        
                        case CLICK_STATE_Y_DROPPING:
                        {
                            // Y axis is dropping (below -2000)
                            // Record the minimum value reached
                            if (mag_y < s_y_drop_min_value) {
                                s_y_drop_min_value = mag_y;
                            }
                            
                            // Wait for Y axis to start rising (check if rising for at least 2 consecutive samples)
                            if (mag_y > s_prev_mag_y) {
                                // Y axis is rising, enter recovering state
                                s_click_state = CLICK_STATE_Y_RECOVERING;
                            }
                            
                            // Check timeout
                            TickType_t current_time = xTaskGetTickCount();
                            TickType_t elapsed_ticks = current_time - s_click_start_time;
                            uint32_t elapsed_ms = elapsed_ticks * portTICK_PERIOD_MS;
                            
                            if (elapsed_ms >= MAG_CLICK_DURATION_MAX_MS) {
                                // Timeout: reset to idle
                                s_click_state = CLICK_STATE_IDLE;
                                s_click_initial_y = mag_y;
                                s_y_drop_click_triggered = false;
                                s_y_drop_min_value = 0;
                            }
                            
                            s_prev_mag_y = mag_y;
                            break;
                        }
                        
                        case CLICK_STATE_Y_RECOVERING:
                        {
                            // Y axis is recovering (rising back)
                            int16_t diff_y_from_initial = abs(mag_y - s_click_initial_y);
                            
                            // Optimized recovery condition: trigger if either:
                            // 1. Y recovers above recovery threshold (-1500), OR
                            // 2. Y recovers to within recovery range (100) of initial value
                            bool recovered = false;
                            if (mag_y >= MAG_CLICK_Y_RECOVER_THRESHOLD) {
                                // Y has recovered above -1500, consider it recovered
                                recovered = true;
                            } else if (diff_y_from_initial <= MAG_CLICK_Y_RECOVER_RANGE) {
                                // Y has recovered to within 100 of initial value, consider it recovered
                                recovered = true;
                            }
                            
                            if (recovered) {
                                // Y axis recovered, trigger click event (only once)
                                if (!s_y_drop_click_triggered) {
                                    s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_SINGLE_CLICK;
                                    ESP_LOGI(TAG, "*** SINGLE_CLICK detected (Y drop) ***: mag_y=%d (initial=%d, min=%d, diff=%d), mag_z=%d (down_center=%d)",
                                             mag_y, s_click_initial_y, s_y_drop_min_value, diff_y_from_initial, mag_z, s_calibrated_down_center);
                                    
                                    // Send event notification
                                    control_serial_send_magnetic_switch_event(s_slider_state);
                                    
                                    // Mark as triggered to prevent multiple triggers
                                    s_y_drop_click_triggered = true;
                                    s_y_drop_trigger_time = xTaskGetTickCount();  // Record trigger time for debounce
                                }
                                
                                // Reset to idle state
                                s_click_state = CLICK_STATE_IDLE;
                                s_click_initial_y = mag_y;  // Update initial value
                                s_y_drop_min_value = 0;  // Reset minimum value
                            } else if (mag_y < s_prev_mag_y && mag_y < MAG_CLICK_Y_DROP_THRESHOLD) {
                                // Y axis dropped again below threshold, go back to dropping state
                                s_click_state = CLICK_STATE_Y_DROPPING;
                                s_click_start_time = xTaskGetTickCount();
                                s_y_drop_min_value = mag_y;  // Reset minimum value
                            }
                            
                            // Check timeout
                            TickType_t current_time = xTaskGetTickCount();
                            TickType_t elapsed_ticks = current_time - s_click_start_time;
                            uint32_t elapsed_ms = elapsed_ticks * portTICK_PERIOD_MS;
                            
                            if (elapsed_ms >= MAG_CLICK_DURATION_MAX_MS) {
                                // Timeout: reset to idle
                                s_click_state = CLICK_STATE_IDLE;
                                s_click_initial_y = mag_y;
                                s_y_drop_click_triggered = false;
                                s_y_drop_min_value = 0;
                            }
                            
                            s_prev_mag_y = mag_y;
                            break;
                        }
                        }
                    } else {
                        // Z axis not stable, reset Y drop detection
                        if (s_click_state == CLICK_STATE_Y_DROPPING || s_click_state == CLICK_STATE_Y_RECOVERING) {
                            s_click_state = CLICK_STATE_IDLE;
                            s_y_drop_initialized = false;
                            s_click_initial_y = 0;
                            s_y_drop_click_triggered = false;
                            s_y_drop_min_value = 0;
                        }
                    }
                    
                    // Continue with slope-based detection (only if not in Y drop states)
                    if (s_click_state != CLICK_STATE_Y_DROPPING && s_click_state != CLICK_STATE_Y_RECOVERING) {
                        switch (s_click_state) {
                        case CLICK_STATE_IDLE:
                        {
                            // Monitor data stability and wait for significant change to start detection
                            if (!s_click_initialized) {
                                // Initialize with first reading
                                s_click_initial_x = mag_x;
                                s_click_initial_y = mag_y;
                                s_prev_mag_x = mag_x;
                                s_prev_mag_y = mag_y;
                                s_prev_slope_x = 0;
                                s_prev_slope_y = 0;
                                s_click_initialized = true;
                            } else {
                                // Check if data is stable (within stable range)
                                int16_t diff_x = abs(mag_x - s_click_initial_x);
                                int16_t diff_y = abs(mag_y - s_click_initial_y);
                                
                                if (diff_x <= MAG_CLICK_STABLE_RANGE && diff_y <= MAG_CLICK_STABLE_RANGE) {
                                    // Data is stable, update initial values
                                    s_click_initial_x = mag_x;
                                    s_click_initial_y = mag_y;
                                    s_prev_mag_x = mag_x;
                                    s_prev_mag_y = mag_y;
                                } else {
                                    // Data has changed significantly, check if Z axis is stable and start detection
                                    if (diff_z_from_down_center <= MAG_STATE_DOWN_OFFSET) {
                                        // Start detection
                                        s_click_state = CLICK_STATE_DETECTING;
                                        s_click_start_time = xTaskGetTickCount();
                                        s_slope_change_count_x = 0;
                                        s_slope_change_count_y = 0;
                                        s_prev_slope_x = 0;
                                        s_prev_slope_y = 0;
                                    } else {
                                        // Z axis not stable, reset initialization
                                        s_click_initialized = false;
                                    }
                                }
                            }
                            break;
                        }
                        
                        case CLICK_STATE_DETECTING:
                        {
                            // Check if Z axis has changed significantly (indicates sliding or pickup/place action)
                            if (diff_z_from_down_center > MAG_STATE_DOWN_OFFSET) {
                                // Z axis changed significantly, reset to idle
                                s_click_state = CLICK_STATE_IDLE;
                                s_click_initialized = false;
                                s_slope_change_count_x = 0;
                                s_slope_change_count_y = 0;
                                s_click_start_time = 0;
                                break;
                            }
                            
                            // Check timeout
                            TickType_t current_time = xTaskGetTickCount();
                            TickType_t elapsed_ticks = current_time - s_click_start_time;
                            uint32_t elapsed_ms = elapsed_ticks * portTICK_PERIOD_MS;
                            
                            if (elapsed_ms >= MAG_CLICK_DURATION_MAX_MS) {
                                // Timeout: not a click, reset to idle
                                s_click_state = CLICK_STATE_IDLE;
                                s_click_initialized = false;
                                s_slope_change_count_x = 0;
                                s_slope_change_count_y = 0;
                                s_click_start_time = 0;
                                break;
                            }
                            
                            // Calculate X axis slope
                            int16_t slope_x = mag_x - s_prev_mag_x;
                            int8_t slope_dir_x = 0;  // -1: down, 0: flat, 1: up
                            
                            if (slope_x > MAG_CLICK_MIN_SLOPE) {
                                slope_dir_x = 1;  // Up
                            } else if (slope_x < -MAG_CLICK_MIN_SLOPE) {
                                slope_dir_x = -1;  // Down
                            } else {
                                slope_dir_x = 0;  // Flat (ignore small changes)
                            }
                            
                            // Detect X axis slope direction change
                            if (slope_dir_x != 0 && s_prev_slope_x != 0 && slope_dir_x != s_prev_slope_x) {
                                // Slope direction changed (up->down or down->up)
                                s_slope_change_count_x++;
                            }
                            
                            // Update X axis previous values
                            if (slope_dir_x != 0) {
                                s_prev_slope_x = slope_dir_x;
                            }
                            s_prev_mag_x = mag_x;
                            
                            // Calculate Y axis slope
                            int16_t slope_y = mag_y - s_prev_mag_y;
                            int8_t slope_dir_y = 0;  // -1: down, 0: flat, 1: up
                            
                            if (slope_y > MAG_CLICK_MIN_SLOPE) {
                                slope_dir_y = 1;  // Up
                            } else if (slope_y < -MAG_CLICK_MIN_SLOPE) {
                                slope_dir_y = -1;  // Down
                            } else {
                                slope_dir_y = 0;  // Flat (ignore small changes)
                            }
                            
                            // Detect Y axis slope direction change
                            if (slope_dir_y != 0 && s_prev_slope_y != 0 && slope_dir_y != s_prev_slope_y) {
                                // Slope direction changed (up->down or down->up)
                                s_slope_change_count_y++;
                            }
                            
                            // Update Y axis previous values
                            if (slope_dir_y != 0) {
                                s_prev_slope_y = slope_dir_y;
                            }
                            s_prev_mag_y = mag_y;
                            
                            // Check if either axis has enough slope direction changes to trigger click
                            if (s_slope_change_count_x >= MAG_CLICK_SLOPE_CHANGE_THRESHOLD || 
                                s_slope_change_count_y >= MAG_CLICK_SLOPE_CHANGE_THRESHOLD) {
                                // Click detected!
                                s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_SINGLE_CLICK;
                                ESP_LOGI(TAG, "*** SINGLE_CLICK detected ***: X_slope_changes=%d, Y_slope_changes=%d, duration=%lums",
                                         s_slope_change_count_x, s_slope_change_count_y, (unsigned long)elapsed_ms);
                                
                                // Send event notification
                                control_serial_send_magnetic_switch_event(s_slider_state);
                                
                                // Reset to idle state
                                s_click_state = CLICK_STATE_IDLE;
                                s_click_initialized = false;
                                s_slope_change_count_x = 0;
                                s_slope_change_count_y = 0;
                                s_click_start_time = 0;
                            }
                            
                            break;
                        }
                        
                        case CLICK_STATE_Y_DROPPING:
                        case CLICK_STATE_Y_RECOVERING:
                        {
                            // These states are handled separately above, should not reach here
                            break;
                        }
                    }  // End of switch
                    }  // End of if (slope-based detection)
                } else {
                    // Not in DOWN state, reset single click detection
                    if (s_click_state != CLICK_STATE_IDLE) {
                        s_click_state = CLICK_STATE_IDLE;
                        s_click_initialized = false;
                        s_slope_change_count_x = 0;
                        s_slope_change_count_y = 0;
                        s_click_start_time = 0;
                        s_y_drop_initialized = false;
                        s_click_initial_y = 0;
                        s_y_drop_min_value = 0;
                    }
                }
#endif
                
                // 4.5. Pairing mode detection (pairing and pairing cancelled)
                // Pairing event: average drops more than MAG_PAIRING_DROP_THRESHOLD from s_calibrated_removed_center
                // Pairing cancelled event: average recovers from pairing state back to REMOVED center
                // Only valid when slider is in REMOVED position (after REMOVE_FROM_UP or REMOVE_FROM_DOWN event)
                static bool s_pairing_event_triggered = false;  // Track if pairing event was already triggered
                static int16_t s_pairing_last_average = 0;  // Last average value for pairing detection
                static uint8_t s_pairing_stable_count = 0;  // Stability counter for pairing detection
                static int16_t s_pairing_cancelled_last_average = 0;  // Last average value for pairing cancelled detection
                static uint8_t s_pairing_cancelled_stable_count = 0;  // Stability counter for pairing cancelled detection
                
                // Only detect pairing when slider is in REMOVED position
                if (s_last_stable_position == MAG_POSITION_REMOVED) {
                    // Calculate drop from REMOVED center
                    int16_t drop_from_removed = s_calibrated_removed_center - average;
                    
                    // 4.5.1. Pairing cancelled detection (check first, before pairing detection)
                    // Pairing cancelled event: average recovers from pairing state back to REMOVED center
                    // Only valid when pairing was previously triggered (s_pairing_event_triggered == true)
                    if (s_pairing_event_triggered) {
                        // Check if average has recovered back to REMOVED center (drop < MAG_PAIRING_DROP_THRESHOLD)
                        if (drop_from_removed < MAG_PAIRING_DROP_THRESHOLD) {
                            // Average has recovered, check stability
                            if (s_pairing_cancelled_last_average == 0) {
                                // First reading, initialize
                                s_pairing_cancelled_last_average = average;
                                s_pairing_cancelled_stable_count = 1;
                            } else {
                                int16_t cancelled_diff = abs(average - s_pairing_cancelled_last_average);
                                if (cancelled_diff <= 10) {  // Value is stable (within 10 units)
                                    s_pairing_cancelled_stable_count++;
                                    
                                    if (s_pairing_cancelled_stable_count >= MAG_STABLE_THRESHOLD) {
                                        // "Pairing cancelled" state is stable, trigger event
                                        ESP_LOGI(TAG, "PAIRING_CANCELLED detected (average: %d, REMOVED center: %d, drop: %d < %d)", 
                                                 average, s_calibrated_removed_center, drop_from_removed, MAG_PAIRING_DROP_THRESHOLD);
                                        
                                        // Send event notification
                                        control_serial_send_magnetic_switch_event(MAGNETIC_SLIDE_SWITCH_EVENT_PAIRING_CANCELLED);
                                        
                                        // Reset pairing state to allow new detection cycle
                                        s_pairing_event_triggered = false;
                                        
                                        // Reset counters for next detection cycle
                                        s_pairing_cancelled_last_average = 0;
                                        s_pairing_cancelled_stable_count = 0;
                                        s_pairing_last_average = 0;
                                        s_pairing_stable_count = 0;
                                    }
                                } else {
                                    // Value changed, reset
                                    s_pairing_cancelled_last_average = average;
                                    s_pairing_cancelled_stable_count = 1;
                                }
                            }
                        } else {
                            // Average is still in pairing range, reset cancelled detection
                            s_pairing_cancelled_last_average = 0;
                            s_pairing_cancelled_stable_count = 0;
                        }
                    } else {
                        // Pairing not triggered, reset cancelled detection
                        s_pairing_cancelled_last_average = 0;
                        s_pairing_cancelled_stable_count = 0;
                    }
                    
                    // 4.5.2. Pairing detection (check after cancelled detection)
                    // Check if average dropped more than threshold
                    if (drop_from_removed >= MAG_PAIRING_DROP_THRESHOLD && !s_pairing_event_triggered) {
                        // Pairing condition met, check stability
                        if (s_pairing_last_average == 0) {
                            // First reading, initialize
                            s_pairing_last_average = average;
                            s_pairing_stable_count = 1;
                        } else {
                            int16_t pairing_diff = abs(average - s_pairing_last_average);
                            if (pairing_diff <= 10) {  // Value is stable (within 10 units)
                                s_pairing_stable_count++;
                                
                                if (s_pairing_stable_count >= MAG_STABLE_THRESHOLD) {
                                    // Pairing state is stable, trigger event
                                    s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_PAIRING;
                                    ESP_LOGI(TAG, "PAIRING mode detected (average: %d, REMOVED center: %d, drop: %d >= %d)", 
                                             average, s_calibrated_removed_center, drop_from_removed, MAG_PAIRING_DROP_THRESHOLD);
                                    
                                    // Send event notification
                                    control_serial_send_magnetic_switch_event(s_slider_state);
                                    
                                    // Mark as triggered to avoid repeated triggers
                                    s_pairing_event_triggered = true;
                                    
                                    // Reset counters for next detection cycle
                                    s_pairing_last_average = 0;
                                    s_pairing_stable_count = 0;
                                }
                            } else {
                                // Value changed, reset
                                s_pairing_last_average = average;
                                s_pairing_stable_count = 1;
                            }
                        }
                    } else if (drop_from_removed < MAG_PAIRING_DROP_THRESHOLD && !s_pairing_event_triggered) {
                        // Average is not low enough and pairing not triggered, reset detection
                        s_pairing_last_average = 0;
                        s_pairing_stable_count = 0;
                    }
                } else {
                    // Not in REMOVED position, reset pairing detection
                    if (s_pairing_event_triggered) {
                        s_pairing_event_triggered = false;
                    }
                    s_pairing_last_average = 0;
                    s_pairing_stable_count = 0;
                    s_pairing_cancelled_last_average = 0;
                    s_pairing_cancelled_stable_count = 0;
                }
                
                // 5. Fish detection (attached and detached)
                // Fish event: only trigger when fish is placed from UP position
                // When fish is placed from UP: average increases 150-200 from s_calibrated_removed_center
                // When fish is detached: average recovers back to s_calibrated_removed_center
                // Only valid after REMOVE_FROM_UP or REMOVE_FROM_DOWN event
                static bool s_fish_state_detected = false;  // Track if "the fish is attached" state was already detected
                static bool s_fish_detection_enabled = false;  // Enable detection only after remove event
                static int16_t s_fish_candidate_average = 0;
                static uint8_t s_fish_stable_count = 0;
                static int16_t s_fish_detached_candidate_average = 0;
                static uint8_t s_fish_detached_stable_count = 0;
                
                // Check if average indicates fish placed from UP position
                // Average should increase 150-200 from REMOVED center
                int16_t diff_from_removed = average - s_calibrated_removed_center;
                bool is_fish_state = (diff_from_removed >= FISH_FROM_UP_MIN_DIFF && 
                                      diff_from_removed <= FISH_FROM_UP_MAX_DIFF);
                
                // 5.1. Fish detached detection (check first, before fish attached detection)
                // Fish detached event: average recovers from fish attached state back to REMOVED center
                // Only valid when fish was previously attached (s_fish_state_detected == true)
                if (s_fish_state_detected && s_fish_detection_enabled && s_last_stable_position == MAG_POSITION_REMOVED) {
                    // Check if average has recovered back to REMOVED center (diff < FISH_FROM_UP_MIN_DIFF)
                    if (diff_from_removed < FISH_FROM_UP_MIN_DIFF) {
                        // Average has recovered, check stability
                        if (s_fish_detached_candidate_average == 0) {
                            // First reading, initialize
                            s_fish_detached_candidate_average = average;
                            s_fish_detached_stable_count = 1;
                        } else {
                            int16_t detached_diff = abs(average - s_fish_detached_candidate_average);
                            if (detached_diff <= 10) {  // Value is stable (within 10 units)
                                s_fish_detached_stable_count++;
                                
                                if (s_fish_detached_stable_count >= MAG_STABLE_THRESHOLD * 2) {
                                    // "Fish detached" state is stable, trigger event
                                    int16_t log_diff_from_removed = average - s_calibrated_removed_center;
                                    ESP_LOGI(TAG, "Fish detached detected (average: %d, REMOVED: %d, diff_from_removed: %d < %d)", 
                                             average, s_calibrated_removed_center, 
                                             log_diff_from_removed, FISH_FROM_UP_MIN_DIFF);
                                    
                                    // Send event notification
                                    control_serial_send_magnetic_switch_event(MAGNETIC_SLIDE_SWITCH_EVENT_FISH_DETACHED);
                                    
                                    // Reset fish attached state to allow new detection cycle
                                    s_fish_state_detected = false;
                                    
                                    // Reset counters for next detection cycle
                                    s_fish_detached_candidate_average = 0;
                                    s_fish_detached_stable_count = 0;
                                    s_fish_candidate_average = 0;
                                    s_fish_stable_count = 0;
                                }
                            } else {
                                // Value changed, reset
                                s_fish_detached_candidate_average = average;
                                s_fish_detached_stable_count = 1;
                            }
                        }
                    } else {
                        // Average is still in fish attached range, reset detached detection
                        s_fish_detached_candidate_average = 0;
                        s_fish_detached_stable_count = 0;
                    }
                } else {
                    // Fish not attached or detection disabled, reset detached detection
                    s_fish_detached_candidate_average = 0;
                    s_fish_detached_stable_count = 0;
                }
                
                // 5.2. Fish attached detection (check after detached detection)
                // Only detect "the fish is attached" state if:
                // 1. Detection is enabled (after remove event)
                // 2. Average is above pairing threshold (disable fish detection during pairing mode)
                // 3. Last stable position is REMOVED (slider was removed)
                // 4. Fish is not already detected (s_fish_state_detected == false)
                // When average < MAG_PAIRING_THRESHOLD, pairing mode is active, so fish detection is disabled
                if (is_fish_state && s_fish_detection_enabled && s_last_stable_position == MAG_POSITION_REMOVED && !s_fish_state_detected) {
                    // Check if value is stable (using same stability mechanism)
                    if (s_fish_candidate_average == 0) {
                        // First reading, initialize
                        s_fish_candidate_average = average;
                        s_fish_stable_count = 1;
                    } else {
                        int16_t fish_diff = abs(average - s_fish_candidate_average);
                        if (fish_diff <= 10) {  // Value is stable (within 10 units)
                            s_fish_stable_count++;
                            
                            if (s_fish_stable_count >= MAG_STABLE_THRESHOLD * 2) {
                                // "the fish is attached" state is stable, trigger event
                                // Recalculate difference for logging
                                int16_t log_diff_from_removed = average - s_calibrated_removed_center;
                                ESP_LOGI(TAG, "Fish attached (from UP position) detected (average: %d, REMOVED: %d, diff_from_removed: %d [%d-%d])", 
                                         average, s_calibrated_removed_center, 
                                         log_diff_from_removed, FISH_FROM_UP_MIN_DIFF, FISH_FROM_UP_MAX_DIFF);
                                control_serial_send_magnetic_switch_event(MAGNETIC_SLIDE_SWITCH_EVENT_FISH_ATTACHED);
                                s_fish_state_detected = true;
                                
                                // Keep detection enabled, but reset counters for next detection cycle
                                s_fish_candidate_average = 0;
                                s_fish_stable_count = 0;
                            }
                        } else {
                            // Value changed, reset
                            s_fish_candidate_average = average;
                            s_fish_stable_count = 1;
                        }
                    }
                } else if (!is_fish_state && !s_fish_state_detected) {
                    // Not in "the fish is attached" state and fish not detected, reset detection counters
                    // But keep s_fish_detection_enabled flag (only reset when detection succeeds or on new remove event)
                    s_fish_candidate_average = 0;
                    s_fish_stable_count = 0;
                }
                
                // Enable/disable fish detection based on remove/place events
                // This is handled in the event detection section above
                // Update fish detection enabled flag based on last event
                static magnetic_slide_switch_event_t s_last_event = MAGNETIC_SLIDE_SWITCH_EVENT_INIT;
                if (s_slider_state != s_last_event && s_slider_state != MAGNETIC_SLIDE_SWITCH_EVENT_INIT) {
                    if (s_slider_state == MAGNETIC_SLIDE_SWITCH_EVENT_REMOVE_FROM_UP || 
                        s_slider_state == MAGNETIC_SLIDE_SWITCH_EVENT_REMOVE_FROM_DOWN) {
                        s_fish_detection_enabled = true;
                        s_fish_state_detected = false;  // Reset detection flag to allow new detection
                        s_fish_candidate_average = 0;
                        s_fish_stable_count = 0;
                        ESP_LOGI(TAG, "Fish detection enabled after remove event");
                    }
                    else if (s_slider_state == MAGNETIC_SLIDE_SWITCH_EVENT_PLACE_FROM_UP || 
                             s_slider_state == MAGNETIC_SLIDE_SWITCH_EVENT_PLACE_FROM_DOWN) {
                        s_fish_detection_enabled = false;
                        s_fish_state_detected = false;
                        s_fish_candidate_average = 0;
                        s_fish_stable_count = 0;
                        ESP_LOGI(TAG, "Fish detection disabled after place event");
                    }
                    s_last_event = s_slider_state;
                }
            }
        }
        
        vTaskDelay(pdMS_TO_TICKS(MAG_SAMPLE_PERIOD_MS));
    }
    
    ESP_LOGI(TAG, "Slide switch event detection task finished.");
    vTaskDelete(NULL);
}

#endif  // CONFIG_SENSOR_MAGNETOMETER

/* ========== Public Function Implementations ========== */

/**
 * @brief Start magnetic slide switch task
 * 
 * @note Creates Hall sensor or magnetometer tasks based on configuration
 * @note For magnetometer: 
 *       1. Creates data read task
 *       2. Creates calibration task and waits for completion
 *       3. Creates slide switch detection task after calibration completes
 *       4. Optionally creates rotary knob detection task
 */
void magnetic_slide_switch_start(void)
{
#ifdef CONFIG_SENSOR_LINEAR_HALL
    xTaskCreate(hall_sensor_read_task, "hall_sensor_read_task", MAGNETIC_SENSOR_TASK_STACK_SIZE, NULL, 2, NULL);
#elif defined(CONFIG_SENSOR_MAGNETOMETER)
    /* Create synchronization objects before starting any worker.  Sensor
     * initialization can fail immediately, so creating the reader first
     * would otherwise race with a NULL semaphore and strand the caller. */
    atomic_store_explicit(&s_magnetometer_init_failed, false, memory_order_release);
    atomic_store_explicit(&s_magnetometer_stop_requested, false, memory_order_release);
    atomic_store_explicit(&s_sensor_init_signaled, false, memory_order_release);
    atomic_store_explicit(&s_magnetometer_ready, false, memory_order_release);

    s_sensor_init_complete_sem = xSemaphoreCreateBinary();
    if (s_sensor_init_complete_sem == NULL) {
        ESP_LOGE(TAG, "Failed to create sensor initialization semaphore; magnetic switch disabled");
        atomic_store_explicit(&s_magnetometer_init_failed, true, memory_order_release);
        return;
    }

    s_calibration_complete_sem = xSemaphoreCreateBinary();
    if (s_calibration_complete_sem == NULL) {
        ESP_LOGE(TAG, "Failed to create calibration completion semaphore; magnetic switch disabled");
        atomic_store_explicit(&s_magnetometer_init_failed, true, memory_order_release);
        vSemaphoreDelete(s_sensor_init_complete_sem);
        s_sensor_init_complete_sem = NULL;
        return;
    }

    // Create magnetometer data read task (shared data provider)
    const BaseType_t data_task_result = xTaskCreate(
        magnetometer_data_read_task, "mag_data_read_task",
        MAGNETIC_SENSOR_TASK_STACK_SIZE, NULL, 3, NULL
    );
    if (data_task_result != pdPASS) {
        ESP_LOGE(TAG, "Failed to create magnetometer data task");
        magnetometer_signal_init_result(false, ESP_ERR_NO_MEM);
        vSemaphoreDelete(s_calibration_complete_sem);
        vSemaphoreDelete(s_sensor_init_complete_sem);
        s_calibration_complete_sem = NULL;
        s_sensor_init_complete_sem = NULL;
        return;
    }

    /* Only wait for bounded hardware discovery here.  The later calibration
     * wait is intentionally unbounded because first-time calibration requires
     * the user to present three physical positions. */
    const TickType_t init_timeout = pdMS_TO_TICKS(MAGNETOMETER_INIT_TIMEOUT_MS);
    if (xSemaphoreTake(s_sensor_init_complete_sem, init_timeout) != pdTRUE) {
        ESP_LOGE(TAG,
                 "Magnetometer initialization timed out after %d ms; magnetic switch disabled",
                 MAGNETOMETER_INIT_TIMEOUT_MS);
        atomic_store_explicit(&s_magnetometer_init_failed, true, memory_order_release);
        atomic_store_explicit(&s_magnetometer_stop_requested, true, memory_order_release);
        return;
    }

    if (atomic_load_explicit(&s_magnetometer_init_failed, memory_order_acquire) ||
        !atomic_load_explicit(&s_magnetometer_ready, memory_order_acquire)) {
        ESP_LOGE(TAG, "Magnetometer identity check failed; magnetic switch events disabled");
        atomic_store_explicit(&s_magnetometer_stop_requested, true, memory_order_release);
        return;
    }

#if CONFIG_DESKBOT_MAGNETIC_SAMPLES_ONLY
    ESP_LOGI(TAG, "Samples-only mode: calibration and magnetic event tasks disabled; NVS unchanged");
    return;
#endif

    // Create calibration task (will signal completion via semaphore)
    const BaseType_t calibration_task_result = xTaskCreate(
        magnetometer_calibration_task, "mag_calibration_task",
        MAGNETIC_SENSOR_TASK_STACK_SIZE, NULL, 3, NULL
    );
    if (calibration_task_result != pdPASS) {
        ESP_LOGE(TAG, "Failed to create magnetometer calibration task");
        atomic_store_explicit(&s_magnetometer_init_failed, true, memory_order_release);
        atomic_store_explicit(&s_magnetometer_stop_requested, true, memory_order_release);
        return;
    }
    
    // Wait for calibration to complete (blocking wait)
    if (xSemaphoreTake(s_calibration_complete_sem, portMAX_DELAY) == pdTRUE) {
        if (atomic_load_explicit(&s_magnetometer_init_failed, memory_order_acquire)) {
            ESP_LOGE(TAG, "Magnetic switch disabled: magnetometer initialization failed");
            atomic_store_explicit(&s_magnetometer_stop_requested, true, memory_order_release);
            return;
        }
        ESP_LOGI(TAG, "Calibration completed, starting slide switch detection task");
        
        // Create slide switch event detection task (uses z-axis data)
        if (xTaskCreate(slide_switch_event_detect_task, "slide_switch_detect_task",
                        MAGNETIC_SENSOR_TASK_STACK_SIZE, NULL, 2, NULL) != pdPASS) {
            ESP_LOGE(TAG, "Failed to create slide switch detection task");
        }
    } else {
        ESP_LOGE(TAG, "Failed to wait for calibration completion");
    }
#endif
}

/**
 * @brief Get current magnetic slide switch event
 * 
 * @return magnetic_slide_switch_event_t Current event type
 */
magnetic_slide_switch_event_t magnetic_slide_switch_get_event(void)
{
    return s_slider_state;
}

/**
 * @brief Trigger recalibration (no restart needed, takes effect immediately)
 * 
 * @note This function sets recalibration flag, magnetometer_read_task will automatically
 *       re-enter calibration mode in next loop, after calibration completes it will
 *       overwrite previously saved calibration data
 */
void magnetic_slide_switch_start_recalibration(void)
{
    ESP_LOGI(TAG, "Recalibration request received");
#if CONFIG_SENSOR_MAGNETOMETER
    s_request_recalibration = true;
#else
    ESP_LOGW(TAG, "Recalibration is unavailable for the linear Hall sensor profile");
#endif
}
