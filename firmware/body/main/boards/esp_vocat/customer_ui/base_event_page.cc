#include "base_event_page.h"

#include "../base_control.h"
#include "../base_event_mapper.h"
#include "alarm_manager.h"

#include <algorithm>
#include <cstdio>

namespace {

constexpr size_t kVisibleEvents = 5;

struct BaseEventPageUi {
    lv_obj_t* container = nullptr;
    lv_obj_t* link = nullptr;
    lv_obj_t* mechanical = nullptr;
    lv_obj_t* latest = nullptr;
    lv_obj_t* rows[kVisibleEvents] = {nullptr, nullptr, nullptr, nullptr, nullptr};
    lv_timer_t* update_timer = nullptr;
};

BaseEventPageUi s_ui;

void set_label_style(lv_obj_t* label, const lv_font_t* font, lv_color_t color)
{
    lv_obj_set_style_text_font(label, font, LV_PART_MAIN);
    lv_obj_set_style_text_color(label, color, LV_PART_MAIN);
    lv_obj_set_style_text_opa(label, LV_OPA_COVER, LV_PART_MAIN);
}

lv_obj_t* create_row(lv_obj_t* parent, lv_coord_t y)
{
    lv_obj_t* row = lv_obj_create(parent);
    lv_obj_set_size(row, 232, 26);
    lv_obj_align(row, LV_ALIGN_CENTER, 0, y);
    lv_obj_set_style_bg_color(row, lv_color_hex(0x171B21), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(row, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(row, 6, LV_PART_MAIN);
    lv_obj_set_style_pad_all(row, 0, LV_PART_MAIN);
    lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t* label = lv_label_create(row);
    lv_obj_set_width(label, 216);
    lv_label_set_long_mode(label, LV_LABEL_LONG_CLIP);
    lv_obj_align(label, LV_ALIGN_LEFT_MID, 8, 0);
    set_label_style(label, &ui_font_Device18, lv_color_hex(0xD9DEE7));
    return label;
}

const char* short_event_name(const std::string& type, bool synthetic)
{
    if (synthetic || type == "base.link.disconnected") return "断开";
    if (type == "base.link.connected") return "连接";
    if (type == "base.mechanical.status") return "机械状态";
    if (type == "base.action.completed") return "动作完成";
    if (type == "base.magnetic_switch.slide_down") return "滑动下";
    if (type == "base.magnetic_switch.slide_up") return "滑动上";
    if (type == "base.magnetic_switch.removed_from_up") return "取下上位";
    if (type == "base.magnetic_switch.removed_from_down") return "取下下位";
    if (type == "base.magnetic_switch.placed_from_up") return "放回上位";
    if (type == "base.magnetic_switch.placed_from_down") return "放回下位";
    if (type == "base.magnetic_switch.single_click") return "单击";
    if (type.find("base.accessory.") == 0) return "配件";
    if (type.find("base.pairing.") == 0) return "配对";
    if (type.find("base.calibration.") == 0) return "校准";
    return "未知事件";
}

void update_page(lv_timer_t* timer)
{
    auto* ui = static_cast<BaseEventPageUi*>(lv_timer_get_user_data(timer));
    if (ui == nullptr || ui->container == nullptr ||
        lv_obj_has_flag(ui->container, LV_OBJ_FLAG_HIDDEN)) {
        return;
    }

    BaseControl* base = BaseControl::GetInstance();
    if (base == nullptr) {
        lv_label_set_text(s_ui.link, "底座：未初始化");
        lv_label_set_text(s_ui.mechanical, "机械状态：--");
        lv_label_set_text(s_ui.latest, "最近事件：--");
        for (lv_obj_t* row : s_ui.rows) {
            lv_label_set_text(row, "");
        }
        return;
    }

    lv_label_set_text(s_ui.link, base->IsOnline() ? "底座：在线" : "底座：离线");
    lv_obj_set_style_text_color(s_ui.link,
                                base->IsOnline() ? lv_color_hex(0x7BD88F) : lv_color_hex(0xF2C66D),
                                LV_PART_MAIN);

    bool mechanical_valid = false;
    const uint16_t mechanical_status = base->GetMechanicalStatus(&mechanical_valid);
    char text[160];
    if (mechanical_valid) {
        std::snprintf(text, sizeof(text), "机械：%s",
                      base_event_mapper::MechanicalStatusName(mechanical_status));
    } else {
        std::snprintf(text, sizeof(text), "机械：尚无状态");
    }
    lv_label_set_text(s_ui.mechanical, text);

    const auto events = base->GetRecentEvents();
    if (events.empty()) {
        lv_label_set_text(s_ui.latest, "最近事件：尚无事件");
        for (lv_obj_t* row : s_ui.rows) {
            lv_label_set_text(row, "");
        }
        return;
    }

    const auto& newest = events.back();
    std::snprintf(text, sizeof(text), "最近事件：#%lu %s",
                  static_cast<unsigned long>(newest.sequence),
                  short_event_name(newest.type, newest.synthetic));
    lv_label_set_text(s_ui.latest, text);

    for (size_t index = 0; index < kVisibleEvents; ++index) {
        const size_t reverse_index = index;
        if (reverse_index >= events.size()) {
            lv_label_set_text(s_ui.rows[index], "");
            continue;
        }
        const auto& event = events[events.size() - 1 - reverse_index];
        if (event.has_value) {
            std::snprintf(text, sizeof(text), "#%lu 0x%02X %04X %s",
                          static_cast<unsigned long>(event.sequence), event.command,
                          event.value, short_event_name(event.type, event.synthetic));
        } else {
            std::snprintf(text, sizeof(text), "#%lu 0x%02X %s",
                          static_cast<unsigned long>(event.sequence), event.command,
                          short_event_name(event.type, event.synthetic));
        }
        lv_label_set_text(s_ui.rows[index], text);
    }
}

}  // namespace

extern "C" lv_obj_t* base_event_page_create_with_parent(lv_obj_t* parent)
{
    lv_obj_t* container = lv_obj_create(parent);
    s_ui.container = container;
    lv_obj_set_size(container, SCREEN_WIDTH, SCREEN_HEIGHT);
    lv_obj_set_style_bg_color(container, lv_color_hex(0x000000), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(container, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(container, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(container, 0, LV_PART_MAIN);
    lv_obj_clear_flag(container, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t* title = lv_label_create(container);
    lv_label_set_text(title, "底座事件");
    lv_obj_align(title, LV_ALIGN_CENTER, 0, -150);
    set_label_style(title, &ui_font_Device24, lv_color_hex(0xFFFFFF));

    lv_obj_t* subtitle = lv_label_create(container);
    lv_label_set_text(subtitle, "只读诊断");
    lv_obj_align(subtitle, LV_ALIGN_CENTER, 0, -124);
    set_label_style(subtitle, &ui_font_Device18, lv_color_hex(0x8F98A8));

    s_ui.link = lv_label_create(container);
    lv_obj_set_width(s_ui.link, 130);
    lv_label_set_long_mode(s_ui.link, LV_LABEL_LONG_CLIP);
    lv_obj_align(s_ui.link, LV_ALIGN_CENTER, -76, -96);
    set_label_style(s_ui.link, &ui_font_Device18, lv_color_hex(0xF2C66D));

    s_ui.mechanical = lv_label_create(container);
    lv_obj_set_width(s_ui.mechanical, 130);
    lv_label_set_long_mode(s_ui.mechanical, LV_LABEL_LONG_CLIP);
    lv_obj_align(s_ui.mechanical, LV_ALIGN_CENTER, 76, -96);
    set_label_style(s_ui.mechanical, &ui_font_Device18, lv_color_hex(0xD9DEE7));

    s_ui.latest = lv_label_create(container);
    lv_obj_set_width(s_ui.latest, 270);
    lv_label_set_long_mode(s_ui.latest, LV_LABEL_LONG_CLIP);
    lv_obj_align(s_ui.latest, LV_ALIGN_CENTER, 0, -67);
    set_label_style(s_ui.latest, &ui_font_Device18, lv_color_hex(0x7BD88F));

    constexpr lv_coord_t row_y[kVisibleEvents] = {-28, 6, 40, 74, 108};
    for (size_t index = 0; index < kVisibleEvents; ++index) {
        s_ui.rows[index] = create_row(container, row_y[index]);
    }

    s_ui.update_timer = lv_timer_create(update_page, 500, &s_ui);
    lv_obj_add_flag(container, LV_OBJ_FLAG_HIDDEN);
    return container;
}
