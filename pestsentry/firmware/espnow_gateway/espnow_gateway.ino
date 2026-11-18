// PestSentry ESP-NOW gateway.
//
// Flash this on a sixth ESP32 (a CAM board with the camera unused is fine).
// It rejoins JPEGs from the five nodes and serves the same MJPEG paths the
// Wi-Fi node sketch serves, so clearcam does not care which radio was used:
//   http://<gateway>:81/stream/node-1
//   ...
//   http://<gateway>:81/stream/node-5
//
// The UNO Q joins the same Wi-Fi network and opens those URLs. Its radio is
// Wi-Fi 5 on the Qualcomm side, not ESP-NOW.
//
// Placeholders only. Do not commit real Wi-Fi credentials.

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

static const char *WIFI_SSID = "YOUR_WIFI_SSID";
static const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
static const uint16_t STREAM_PORT = 81;
static const size_t CHUNK = 200;
static const int NODES = 5;
static const size_t MAX_JPEG = 24 * 1024;

#pragma pack(push, 1)
struct ChunkHeader {
  uint8_t cam_id;
  uint16_t frame_id;
  uint8_t seq;
  uint8_t count;
  uint16_t total;
};
#pragma pack(pop)

struct Assembly {
  uint16_t frame_id = 0;
  uint8_t count = 0;
  uint16_t total = 0;
  uint8_t got[128] = {};
  uint8_t data[MAX_JPEG] = {};
};

struct Latest {
  uint8_t data[MAX_JPEG] = {};
  size_t len = 0;
  uint32_t updated = 0;
};

Assembly build[NODES];
Latest latest[NODES];
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
WiFiServer server(STREAM_PORT);

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
void onReceive(const esp_now_recv_info_t *info, const uint8_t *payload, int len) {
  (void)info;
#else
void onReceive(const uint8_t *mac, const uint8_t *payload, int len) {
  (void)mac;
#endif
  if (len < (int)sizeof(ChunkHeader)) return;
  ChunkHeader header;
  memcpy(&header, payload, sizeof(header));
  if (header.cam_id < 1 || header.cam_id > NODES) return;
  if (header.count == 0 || header.count > 128) return;
  if (header.seq >= header.count) return;
  if (header.total == 0 || header.total > MAX_JPEG) return;
  size_t n = len - sizeof(header);
  size_t offset = (size_t)header.seq * CHUNK;
  if (offset + n > header.total || offset + n > MAX_JPEG) return;

  Assembly &slot = build[header.cam_id - 1];
  if (slot.frame_id != header.frame_id || slot.count != header.count) {
    memset(slot.got, 0, sizeof(slot.got));
    slot.frame_id = header.frame_id;
    slot.count = header.count;
    slot.total = header.total;
  }
  memcpy(slot.data + offset, payload + sizeof(header), n);
  slot.got[header.seq] = 1;
  for (uint8_t i = 0; i < slot.count; i++) {
    if (!slot.got[i]) return;
  }
  portENTER_CRITICAL(&mux);
  memcpy(latest[header.cam_id - 1].data, slot.data, slot.total);
  latest[header.cam_id - 1].len = slot.total;
  latest[header.cam_id - 1].updated = millis();
  portEXIT_CRITICAL(&mux);
  memset(slot.got, 0, sizeof(slot.got));
}

void sendStream(WiFiClient &client, int index) {
  client.print(
      "HTTP/1.1 200 OK\r\n"
      "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
      "Cache-Control: no-cache\r\n"
      "Connection: close\r\n\r\n");
  uint8_t copy[MAX_JPEG];
  while (client.connected()) {
    size_t n = 0;
    portENTER_CRITICAL(&mux);
    n = latest[index].len;
    if (n) memcpy(copy, latest[index].data, n);
    portEXIT_CRITICAL(&mux);
    if (!n) {
      delay(50);
      continue;
    }
    client.printf("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", (unsigned)n);
    client.write(copy, n);
    client.print("\r\n");
    delay(200);
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("wifi");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("wifi failed, restarting");
    delay(2000);
    ESP.restart();
  }
  uint8_t channel = WiFi.channel();
  if (esp_now_init() != ESP_OK) {
    Serial.println("esp-now init failed");
    ESP.restart();
  }
  esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
  esp_now_register_recv_cb(onReceive);
  server.begin();
  Serial.print("gateway mac ");
  Serial.println(WiFi.macAddress());
  Serial.printf("channel %u\n", channel);
  Serial.print("streams at http://");
  Serial.print(WiFi.localIP());
  Serial.println(":81/stream/node-1");
}

void loop() {
  WiFiClient client = server.available();
  if (!client) return;
  client.setTimeout(3);
  String req = client.readStringUntil('\n');
  int index = -1;
  for (int i = 0; i < NODES; i++) {
    String path = "/stream/node-" + String(i + 1);
    if (req.indexOf("GET " + path) >= 0) index = i;
  }
  if (index < 0) {
    client.print(
        "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n"
        "<h1>PestSentry gateway</h1>"
        "<p>/stream/node-1 through /stream/node-5</p>");
  } else {
    sendStream(client, index);
  }
  client.stop();
}
