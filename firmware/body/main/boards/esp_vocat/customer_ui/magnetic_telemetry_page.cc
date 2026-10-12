#include "magnetic_telemetry_page.h"

#include "../base_control.h"
#include "../magnetic_shell_manager.h"
#include "alarm_manager.h"

#include <algorithm>
#include <cstdio>

namespace {

constexpr size_t kProfilesPerPage = 3;

struct MagneticPageUi {
    lv_obj_t* container = nullptr;
    lv_obj_t* title = nullptr;
    lv_obj_t* live_view = nullptr;
    lv_obj_t* list_view = nullptr;
    lv_obj_t* sensor_status = nullptr;
    lv_obj_t* recognition = nullptr;
    lv_obj_t* recognition_detail = nullptr;
    lv_obj_t* axis_values = nullptr;
    lv_obj_t* strength_bar = nullptr;
    lv_obj_t* strength = nullptr;
    lv_obj_t* learn_button = nullptr;
    lv_obj_t* learn_button_label = nullptr;
    lv_obj_t* list_button_label = nullptr;
    lv_obj_t* footer = nullptr;
    lv_obj_t* event_footer = nullptr;
    lv_obj_t* list_summary = nullptr;
    lv_obj_t* profile_rows[kProfilesPerPage] = {nullptr, nullptr, nullptr};
    lv_obj_t* previous_button = nullptr;
    lv_obj_t* next_button = nullptr;
    size_t list_page = 0;
    bool showing_list = false;
};

MagneticPageUi s_ui;

void set_label_style(lv_obj_t* label, const lv_font_t* font, lv_color_t color)
{
    lv_obj_set_style_text_font(label, font, LV_PART_MAIN);
    lv_obj_set_style_text_color(label, color, LV_PART_MAIN);
    lv_obj_set_style_text_opa(label, LV_OPA_COVER, LV_PART_MAIN);
}

lv_obj_t* create_button(lv_obj_t* parent, lv_coord_t width, lv_coord_t height,
                        lv_coord_t x, lv_coord_t y, lv_color_t color,
                        const char* text, lv_obj_t** label_out)
{
    lv_obj_t* button = lv_btn_create(parent);
    lv_obj_set_size(button, width, height);
    lv_obj_align(button, LV_ALIGN_CENTER, x, y);
    lv_obj_set_style_radius(button, 8, LV_PART_MAIN);
    lv_obj_set_style_bg_color(button, color, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(button, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(button, 0, LV_PART_MAIN);
    lv_obj_set_style_shadow_width(button, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(button, 0, LV_PART_MAIN);

    lv_obj_t* label = lv_label_create(button);
    lv_label_set_text(label, text);
    lv_obj_center(label);
    set_label_style(label, &ui_font_Device18, lv_color_hex(0xFFFFFF));
    if (label_out != nullptr) {
        *label_out = label;
    }
    return button;
}

const char* sensor_name(BaseControl::MagneticSensorKind sensor_kind)
{
    switch (sensor_kind) {
    case BaseControl::MagneticSensorKind::Bmm150:
        return "BMM150";
    case BaseControl::MagneticSensorKind::Qmc6309:
        return "QMC6309";
    case BaseControl::MagneticSensorKind::Ss49e:
        return "SS49E";
    default:
        return "未知";
    }
}

const char* event_name(MagneticShellManager::EventType type)
{
    switch (type) {
    case MagneticShellManager::EventType::Attached:
        return "attached";
    case MagneticShellManager::EventType::Changed:
        return "changed";
    case MagneticShellManager::EventType::Unknown:
        return "unknown";
    case MagneticShellManager::EventType::Detached:
        return "detached";
    default:
        return "--";
    }
}

void show_live_view(MagneticPageUi* ui)
{
    ui->showing_list = false;
    lv_obj_clear_flag(ui->live_view, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(ui->list_view, LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text(ui->title, "外壳识别");
}

void show_list_view(MagneticPageUi* ui)
{
    ui->showing_list = true;
    lv_obj_add_flag(ui->live_view, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(ui->list_view, LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text(ui->title, "外壳列表");
}

void learn_button_event(lv_event_t* event)
{
    if (lv_event_get_code(event) != LV_EVENT_CLICKED) {
        return;
    }
    auto* ui = static_cast<MagneticPageUi*>(lv_event_get_user_data(event));
    std::string message;
    MagneticShellManager::GetInstance().StartLearning("", &message);
    if (ui != nullptr && ui->recognition_detail != nullptr) {
        lv_label_set_text(ui->recognition_detail, message.c_str());
    }
}

void list_button_event(lv_event_t* event)
{
    if (lv_event_get_code(event) != LV_EVENT_CLICKED) {
        return;
    }
    auto* ui = static_cast<MagneticPageUi*>(lv_event_get_user_data(event));
    if (ui != nullptr) {
        ui->list_page = 0;
        show_list_view(ui);
    }
}

void return_button_event(lv_event_t* event)
{
    if (lv_event_get_code(event) != LV_EVENT_CLICKED) {
        return;
    }
    auto* ui = static_cast<MagneticPageUi*>(lv_event_get_user_data(event));
    if (ui != nullptr) {
        show_live_view(ui);
    }
}

void previous_button_event(lv_event_t* event)
{
    if (lv_event_get_code(event) != LV_EVENT_CLICKED) {
        return;
    }
    auto* ui = static_cast<MagneticPageUi*>(lv_event_get_user_data(event));
    if (ui != nullptr && ui->list_page > 0) {
        --ui->list_page;
    }
}

void next_button_event(lv_event_t* event)
{
    if (lv_event_get_code(event) != LV_EVENT_CLICKED) {
        return;
    }
    auto* ui = static_cast<MagneticPageUi*>(lv_event_get_user_data(event));
    const auto snapshot = MagneticShellManager::GetInstance().GetSnapshot(true);
    const size_t page_count = std::max<size_t>(
        1, (snapshot.profiles.size() + kProfilesPerPage - 1) / kProfilesPerPage
    );
    if (ui != nullptr && ui->list_page + 1 < page_count) {
        ++ui->list_page;
    }
}

void update_live_view(MagneticPageUi* ui,
                      const MagneticShellManager::Snapshot& snapshot)
{
    BaseControl::MagneticTelemetry telemetry;
    BaseControl* base = BaseControl::GetInstance();
    char text[96];
    if (base == nullptr || !base->GetMagneticTelemetry(&telemetry)) {
        lv_label_set_text(ui->sensor_status, "底座未连接");
    } else {
        const int64_t now_ms = esp_timer_get_time() / 1000;
        const int64_t age_ms = std::max<int64_t>(0, now_ms - telemetry.last_sample_ms);
        const unsigned long age_ms_u32 = age_ms > UINT32_MAX
                                             ? static_cast<unsigned long>(UINT32_MAX)
                                             : static_cast<unsigned long>(age_ms);
        std::snprintf(text, sizeof(text), "%s · %lums",
                      sensor_name(telemetry.sensor_kind), age_ms_u32);
        lv_label_set_text(ui->sensor_status, text);
    }

    if (!snapshot.has_data) {
        lv_label_set_text(ui->recognition, "等待磁场数据");
        lv_label_set_text(ui->recognition_detail, "请确认底座已连接");
        lv_label_set_text(ui->axis_values, "X --   Y --   Z --");
        lv_label_set_text(ui->strength, "强度 --");
        lv_bar_set_value(ui->strength_bar, 0, LV_ANIM_OFF);
    } else {
        if (snapshot.learning) {
            lv_label_set_text(ui->recognition, "正在学习外壳");
            std::snprintf(text, sizeof(text), "保持不动 · 样本 %u/%u",
                          static_cast<unsigned>(snapshot.learning_samples),
                          static_cast<unsigned>(MagneticShellManager::kLearningSamples));
            lv_label_set_text(ui->recognition_detail, text);
        } else {
            switch (snapshot.recognition) {
            case MagneticShellManager::RecognitionState::Matched:
                std::snprintf(text, sizeof(text), "#%d %s",
                              snapshot.matched_profile_id, snapshot.matched_name.c_str());
                lv_label_set_text(ui->recognition, text);
                std::snprintf(text, sizeof(text), "已识别 · 置信度 %d%%", snapshot.confidence);
                lv_label_set_text(ui->recognition_detail, text);
                break;
            case MagneticShellManager::RecognitionState::Unknown:
                lv_label_set_text(ui->recognition, "可能是新外壳");
                std::snprintf(text, sizeof(text), "与已有档案距离 %d", snapshot.distance);
                lv_label_set_text(ui->recognition_detail, text);
                break;
            case MagneticShellManager::RecognitionState::NoProfiles:
                lv_label_set_text(ui->recognition, "还没有外壳档案");
                lv_label_set_text(ui->recognition_detail, "保持不动后学习当前外壳");
                break;
            case MagneticShellManager::RecognitionState::Unstable:
                lv_label_set_text(ui->recognition, "磁场变化中");
                lv_label_set_text(ui->recognition_detail, "保持不动后自动识别");
                break;
            default:
                lv_label_set_text(ui->recognition, "等待磁场数据");
                lv_label_set_text(ui->recognition_detail, "请确认底座已连接");
                break;
            }
        }

        std::snprintf(text, sizeof(text), "X %d   Y %d   Z %d",
                      snapshot.x, snapshot.y, snapshot.z);
        lv_label_set_text(ui->axis_values, text);
        std::snprintf(text, sizeof(text), "强度 %d", snapshot.magnitude);
        lv_label_set_text(ui->strength, text);
        lv_bar_set_value(ui->strength_bar, std::min(snapshot.magnitude, 4000), LV_ANIM_OFF);
    }

    if (snapshot.learning) {
        std::snprintf(text, sizeof(text), "采样 %u/%u",
                      static_cast<unsigned>(snapshot.learning_samples),
                      static_cast<unsigned>(MagneticShellManager::kLearningSamples));
        lv_label_set_text(ui->learn_button_label, text);
        lv_obj_add_state(ui->learn_button, LV_STATE_DISABLED);
    } else {
        lv_label_set_text(ui->learn_button_label, "学习当前");
        lv_obj_clear_state(ui->learn_button, LV_STATE_DISABLED);
    }
    std::snprintf(text, sizeof(text), "档案列表 %u",
                  static_cast<unsigned>(snapshot.profile_count));
    lv_label_set_text(ui->list_button_label, text);
    std::snprintf(text, sizeof(text), "稳定 %s · 噪声 %d",
                  snapshot.stable ? "是" : "否", snapshot.noise);
    lv_label_set_text(ui->footer, text);
    if (snapshot.last_event.sequence == 0) {
        lv_label_set_text(ui->event_footer, "Event --");
    } else {
        std::snprintf(text, sizeof(text), "Event #%u %s",
                      static_cast<unsigned>(snapshot.last_event.sequence),
                      event_name(snapshot.last_event.type));
        lv_label_set_text(ui->event_footer, text);
    }
}

void update_list_view(MagneticPageUi* ui,
                      const MagneticShellManager::Snapshot& snapshot)
{
    const size_t page_count = std::max<size_t>(
        1, (snapshot.profiles.size() + kProfilesPerPage - 1) / kProfilesPerPage
    );
    if (ui->list_page >= page_count) {
        ui->list_page = page_count - 1;
    }

    char text[96];
    std::snprintf(text, sizeof(text), "已记录 %u/6 · 第 %u/%u 页",
                  static_cast<unsigned>(snapshot.profiles.size()),
                  static_cast<unsigned>(ui->list_page + 1),
                  static_cast<unsigned>(page_count));
    lv_label_set_text(ui->list_summary, text);

    for (size_t row = 0; row < kProfilesPerPage; ++row) {
        const size_t index = ui->list_page * kProfilesPerPage + row;
        if (index >= snapshot.profiles.size()) {
            lv_label_set_text(ui->profile_rows[row], row == 0 && snapshot.profiles.empty()
                ? "暂无外壳档案" : "");
            continue;
        }
        const auto& profile = snapshot.profiles[index];
        std::snprintf(text, sizeof(text), "#%d  %s   强度 %d",
                      profile.id, profile.name.c_str(), profile.magnitude);
        lv_label_set_text(ui->profile_rows[row], text);
    }

    if (ui->list_page == 0) {
        lv_obj_add_state(ui->previous_button, LV_STATE_DISABLED);
    } else {
        lv_obj_clear_state(ui->previous_button, LV_STATE_DISABLED);
    }
    if (ui->list_page + 1 >= page_count) {
        lv_obj_add_state(ui->next_button, LV_STATE_DISABLED);
    } else {
        lv_obj_clear_state(ui->next_button, LV_STATE_DISABLED);
    }
}

void update_page(lv_timer_t* timer)
{
    auto* ui = static_cast<MagneticPageUi*>(lv_timer_get_user_data(timer));
    if (ui == nullptr || ui->container == nullptr ||
        lv_obj_has_flag(ui->container, LV_OBJ_FLAG_HIDDEN)) {
        return;
    }

    const auto snapshot = MagneticShellManager::GetInstance().GetSnapshot(ui->showing_list);
    if (ui->showing_list) {
        update_list_view(ui, snapshot);
    } else {
        update_live_view(ui, snapshot);
    }
}

} // namespace

extern "C" lv_obj_t* magnetic_telemetry_page_create_with_parent(lv_obj_t* parent)
{
    lv_obj_t* container = lv_obj_create(parent);
    s_ui.container = container;
    lv_obj_set_size(container, SCREEN_WIDTH, SCREEN_HEIGHT);
    lv_obj_set_style_bg_color(container, lv_color_hex(0x000000), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(container, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(container, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(container, 0, LV_PART_MAIN);
    lv_obj_clear_flag(container, LV_OBJ_FLAG_SCROLLABLE);

    s_ui.title = lv_label_create(container);
    lv_label_set_text(s_ui.title, "外壳识别");
    lv_obj_align(s_ui.title, LV_ALIGN_CENTER, 0, -136);
    set_label_style(s_ui.title, &ui_font_Device24, lv_color_hex(0xFFFFFF));

    s_ui.live_view = lv_obj_create(container);
    lv_obj_remove_style_all(s_ui.live_view);
    lv_obj_set_size(s_ui.live_view, SCREEN_WIDTH, 300);
    lv_obj_align(s_ui.live_view, LV_ALIGN_CENTER, 0, 24);
    lv_obj_clear_flag(s_ui.live_view, LV_OBJ_FLAG_SCROLLABLE);

    s_ui.sensor_status = lv_label_create(s_ui.live_view);
    lv_label_set_text(s_ui.sensor_status, "底座未连接");
    lv_obj_align(s_ui.sensor_status, LV_ALIGN_CENTER, 0, -126);
    set_label_style(s_ui.sensor_status, &ui_font_Device18, lv_color_hex(0x7BD88F));

    lv_obj_t* recognition_card = lv_obj_create(s_ui.live_view);
    lv_obj_set_size(recognition_card, 266, 62);
    lv_obj_align(recognition_card, LV_ALIGN_CENTER, 0, -76);
    lv_obj_set_style_bg_color(recognition_card, lv_color_hex(0x171B21), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(recognition_card, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(recognition_card, 1, LV_PART_MAIN);
    lv_obj_set_style_border_color(recognition_card, lv_color_hex(0x303741), LV_PART_MAIN);
    lv_obj_set_style_radius(recognition_card, 8, LV_PART_MAIN);
    lv_obj_set_style_pad_all(recognition_card, 0, LV_PART_MAIN);
    lv_obj_clear_flag(recognition_card, LV_OBJ_FLAG_SCROLLABLE);

    s_ui.recognition = lv_label_create(recognition_card);
    lv_label_set_text(s_ui.recognition, "等待磁场数据");
    lv_obj_align(s_ui.recognition, LV_ALIGN_CENTER, 0, -12);
    set_label_style(s_ui.recognition, &ui_font_Device18, lv_color_hex(0xFFFFFF));

    s_ui.recognition_detail = lv_label_create(recognition_card);
    lv_label_set_text(s_ui.recognition_detail, "请确认底座已连接");
    lv_obj_set_width(s_ui.recognition_detail, 240);
    lv_obj_set_style_text_align(s_ui.recognition_detail, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_align(s_ui.recognition_detail, LV_ALIGN_CENTER, 0, 14);
    set_label_style(s_ui.recognition_detail, &ui_font_Device18, lv_color_hex(0x8F98A8));

    s_ui.axis_values = lv_label_create(s_ui.live_view);
    lv_label_set_text(s_ui.axis_values, "X --   Y --   Z --");
    lv_obj_align(s_ui.axis_values, LV_ALIGN_CENTER, 0, -27);
    set_label_style(s_ui.axis_values, &lv_font_montserrat_20, lv_color_hex(0xE8EAF0));

    s_ui.strength = lv_label_create(s_ui.live_view);
    lv_label_set_text(s_ui.strength, "强度 --");
    lv_obj_align(s_ui.strength, LV_ALIGN_CENTER, 0, 2);
    set_label_style(s_ui.strength, &ui_font_Device18, lv_color_hex(0xE8EAF0));

    s_ui.strength_bar = lv_bar_create(s_ui.live_view);
    lv_obj_set_size(s_ui.strength_bar, 218, 9);
    lv_obj_align(s_ui.strength_bar, LV_ALIGN_CENTER, 0, 27);
    lv_bar_set_range(s_ui.strength_bar, 0, 4000);
    lv_bar_set_value(s_ui.strength_bar, 0, LV_ANIM_OFF);
    lv_obj_set_style_radius(s_ui.strength_bar, 5, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_ui.strength_bar, lv_color_hex(0x252A32), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_ui.strength_bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(s_ui.strength_bar, 5, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(s_ui.strength_bar, lv_color_hex(0x4FA3FF), LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(s_ui.strength_bar, LV_OPA_COVER, LV_PART_INDICATOR);

    s_ui.learn_button = create_button(s_ui.live_view, 122, 40, -66, 72,
                                      lv_color_hex(0x2867B2), "学习当前",
                                      &s_ui.learn_button_label);
    lv_obj_add_event_cb(s_ui.learn_button, learn_button_event, LV_EVENT_CLICKED, &s_ui);

    lv_obj_t* list_button = create_button(s_ui.live_view, 122, 40, 66, 72,
                                          lv_color_hex(0x303741), "档案列表 0",
                                          &s_ui.list_button_label);
    lv_obj_add_event_cb(list_button, list_button_event, LV_EVENT_CLICKED, &s_ui);

    s_ui.footer = lv_label_create(s_ui.live_view);
    lv_label_set_text(s_ui.footer, "稳定 否 · 噪声 0");
    lv_obj_align(s_ui.footer, LV_ALIGN_CENTER, 0, 119);
    set_label_style(s_ui.footer, &ui_font_Device18, lv_color_hex(0x707887));

    s_ui.event_footer = lv_label_create(s_ui.live_view);
    lv_label_set_text(s_ui.event_footer, "Event --");
    lv_obj_align(s_ui.event_footer, LV_ALIGN_CENTER, 0, 143);
    set_label_style(s_ui.event_footer, &ui_font_Device18, lv_color_hex(0x707887));

    s_ui.list_view = lv_obj_create(container);
    lv_obj_remove_style_all(s_ui.list_view);
    lv_obj_set_size(s_ui.list_view, SCREEN_WIDTH, 300);
    lv_obj_align(s_ui.list_view, LV_ALIGN_CENTER, 0, 24);
    lv_obj_clear_flag(s_ui.list_view, LV_OBJ_FLAG_SCROLLABLE);

    s_ui.list_summary = lv_label_create(s_ui.list_view);
    lv_label_set_text(s_ui.list_summary, "已记录 0/6 · 第 1/1 页");
    lv_obj_align(s_ui.list_summary, LV_ALIGN_CENTER, 0, -123);
    set_label_style(s_ui.list_summary, &ui_font_Device18, lv_color_hex(0x8F98A8));

    constexpr lv_coord_t row_y[kProfilesPerPage] = {-75, -27, 21};
    for (size_t index = 0; index < kProfilesPerPage; ++index) {
        lv_obj_t* row = lv_obj_create(s_ui.list_view);
        lv_obj_set_size(row, 270, 38);
        lv_obj_align(row, LV_ALIGN_CENTER, 0, row_y[index]);
        lv_obj_set_style_bg_color(row, lv_color_hex(0x171B21), LV_PART_MAIN);
        lv_obj_set_style_bg_opa(row, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_border_width(row, 0, LV_PART_MAIN);
        lv_obj_set_style_radius(row, 7, LV_PART_MAIN);
        lv_obj_set_style_pad_all(row, 0, LV_PART_MAIN);
        lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);

        s_ui.profile_rows[index] = lv_label_create(row);
        lv_label_set_text(s_ui.profile_rows[index], index == 0 ? "暂无外壳档案" : "");
        lv_obj_set_width(s_ui.profile_rows[index], 244);
        lv_obj_set_style_text_align(s_ui.profile_rows[index], LV_TEXT_ALIGN_LEFT, LV_PART_MAIN);
        lv_obj_align(s_ui.profile_rows[index], LV_ALIGN_LEFT_MID, 12, 0);
        set_label_style(s_ui.profile_rows[index], &ui_font_Device18, lv_color_hex(0xE8EAF0));
    }

    lv_obj_t* previous_label = nullptr;
    s_ui.previous_button = create_button(s_ui.list_view, 58, 38, -93, 75,
                                         lv_color_hex(0x303741), "<", &previous_label);
    set_label_style(previous_label, &lv_font_montserrat_20, lv_color_hex(0xFFFFFF));
    lv_obj_add_event_cb(s_ui.previous_button, previous_button_event, LV_EVENT_CLICKED, &s_ui);

    lv_obj_t* return_button = create_button(s_ui.list_view, 112, 38, 0, 75,
                                            lv_color_hex(0x2867B2), "返回识别", nullptr);
    lv_obj_add_event_cb(return_button, return_button_event, LV_EVENT_CLICKED, &s_ui);

    lv_obj_t* next_label = nullptr;
    s_ui.next_button = create_button(s_ui.list_view, 58, 38, 93, 75,
                                     lv_color_hex(0x303741), ">", &next_label);
    set_label_style(next_label, &lv_font_montserrat_20, lv_color_hex(0xFFFFFF));
    lv_obj_add_event_cb(s_ui.next_button, next_button_event, LV_EVENT_CLICKED, &s_ui);

    lv_obj_t* list_hint = lv_label_create(s_ui.list_view);
    lv_label_set_text(list_hint, "对小智说可改名或删除");
    lv_obj_align(list_hint, LV_ALIGN_CENTER, 0, 120);
    set_label_style(list_hint, &ui_font_Device18, lv_color_hex(0x707887));

    show_live_view(&s_ui);
    lv_timer_create(update_page, 750, &s_ui);
    lv_obj_add_flag(container, LV_OBJ_FLAG_HIDDEN);
    return container;
}
