/* Real pinned IDF functions run against a small heap and queued-DMA model. */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int esp_err_t;
enum { ESP_OK, ESP_ERR_NO_MEM, ESP_ERR_INVALID_ARG, ESP_ERR_INVALID_STATE };
enum { MALLOC_CAP_DMA=1, MALLOC_CAP_SPIRAM=2, MALLOC_CAP_INTERNAL=4,
       MALLOC_CAP_DMA_DESC_AHB=8, MALLOC_CAP_DMA_DESC_AXI=16,
       MALLOC_CAP_CACHE_ALIGNED=32, MALLOC_CAP_SIMD=64, MALLOC_CAP_INVALID=128 };
enum { SPI_TRANS_CS_KEEP_ACTIVE=1, SPI_TRANS_DMA_USE_PSRAM=2,
       SPI_TRANS_MULTILINE_CMD=4, SPI_TRANS_MULTILINE_ADDR=8,
       SPI_TRANS_MODE_OCT=16, SPI_TRANS_MODE_QIO=32, SPI_TRANS_USE_RXDATA=64,
       SPI_TRANS_DMA_BUFFER_ALIGN_MANUAL=128 };
#define CONFIG_IDF_TARGET_ESP32S2 0
#define CONFIG_APP_BUILD_TYPE_PURE_RAM_APP 0
#define SOC_GDMA_SUPPORTED 1
#define SOC_AXI_GDMA_SUPPORTED 0
#define SOC_PSRAM_DMA_CAPABLE 1
#define SOC_DMA_CAN_ACCESS_FLASH 0
#define SOC_SIMD_INSTRUCTION_SUPPORTED 0
#define SOC_MEMSPI_ENCRYPTION_ALIGNMENT 16
#define SPI_MASTER_ISR_ATTR
#define HEAP_IRAM_ATTR
#define SPI_TAG "spi"
#define TAG "lcd"
#define portMAX_DELAY UINT32_MAX
#define ESP_CACHE_MSYNC_FLAG_DIR_C2M 1
#define ESP_CACHE_MSYNC_FLAG_UNALIGNED 2
#define ESP_CACHE_MSYNC_FLAG_DIR_M2C 4
#define MAX(a,b) ((a)>(b)?(a):(b))
#define __containerof(p,t,m) ((t*)((char*)(p)-offsetof(t,m)))
#define ESP_LOGE(...) ((void)0)
#define ESP_EARLY_LOGV(...) ((void)0)
#define ESP_RETURN_ON_FALSE_ISR(expr, error, ...) do { if (!(expr)) return error; } while (0)
#define ESP_RETURN_ON_ERROR(expr, ...) do { esp_err_t r=(expr); if(r!=ESP_OK) return r; } while (0)
#define ESP_GOTO_ON_ERROR(expr, label, ...) do { ret=(expr); if(ret!=ESP_OK) goto label; } while (0)

typedef struct { size_t dma_align_tx_ext,dma_align_tx_int,dma_align_rx_ext,dma_align_rx_int; } dma_ctx_t;
typedef struct { size_t cache_align_int,cache_align_ext; } bus_attr_t;
typedef struct { int id; dma_ctx_t *dma_ctx; bus_attr_t *bus_attr; } spi_host_t;
typedef struct { void *user; size_t length,rxlength; const void *tx_buffer; void *rx_buffer;
                 uint32_t flags; uint8_t tx_data[4],rx_data[4]; } spi_transaction_t;
typedef struct { spi_transaction_t *trans; const uint32_t *buffer_to_send; uint32_t *buffer_to_rcv; } spi_trans_priv_t;
typedef struct { spi_transaction_t base; struct {int dc_gpio_level,en_trans_done_cb;} flags; } lcd_spi_trans_descriptor_t;
typedef struct { int unused; } esp_lcd_panel_io_t;
typedef struct {
    esp_lcd_panel_io_t base;
    void *spi_dev;
    struct { unsigned octal_mode,quad_mode,psram_dma_direct,dc_cmd_level,dc_data_level; } flags;
    size_t num_trans_inflight,queue_size;
    lcd_spi_trans_descriptor_t trans_pool[2];
    size_t spi_trans_max_bytes,lcd_cmd_bits;
} esp_lcd_panel_io_spi_t;

typedef struct { void *ptr,*backing; size_t bytes; uint32_t caps; } allocation_t;
static allocation_t allocations[8];
static _Alignas(64) uint8_t source[262272], received[262272];
static uint8_t *begin;
static size_t total, transferred, count, head, internal_requests, external_requests, freed, cache_syncs, done, queue_attempts;
static size_t fail_queue_at;
static size_t content_cases;
static bool fail_internal=true,fail_external=false,source_external=true;
static spi_transaction_t *queued[2];
static spi_trans_priv_t private_desc[2];
static dma_ctx_t dma={1,4,32,4};
static bus_attr_t bus={1,64};
static spi_host_t host={2,&dma,&bus};
static esp_lcd_panel_io_spi_t io;

static bool in_range(const void *p,const void *start,size_t n) {
    return (uintptr_t)p>=(uintptr_t)start && (uintptr_t)p-(uintptr_t)start<n;
}
static bool esp_ptr_external_ram(const void *p) {
    if(source_external && in_range(p,source,sizeof(source))) return true;
    for(size_t i=0;i<8;i++)
        if(allocations[i].ptr && (allocations[i].caps&MALLOC_CAP_SPIRAM) &&
           in_range(p,allocations[i].ptr,allocations[i].bytes)) return true;
    return false;
}
static bool esp_ptr_dma_ext_capable(const void *p) { return esp_ptr_external_ram(p); }
static bool esp_ptr_dma_capable(const void *p) { return !esp_ptr_external_ram(p) && !((uintptr_t)p&3); }
static bool efuse_hal_flash_encryption_enabled(void) { return false; }
static esp_err_t esp_cache_get_alignment(uint32_t caps,size_t *alignment) {
    *alignment=(caps&MALLOC_CAP_SPIRAM)?64:1; return ESP_OK;
}
static esp_err_t esp_cache_msync(void *p,size_t n,uint32_t flags) {
    assert(p && n && (flags&ESP_CACHE_MSYNC_FLAG_DIR_C2M)); ++cache_syncs; return ESP_OK;
}

#include "psram_heap_adjust.inc"

static void *heap_caps_aligned_alloc(size_t alignment,size_t bytes,uint32_t caps) {
    if(caps&MALLOC_CAP_SPIRAM) ++external_requests; else ++internal_requests;
    if(((caps&MALLOC_CAP_SPIRAM)&&fail_external) || (!(caps&MALLOC_CAP_SPIRAM)&&fail_internal)) return NULL;
    esp_heap_adjust_alignment_to_hw(&alignment,&bytes,&caps);
    /* Model the real S3 heap tags: SPIRAM itself has no MALLOC_CAP_DMA bit. */
    assert(!((caps&MALLOC_CAP_SPIRAM)&&(caps&MALLOC_CAP_DMA)));
    void *raw=malloc(bytes+alignment);
    assert(raw);
    void *p=(void*)(((uintptr_t)raw+alignment-1)&~(uintptr_t)(alignment-1));
    for(size_t i=0;i<8;i++) if(!allocations[i].ptr) {
        allocations[i]=(allocation_t){p,raw,bytes,caps}; return p;
    }
    abort();
}
static void release_buffer(void *p) {
    if(!p) return;
    for(size_t i=0;i<8;i++) if(allocations[i].ptr==p) {
        free(allocations[i].backing); allocations[i]=(allocation_t){0}; ++freed; return;
    }
    abort();
}
#define free release_buffer
#include "psram_dma_setup.inc"
#include "psram_dma_release.inc"
#undef free

static esp_err_t spi_device_acquire_bus(void *dev,unsigned wait) { (void)dev;(void)wait;return ESP_OK; }
static esp_err_t spi_device_release_bus(void *dev) { (void)dev;return ESP_OK; }
static void spi_lcd_prepare_cmd_buffer(esp_lcd_panel_io_spi_t *obj,int *cmd) { (void)obj;(void)cmd; }
static esp_err_t spi_device_polling_transmit(void *dev,spi_transaction_t *t) {
    (void)dev;(void)t;assert(count==0);return ESP_OK;
}
static esp_err_t spi_device_get_trans_result(void *dev,spi_transaction_t **out,unsigned wait) {
    (void)dev;(void)wait;
    assert(count);
    size_t slot=head;
    *out=queued[slot];
    spi_trans_priv_t *priv=&private_desc[slot];
    size_t n=((*out)->length+7)/8;
    assert(transferred+n<=total);
    /* DMA reads only now: a temporary copy must survive until completion. */
    /* AHB DMA v1 reads bytes; it does not perform a CPU uint32_t load. */
    const uint8_t *dma_bytes=NULL;
    memcpy(&dma_bytes,&priv->buffer_to_send,sizeof(dma_bytes));
    assert(memcmp(dma_bytes,begin+transferred,n)==0);
    memcpy(received+transferred,dma_bytes,n); transferred+=n;
    if(__containerof(*out,lcd_spi_trans_descriptor_t,base)->flags.en_trans_done_cb) ++done;
    uninstall_priv_desc(priv);
    head=(head+1)%2; --count;return ESP_OK;
}
static esp_err_t spi_device_queue_trans(void *dev,spi_transaction_t *t,unsigned wait) {
    (void)dev;(void)wait;
    if(++queue_attempts==fail_queue_at) return ESP_ERR_NO_MEM;
    assert(count<2);
    uint32_t *p=NULL;
    esp_err_t r=setup_dma_priv_buffer(&host,(uint32_t*)t->tx_buffer,(uint32_t)((t->length+7)/8),true,t->flags,&p);
    if(r!=ESP_OK) return r;
    size_t slot=(head+count)%2;
    queued[slot]=t;
    private_desc[slot]=(spi_trans_priv_t){t,p,NULL}; ++count;
    return ESP_OK;
}
#include "psram_lcd_io.inc"

static void drain(void) {
    while(count) {spi_transaction_t *t=NULL;assert(spi_device_get_trans_result(NULL,&t,0)==ESP_OK);--io.num_trans_inflight;}
    assert(io.num_trans_inflight==0);
    for(size_t i=0;i<8;i++) assert(!allocations[i].ptr);
}
static void reset(size_t bytes,size_t offset,bool direct,size_t alignment) {
    assert(count==0);
    io=(esp_lcd_panel_io_spi_t){0};io.queue_size=2;io.spi_trans_max_bytes=4092;io.lcd_cmd_bits=32;
    io.flags.psram_dma_direct=direct;io.flags.quad_mode=1;
    total=bytes;begin=source+offset;transferred=0;head=0;
    internal_requests=external_requests=freed=cache_syncs=done=queue_attempts=fail_queue_at=0;
    dma.dma_align_tx_ext=alignment;fail_internal=true;fail_external=false;source_external=true;
    for(size_t i=0;i<bytes;i++) begin[i]=(uint8_t)((i*31+13)%251);
    memset(received,0,bytes);
}
static void successful_send(size_t bytes,size_t offset,size_t alignment) {
    reset(bytes,offset,true,alignment);
    assert(panel_io_spi_tx_color(&io.base,0x32002c00,begin,bytes)==ESP_OK);
    assert(done==0); /* Last queued transfer still owns its source. */
    drain();
    assert(transferred==bytes && memcmp(received,begin,bytes)==0 && done==1);
    assert(internal_requests==0 && freed==external_requests);
    assert(cache_syncs==queue_attempts);
    if(alignment==1) assert(external_requests==0);
    ++content_cases;
}
int main(void) {
    const size_t sizes[]={2,64,4092,5760,7200,11520,3590,259200};
    const size_t offsets[]={0,1,2,6};
    for(size_t a=1;a<=64;a*=8)
        for(size_t i=0;i<sizeof(sizes)/sizeof(sizes[0]);i++)
            for(size_t j=0;j<sizeof(offsets)/sizeof(offsets[0]);j++) successful_send(sizes[i],offsets[j],a);
    successful_send(11520,1,32);
    /* The old setting reproduces internal allocation failure with ample PSRAM. */
    reset(11520,0,false,1);
    assert(panel_io_spi_tx_color(&io.base,0x32002c00,begin,total)==ESP_ERR_NO_MEM);
    assert(internal_requests==1 && external_requests==0 && count==0);drain();
    /* Internal, already DMA-capable pixels keep their previous direct path. */
    reset(5760,0,true,1);source_external=false;
    assert(panel_io_spi_tx_color(&io.base,0x32002c00,begin,total)==ESP_OK);drain();
    assert(!internal_requests && !external_requests && done==1);
    /* A generic aligned-copy allocation can still fail; errors must propagate. */
    reset(11520,1,true,32);fail_external=true;
    assert(panel_io_spi_tx_color(&io.base,0x32002c00,begin,total)==ESP_ERR_NO_MEM);
    assert(count==0 && external_requests==1 && !internal_requests);drain();
    /* Mid-frame queue failure recycles outstanding copies before the next frame. */
    reset(11520,1,true,32);fail_queue_at=3;
    assert(panel_io_spi_tx_color(&io.base,0x32002c00,begin,total)==ESP_ERR_NO_MEM);
    drain();assert(done==0 && freed==external_requests);
    fail_queue_at=0;transferred=0;
    assert(panel_io_spi_tx_color(&io.base,0x32002c00,begin,total)==ESP_OK);drain();
    assert(done==1 && transferred==total && memcmp(received,begin,total)==0);
    printf("psram_dma_native_passed: %zu full-content cases; baseline failure, allocation failure, queue recovery, completion lifetime\n",content_cases);
    return 0;
}
