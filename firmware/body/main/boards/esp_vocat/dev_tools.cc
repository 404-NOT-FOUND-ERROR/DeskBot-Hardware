#include "dev_tools.h"
#include "esp_vocat.h"
#include "base_control.h"
#include "base_event_mapper.h"
#include "vocat_base_control.h"
#include "audio_analysis.h"
#include "magnetic_shell_manager.h"
#include "mcp_server.h"
#include "board.h"
#include "assets/lang_config.h"
#include <esp_log.h>
#include <cstdio>
#include <cstring>
#include "customer_ui/alarm_api.h"
#include "ui_bridge.h"

#define TAG "DevTools"

void DevTools::Initialize(EspS3Cat* board)
{
    auto &mcp_server = McpServer::GetInstance();
    char buffer[1024];
    const bool is_zh = (std::strncmp(Lang::CODE, "zh", 2) == 0);

    // Echo base action control
    std::snprintf(buffer, sizeof(buffer), "%s",
                  is_zh ?
                  "底座动作控制。可选动作:\n"
                  "shark_head: 摇头动作\n"
                  "shark_head_decay: 缓慢摇头动作\n"
                  "look_around: 环顾四周动作\n"
                  "beat_swing: 节拍摇摆动作\n"
                  "cat_nuzzle: 蹭头撒娇动作\n"
                  "calibrate: 校准底座\n"
                  "go_home: 返回主页面\n"
                  :
                  "Echo base action control. Available actions:\n"
                  "shark_head: shake head\n"
                  "shark_head_decay: slow shake head\n"
                  "look_around: look around\n"
                  "beat_swing: beat swing\n"
                  "cat_nuzzle: cat nuzzle\n"
                  "calibrate: calibrate base\n"
                  "go_home: switch to home page\n");
    mcp_server.AddTool("self.echo_base.set_action", buffer,
    PropertyList({
        Property("action", kPropertyTypeString),
    }), [board](const PropertyList & properties) -> ReturnValue {
        const std::string &action = properties["action"].value<std::string>();
        int action_value = -1;

        ESP_LOGI(TAG, "&&& Do Action: %s", action.c_str());
        if (action == "shark_head") {
            action_value = VOCAT_BASE_CMD_SET_ACTION_SHARK_HEAD;
        } else if (action == "shark_head_decay") {
            action_value = VOCAT_BASE_CMD_SET_ACTION_SHARK_HEAD_DECAY;
        } else if (action == "look_around")
        {
            action_value = VOCAT_BASE_CMD_SET_ACTION_LOOK_AROUND;
        } else if (action == "beat_swing")
        {
            action_value = VOCAT_BASE_CMD_SET_ACTION_BEAT_SWING;
        } else if (action == "cat_nuzzle")
        {
            action_value = VOCAT_BASE_CMD_SET_ACTION_CAT_NUZZLE;
        } else if (action == "calibrate")
        {
            vocat_base_control_set_calibrate();
            BaseControl* base_control = board->GetBaseControl();
            if (base_control != nullptr) {
                bool completed = base_control->WaitForCalibrationComplete(30000);
                if (!completed) {
                    ESP_LOGW(TAG, "Calibration wait timeout");
                    return false;
                }
            }
        } else if (action == "go_home")
        {
            ui_bridge_switch_page(UI_BRIDGE_PAGE_HOME);
        } else
        {
            return false;
        }

        if (action_value != -1)
        {
            esp_err_t ret = vocat_base_control_set_action(action_value);
            if (ret != ESP_OK) {
                ESP_LOGE(TAG, "Failed to set action: %d", ret);
                return false;
            }
        }
        return true;
    });

    // Audio analysis mode control
    std::snprintf(buffer, sizeof(buffer), "%s",
                  is_zh ?
                  "设置音频分析模式。可选模式:\n"
                  "beat_detection: 鼓点检测模式\n"
                  "doa_follow: 声源方向跟随模式\n"
                  "disabled: 关闭音频分析"
                  :
                  "Set audio analysis mode. Available modes:\n"
                  "beat_detection: beat detection mode\n"
                  "doa_follow: DOA follow mode\n"
                  "disabled: disable audio analysis");
    mcp_server.AddTool("self.echo_base.set_audio_mode", buffer,
    PropertyList({
        Property("mode", kPropertyTypeString),
    }), [board](const PropertyList & properties) -> ReturnValue {
        const std::string &mode = properties["mode"].value<std::string>();
        AudioAnalysisMode analysis_mode = AudioAnalysisMode::DISABLED;

        if (mode == "beat_detection") {
            analysis_mode = AudioAnalysisMode::BEAT_DETECTION;
        } else if (mode == "doa_follow") {
            analysis_mode = AudioAnalysisMode::DOA_FOLLOW;
        } else if (mode == "disabled")
        {
            analysis_mode = AudioAnalysisMode::DISABLED;
        } else
        {
            ESP_LOGE(TAG, "Unknown audio analysis mode: %s", mode.c_str());
            return false;
        }

        board->SetAudioAnalysisMode(analysis_mode);
        ESP_LOGI(TAG, "Audio analysis mode set to: %s", mode.c_str());
        return true;
    });

    mcp_server.AddTool("self.echo_base.events",
    is_zh ? "读取底座连接、机械状态和最近事件。只读，不发送串口或动作指令。无需参数。"
          : "Read base link state, mechanical status, and recent events. Read-only; no UART or action commands.",
    PropertyList(), [](const PropertyList&) -> ReturnValue {
        cJSON* root = cJSON_CreateObject();
        if (root == nullptr) {
            return std::string("无法创建底座事件结果");
        }

        BaseControl* base = BaseControl::GetInstance();
        if (base == nullptr) {
            cJSON_AddBoolToObject(root, "online", false);
            cJSON_AddStringToObject(root, "mechanical_status", "unavailable");
            cJSON_AddStringToObject(root, "mechanical_status_name", "unavailable");
            cJSON_AddArrayToObject(root, "events");
            return root;
        }

        cJSON_AddBoolToObject(root, "online", base->IsOnline());
        bool mechanical_valid = false;
        const uint16_t mechanical_status = base->GetMechanicalStatus(&mechanical_valid);
        if (mechanical_valid) {
            cJSON_AddNumberToObject(root, "mechanical_status", mechanical_status);
            cJSON_AddStringToObject(root, "mechanical_status_name",
                                    base_event_mapper::MechanicalStatusName(mechanical_status));
        } else {
            cJSON_AddStringToObject(root, "mechanical_status", "unavailable");
            cJSON_AddStringToObject(root, "mechanical_status_name", "unavailable");
        }

        cJSON* events = cJSON_AddArrayToObject(root, "events");
        if (events != nullptr) {
            for (const auto& event : base->GetRecentEvents()) {
                cJSON* item = cJSON_CreateObject();
                if (item == nullptr) {
                    continue;
                }
                cJSON_AddNumberToObject(item, "sequence", event.sequence);
                cJSON_AddStringToObject(item, "type", event.type.c_str());
                cJSON_AddNumberToObject(item, "command", event.command);
                cJSON_AddNumberToObject(item, "value", event.value);
                cJSON_AddBoolToObject(item, "has_value", event.has_value);
                cJSON_AddBoolToObject(item, "synthetic", event.synthetic);
                cJSON_AddNumberToObject(item, "timestamp_ms",
                                        static_cast<double>(event.timestamp_ms));

                std::string data_hex;
                data_hex.reserve(static_cast<size_t>(event.data_length) * 2);
                char byte_hex[3] = {};
                for (size_t index = 0; index < event.data_length; ++index) {
                    std::snprintf(byte_hex, sizeof(byte_hex), "%02X", event.data[index]);
                    data_hex += byte_hex;
                }
                cJSON_AddStringToObject(item, "data_hex", data_hex.c_str());
                cJSON_AddStringToObject(item, "frame_hex", event.frame_hex.c_str());
                cJSON_AddItemToArray(events, item);
            }
        }
        return root;
    });

    mcp_server.AddTool("self.shell.identify",
    is_zh ? "查询当前磁吸外壳的识别结果和置信度。无需参数。"
          : "Identify the current magnetic shell and report confidence.",
    PropertyList(), [](const PropertyList&) -> ReturnValue {
        return MagneticShellManager::GetInstance().DescribeCurrent();
    });

    mcp_server.AddTool("self.shell.learn",
    is_zh ? "学习当前安装的磁吸外壳。学习期间让用户保持外壳不动。name 是用户为外壳指定的名称，例如蓝色猫耳。"
          : "Learn the currently attached magnetic shell. Ask the user to keep it still.",
    PropertyList({
        Property("name", kPropertyTypeString),
    }), [](const PropertyList& properties) -> ReturnValue {
        std::string message;
        MagneticShellManager::GetInstance().StartLearning(
            properties["name"].value<std::string>(), &message
        );
        return message;
    });

    mcp_server.AddTool("self.shell.list",
    is_zh ? "列出已经学习并保存的磁吸外壳档案及编号。无需参数。"
          : "List saved magnetic shell profiles and their IDs.",
    PropertyList(), [](const PropertyList&) -> ReturnValue {
        return MagneticShellManager::GetInstance().DescribeProfiles();
    });

    mcp_server.AddTool("self.shell.events",
    is_zh ? "查询最近的磁吸外壳识别事件，例如安装、切换、未知或移除。无需参数。"
          : "List recent magnetic shell recognition events.",
    PropertyList(), [](const PropertyList&) -> ReturnValue {
        return MagneticShellManager::GetInstance().DescribeEvents();
    });

    mcp_server.AddTool("self.shell.manage",
    is_zh ? "管理磁吸外壳档案。action 为 rename 或 delete；id 是外壳编号；rename 时填写 name。"
          : "Manage magnetic shell profiles. action: rename or delete.",
    PropertyList({
        Property("action", kPropertyTypeString),
        Property("id", kPropertyTypeInteger, 1, 6),
        Property("name", kPropertyTypeString, std::string("")),
    }), [](const PropertyList& properties) -> ReturnValue {
        const std::string& action = properties["action"].value<std::string>();
        const int id = properties["id"].value<int>();
        const std::string& name = properties["name"].value<std::string>();
        std::string message;
        if (action == "rename") {
            MagneticShellManager::GetInstance().RenameProfile(id, name, &message);
        } else if (action == "delete") {
            MagneticShellManager::GetInstance().DeleteProfile(id, &message);
        } else {
            message = "action 只支持 rename 或 delete";
        }
        return message;
    });

    // Pomodoro timer control
    mcp_server.AddTool("self.pomodoro.start",
    is_zh ? "开启番茄钟定时器，设置倒计时时间（1-60分钟，默认5分钟）"
          : "Start pomodoro timer with countdown minutes (1-60, default 5).",
    PropertyList({
        Property("minutes", kPropertyTypeInteger, 5, 1, 60),
    }), [](const PropertyList& properties) -> ReturnValue {
        int minutes = properties["minutes"].value<int>();
        ESP_LOGI(TAG, "Starting pomodoro timer with %d minutes", minutes);
        alarm_start_pomodoro(minutes);
        return true;
    });

    // Pomodoro timer control (start/pause)
    mcp_server.AddTool("self.pomodoro.control",
    is_zh ? "控制番茄钟运行状态。参数：start-启动，pause-暂停"
          : "Control pomodoro running state. action: start or pause.",
    PropertyList({
        Property("action", kPropertyTypeString),
    }), [](const PropertyList& properties) -> ReturnValue {
        const std::string &action = properties["action"].value<std::string>();
        
        bool success = false;
        if (action == "start") {
            ESP_LOGI(TAG, "Starting pomodoro timer");
            success = alarm_resume_pomodoro();
        } else if (action == "pause") {
            ESP_LOGI(TAG, "Pausing pomodoro timer");
            success = alarm_pause_pomodoro();
        } else {
            ESP_LOGE(TAG, "Unknown pomodoro action: %s (expected 'start' or 'pause')", action.c_str());
            return false;
        }
        return success;
    });

    // Sleep timer control
    mcp_server.AddTool("self.sleep.start",
    is_zh ? "设置睡眠闹钟，从当前时间到指定结束时间（24小时制）。参数：end_hour(0-23), end_min(0-59)"
          : "Set sleep timer from current time to specified end time (24h). params: end_hour(0-23), end_min(0-59).",
    PropertyList({
        Property("end_hour", kPropertyTypeInteger, 8, 0, 23),
        Property("end_min", kPropertyTypeInteger, 0, 0, 59),
    }), [](const PropertyList& properties) -> ReturnValue {
        int end_hour = properties["end_hour"].value<int>();
        int end_min = properties["end_min"].value<int>();
        
        // Validate parameters
        if (end_hour < 0 || end_hour >= 24) {
            ESP_LOGE(TAG, "Invalid end_hour: %d (must be 0-23)", end_hour);
            return false;
        }
        if (end_min < 0 || end_min >= 60) {
            ESP_LOGE(TAG, "Invalid end_min: %d (must be 0-59)", end_min);
            return false;
        }
        
        ESP_LOGI(TAG, "Setting sleep timer: current time -> %02d:%02d", end_hour, end_min);
        alarm_start_sleep(end_hour, end_min);
        return true;
    });
}
