"""The isolated USB path correction applied to the retained E3c-3 source."""
import hashlib

SOURCE_NAME = "main/boards/esp_vocat/studio_usb_diagnostic.cc"
BASE_SOURCE_SHA256 = "71747d5efb4d69899b9866a241eacb6cc1147225f736e0b9242873525a69a231"


def candidate_bytes(original):
    if hashlib.sha256(original).hexdigest() != BASE_SOURCE_SHA256:
        raise ValueError("expected frozen E3c-3 USB source")
    source = original.decode("utf-8")
    source = source.replace("#include <driver/usb_serial_jtag.h>",
                            "#include <cerrno>\n#include <driver/usb_serial_jtag.h>")
    source = source.replace("Endpoint endpoint;\nvoid DiagnosticTask(void*) {", """Endpoint endpoint;
#if CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG
constexpr char kUsbDevice[] = "/dev/secondary";
#elif CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
constexpr char kUsbDevice[] = "/dev/usbserjtag";
#else
#error "Studio USB diagnostics require a USB Serial/JTAG console VFS"
#endif
void DiagnosticTask(void*) {""")
    source = source.replace('open("/dev/usbserjtag", O_RDONLY)', "open(kUsbDevice, O_RDONLY)")
    source = source.replace('ESP_LOGW("DeskBotSession", "USB input unavailable");',
                            'ESP_LOGW("DeskBotSession", "USB input unavailable: %s (errno=%d)", kUsbDevice, errno);')
    source = source.replace("    bool connected = false;", """    ESP_LOGI("DeskBotSession", "USB input ready: %s", kUsbDevice);
    bool connected = false;""")
    return source.encode("utf-8")
