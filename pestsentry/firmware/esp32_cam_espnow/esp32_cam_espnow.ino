// PestSentry node: JPEG chunks over ESP-NOW to the gateway sketch.
//
// ESP-NOW frames are 250 bytes max, so a QVGA JPEG is split. Five nodes
// share one radio. This sketch sends one frame every two seconds, in a
// slot based on CAMERA_ID, which is slow on purpose.
//
// Set GATEWAY_MAC to the gateway board (printed on its serial console).
// Set WIFI_CHANNEL to the channel of the Wi-Fi network the gateway joined.
// ESP-NOW does not use the Wi-Fi password, but both radios must share a channel.
//
// Do not commit a real SSID. The SSID is only a comment so you can look up
// the channel on your router; this sketch does not join the network.

#include "esp_camera.h"
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

static const uint8_t CAMERA_ID = 1;  // 1..5, one value per board
static const uint8_t WIFI_CHANNEL = 1;
static const uint8_t GATEWAY_MAC[6] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00};

#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

static const size_t CHUNK = 200;

#pragma pack(push, 1)
struct ChunkHeader {
  uint8_t cam_id;
  uint16_t frame_id;
  uint8_t seq;
  uint8_t count;
  uint16_t total;
};
#pragma pack(pop)

uint16_t frameId = 0;
uint32_t lastSend = 0;

bool startCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_QVGA;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 30;
  config.fb_count = 1;
  return esp_camera_init(&config) == ESP_OK;
}

void sendFrame(const uint8_t *jpg, size_t len) {
  if (len > 24 * 1024) {
    Serial.println("frame too large, drop");
    return;
  }
  frameId++;
  uint8_t count = (len + CHUNK - 1) / CHUNK;
  uint8_t packet[sizeof(ChunkHeader) + CHUNK];
  for (uint8_t seq = 0; seq < count; seq++) {
    size_t offset = seq * CHUNK;
    size_t n = len - offset;
    if (n > CHUNK) n = CHUNK;
    ChunkHeader header{CAMERA_ID, frameId, seq, count, (uint16_t)len};
    memcpy(packet, &header, sizeof(header));
    memcpy(packet + sizeof(header), jpg + offset, n);
    esp_now_send(GATEWAY_MAC, packet, sizeof(header) + n);
    delay(4);
  }
}

void setup() {
  Serial.begin(115200);
  if (!startCamera()) {
    Serial.println("camera init failed");
    delay(2000);
    ESP.restart();
  }
  WiFi.mode(WIFI_STA);
  esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) {
    Serial.println("esp-now init failed");
    ESP.restart();
  }
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, GATEWAY_MAC, 6);
  peer.channel = WIFI_CHANNEL;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) {
    Serial.println("peer add failed");
    ESP.restart();
  }
  Serial.printf("node %u sending on channel %u\n", CAMERA_ID, WIFI_CHANNEL);
}

void loop() {
  // One frame every two seconds, staggered by id so the five nodes do not talk at once.
  uint32_t now = millis();
  if (now - lastSend < 2000) {
    delay(20);
    return;
  }
  if ((now % 2000) < (uint32_t)(CAMERA_ID - 1) * 400) {
    delay(10);
    return;
  }
  lastSend = now;
  camera_fb_t *fb = esp_camera_fb_get();
  if (fb) {
    sendFrame(fb->buf, fb->len);
    esp_camera_fb_return(fb);
  }
}
