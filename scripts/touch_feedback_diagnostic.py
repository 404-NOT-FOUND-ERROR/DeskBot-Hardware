"""Add bounded observation to the retained touch implementation."""
import hashlib

BOARD = "main/boards/esp_vocat/touch_sensor.cc"
BASE_REPORT_SHA = "2c7b302258369f31206f1fd8b319aaa4e171aa094e5dc3f0eba69fa4d3d5a21a"
EDITION = "E3f-9d.4-touch"
BASE_SOURCE_SHA = "7b9b3cbe3c8bdeef85080ddb8b6c546dce43263d33a045ac09190ce2a19a081e"

PROBE = r'''
// Observe existing button events; keep gesture dispatch in the original callback.
struct TouchProbeStats {
    uint32_t downs;
    uint32_t ups;
    bool pressed;
};
static TouchProbeStats touch_probe_stats[2] = {};
static portMUX_TYPE touch_probe_lock = portMUX_INITIALIZER_UNLOCKED;

static void touch_probe_event(void *button, void *context)
{
    auto *stats = static_cast<TouchProbeStats *>(context);
    const auto event = iot_button_get_event(static_cast<button_handle_t>(button));
    portENTER_CRITICAL(&touch_probe_lock);
    if (event == BUTTON_PRESS_DOWN) {
        if (stats->downs != UINT32_MAX) ++stats->downs;
        stats->pressed = true;
    } else if (event == BUTTON_PRESS_UP) {
        if (stats->ups != UINT32_MAX) ++stats->ups;
        stats->pressed = false;
    }
    portEXIT_CRITICAL(&touch_probe_lock);
}

static void touch_probe_task(void *)
{
    // Log in a low priority task. Reading key levels would advance the button
    // state machine, so sample only the driver's read-only raw-data API here.
    vTaskDelay(pdMS_TO_TICKS(5000));
    while (true) {
        for (size_t i = 0; i < sizeof(touch_channel_list) / sizeof(touch_channel_list[0]); ++i) {
            uint32_t raw = 0;
            const esp_err_t error = touch_sensor_lowlevel_get_data(touch_channel_list[i], &raw);
            portENTER_CRITICAL(&touch_probe_lock);
            const TouchProbeStats stats = touch_probe_stats[i];
            portEXIT_CRITICAL(&touch_probe_lock);
            ESP_LOGI("TouchProbe", "pad=%u raw=%u down=%u up=%u held=%u err=%d",
                     static_cast<unsigned>(touch_channel_list[i]), static_cast<unsigned>(raw),
                     static_cast<unsigned>(stats.downs), static_cast<unsigned>(stats.ups),
                     static_cast<unsigned>(stats.pressed), static_cast<int>(error));
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

static esp_err_t register_touch_probe()
{
    static_assert(sizeof(touch_channel_list) / sizeof(touch_channel_list[0]) == 2,
                  "touch diagnostic requires the reviewed two-channel board");
    for (size_t i = 0; i < 2; ++i) {
        ESP_RETURN_ON_ERROR(iot_button_register_cb(touch_btn_handle[i], BUTTON_PRESS_DOWN,
            nullptr, touch_probe_event, &touch_probe_stats[i]), TAG, "Probe down callback failed");
        ESP_RETURN_ON_ERROR(iot_button_register_cb(touch_btn_handle[i], BUTTON_PRESS_UP,
            nullptr, touch_probe_event, &touch_probe_stats[i]), TAG, "Probe up callback failed");
    }
    return ESP_OK;
}

'''

REGISTER = '''    // Install diagnostic callbacks before the driver starts delivering samples.
    const esp_err_t probe_status = register_touch_probe();
    if (probe_status != ESP_OK) {
        ESP_LOGE("TouchProbe", "setup failed: %s", esp_err_to_name(probe_status));
    }
'''

START = '''    if (probe_status == ESP_OK) {
        if (xTaskCreate(touch_probe_task, "touch_probe", 3072, nullptr, 1, nullptr) == pdPASS) {
            ESP_LOGI("TouchProbe", "ready period_ms=500 threshold_percent=5 gesture=original");
        } else {
            ESP_LOGE("TouchProbe", "task creation failed");
        }
    }
'''


def patch_touch(raw: bytes) -> bytes:
    """Require the exact retained source before adding observation."""
    if hashlib.sha256(raw).hexdigest() != BASE_SOURCE_SHA:
        raise ValueError("retained touch source changed")
    source = raw.replace(b"\r\n", b"\n")
    before = b"// Touch button event callback\n"
    driver_start = b"    touch_sensor_lowlevel_start();\n"
    task_start = b"    return ESP_OK;\n}\n\n#if TOUCH_SLIDER_ENABLED\nstatic void touch_slider_callback"
    if any(source.count(anchor) != 1 for anchor in (before, driver_start, task_start)):
        raise ValueError("touch diagnostic insertion point changed")
    return (source.replace(before, PROBE.encode() + before)
            .replace(driver_start, REGISTER.encode() + driver_start)
            .replace(task_start, START.encode() + task_start)
            .replace(b"\n", b"\r\n"))
