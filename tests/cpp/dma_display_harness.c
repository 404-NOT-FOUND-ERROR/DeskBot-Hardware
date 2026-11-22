#include <assert.h>
#include <setjmp.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int esp_err_t;
enum {
    ESP_OK = 0,
    ESP_ERR_NO_MEM = -1,
    ESP_ERR_INVALID_ARG = -2,
    ESP_ERR_INVALID_STATE = -3,
    ESP_ERR_TEST = -4,
};

#define TAG "dma-test"
#define LCD_CMD_CASET 0x2A
#define LCD_CMD_RASET 0x2B
#define LCD_CMD_RAMWR 0x2C
#define SPI_TRANS_CS_KEEP_ACTIVE (1u << 0)
#define SPI_TRANS_DMA_USE_PSRAM (1u << 1)
#define SPI_TRANS_MULTILINE_CMD (1u << 2)
#define SPI_TRANS_MULTILINE_ADDR (1u << 3)
#define SPI_TRANS_MODE_OCT (1u << 4)
#define SPI_TRANS_MODE_QIO (1u << 5)
#define SPI_TRANS_USE_TXDATA (1u << 6)
#define SPI_TRANS_USE_RXDATA (1u << 7)
#define ESP_LV_ADAPTER_PANEL_IF_OTHER 1
#define ESP_LV_ADAPTER_DUMMY_DRAW_EVT_COLOR_DONE (1u << 0)
#define SPI_MASTER_ISR_ATTR
#define SPI_MASTER_ATTR
#define portMAX_DELAY UINT32_MAX
#define pdTRUE 1
#define pdFALSE 0
#define eSetBits 1
#define IRAM_ATTR

#define ESP_RETURN_ON_ERROR(expr, tag, msg) \
    do { \
        (void)(tag); \
        (void)(msg); \
        esp_err_t local_ret = (expr); \
        if (local_ret != ESP_OK) return local_ret; \
    } while (0)
#define ESP_GOTO_ON_ERROR(expr, label, tag, msg) \
    do { \
        (void)(tag); \
        (void)(msg); \
        ret = (expr); \
        if (ret != ESP_OK) goto label; \
    } while (0)
#define ESP_LOGE(tag, format, ...) \
    do { \
        (void)(tag); \
        fprintf(stderr, format "\n", __VA_ARGS__); \
    } while (0)
#define __containerof(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))

typedef struct {
    void *user;
    size_t length;
    size_t rxlength;
    const void *tx_buffer;
    void *rx_buffer;
    uint32_t flags;
    uint8_t tx_data[4];
    uint8_t rx_data[4];
} spi_transaction_t;

typedef struct {
    spi_transaction_t *trans;
    uint32_t *buffer_to_send;
    uint32_t *buffer_to_rcv;
} spi_trans_priv_t;

typedef struct {
    spi_transaction_t base;
    struct {
        int dc_gpio_level;
        int en_trans_done_cb;
    } flags;
} lcd_spi_trans_descriptor_t;

typedef struct esp_lcd_panel_io_t {
    int unused;
} esp_lcd_panel_io_t;
typedef esp_lcd_panel_io_t *esp_lcd_panel_io_handle_t;

typedef struct {
    esp_lcd_panel_io_t base;
    void *spi_dev;
    struct {
        int octal_mode;
        int quad_mode;
        int psram_dma_direct;
        int dc_cmd_level;
        int dc_data_level;
    } flags;
    size_t num_trans_inflight;
    size_t queue_size;
    lcd_spi_trans_descriptor_t trans_pool[2];
    size_t spi_trans_max_bytes;
    size_t lcd_cmd_bits;
} esp_lcd_panel_io_spi_t;

typedef struct esp_lcd_panel_t {
    int unused;
} esp_lcd_panel_t;
typedef esp_lcd_panel_t *esp_lcd_panel_handle_t;

typedef struct {
    esp_lcd_panel_t base;
    esp_lcd_panel_io_handle_t io;
    int x_gap;
    int y_gap;
    int fb_bits_per_pixel;
} st77916_panel_t;

typedef struct esp_lv_adapter_display_bridge_t {
    int unused;
} esp_lv_adapter_display_bridge_t;
typedef struct lv_display_t lv_display_t;
typedef struct {
    struct {
        void (*on_color_trans_done)(lv_display_t *display, bool success, void *user_ctx);
    } dummy_draw_cbs;
    void *dummy_draw_user_ctx;
    void *lv_disp;
    void *te_ctx;
    struct {
        struct {
            int interface;
        } profile;
        int tear_avoid_mode;
    } base;
} adapter_runtime_config_t;
typedef struct {
    adapter_runtime_config_t cfg;
    esp_lcd_panel_handle_t panel;
    int dummy_draw;
    void *dummy_draw_wait_task;
    uint32_t dummy_draw_wait_mask;
} esp_lv_adapter_display_bridge_v9_t;

typedef struct {
    int x1;
    int y1;
    int x2;
    int y2;
} lv_area_t;
struct lv_display_t {
    int unused;
};
typedef void *spi_device_handle_t;
typedef unsigned TickType_t;
typedef int BaseType_t;
typedef void *TaskHandle_t;
typedef int eNotifyAction;
typedef struct {
    int unused;
} esp_lcd_panel_io_event_data_t;

static esp_lcd_panel_io_spi_t g_io;
static st77916_panel_t g_panel;
static int g_fail_param_cmd = -1;
static unsigned g_queue_attempts;
static unsigned g_fail_queue_at;
static unsigned g_queue_count;
static unsigned g_queue_head;
static unsigned g_recycled;
static unsigned g_done_descriptors;
static unsigned g_live_private_buffers;
static unsigned g_allocated_private_buffers;
static unsigned g_freed_private_buffers;
static unsigned g_flush_ready_calls;
static unsigned g_notify_wait_calls;
static unsigned g_dma_completion_calls;
static unsigned g_notify_from_isr_calls;
static unsigned g_notifications_outside_wait;
static unsigned g_queue_notify_count;
static uint32_t g_pending_events;
static unsigned g_event_sequence;
static unsigned g_wait_sequence;
static unsigned g_completion_sequence;
static unsigned g_notify_sequence;
static bool g_wait_active;
static uint32_t g_last_notify_value;
static eNotifyAction g_last_notify_action;
static TaskHandle_t g_last_notify_task;
static unsigned g_pending_dma_completions;
static size_t g_live_at_command;
static spi_transaction_t *g_queued_trans[8];
static spi_trans_priv_t g_queued_priv[8];
static jmp_buf g_wait_env;
static uint16_t g_color_pixels[6] = {0x1234, 0x2345, 0x3456, 0x4567, 0x5678, 0x6789};
static const void *g_color_data;
static esp_lv_adapter_display_bridge_v9_t *g_callback_impl;

static const char *esp_err_to_name(esp_err_t err)
{
    (void)err;
    return "test-error";
}
static esp_err_t tx_param(void *panel, esp_lcd_panel_io_handle_t io, int command,
                          const void *data, size_t size);
static esp_err_t tx_color(void *panel, esp_lcd_panel_io_handle_t io, int command,
                          const void *data, size_t size);
static esp_err_t spi_device_acquire_bus(spi_device_handle_t device, TickType_t wait);
static esp_err_t spi_device_get_trans_result(spi_device_handle_t device,
                                             spi_transaction_t **trans, TickType_t wait);
static esp_err_t spi_device_polling_transmit(spi_device_handle_t device,
                                             spi_transaction_t *trans);
static esp_err_t spi_device_queue_trans(spi_device_handle_t device,
                                        spi_transaction_t *trans, TickType_t wait);
static esp_err_t spi_device_release_bus(spi_device_handle_t device);
static void spi_lcd_prepare_cmd_buffer(esp_lcd_panel_io_spi_t *io, int *command);
static int esp_ptr_external_ram(const void *pointer);
static esp_err_t esp_lcd_panel_io_tx_color(esp_lcd_panel_io_handle_t io, int command,
                                           const void *color, size_t color_size);
static esp_err_t esp_lcd_panel_draw_bitmap(esp_lcd_panel_handle_t panel, int x_start,
                                           int y_start, int x_end, int y_end,
                                           const void *color_data);
static esp_err_t display_lcd_blit_area(esp_lcd_panel_handle_t panel, int x_start,
                                       int y_start, int x_end, int y_end,
                                       const void *frame_buffer);
static void display_manager_flush_ready(lv_display_t *display);
static size_t lv_area_get_size(const lv_area_t *area);
static void lv_draw_sw_rgb565_swap(uint8_t *color_map, size_t pixel_count);
static lv_display_t *lv_display_get_default(void);
static void *xTaskGetCurrentTaskHandle(void);
static int xTaskNotifyWait(uint32_t clear_on_entry, uint32_t clear_on_exit,
                           uint32_t *events, TickType_t wait);
static BaseType_t xTaskNotifyFromISR(TaskHandle_t task, uint32_t value,
                                     eNotifyAction action, BaseType_t *need_yield);
static bool display_bridge_v9_handle_vsync(esp_lv_adapter_display_bridge_v9_t *impl);
static void esp_lv_adapter_te_sync_record_tx_done(void *ctx);

static void tracked_free(void *pointer)
{
    if (pointer != NULL) {
        ++g_freed_private_buffers;
        --g_live_private_buffers;
        free(pointer);
    }
}
#define free tracked_free
#include "dma_idf_io.inc"
#undef free

static esp_err_t tx_param(void *panel, esp_lcd_panel_io_handle_t io, int command,
                          const void *data, size_t size)
{
    (void)panel;
    (void)io;
    (void)data;
    (void)size;
    return command == g_fail_param_cmd ? ESP_ERR_TEST : ESP_OK;
}

static esp_err_t tx_color(void *panel, esp_lcd_panel_io_handle_t io, int command,
                          const void *data, size_t size)
{
    (void)panel;
    return esp_lcd_panel_io_tx_color(io, command, data, size);
}

#include "dma_panel.inc"
#include "dma_adapter.inc"

static esp_err_t spi_device_acquire_bus(spi_device_handle_t device, TickType_t wait)
{
    (void)device;
    (void)wait;
    return ESP_OK;
}

static esp_err_t spi_device_get_trans_result(spi_device_handle_t device,
                                             spi_transaction_t **trans, TickType_t wait)
{
    (void)device;
    (void)wait;
    if (g_queue_count == 0) return ESP_ERR_INVALID_STATE;
    unsigned slot = g_queue_head;
    *trans = g_queued_trans[slot];
    uninstall_priv_desc(&g_queued_priv[slot]);
    g_queue_head = (g_queue_head + 1u) % 8u;
    --g_queue_count;
    ++g_recycled;
    return ESP_OK;
}

static esp_err_t spi_device_polling_transmit(spi_device_handle_t device,
                                             spi_transaction_t *trans)
{
    (void)device;
    (void)trans;
    g_live_at_command = g_live_private_buffers;
    return ESP_OK;
}

static esp_err_t spi_device_queue_trans(spi_device_handle_t device,
                                        spi_transaction_t *trans, TickType_t wait)
{
    (void)device;
    (void)wait;
    ++g_queue_attempts;
    if (g_fail_queue_at == g_queue_attempts) return ESP_ERR_NO_MEM;

    assert(g_queue_count < 8u);
    unsigned slot = (g_queue_head + g_queue_count) % 8u;
    g_queued_trans[slot] = trans;
    g_queued_priv[slot].trans = trans;
    size_t bytes = (trans->length + 7u) / 8u;
    if (bytes == 0) bytes = 1;
    uint32_t *temporary = (uint32_t *)malloc(bytes);
    assert(temporary != NULL);
    memcpy(temporary, trans->tx_buffer, bytes);
    g_queued_priv[slot].buffer_to_send = temporary;
    g_queued_priv[slot].buffer_to_rcv = NULL;
    ++g_live_private_buffers;
    ++g_allocated_private_buffers;
    ++g_queue_count;
    if (__containerof(trans, lcd_spi_trans_descriptor_t, base)->flags.en_trans_done_cb) {
        ++g_done_descriptors;
        ++g_pending_dma_completions;
        g_queue_notify_count = g_notify_from_isr_calls;
    }
    return ESP_OK;
}

static esp_err_t spi_device_release_bus(spi_device_handle_t device)
{
    (void)device;
    return ESP_OK;
}

static void spi_lcd_prepare_cmd_buffer(esp_lcd_panel_io_spi_t *io, int *command)
{
    (void)io;
    (void)command;
}

static int esp_ptr_external_ram(const void *pointer)
{
    (void)pointer;
    return 0;
}

static esp_err_t esp_lcd_panel_io_tx_color(esp_lcd_panel_io_handle_t io, int command,
                                           const void *color, size_t color_size)
{
    return panel_io_spi_tx_color(io, command, color, color_size);
}

static esp_err_t esp_lcd_panel_draw_bitmap(esp_lcd_panel_handle_t panel, int x_start,
                                           int y_start, int x_end, int y_end,
                                           const void *color_data)
{
    return panel_st77916_draw_bitmap(panel, x_start, y_start, x_end, y_end, color_data);
}

static esp_err_t display_lcd_blit_area(esp_lcd_panel_handle_t panel, int x_start,
                                       int y_start, int x_end, int y_end,
                                       const void *frame_buffer)
{
    return esp_lcd_panel_draw_bitmap(panel, x_start, y_start, x_end, y_end, frame_buffer);
}

static void display_manager_flush_ready(lv_display_t *display)
{
    (void)display;
    ++g_flush_ready_calls;
}

static lv_display_t *lv_display_get_default(void)
{
    return NULL;
}

static void esp_lv_adapter_te_sync_record_tx_done(void *ctx)
{
    (void)ctx;
}

static bool display_bridge_v9_handle_vsync(esp_lv_adapter_display_bridge_v9_t *impl)
{
    (void)impl;
    return false;
}

static size_t lv_area_get_size(const lv_area_t *area)
{
    return (size_t)(area->x2 - area->x1 + 1) * (size_t)(area->y2 - area->y1 + 1);
}

static void lv_draw_sw_rgb565_swap(uint8_t *color_map, size_t pixel_count)
{
    (void)color_map;
    (void)pixel_count;
}

static void *xTaskGetCurrentTaskHandle(void)
{
    return (void *)(uintptr_t)0x1234;
}

static int xTaskNotifyWait(uint32_t clear_on_entry, uint32_t clear_on_exit,
                           uint32_t *events, TickType_t wait)
{
    (void)clear_on_entry;
    (void)wait;
    ++g_notify_wait_calls;
    g_wait_active = true;
    g_wait_sequence = ++g_event_sequence;
    if (g_pending_dma_completions == 0) {
        g_wait_active = false;
        longjmp(g_wait_env, 1);
    }
    --g_pending_dma_completions;
    ++g_dma_completion_calls;
    g_completion_sequence = ++g_event_sequence;
    (void)display_bridge_v9_on_io_color_trans_done(&g_io.base, NULL, g_callback_impl);
    *events = g_pending_events;
    g_pending_events &= ~clear_on_exit;
    g_wait_active = false;
    return pdTRUE;
}

static BaseType_t xTaskNotifyFromISR(TaskHandle_t task, uint32_t value,
                                     eNotifyAction action, BaseType_t *need_yield)
{
    ++g_notify_from_isr_calls;
    g_notify_sequence = ++g_event_sequence;
    g_last_notify_task = task;
    g_last_notify_value = value;
    g_last_notify_action = action;
    if (!g_wait_active) {
        ++g_notifications_outside_wait;
    }
    g_pending_events |= value;
    if (need_yield) {
        *need_yield = pdFALSE;
    }
    return pdTRUE;
}

static void drain_all(void)
{
    while (g_queue_count != 0) {
        spi_transaction_t *trans = NULL;
        assert(spi_device_get_trans_result(g_io.spi_dev, &trans, portMAX_DELAY) == ESP_OK);
        --g_io.num_trans_inflight;
    }
}

static void reset_fixture(void)
{
    drain_all();
    memset(&g_io, 0, sizeof(g_io));
    memset(&g_panel, 0, sizeof(g_panel));
    memset(g_queued_trans, 0, sizeof(g_queued_trans));
    memset(g_queued_priv, 0, sizeof(g_queued_priv));
    g_io.queue_size = 2;
    g_io.spi_trans_max_bytes = 4;
    g_io.lcd_cmd_bits = 8;
    g_io.spi_dev = &g_io;
    g_panel.io = &g_io.base;
    g_panel.fb_bits_per_pixel = 16;
    g_fail_param_cmd = -1;
    g_queue_attempts = 0;
    g_fail_queue_at = 0;
    g_queue_count = 0;
    g_queue_head = 0;
    g_recycled = 0;
    g_done_descriptors = 0;
    g_live_private_buffers = 0;
    g_allocated_private_buffers = 0;
    g_freed_private_buffers = 0;
    g_flush_ready_calls = 0;
    g_notify_wait_calls = 0;
    g_dma_completion_calls = 0;
    g_notify_from_isr_calls = 0;
    g_notifications_outside_wait = 0;
    g_queue_notify_count = 0;
    g_pending_events = 0;
    g_event_sequence = 0;
    g_wait_sequence = 0;
    g_completion_sequence = 0;
    g_notify_sequence = 0;
    g_wait_active = false;
    g_last_notify_value = 0;
    g_last_notify_action = 0;
    g_last_notify_task = NULL;
    g_pending_dma_completions = 0;
    g_live_at_command = 0;
    g_color_data = g_color_pixels;
    g_callback_impl = NULL;
}

static unsigned g_assertions;
static unsigned g_failures;
#define CHECK(condition) \
    do { \
        ++g_assertions; \
        if (!(condition)) { \
            ++g_failures; \
            printf("CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        } \
    } while (0)

static void test_success_and_parameter_errors(void)
{
    reset_fixture();
    esp_err_t ret = panel_st77916_draw_bitmap(&g_panel.base, 0, 0, 6, 1, g_color_data);
    CHECK(ret == ESP_OK);
    CHECK(g_queue_attempts == 3);
    CHECK(g_done_descriptors == 1);
    CHECK(g_io.num_trans_inflight == 2);
    drain_all();
    CHECK(g_live_private_buffers == 0);
    CHECK(g_allocated_private_buffers == g_freed_private_buffers);

    reset_fixture();
    g_fail_param_cmd = LCD_CMD_CASET;
    ret = panel_st77916_draw_bitmap(&g_panel.base, 0, 0, 6, 1, g_color_data);
    CHECK(ret == ESP_ERR_TEST);
    CHECK(g_queue_attempts == 0);

    reset_fixture();
    g_fail_param_cmd = LCD_CMD_RASET;
    ret = panel_st77916_draw_bitmap(&g_panel.base, 0, 0, 6, 1, g_color_data);
    CHECK(ret == ESP_ERR_TEST);
    CHECK(g_queue_attempts == 0);
}

static void test_chunk_failure_and_recovery(unsigned fail_at)
{
    reset_fixture();
    g_fail_queue_at = fail_at;
    esp_err_t ret = panel_st77916_draw_bitmap(&g_panel.base, 0, 0, 6, 1, g_color_data);
    CHECK(g_queue_attempts == fail_at);
    CHECK(g_done_descriptors == 0);
    CHECK(g_io.num_trans_inflight == (fail_at == 1 ? 0u : 1u));
    CHECK(g_live_private_buffers == g_io.num_trans_inflight);
#if TEST_PATCHED_DRIVER
    CHECK(ret == ESP_ERR_NO_MEM);
#else
    CHECK(ret == ESP_OK);
#endif

    unsigned expected_recycle = g_recycled + g_io.num_trans_inflight + 1u;
    g_fail_queue_at = 0;
    g_queue_attempts = 0;
    ret = panel_st77916_draw_bitmap(&g_panel.base, 0, 0, 6, 1, g_color_data);
    CHECK(ret == ESP_OK);
    CHECK(g_recycled == expected_recycle);
    CHECK(g_live_at_command == 0);
    CHECK(g_done_descriptors == 1);
    CHECK(g_io.num_trans_inflight == 2);
    drain_all();
    CHECK(g_live_private_buffers == 0);
    CHECK(g_allocated_private_buffers == g_freed_private_buffers);
}

static void test_default_flush_error_completion(unsigned fail_at)
{
    reset_fixture();
    g_fail_queue_at = fail_at;
    esp_lv_adapter_display_bridge_v9_t impl;
    memset(&impl, 0, sizeof(impl));
    impl.panel = &g_panel.base;
    lv_display_t display = {0};
    lv_area_t area = {.x1 = 0, .y1 = 0, .x2 = 5, .y2 = 0};
    display_bridge_v9_flush_default(&impl, &display, &area, (uint8_t *)g_color_data);
#if TEST_PATCHED_DRIVER
    CHECK(g_flush_ready_calls == 1);
#else
    CHECK(g_flush_ready_calls == 0);
#endif
    drain_all();
}

static void test_dummy_wait_error(unsigned fail_at)
{
    reset_fixture();
    g_fail_queue_at = fail_at;
    esp_lv_adapter_display_bridge_v9_t impl;
    memset(&impl, 0, sizeof(impl));
    impl.panel = &g_panel.base;
    impl.dummy_draw = 1;
    uint16_t frame[6] = {0};
    esp_err_t ret = ESP_OK;
    (void)ret;
    int jumped = setjmp(g_wait_env);
    if (jumped == 0) {
        ret = display_bridge_v9_dummy_draw_blit((esp_lv_adapter_display_bridge_t *)&impl,
                                                0, 0, 6, 1, frame, true);
#if TEST_PATCHED_DRIVER
        CHECK(ret == ESP_ERR_NO_MEM);
        CHECK(g_notify_wait_calls == 0);
        CHECK(g_dma_completion_calls == 0);
        CHECK(g_notify_from_isr_calls == 0);
        CHECK(impl.dummy_draw_wait_task == NULL);
        CHECK(impl.dummy_draw_wait_mask == 0);
#else
        CHECK(false);
#endif
    } else {
#if TEST_PATCHED_DRIVER
        CHECK(false);
#else
        CHECK(g_notify_wait_calls == 1);
        CHECK(g_dma_completion_calls == 0);
        CHECK(g_notify_from_isr_calls == 0);
        CHECK(impl.dummy_draw_wait_task == xTaskGetCurrentTaskHandle());
        CHECK(impl.dummy_draw_wait_mask == ESP_LV_ADAPTER_DUMMY_DRAW_EVT_COLOR_DONE);
#endif
    }
    drain_all();
}

static void test_dummy_wait_success(void)
{
    reset_fixture();
    esp_lv_adapter_display_bridge_v9_t impl;
    memset(&impl, 0, sizeof(impl));
    impl.panel = &g_panel.base;
    impl.dummy_draw = 1;
    g_callback_impl = &impl;
    uint16_t frame[6] = {0};
    esp_err_t ret = display_bridge_v9_dummy_draw_blit((esp_lv_adapter_display_bridge_t *)&impl,
                                                      0, 0, 6, 1, frame, true);
    CHECK(ret == ESP_OK);
    CHECK(g_notify_wait_calls == 1);
    CHECK(g_done_descriptors == 1);
    CHECK(g_queue_notify_count == 0);
    CHECK(g_wait_sequence != 0);
    CHECK(g_wait_sequence < g_completion_sequence);
    CHECK(g_completion_sequence < g_notify_sequence);
    CHECK(g_dma_completion_calls == 1);
    CHECK(g_notify_from_isr_calls == 1);
    CHECK(g_notifications_outside_wait == 0);
    CHECK(g_last_notify_task == xTaskGetCurrentTaskHandle());
    CHECK(g_last_notify_value == ESP_LV_ADAPTER_DUMMY_DRAW_EVT_COLOR_DONE);
    CHECK(g_last_notify_action == eSetBits);
    CHECK(g_pending_events == 0);
    CHECK(g_pending_dma_completions == 0);
    CHECK(!g_wait_active);
    CHECK(impl.dummy_draw_wait_task == NULL);
    CHECK(impl.dummy_draw_wait_mask == 0);
    drain_all();
}

int main(void)
{
    test_success_and_parameter_errors();
    test_chunk_failure_and_recovery(1);
    test_chunk_failure_and_recovery(2);
    test_chunk_failure_and_recovery(3);
    test_default_flush_error_completion(1);
    test_default_flush_error_completion(2);
    test_default_flush_error_completion(3);
    test_dummy_wait_error(1);
    test_dummy_wait_error(2);
    test_dummy_wait_error(3);
    test_dummy_wait_success();
    if (g_failures != 0) {
        printf("dma_display_native_tests_failed: %u/%u\n", g_failures, g_assertions);
        return 1;
    }
#if TEST_PATCHED_DRIVER
    puts("dma_display_native_tests_passed: patched");
#else
    puts("dma_display_native_tests_passed: frozen");
#endif
    printf("dma_display_assertions: %u\n", g_assertions);
    return 0;
}
