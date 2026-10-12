#include "device_capability.h"

#include "alarm_manager.h"
#include "lvgl.h"

namespace {

constexpr lv_coord_t kScreenWidth = SCREEN_WIDTH;
constexpr lv_coord_t kScreenHeight = SCREEN_HEIGHT;

void set_label_style(lv_obj_t *label, const lv_font_t *font, lv_color_t color)
{
    lv_obj_set_style_text_font(label, font, LV_PART_MAIN);
    lv_obj_set_style_text_color(label, color, LV_PART_MAIN);
    lv_obj_set_style_text_opa(label, LV_OPA_COVER, LV_PART_MAIN);
}

void add_status_row(lv_obj_t *parent,
                    lv_coord_t y,
                    const char *name,
                    const char *status,
                    lv_color_t status_color)
{
    lv_obj_t *row = lv_obj_create(parent);
    lv_obj_set_size(row, 260, 30);
    lv_obj_align(row, LV_ALIGN_CENTER, 0, y);
    lv_obj_set_style_bg_color(row, lv_color_hex(0x1A1D23), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(row, 15, LV_PART_MAIN);
    lv_obj_set_style_border_width(row, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(row, 0, LV_PART_MAIN);
    lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *name_label = lv_label_create(row);
    lv_label_set_text(name_label, name);
    lv_obj_align(name_label, LV_ALIGN_LEFT_MID, 12, 0);
    set_label_style(name_label, &ui_font_Device18, lv_color_hex(0xE8EAF0));

    lv_obj_t *status_label = lv_label_create(row);
    lv_label_set_text(status_label, status);
    lv_obj_align(status_label, LV_ALIGN_RIGHT_MID, -12, 0);
    set_label_style(status_label, &ui_font_Device18, status_color);
}

} // namespace

extern "C" lv_obj_t *device_capability_create_with_parent(lv_obj_t *parent)
{
    lv_obj_t *container = lv_obj_create(parent);
    lv_obj_set_size(container, kScreenWidth, kScreenHeight);
    lv_obj_set_style_bg_color(container, lv_color_hex(0x000000), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(container, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(container, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(container, 0, LV_PART_MAIN);
    lv_obj_clear_flag(container, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *title = lv_label_create(container);
    lv_label_set_text(title, "设备能力");
    lv_obj_align(title, LV_ALIGN_CENTER, 0, -135);
    set_label_style(title, &ui_font_Device24, lv_color_hex(0xFFFFFF));

    lv_obj_t *subtitle = lv_label_create(container);
    lv_label_set_text(subtitle, "只读诊断 · 不控制硬件");
    lv_obj_align(subtitle, LV_ALIGN_CENTER, 0, -104);
    set_label_style(subtitle, &ui_font_Device18, lv_color_hex(0x8F98A8));

    // These labels describe the current source ownership boundary. They are
    // deliberately not presented as live sensor values or connection claims.
    add_status_row(container, -63, "底座 UART", "代码已接入", lv_color_hex(0x7BD88F));
    add_status_row(container, -25, "双麦 DOA", "代码已接入", lv_color_hex(0x7BD88F));
    add_status_row(container, 13, "顶部摸头", "GPIO7 三摸", lv_color_hex(0x7BD88F));
    add_status_row(container, 51, "外壳识别", "学习与列表", lv_color_hex(0x7BD88F));
    add_status_row(container, 89, "CSI 感知", "未启用", lv_color_hex(0xF2C66D));

    lv_obj_t *footer = lv_label_create(container);
    lv_label_set_text(footer, "磁场数据与外壳档案已接入");
    lv_obj_align(footer, LV_ALIGN_CENTER, 0, 137);
    set_label_style(footer, &ui_font_Device18, lv_color_hex(0x707887));

    lv_obj_add_flag(container, LV_OBJ_FLAG_HIDDEN);
    return container;
}
