# Select profile-specific magnetic slide switch implementation.
if(NOT DEFINED MAG_SW_PROFILE)
    set(MAG_SW_PROFILE "base")
endif()

if(MAG_SW_PROFILE STREQUAL "base")
    set(MAG_SW_PROFILE_DIR "profiles/base")
elseif(MAG_SW_PROFILE STREQUAL "bell")
    set(MAG_SW_PROFILE_DIR "profiles/bell")
elseif(MAG_SW_PROFILE STREQUAL "iphone")
    set(MAG_SW_PROFILE_DIR "profiles/iphone")
elseif(MAG_SW_PROFILE STREQUAL "magnetic_accessory")
    set(MAG_SW_PROFILE_DIR "profiles/magnetic_accessory")
else()
    message(FATAL_ERROR "Unknown MAG_SW_PROFILE=${MAG_SW_PROFILE}")
endif()

idf_component_register(
    SRCS "${MAG_SW_PROFILE_DIR}/magnetic_slide_switch.c"
    INCLUDE_DIRS "${MAG_SW_PROFILE_DIR}/include"
    REQUIRES esp_driver_i2c
    PRIV_REQUIRES esp_adc control_serial nvs_flash BMM150_SensorAPI
)
