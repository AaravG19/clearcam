// PestSentry hub integration for clearcam. Copyright (C) 2026 Aarav Gaudani. Licensed under GPL-3.0-or-later; see ../../../LICENSE.md (repo root).
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_idf_version.h>

// Connect the ESP32 USB/UART bridge to the UNO Q USB host. For direct UART,
// use compatible 3.3 V logic, gateway TX to host RX, and common ground.
// Set the host port to the matching device and baud. Keep this UART binary.
#define SERIAL_BAUD 921600
#define WIFI_CHANNEL 1  // Every node must use this channel; no AP is needed.

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

struct Frame {
  uint8_t *data;
  uint16_t next;
  uint16_t count;
  uint8_t seq;
  uint32_t last;
};
Frame frames[5] = {};
QueueHandle_t packets;

void discard(Frame &frame) {
  free(frame.data);
  frame = {};
}

// The WiFi callback copies into a bounded queue. The main loop owns all
// assembly buffers and UART writes. Queue overflow creates a detectable gap.
#if ESP_IDF_VERSION_MAJOR >= 5
void onReceive(const esp_now_recv_info_t *, const uint8_t *data, int len) {
#else
void onReceive(const uint8_t *, const uint8_t *data, int len) {
#endif
  if (len <= int(HEADER) || len > int(sizeof(Chunk))) return;
  Chunk chunk = {};
  memcpy(&chunk, data, len);
  if (chunk.magic != 0xA7 || chunk.node < 1 || chunk.node > 5 ||
      len != HEADER + chunk.length) return;
  xQueueSend(packets, &chunk, 0);
}

void acceptChunk(const Chunk &chunk) {
  Frame &frame = frames[chunk.node - 1];
  if (chunk.index == 0 || chunk.seq != frame.seq) discard(frame);
  if (!chunk.count || chunk.count > MAX_FRAME / sizeof(chunk.data) ||
      chunk.index >= chunk.count ||
      (chunk.index + 1 < chunk.count && chunk.length != sizeof(chunk.data))) {
    discard(frame);
    return;
  }
  if (chunk.index == 0) {
    frame.data = static_cast<uint8_t *>(malloc(chunk.count * sizeof(chunk.data)));
    frame.seq = chunk.seq;
    frame.count = chunk.count;
  }
  if (!frame.data || frame.next != chunk.index || frame.count != chunk.count) {
    discard(frame);
    return;
  }
  size_t offset = chunk.index * sizeof(chunk.data);
  memcpy(frame.data + offset, chunk.data, chunk.length);
  frame.last = millis();
  if (++frame.next == chunk.count) {
    uint32_t n = offset + chunk.length;
    uint8_t header[] = {'P', 'S', 'N', '1', chunk.node, uint8_t(n >> 24),
                        uint8_t(n >> 16), uint8_t(n >> 8), uint8_t(n)};
    Serial.write(header, sizeof(header));
    Serial.write(frame.data, n);
    discard(frame);
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  packets = xQueueCreate(48, sizeof(Chunk));
  if (!packets || !WiFi.mode(WIFI_STA)) return;
  if (esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE) != ESP_OK) return;
  if (esp_now_init() == ESP_OK) esp_now_register_recv_cb(onReceive);
}

void loop() {
  if (!packets) { delay(100); return; }
  Chunk chunk;
  if (xQueueReceive(packets, &chunk, pdMS_TO_TICKS(25))) acceptChunk(chunk);
  for (Frame &frame : frames) {
    if (frame.data && millis() - frame.last > 1500) discard(frame);
  }
}
