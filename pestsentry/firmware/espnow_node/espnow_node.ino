// PestSentry hub integration for clearcam. Copyright (C) 2026 Aarav Gaudani. Licensed under GPL-3.0-or-later; see ../../../LICENSE.md (repo root).
#include <algorithm>
#include <WiFi.h>
#include <esp_camera.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_idf_version.h>

#define NODE_ID 1
#define GATEWAY_MAC {0x00, 0x00, 0x00, 0x00, 0x00, 0x00}  // Example only
#define WIFI_CHANNEL 1
#define CAMERA_FRAME_SIZE FRAMESIZE_QVGA
#define CAPTURE_INTERVAL_MS 1000
// Optional PIR: use common ground and 3.3 V logic OUT to GPIO13 (SD unused).
// #define PIR_PIN 13
// Supply regulated 5 V at the board's 5V pin, with headroom for camera/radio
// current spikes (allow 1 A). Never connect a 5 V signal to a GPIO.

// Multibyte radio fields use native little-endian order on both ESP32 boards.
struct __attribute__((packed)) Chunk {
  uint8_t magic;
  uint8_t node;
  uint8_t seq;
  uint16_t index;
  uint16_t count;
  uint8_t length;
  uint8_t data[240];
};
constexpr size_t HEADER = offsetof(Chunk, data);
static_assert(sizeof(Chunk) <= 250, "ESP-NOW packet too large");
constexpr size_t MAX_FRAME = 60 * 1024;

static_assert(NODE_ID >= 1 && NODE_ID <= 5, "Choose a node from 1 to 5");
const uint8_t gateway[] = GATEWAY_MAC;
QueueHandle_t sent;
bool active = false;
uint8_t sequence = 0;

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
void onSent(const wifi_tx_info_t *, esp_now_send_status_t status) {
#else
void onSent(const uint8_t *, esp_now_send_status_t status) {
#endif
  xQueueSend(sent, &status, 0);
}

bool initCamera() {
  camera_config_t c = {};
  // AI-Thinker pin map. D0..D7 are the sensor's Y2..Y9 respectively.
  c.pin_pwdn = 32; c.pin_reset = -1; c.pin_xclk = 0;
  c.pin_sccb_sda = 26; c.pin_sccb_scl = 27;
  c.pin_d0 = 5; c.pin_d1 = 18; c.pin_d2 = 19; c.pin_d3 = 21;
  c.pin_d4 = 36; c.pin_d5 = 39; c.pin_d6 = 34; c.pin_d7 = 35;
  c.pin_vsync = 25; c.pin_href = 23; c.pin_pclk = 22;
  c.ledc_timer = LEDC_TIMER_0; c.ledc_channel = LEDC_CHANNEL_0;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = CAMERA_FRAME_SIZE;
  c.jpeg_quality = 12;
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.fb_count = 1;
  return esp_camera_init(&c) == ESP_OK;
}

void setup() {
#ifdef PIR_PIN
  pinMode(PIR_PIN, INPUT);
#endif
  if (!initCamera() || !WiFi.mode(WIFI_STA)) return;
  if (esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE) != ESP_OK) return;
  sent = xQueueCreate(1, sizeof(esp_now_send_status_t));
  if (!sent || esp_now_init() != ESP_OK || esp_now_register_send_cb(onSent) != ESP_OK) return;
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, gateway, 6);
  peer.channel = WIFI_CHANNEL;
  peer.ifidx = WIFI_IF_STA;
  active = esp_now_add_peer(&peer) == ESP_OK;
}

void sendFrame(camera_fb_t *fb) {
  if (!fb->len || fb->len > MAX_FRAME) return;
  Chunk chunk = {};
  chunk.magic = 0xA7;
  chunk.node = NODE_ID;
  chunk.seq = sequence++;
  chunk.count = (fb->len + sizeof(chunk.data) - 1) / sizeof(chunk.data);
  for (chunk.index = 0; chunk.index < chunk.count; ++chunk.index) {
    size_t offset = chunk.index * sizeof(chunk.data);
    chunk.length = std::min(sizeof(chunk.data), fb->len - offset);
    memcpy(chunk.data, fb->buf + offset, chunk.length);
    if (esp_now_send(gateway, reinterpret_cast<uint8_t *>(&chunk),
                     HEADER + chunk.length) != ESP_OK) break;
    esp_now_send_status_t status;
    if (!xQueueReceive(sent, &status, pdMS_TO_TICKS(500))) {
      ESP.restart();  // Reset before a late callback can acknowledge another packet.
      return;
    }
    if (status != ESP_NOW_SEND_SUCCESS) break;
  }
}

void loop() {
  static uint32_t last = millis() - CAPTURE_INTERVAL_MS;
  bool trigger = false;
#ifdef PIR_PIN
  static bool previous = false;
  bool high = digitalRead(PIR_PIN);
  trigger = high && !previous;
  previous = high;
#endif
  if (active && (trigger || millis() - last >= CAPTURE_INTERVAL_MS)) {
    last = millis();
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb) {
      sendFrame(fb);
      esp_camera_fb_return(fb);
    }
  }
  delay(5);
}
