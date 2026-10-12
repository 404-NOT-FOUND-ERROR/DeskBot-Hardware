#ifndef DESKBOT_HOST_ESP_LOG_H
#define DESKBOT_HOST_ESP_LOG_H

#define DESKBOT_HOST_LOG(tag, format, ...) \
    ((void)sizeof(tag), (void)sizeof(format))
#define ESP_LOGE(tag, format, ...) DESKBOT_HOST_LOG(tag, format, ##__VA_ARGS__)
#define ESP_LOGW(tag, format, ...) DESKBOT_HOST_LOG(tag, format, ##__VA_ARGS__)
#define ESP_LOGI(tag, format, ...) DESKBOT_HOST_LOG(tag, format, ##__VA_ARGS__)

#endif
