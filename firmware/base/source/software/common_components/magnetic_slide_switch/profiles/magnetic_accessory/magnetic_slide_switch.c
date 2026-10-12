/*
 * SPDX-FileCopyrightText: 2024-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdio.h>
#include <stdlib.h>
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
#include "i2c_bus.h"
#include "magnetic_slide_switch.h"
#include "control_serial.h"

static const char *TAG = "Magnetic Slide Switch";

/* ========== NVS Storage Configuration ========== */
#define NVS_NAMESPACE       "mag_calib"     /**< NVS namespace */
#define NVS_KEY_REMOVED     "removed"       /**< REMOVED state center value key name */
#define NVS_KEY_UP          "up"            /**< UP state center value key name */
#define NVS_KEY_DOWN        "down"          /**< DOWN state center value key name */
#define NVS_KEY_CALIBRATED  "calibrated"    /**< Calibration flag key name */
/* Calibration values are sensor-specific.  Keep the legacy keys for
 * compatibility, but tag newly written values so a BMM150 calibration can
 * never be silently reused by a QMC6309 build (or vice versa). */
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

/* ========== Callback Function Management ========== */
/**
 * @brief Callback function list node structure
 */
typedef struct callback_node {
    magnetic_slide_switch_event_callback_t callback;  /**< Callback function pointer */
    struct callback_node *next;                      /**< Next node pointer */
} callback_node_t;

static callback_node_t *s_callback_list = NULL;      /**< Callback function list head */
static SemaphoreHandle_t s_callback_mutex = NULL;    /**< Mutex for protecting callback list */

#if CONFIG_SENSOR_LINEAR_HALL
    static int adc_raw[2][10];
    static int voltage[2][10];
    static int s_last_hall_voltage = 0;
    static bool s_first_hall_reading = true;
#endif

#if CONFIG_SENSOR_BMM150
static struct bmm150_dev s_bmm150 = { 0 };
static uint8_t s_bmm150_addr = BMM150_DEFAULT_I2C_ADDRESS;  /**< BMM150 default I2C address */
#endif

#ifdef CONFIG_SENSOR_MAGNETOMETER
/** I2C bus/device handles */
static i2c_bus_handle_t s_i2c_bus = NULL;
static i2c_bus_device_handle_t s_i2c_dev = NULL;
static uint8_t dev_addr;

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

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
#define DIAG_BMM150_I2C_ADDR  0x10
#define DIAG_BMM150_ID_REG    0x40
#define DIAG_BMM150_CHIP_ID   0x32
#define DIAG_QMC6309_I2C_ADDR 0x7C
#define DIAG_QMC6309_ID_REG   0x00
#define DIAG_QMC6309_CHIP_ID  0x90

static void diagnostic_probe_magnetometer(const char *sensor, uint8_t address,
                                           uint8_t id_register, uint8_t expected_id)
{
    i2c_bus_device_handle_t probe = i2c_bus_device_create(s_i2c_bus, address, 0);
    if (probe == NULL) {
        ESP_LOGI(TAG, "[diag] probe sensor=%s i2c_addr=0x%02X create=failed match=unknown",
                 sensor, address);
        return;
    }

    uint8_t chip_id = 0;
    esp_err_t read_ret = i2c_bus_read_byte(probe, id_register, &chip_id);
    if (read_ret == ESP_OK) {
        ESP_LOGI(TAG,
                 "[diag] probe sensor=%s i2c_addr=0x%02X id_reg=0x%02X read=%s chip_id=0x%02X expected=0x%02X match=%s",
                 sensor, address, id_register, esp_err_to_name(read_ret), chip_id,
                 expected_id, chip_id == expected_id ? "yes" : "no");
    } else {
        ESP_LOGI(TAG,
                 "[diag] probe sensor=%s i2c_addr=0x%02X id_reg=0x%02X read=%s match=unknown",
                 sensor, address, id_register, esp_err_to_name(read_ret));
    }

    esp_err_t delete_ret = i2c_bus_device_delete(&probe);
    if (delete_ret != ESP_OK) {
        ESP_LOGW(TAG, "[diag] probe cleanup sensor=%s result=%s",
                 sensor, esp_err_to_name(delete_ret));
    }
}

static void diagnostic_probe_magnetometers(void)
{
    diagnostic_probe_magnetometer("BMM150", DIAG_BMM150_I2C_ADDR,
                                  DIAG_BMM150_ID_REG, DIAG_BMM150_CHIP_ID);
    diagnostic_probe_magnetometer("QMC6309", DIAG_QMC6309_I2C_ADDR,
                                  DIAG_QMC6309_ID_REG, DIAG_QMC6309_CHIP_ID);
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

    while (1) {
        ESP_ERROR_CHECK(adc_oneshot_read(adc1_handle, HALL_SENSOR_ADC_CHANNEL, &adc_raw[0][0]));
        if (do_calibration1_chan0) {
            ESP_ERROR_CHECK(adc_cali_raw_to_voltage(adc1_cali_chan0_handle, adc_raw[0][0], &voltage[0][0]));
            
            // Update last voltage value (slider events disabled; only fish/pairing supported on magnetometer)
            s_last_hall_voltage = voltage[0][0];
            
            // ESP_LOGI(TAG, "Hall sensor voltage: %d mV", voltage[0][0]);
        }
        vTaskDelay(pdMS_TO_TICKS(HALL_SENSOR_SAMPLE_PERIOD_MS));
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

    esp_err_t ret = i2c_bus_read_bytes(s_i2c_dev, reg_addr, (uint16_t)length, reg_data);
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

    esp_err_t ret = i2c_bus_write_bytes(s_i2c_dev, reg_addr, (uint16_t)length, (uint8_t*)reg_data);
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
    
    /* I2C bus setup */
    const i2c_config_t i2c_conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_io_num = I2C_MASTER_SCL_IO,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_MASTER_FREQ_HZ
    };
    
    s_i2c_bus = i2c_bus_create(I2C_MASTER_NUM, &i2c_conf);
    if (s_i2c_bus == NULL) {
        ESP_LOGE(TAG, "I2C bus create failed");
        return BMM150_E_COM_FAIL;
    }

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    diagnostic_probe_magnetometers();
#endif
    
    /* Create I2C device handle */
    s_i2c_dev = i2c_bus_device_create(s_i2c_bus, device_addr, 0);
    if (s_i2c_dev == NULL) {
        ESP_LOGE(TAG, "I2C device create failed for address 0x%02X", device_addr);
        i2c_bus_delete(&s_i2c_bus);
        s_i2c_bus = NULL;
        return BMM150_E_COM_FAIL;
    }
    
    dev_addr = device_addr;
    ESP_LOGI(TAG, "I2C interface initialized (bus + device at 0x%02X)", device_addr);
    
    return BMM150_OK;
}

/**
 * @brief BMM150 interface deinitialization
 */
void bmm150_coines_deinit(void)
{
    if (s_i2c_dev) {
        i2c_bus_device_delete(&s_i2c_dev);
        s_i2c_dev = NULL;
    }
    if (s_i2c_bus) {
        i2c_bus_delete(&s_i2c_bus);
        s_i2c_bus = NULL;
    }
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

        if (s_bmm150.chip_id != MAGNETOMETER_CHIP_ID) {
            bmm150_coines_deinit();
            continue;
        }

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
        ESP_LOGI(TAG, "[diag] sensor=BMM150 i2c_addr=0x%02X chip_id=0x%02X expected=0x%02X",
                 addr, s_bmm150.chip_id, MAGNETOMETER_CHIP_ID);
#endif

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

static uint8_t s_qmc6309_chip_id = 0;

/* ========== QMC6309 Geomagnetic Sensor Related Functions ========== */

/**
 * @brief QMC6309 interface initialization
 * 
 * @return esp_err_t ESP_OK indicates success
 */
static esp_err_t qmc6309_interface_init(void)
{
    const i2c_config_t i2c_conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_io_num = I2C_MASTER_SCL_IO,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_MASTER_FREQ_HZ
    };

    s_i2c_bus = i2c_bus_create(I2C_MASTER_NUM, &i2c_conf);

    if (s_i2c_bus == NULL) {
        ESP_LOGE(TAG, "I2C bus create failed");
        return ESP_FAIL;
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
    return i2c_bus_write_bytes(s_i2c_dev, reg, 1, &val);
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
    return i2c_bus_read_bytes(s_i2c_dev, reg, len, data);
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
    s_i2c_dev = i2c_bus_device_create(s_i2c_bus, MAGNETOMETER_I2C_ADDR, 0);
    if (s_i2c_dev == NULL) {
        ESP_LOGE(TAG, "Failed to create I2C device for QMC6309");
        return ESP_FAIL;
    }

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    esp_err_t id_ret = qmc_read_reg(0x00, &s_qmc6309_chip_id, 1);
    ESP_LOGI(TAG, "[diag] sensor=QMC6309 i2c_addr=0x%02X chip_id=0x%02X expected=0x%02X read=%s",
             MAGNETOMETER_I2C_ADDR, s_qmc6309_chip_id, MAGNETOMETER_CHIP_ID,
             esp_err_to_name(id_ret));
#else
    esp_err_t id_ret = qmc_read_reg(0x00, &s_qmc6309_chip_id, 1);
#endif

    if (id_ret != ESP_OK || s_qmc6309_chip_id != MAGNETOMETER_CHIP_ID) {
        ESP_LOGE(TAG,
                 "QMC6309 identity check failed: read=%s chip_id=0x%02X expected=0x%02X",
                 esp_err_to_name(id_ret), s_qmc6309_chip_id,
                 MAGNETOMETER_CHIP_ID);
        return id_ret == ESP_OK ? ESP_ERR_NOT_FOUND : id_ret;
    }

    /* Soft reset */
    ESP_ERROR_CHECK(qmc_write_reg(0x0B, 0x80));
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_ERROR_CHECK(qmc_write_reg(0x0B, 0x00));
    vTaskDelay(pdMS_TO_TICKS(10));

    /* Continuous mode, ±32G, ODR=200Hz, OSR=8/8 */
    ESP_ERROR_CHECK(qmc_write_reg(0x0B, 0x00));  // RNG=00
    ESP_ERROR_CHECK(qmc_write_reg(0x0A, 0x63));  // MODE=11, OSR=8
    
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
    uint8_t status = 0;
    /* Wait briefly for data ready, but never abort the whole firmware if the
     * bus disappears.  A bounded poll also keeps the reader task schedulable
     * during a loose cable or sensor reset. */
    for (int poll = 0; poll < 20; ++poll) {
        esp_err_t status_ret = qmc_read_reg(0x09, &status, 1);
        if (status_ret != ESP_OK) {
            return status_ret;
        }
        if ((status & 0x01) != 0) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
        if (poll == 19) {
            return ESP_ERR_TIMEOUT;
        }
    }
    
    uint8_t data[6];
    esp_err_t data_ret = qmc_read_reg(0x01, data, 6);
    if (data_ret != ESP_OK) {
        return data_ret;
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
    if (s_i2c_dev) {
        i2c_bus_device_delete(&s_i2c_dev);
        s_i2c_dev = NULL;
    }
    if (s_i2c_bus) {
        i2c_bus_delete(&s_i2c_bus);
        s_i2c_bus = NULL;
    }
    ESP_LOGI(TAG, "QMC6309 interface deinitialized");
    return ESP_OK;
}
#endif  // CONFIG_SENSOR_BMM150 / CONFIG_SENSOR_QMC6309

#if CONFIG_SENSOR_MAGNETOMETER

static void notify_event_callbacks(magnetic_slide_switch_event_t event);

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

    /* Legacy images stored only the three center values.  They are not safe
     * to use after changing the magnetometer model because the units and
     * ranges differ.  Treat missing or mismatched tags as an intentional
     * request for calibration; leave the old keys untouched in NVS. */
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
    ESP_LOGI(TAG, "Magnetometer data read task started");

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
    TickType_t last_diag_tick = 0;
#endif
    
    // Initialize magnetometer sensor
    if (magnetometer_sensor_init() != ESP_OK) {
        ESP_LOGE(TAG, "Magnetometer sensor initialization failed");
        goto cleanup;
    }
    
    // Create mutex for protecting shared data
    s_mag_data_mutex = xSemaphoreCreateMutex();
    if (s_mag_data_mutex == NULL) {
        ESP_LOGE(TAG, "Failed to create mutex for magnetometer data");
        goto cleanup;
    }
    
    // Initialize shared data
    s_mag_data.x = 0;
    s_mag_data.y = 0;
    s_mag_data.z = 0;
    s_mag_data.valid = false;
    
    while (1) {
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
        } else {
            ESP_LOGE(TAG, "Failed to read magnetometer data: %d", ret);
        }

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
        TickType_t now = xTaskGetTickCount();
        if ((ret == ESP_OK || ret == BMM150_OK) &&
            (now - last_diag_tick >= pdMS_TO_TICKS(500))) {
            ESP_LOGI(TAG, "[diag] sample x=%d y=%d z=%d (shared/processed units)",
                     mag_x, mag_y, mag_z);
            last_diag_tick = now;
        }
#endif
        
        vTaskDelay(pdMS_TO_TICKS(MAG_SAMPLE_PERIOD_MS));
    }

cleanup:
#ifdef CONFIG_SENSOR_BMM150
    bmm150_coines_deinit();
#elif defined(CONFIG_SENSOR_QMC6309)
    qmc6309_interface_deinit();
#endif
    if (s_mag_data_mutex != NULL) {
        vSemaphoreDelete(s_mag_data_mutex);
        s_mag_data_mutex = NULL;
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
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
        ESP_LOGI(TAG, "[diag] calibration_loaded removed=%d up=%d down=%d",
                 s_calibrated_removed_center, s_calibrated_up_center,
                 s_calibrated_down_center);
#endif
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
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
        ESP_LOGI(TAG, "[diag] calibration_started reason=%s", esp_err_to_name(load_err));
#endif
        s_calibration_state = CALIBRATION_DETECTING_FIRST;
    }
    
    while (1) {
        // Check if recalibration is needed
        if (s_request_recalibration && s_calibration_state == CALIBRATION_COMPLETED) {
            ESP_LOGI(TAG, "========== Re-calibration Requested ==========");
            ESP_LOGI(TAG, "Starting automatic recalibration...");
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
            ESP_LOGI(TAG, "[diag] recalibration_started");
#endif
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
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
                                        ESP_LOGI(TAG, "[diag] calibration_position index=1 value=%d", average);
#endif
                                        
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
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
                                        ESP_LOGI(TAG, "[diag] calibration_position index=2 value=%d diff_from_first=%d",
                                                 average, diff_from_first);
#endif
                                        
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
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
                                        ESP_LOGI(TAG, "[diag] calibration_position index=3 value=%d diff_from_first=%d diff_from_second=%d",
                                                 average, diff_from_first, diff_from_second);
#endif
                                        
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
#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
                                        ESP_LOGI(TAG, "[diag] calibration_centers removed=%d up=%d down=%d",
                                                 s_calibrated_removed_center, s_calibrated_up_center,
                                                 s_calibrated_down_center);
#endif
                                        
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
    
    while (1) {
        
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
                
                // Position detection: determine position by which calibrated range the average falls in
                int16_t current_center = s_calibrated_removed_center;
                mag_position_t current_center_position = MAG_POSITION_REMOVED;
                bool is_near_center = false;

                if (average >= calibrated_up_min && average <= calibrated_up_max) {
                    current_center = s_calibrated_up_center;
                    current_center_position = MAG_POSITION_UP;
                    is_near_center = true;
                } else if (average >= calibrated_down_min && average <= calibrated_down_max) {
                    current_center = s_calibrated_down_center;
                    current_center_position = MAG_POSITION_DOWN;
                    is_near_center = true;
                } else if (average >= calibrated_removed_min && average <= calibrated_removed_max) {
                    current_center = s_calibrated_removed_center;
                    current_center_position = MAG_POSITION_REMOVED;
                    is_near_center = true;
                }

                // Track last stable center value and position
                static int16_t s_last_stable_center = 0;
                static mag_position_t s_last_stable_position = MAG_POSITION_UNKNOWN;
                static int16_t s_candidate_center = 0;
                static mag_position_t s_candidate_position = MAG_POSITION_UNKNOWN;
                static uint8_t s_stable_count = 0;
                
                if (is_near_center) {
                    // Average is near a center value
                    if (current_center == s_candidate_center && current_center_position == s_candidate_position) {
                        // Same candidate, accumulate stable count
                        s_stable_count++;
                        
                        // Check if stable enough to confirm position change
                        if (s_stable_count >= MAG_STABLE_THRESHOLD) {
                            // Position is stable, check if it's different from last stable position
                            if (s_last_stable_position != current_center_position && s_last_stable_position != MAG_POSITION_UNKNOWN) {
                                // Position changed, update stable position
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
                
                // Pairing mode detection (pairing and pairing cancelled)
                // Pairing event: average drops more than MAG_PAIRING_DROP_THRESHOLD from s_calibrated_removed_center
                // Pairing cancelled event: average recovers from pairing state back to REMOVED center
                // Only valid when slider is in REMOVED position
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
                                        s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_PAIRING_CANCELLED;
                                        notify_event_callbacks(MAGNETIC_SLIDE_SWITCH_EVENT_PAIRING_CANCELLED);
                                        
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
                                    notify_event_callbacks(s_slider_state);
                                    
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
                
                // Fish / Ice cream / Donut: diff_z 由动态基线算出，随环境磁场自适应（diff_z = average - 动态基线）
                static int16_t s_dynamic_baseline = 0;
                static bool s_dynamic_baseline_inited = false;
                static bool s_baseline_locked = false;      /* 变化大时锁定，防止吸上配件后基线被更新导致 diff_z 回落 */
                static bool s_pending_fish = false;
                static TickType_t s_pending_fish_start = 0;
                static bool s_pending_ice_cream = false;
                static TickType_t s_pending_ice_cream_start = 0;
                static bool s_fish_attached = false;
                static bool s_ice_cream_attached = false;
                static bool s_donut_attached = false;
                if (!s_dynamic_baseline_inited) {
                    s_dynamic_baseline = average;
                    s_dynamic_baseline_inited = true;
                }
                int16_t diff_z = average - s_dynamic_baseline;
                /* 基线锁定：diff_z 超过阈值视为“变化大”（有配件或大干扰），锁定基线；回落到解锁阈值以下再解锁 */
                if (diff_z > DIFF_Z_BASELINE_LOCK_THRESHOLD) {
                    s_baseline_locked = true;
                } else if (diff_z < DIFF_Z_BASELINE_UNLOCK_THRESHOLD) {
                    s_baseline_locked = false;
                }
                if (!s_baseline_locked && !s_fish_attached && !s_ice_cream_attached && !s_donut_attached && !s_pending_fish && !s_pending_ice_cream && diff_z < FISH_ATTACHED_Z_MIN) {
                    int16_t delta = average - s_dynamic_baseline;
                    s_dynamic_baseline += (delta >> DYNAMIC_BASELINE_EMA_SHIFT);
                    diff_z = average - s_dynamic_baseline;
                }

#if CONFIG_DESKBOT_BASE_DIAGNOSTICS
                TickType_t diag_now = xTaskGetTickCount();
                if (diag_now - last_diag_tick >= pdMS_TO_TICKS(500)) {
                    ESP_LOGI(TAG,
                             "[diag] detect avg=%d x=%d y=%d z=%d baseline=%d diff_z=%d locked=%s position=%d candidate=%d stable=%u pairing=%s",
                             average, mag_x, mag_y, mag_z, s_dynamic_baseline, diff_z,
                             s_baseline_locked ? "yes" : "no",
                             (int)s_last_stable_position, (int)s_candidate_position,
                             s_stable_count, s_pairing_event_triggered ? "yes" : "no");
                    last_diag_tick = diag_now;
                }
#endif

                // ESP_LOGI(TAG, "diff_z: avg=%d baseline=%d diff_z=%d", average, s_dynamic_baseline, diff_z);

                TickType_t now = xTaskGetTickCount();
                if (s_pending_fish) {
                    if ((now - s_pending_fish_start) >= pdMS_TO_TICKS(ACCESSORY_CONFIRM_MS)) {
                        if (diff_z >= FISH_ATTACHED_Z_MIN && diff_z <= FISH_ATTACHED_Z_MAX) {
                                ESP_LOGI(TAG, "喂鱼 (喂鱼, 200ms 无雪糕/甜甜圈确认): avg=%d, baseline=%d, diff_z=%d", average, s_dynamic_baseline, diff_z);
                            s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_FISH_ATTACHED;
                            notify_event_callbacks(MAGNETIC_SLIDE_SWITCH_EVENT_FISH_ATTACHED);
                            s_fish_attached = true;
                        }
                        s_pending_fish = false;
                    } else if (diff_z < FISH_ATTACHED_Z_MIN || diff_z > FISH_ATTACHED_Z_MAX) {
                        s_pending_fish = false;
                    }
                }
                if (s_pending_ice_cream) {
                    if ((now - s_pending_ice_cream_start) >= pdMS_TO_TICKS(ACCESSORY_CONFIRM_MS)) {
                        if (diff_z >= ICE_CREAM_ATTACHED_Z_MIN && diff_z <= ICE_CREAM_ATTACHED_Z_MAX) {
                                ESP_LOGI(TAG, "Ice cream attached (吃雪糕, 200ms 无甜甜圈确认): avg=%d, baseline=%d, diff_z=%d", average, s_dynamic_baseline, diff_z);
                            s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_ICE_CREAM_ATTACHED;
                            notify_event_callbacks(MAGNETIC_SLIDE_SWITCH_EVENT_ICE_CREAM_ATTACHED);
                            s_ice_cream_attached = true;
                        }
                        s_pending_ice_cream = false;
                    } else if (diff_z < ICE_CREAM_ATTACHED_Z_MIN || diff_z > ICE_CREAM_ATTACHED_Z_MAX) {
                        s_pending_ice_cream = false;
                    }
                }

                static int16_t s_fish_last_z = 0;
                static uint8_t s_fish_stable_count = 0;

                if (s_fish_attached) {
                    // Z left fish range upward (going to ice cream): silently clear fish state, do not notify fish detached
                    if (diff_z > FISH_ATTACHED_Z_MAX) {
                        s_fish_attached = false;
                        s_fish_stable_count = 0;
                        s_fish_last_z = 0;
                    }
                    // Fish detached: Z recovers back to near REMOVED center (diff_z < FISH_ATTACHED_Z_MIN)
                    else if (diff_z < FISH_ATTACHED_Z_MIN) {
                        if (s_fish_last_z != 0 && abs(average - s_fish_last_z) <= 10) {
                            s_fish_stable_count++;
                            if (s_fish_stable_count >= MAG_STABLE_THRESHOLD) {
                                ESP_LOGI(TAG, "不喂鱼 (不喂鱼, diff_z 回落): avg=%d, baseline=%d, diff_z=%d", average, s_dynamic_baseline, diff_z);
                                s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_FISH_DETACHED;
                                notify_event_callbacks(MAGNETIC_SLIDE_SWITCH_EVENT_FISH_DETACHED);
                                s_fish_attached = false;
                                s_fish_stable_count = 0;
                                s_fish_last_z = 0;
                            }
                        } else {
                            s_fish_last_z = average;
                            s_fish_stable_count = 1;
                        }
                    } else {
                        s_fish_stable_count = 0;
                        s_fish_last_z = 0;
                    }
                } else {
                    // Fish attached: Z in [REMOVED+120, REMOVED+160], and Z must not be rising (avoid false trigger when passing through to ice cream)
                    if (diff_z >= FISH_ATTACHED_Z_MIN && diff_z <= FISH_ATTACHED_Z_MAX) {
                        if (s_fish_last_z != 0 && (average - s_fish_last_z) > FISH_RISE_RESET_THRESHOLD) {
                            // Z is rising in fish range = passing through to ice cream, reset fish candidate
                            s_fish_last_z = average;
                            s_fish_stable_count = 1;
                        } else if (s_fish_last_z != 0 && abs(average - s_fish_last_z) <= 10) {
                            s_fish_stable_count++;
                            if (s_fish_stable_count >= MAG_STABLE_THRESHOLD && !s_pending_fish && !s_fish_attached) {
                                s_pending_fish = true;
                                s_pending_fish_start = xTaskGetTickCount();
                                s_fish_stable_count = 0;
                                s_fish_last_z = 0;
                            }
                        } else {
                            s_fish_last_z = average;
                            s_fish_stable_count = 1;
                        }
                    } else {
                        s_fish_stable_count = 0;
                        s_fish_last_z = 0;
                    }
                }

                // Ice cream event (吃雪糕/不吃雪糕, Z-axis only): Z in [REMOVED+205, REMOVED+245] = ice cream attached; Z back to REMOVED = ice cream detached
                static int16_t s_ice_cream_last_z = 0;
                static uint8_t s_ice_cream_stable_count = 0;

                if (s_ice_cream_attached) {
                    if (diff_z > ICE_CREAM_ATTACHED_Z_MAX) {
                        s_ice_cream_attached = false;
                        s_ice_cream_stable_count = 0;
                        s_ice_cream_last_z = 0;
                    } else if (diff_z < ICE_CREAM_ATTACHED_Z_MIN) {
                        if (s_ice_cream_last_z != 0 && abs(average - s_ice_cream_last_z) <= 10) {
                            s_ice_cream_stable_count++;
                            if (s_ice_cream_stable_count >= MAG_STABLE_THRESHOLD) {
                                ESP_LOGI(TAG, "Ice cream detached (不吃雪糕, Z back to REMOVED): avg=%d, baseline=%d, diff_z=%d", average, s_dynamic_baseline, diff_z);
                                s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_ICE_CREAM_DETACHED;
                                notify_event_callbacks(MAGNETIC_SLIDE_SWITCH_EVENT_ICE_CREAM_DETACHED);
                                s_ice_cream_attached = false;
                                s_ice_cream_stable_count = 0;
                                s_ice_cream_last_z = 0;
                            }
                        } else {
                            s_ice_cream_last_z = average;
                            s_ice_cream_stable_count = 1;
                        }
                    } else {
                        s_ice_cream_stable_count = 0;
                        s_ice_cream_last_z = 0;
                    }
                } else {
                    if (diff_z >= ICE_CREAM_ATTACHED_Z_MIN && diff_z <= ICE_CREAM_ATTACHED_Z_MAX) {
                        if (s_ice_cream_last_z != 0 && (average - s_ice_cream_last_z) > ICE_CREAM_RISE_RESET_THRESHOLD) {
                            s_ice_cream_last_z = average;
                            s_ice_cream_stable_count = 1;
                        } else if (s_ice_cream_last_z != 0 && abs(average - s_ice_cream_last_z) <= 10) {
                            s_ice_cream_stable_count++;
                            if (s_ice_cream_stable_count >= MAG_STABLE_THRESHOLD && !s_pending_ice_cream && !s_ice_cream_attached) {
                                s_pending_fish = false;
                                s_pending_ice_cream = true;
                                s_pending_ice_cream_start = xTaskGetTickCount();
                                s_ice_cream_stable_count = 0;
                                s_ice_cream_last_z = 0;
                            }
                        } else {
                            s_ice_cream_last_z = average;
                            s_ice_cream_stable_count = 1;
                        }
                    } else {
                        s_ice_cream_stable_count = 0;
                        s_ice_cream_last_z = 0;
                    }
                }

                // Donut event (吃甜甜圈/不吃甜甜圈, Z-axis only): Z in [REMOVED+314, REMOVED+334] = donut attached; Z back to REMOVED = donut detached
                static int16_t s_donut_last_z = 0;
                static uint8_t s_donut_stable_count = 0;

                if (s_donut_attached) {
                    if (diff_z < DONUT_ATTACHED_Z_MIN) {
                        if (s_donut_last_z != 0 && abs(average - s_donut_last_z) <= 10) {
                            s_donut_stable_count++;
                            if (s_donut_stable_count >= MAG_STABLE_THRESHOLD) {
                                ESP_LOGI(TAG, "不吃甜甜圈 (不吃甜甜圈, Z back to REMOVED): avg=%d, baseline=%d, diff_z=%d", average, s_dynamic_baseline, diff_z);
                                s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_DONUT_DETACHED;
                                notify_event_callbacks(MAGNETIC_SLIDE_SWITCH_EVENT_DONUT_DETACHED);
                                s_donut_attached = false;
                                s_donut_stable_count = 0;
                                s_donut_last_z = 0;
                            }
                        } else {
                            s_donut_last_z = average;
                            s_donut_stable_count = 1;
                        }
                    } else {
                        s_donut_stable_count = 0;
                        s_donut_last_z = 0;
                    }
                } else {
                    if (diff_z >= DONUT_ATTACHED_Z_MIN && diff_z <= DONUT_ATTACHED_Z_MAX) {
                        if (s_donut_last_z != 0 && abs(average - s_donut_last_z) <= 10) {
                            s_donut_stable_count++;
                            if (s_donut_stable_count >= MAG_STABLE_THRESHOLD) {
                                s_pending_fish = false;
                                s_pending_ice_cream = false;
                                ESP_LOGI(TAG, "吃甜甜圈 (吃甜甜圈, Z in [%d,%d]): avg=%d, baseline=%d, diff_z=%d", DONUT_ATTACHED_Z_MIN, DONUT_ATTACHED_Z_MAX, average, s_dynamic_baseline, diff_z);
                                s_slider_state = MAGNETIC_SLIDE_SWITCH_EVENT_DONUT_ATTACHED;
                                notify_event_callbacks(MAGNETIC_SLIDE_SWITCH_EVENT_DONUT_ATTACHED);
                                s_donut_attached = true;
                                s_donut_stable_count = 0;
                                s_donut_last_z = 0;
                            }
                        } else {
                            s_donut_last_z = average;
                            s_donut_stable_count = 1;
                        }
                    } else {
                        s_donut_stable_count = 0;
                        s_donut_last_z = 0;
                    }
                }
            }
        }
        
        vTaskDelay(pdMS_TO_TICKS(MAG_SAMPLE_PERIOD_MS));
    }
    
    ESP_LOGI(TAG, "Slide switch event detection task finished.");
    vTaskDelete(NULL);
}

#endif  // CONFIG_SENSOR_MAGNETOMETER

/* ========== Callback Function Management ========== */

/**
 * @brief Internal function to notify all registered callbacks
 * 
 * @param event Event to notify
 * 
 * @note This function is thread-safe and can be called from any task
 */
static void notify_event_callbacks(magnetic_slide_switch_event_t event)
{
    if (s_callback_mutex == NULL) {
        return;  // Callbacks not initialized yet
    }
    
    if (xSemaphoreTake(s_callback_mutex, portMAX_DELAY) == pdTRUE) {
        callback_node_t *node = s_callback_list;
        while (node != NULL) {
            if (node->callback != NULL) {
                node->callback(event);
            }
            node = node->next;
        }
        xSemaphoreGive(s_callback_mutex);
    }
}

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
    // Create magnetometer data read task (shared data provider)
    xTaskCreate(magnetometer_data_read_task, "mag_data_read_task", MAGNETIC_SENSOR_TASK_STACK_SIZE, NULL, 8, NULL);
    
    // Create semaphore for calibration completion synchronization
    s_calibration_complete_sem = xSemaphoreCreateBinary();
    if (s_calibration_complete_sem == NULL) {
        ESP_LOGE(TAG, "Failed to create calibration completion semaphore");
        return;
    }
    
    // Create calibration task (will signal completion via semaphore)
    xTaskCreate(magnetometer_calibration_task, "mag_calibration_task", MAGNETIC_SENSOR_TASK_STACK_SIZE, NULL, 2, NULL);
    
    // Wait for calibration to complete (blocking wait)
    if (xSemaphoreTake(s_calibration_complete_sem, portMAX_DELAY) == pdTRUE) {
        ESP_LOGI(TAG, "Calibration completed, starting slide switch detection task");
        
        // Create slide switch event detection task (uses z-axis data)
        xTaskCreate(slide_switch_event_detect_task, "slide_switch_detect_task", MAGNETIC_SENSOR_TASK_STACK_SIZE, NULL, 5, NULL);
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
 * @brief Register event callback function
 * 
 * @param callback Callback function pointer
 * @return esp_err_t ESP_OK on success, ESP_ERR_INVALID_ARG if callback is NULL, ESP_ERR_NO_MEM if out of memory
 * 
 * @note Multiple callbacks can be registered, all will be called when event occurs
 */
esp_err_t magnetic_slide_switch_register_callback(magnetic_slide_switch_event_callback_t callback)
{
    if (callback == NULL) {
        ESP_LOGE(TAG, "Callback function is NULL");
        return ESP_ERR_INVALID_ARG;
    }
    
    // Create mutex if not exists
    if (s_callback_mutex == NULL) {
        s_callback_mutex = xSemaphoreCreateMutex();
        if (s_callback_mutex == NULL) {
            ESP_LOGE(TAG, "Failed to create callback mutex");
            return ESP_ERR_NO_MEM;
        }
    }
    
    if (xSemaphoreTake(s_callback_mutex, portMAX_DELAY) == pdTRUE) {
        // Check if callback already registered
        callback_node_t *node = s_callback_list;
        while (node != NULL) {
            if (node->callback == callback) {
                xSemaphoreGive(s_callback_mutex);
                ESP_LOGW(TAG, "Callback already registered");
                return ESP_OK;  // Already registered, return success
            }
            node = node->next;
        }
        
        // Create new node
        callback_node_t *new_node = (callback_node_t *)malloc(sizeof(callback_node_t));
        if (new_node == NULL) {
            xSemaphoreGive(s_callback_mutex);
            ESP_LOGE(TAG, "Failed to allocate memory for callback node");
            return ESP_ERR_NO_MEM;
        }
        
        new_node->callback = callback;
        new_node->next = s_callback_list;
        s_callback_list = new_node;
        
        xSemaphoreGive(s_callback_mutex);
        ESP_LOGI(TAG, "Callback registered successfully");
        return ESP_OK;
    }
    
    return ESP_FAIL;
}

/**
 * @brief Unregister event callback function
 * 
 * @param callback Callback function pointer to unregister
 * @return esp_err_t ESP_OK on success, ESP_ERR_NOT_FOUND if callback not found
 */
esp_err_t magnetic_slide_switch_unregister_callback(magnetic_slide_switch_event_callback_t callback)
{
    if (callback == NULL) {
        ESP_LOGE(TAG, "Callback function is NULL");
        return ESP_ERR_INVALID_ARG;
    }
    
    if (s_callback_mutex == NULL) {
        ESP_LOGW(TAG, "Callback mutex not initialized, no callbacks to unregister");
        return ESP_ERR_NOT_FOUND;
    }
    
    if (xSemaphoreTake(s_callback_mutex, portMAX_DELAY) == pdTRUE) {
        callback_node_t *prev = NULL;
        callback_node_t *node = s_callback_list;
        
        while (node != NULL) {
            if (node->callback == callback) {
                // Found the callback, remove it
                if (prev == NULL) {
                    // First node
                    s_callback_list = node->next;
                } else {
                    prev->next = node->next;
                }
                free(node);
                xSemaphoreGive(s_callback_mutex);
                ESP_LOGI(TAG, "Callback unregistered successfully");
                return ESP_OK;
            }
            prev = node;
            node = node->next;
        }
        
        xSemaphoreGive(s_callback_mutex);
        ESP_LOGW(TAG, "Callback not found");
        return ESP_ERR_NOT_FOUND;
    }
    
    return ESP_FAIL;
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
    s_request_recalibration = true;
}
