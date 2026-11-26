/* SPDX-License-Identifier: MIT */
#ifndef AIRBOAT_LISTEN_OVERLAY_RGB565_H
#define AIRBOAT_LISTEN_OVERLAY_RGB565_H

#include <stdint.h>

/* Use only for the verified white-on-black listen.eaf mask, after drawing the
 * current face. Both arguments are host-order RGB565, before panel byte swap.
 * Its black matte becomes zero coverage; gray edges blend toward white.
 * The existing display owner supplies/owns all pixels; no allocation or state. */
static inline uint16_t airboat_listen_over_rgb565(uint16_t background, uint16_t mask)
{
    const unsigned r = (background >> 11) & 31U;
    const unsigned g = (background >> 5) & 63U;
    const unsigned b = background & 31U;
    const unsigned out_r = r + ((31U - r) * ((mask >> 11) & 31U) + 15U) / 31U;
    const unsigned out_g = g + ((63U - g) * ((mask >> 5) & 63U) + 31U) / 63U;
    const unsigned out_b = b + ((31U - b) * (mask & 31U) + 15U) / 31U;
    return (uint16_t)((out_r << 11) | (out_g << 5) | out_b);
}

#endif
