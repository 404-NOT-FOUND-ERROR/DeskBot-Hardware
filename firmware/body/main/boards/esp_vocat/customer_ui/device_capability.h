#ifndef DESKBOT_DEVICE_CAPABILITY_H
#define DESKBOT_DEVICE_CAPABILITY_H

#include "lvgl.h"

#define PAGE_CAPABILITIES "CAPABILITIES"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Create the first read-only DeskBot capability page.
 *
 * This page intentionally reports source-level integration boundaries only.
 * It does not start sensors, send UART commands, or invent magnetic/CSI data.
 */
lv_obj_t *device_capability_create_with_parent(lv_obj_t *parent);

#ifdef __cplusplus
}
#endif

#endif /* DESKBOT_DEVICE_CAPABILITY_H */
