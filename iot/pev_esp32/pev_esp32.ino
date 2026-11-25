// PEV inteligente · ESP32 + HC-SR04
// Mede a distância até o lixo e manda para o Loginc, que mostra a lata enchendo ao vivo em /pev.
//
// Ligação (HC-SR04 alimentado em 5V):
//   HC-SR04 VCC  -> VIN/5V da ESP32
//   HC-SR04 GND  -> GND
//   HC-SR04 TRIG -> GPIO 32 (3,3V da ESP32 já basta para disparar; 34 e 35 só leem, não servem)
//   HC-SR04 ECHO -> divisor -> GPIO 34
//                   ECHO --[1 kΩ]--+--> GPIO 34
//                                  |
//                                [2 kΩ]
//                                  |
//                                 GND
//   O ECHO sai em 5V e a ESP32 aceita 3,3V: sem o divisor você pode queimar o pino.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <esp_task_wdt.h>
#include "segredos.h"  // WIFI_SSID, WIFI_SENHA, SENSOR_TOKEN

// ---- Ajuste aqui ----
const char* URL = "https://loginc.tailb54547.ts.net/api/sensor";
const char* PEV_ID = "v2";           // v1 Praça José de Barros · v2 Escola Campo Novo · v3 IFCE Quixadá
const float ALTURA_CM = 32.0;        // calibrado: o que o sensor lê com a lata VAZIA (31,9 cm estável)
const float DIST_CHEIO_CM = 5.8;     // medido: papelão na BORDA da caixa (= 100%); confira com régua
const float ZONA_MORTA_CM = 0.8;     // até isso acima do fundo conta como vazio (ruído do sensor)
const float CAPACIDADE_L = 9.0;      // capacidade da caixa (rótulo); 23 x 17 cm externos dariam ~10,2 L
const uint32_t INTERVALO_MS = 250;   // demo em tempo real: ~4 leituras/s; em campo, 15 min (900000)

// Altura útil: do fundo até a borda (o trecho entre a borda e o sensor não guarda lixo).
const float UTIL_CM = ALTURA_CM - DIST_CHEIO_CM;

// Volume só no código da placa (o site continua em kg): fração da altura útil × capacidade.
float litrosOcupados(float cm) {
  float ocupado = ALTURA_CM - cm;  // altura do lixo dentro da caixa
  if (ocupado < 0) ocupado = 0;
  if (ocupado > UTIL_CM) ocupado = UTIL_CM;
  return CAPACIDADE_L * ocupado / UTIL_CM;
}

const int PINO_TRIG = 32;  // precisa ser pino de saída: 34 a 39 não servem
const int PINO_ECHO = 34;  // só entrada, ótimo para o ECHO
// ---------------------

WiFiClientSecure cliente;
HTTPClient http;

float medirCm() {
  digitalWrite(PINO_TRIG, LOW);
  delayMicroseconds(4);
  digitalWrite(PINO_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PINO_TRIG, LOW);
  unsigned long us = pulseIn(PINO_ECHO, HIGH, 6000);  // 6 ms ≈ 1 m (a caixa tem 32 cm); 0 = sem eco
  if (us == 0) return -1;
  return us * 0.0343f / 2.0f;  // som a ~343 m/s, ida e volta
}

// 7 disparos e fica com o 2º eco mais perto: papel amassado e sacola só devolvem eco
// de vez em quando (o resto do som "atravessa" pelos vãos e volta do fundo), então basta
// o objeto responder 2 vezes para contar; um único eco curto falso não ganha.
const int DISPAROS = 7;   // era 15: menos disparos = leitura mais rápida para a demo
float leituraBrutaCm() {
  float v[DISPAROS];
  int n = 0;
  for (int i = 0; i < DISPAROS; i++) {
    float d = medirCm();
    if (d >= 2 && d <= 400) v[n++] = d;
    delay(15);  // deixa o eco anterior morrer dentro da lata (32 cm: o eco volta em ~2 ms)
  }
  if (n < 3) return -1;  // poucos ecos: leitura não confiável
  for (int i = 1; i < n; i++) {
    float x = v[i];
    int j = i - 1;
    while (j >= 0 && v[j] > x) { v[j + 1] = v[j]; j--; }
    v[j + 1] = x;
  }
  return v[1];
}

// Filtro assimétrico: lixo aparecendo (mais perto) vale na hora; ficar mais longe
// (esvaziou?) só vale depois de CONFIRMA_LONGE leituras seguidas. Some com os pulos
// para o fundo quando o pulso desvia de um objeto macio ou inclinado.
const int CONFIRMA_LONGE = 2;  // lixo não some sozinho: baixar o nível exige 2 leituras seguidas (~0,5 s)
const float TOLERANCIA_CM = 1.5;
float estavelCm = -1;
int seguidasLonge = 0;

int seguidasSemEco = 0;

float filtrarCm(float cm) {
  if (cm < 0) {  // sem eco: segura o último valor bom por até 10 leituras, depois para de enviar
    if (++seguidasSemEco >= 10) estavelCm = -1;
    return estavelCm;
  }
  seguidasSemEco = 0;
  if (estavelCm < 0 || cm <= estavelCm + TOLERANCIA_CM) {
    seguidasLonge = 0;
    // perto do valor atual: média leve (tira o tremido sem atrasar); bem mais perto: aceita direto
    estavelCm = (estavelCm >= 0 && cm >= estavelCm - TOLERANCIA_CM) ? 0.3f * estavelCm + 0.7f * cm : cm;
  } else if (++seguidasLonge >= CONFIRMA_LONGE) {
    seguidasLonge = 0;
    estavelCm = cm;
  }
  return estavelCm;
}

const char* motivoWifi(wl_status_t s) {
  switch (s) {
    case WL_NO_SSID_AVAIL: return "rede não encontrada (nome errado ou rede só 5 GHz?)";
    case WL_CONNECT_FAILED: return "falha ao conectar (senha errada?)";
    case WL_CONNECTION_LOST: return "conexão perdida";
    case WL_DISCONNECTED: return "desconectado (senha errada ou sinal fraco?)";
    case WL_IDLE_STATUS: return "ainda tentando";
    default: return "outro";
  }
}

// Lista as redes que a ESP32 enxerga: só aparecem as de 2,4 GHz.
void listarRedes() {
  Serial.println("Redes 2,4 GHz visíveis:");
  int n = WiFi.scanNetworks();
  bool achou = false;
  for (int i = 0; i < n; i++) {
    bool alvo = WiFi.SSID(i) == WIFI_SSID;
    achou |= alvo;
    Serial.printf("  %s%-32s %4d dBm canal %2d%s\n", alvo ? "-> " : "   ", WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i),
                  WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? " (aberta)" : "");
  }
  Serial.printf(achou ? "A rede \"%s\" está visível: o problema deve ser a senha.\n"
                      : "A rede \"%s\" NÃO apareceu: confira o nome (maiúsculas contam) e se ela tem 2,4 GHz.\n", WIFI_SSID);
  WiFi.scanDelete();
}

// Chama WiFi.begin uma vez e deixa o reconectar automático trabalhar;
// chamar de novo no meio da tentativa dá "sta is connecting, cannot set config".
void conectarWifi() {
  Serial.printf("Conectando em \"%s\"", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_SENHA);
  for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) {
    delay(250);
    Serial.print('.');
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf(" ok, IP %s, sinal %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    return;
  }
  Serial.printf(" falhou: %s\n", motivoWifi(WiFi.status()));
  WiFi.disconnect();
  listarRedes();
}

// Quando não há eco: dispara o TRIG e observa o ECHO por 60 ms para dizer onde está o problema.
void diagnosticarEco() {
  int antes = digitalRead(PINO_ECHO);
  digitalWrite(PINO_TRIG, LOW);
  delayMicroseconds(4);
  digitalWrite(PINO_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PINO_TRIG, LOW);
  uint32_t t0 = micros(), subiu = 0, desceu = 0;
  int trocas = 0, ultimo = antes;
  while (micros() - t0 < 60000) {
    int v = digitalRead(PINO_ECHO);
    if (v != ultimo) {
      trocas++;
      if (v && !subiu) subiu = micros() - t0;
      if (!v && subiu && !desceu) desceu = micros() - t0;
      ultimo = v;
    }
  }
  Serial.printf("  diagnóstico ECHO (GPIO %d): antes=%d depois=%d trocas=%d subiu=%luus desceu=%luus -> ",
                PINO_ECHO, antes, ultimo, trocas, (unsigned long)subiu, (unsigned long)desceu);
  if (antes == 1 && trocas == 0) Serial.println("ECHO preso em ALTO: sensor travado ou divisor invertido; tire e recoloque o VCC do sensor");
  else if (trocas == 0) Serial.println("ECHO nunca sobe: sensor sem 5V/GND, TRIG fora do GPIO 32, ou ECHO não chega no GPIO 34");
  else if (trocas > 6) Serial.println("ECHO oscilando: fio solto ou pino flutuando (ECHO não está ligado de verdade)");
  else if (subiu && !desceu) Serial.println("ECHO subiu e não desceu em 60 ms: nada na frente do sensor ou sensor travado");
  else Serial.println("pulso normal: o sensor respondeu");
  varrerPinos();
}

// Só LÊ os pinos do lado do VIN (nunca escreve neles): acha fio deslocado uma fileira.
void varrerPinos() {
  const int pinos[] = {36, 39, 34, 35, 33, 25, 26, 27, 14, 12, 13};
  const int n = sizeof pinos / sizeof pinos[0];
  int repouso[n], trocas[n], ultimo[n];
  for (int i = 0; i < n; i++) {
    pinMode(pinos[i], pinos[i] >= 34 ? INPUT : INPUT_PULLDOWN);  // 34-39 não têm pull-down interno
  }
  delay(2);
  for (int i = 0; i < n; i++) { repouso[i] = ultimo[i] = digitalRead(pinos[i]); trocas[i] = 0; }
  digitalWrite(PINO_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PINO_TRIG, LOW);
  uint32_t t0 = micros();
  while (micros() - t0 < 40000) {
    for (int i = 0; i < n; i++) {
      int v = digitalRead(pinos[i]);
      if (v != ultimo[i]) { trocas[i]++; ultimo[i] = v; }
    }
  }
  Serial.print("  varredura (repouso/trocas): ");
  for (int i = 0; i < n; i++) Serial.printf("D%d=%d/%d  ", pinos[i], repouso[i], trocas[i]);
  Serial.println();
  for (int i = 0; i < n; i++) {
    if (pinos[i] != PINO_ECHO && trocas[i] > 0 && trocas[i] <= 4)
      Serial.printf("  >> pulso chegou no D%d: o fio do ECHO deve estar nesse pino, não no D%d\n", pinos[i], PINO_ECHO);
    if (pinos[i] < 34 && repouso[i] == 1)
      Serial.printf("  >> D%d está recebendo tensão em repouso: tem algum fio (VCC?) encostado nele\n", pinos[i]);
  }
  pinMode(PINO_ECHO, INPUT);
}

void garantirWifi() {
  static uint32_t ultimaTentativa = 0;
  if (WiFi.status() == WL_CONNECTED) return;
  if (ultimaTentativa && millis() - ultimaTentativa < 20000) return;  // espera 20 s entre tentativas
  ultimaTentativa = millis();
  conectarWifi();
}

int falhasSeguidas = 0;

// Derruba a conexão TLS reaproveitada: a próxima tentativa abre uma limpa.
void descartarConexao() {
  http.end();
  cliente.stop();
}

void enviar(float cm) {
  uint32_t t0 = millis();
  if (!http.begin(cliente, URL)) {
    Serial.println("http.begin falhou");
    descartarConexao();
    return;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + SENSOR_TOKEN);
  char corpo[96];
  // o servidor calcula nível = (altura - distância) / altura; mandando tudo medido a partir
  // da borda, 100% vira "lixo na borda" e não "lixo encostado no sensor"
  float dBorda = cm - DIST_CHEIO_CM;
  if (dBorda < 0) dBorda = 0;
  snprintf(corpo, sizeof corpo, "{\"pev\":\"%s\",\"distancia_cm\":%.1f,\"altura_cm\":%.1f}", PEV_ID, dBorda, UTIL_CM);
  int codigo = http.POST((uint8_t*)corpo, strlen(corpo));
  String resposta = codigo > 0 ? http.getString() : http.errorToString(codigo);
  float litros = litrosOcupados(cm);
  Serial.printf("%.1f cm | %.2f L de %.2f L (%.0f%%) -> HTTP %d em %lu ms %s\n", cm, litros, CAPACIDADE_L, litros / CAPACIDADE_L * 100,
                codigo, (unsigned long)(millis() - t0), resposta.c_str());
  if (codigo == 200) {
    falhasSeguidas = 0;
    http.end();  // com setReuse, mantém a conexão para o próximo envio
    return;
  }
  descartarConexao();
  if (++falhasSeguidas >= 5) {
    Serial.println("5 falhas seguidas: reconectando o Wi-Fi");
    falhasSeguidas = 0;
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_SENHA);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PINO_TRIG, OUTPUT);
  pinMode(PINO_ECHO, INPUT);
  delay(300);
  Serial.printf("\nPEV ESP32 iniciando · caixa de %.1f L, fundo a %.1f cm, borda a %.1f cm do sensor\n", CAPACIDADE_L, ALTURA_CM, DIST_CHEIO_CM);
  esp_reset_reason_t motivo = esp_reset_reason();
  if (motivo == ESP_RST_TASK_WDT || motivo == ESP_RST_PANIC || motivo == ESP_RST_INT_WDT)
    Serial.printf("Reiniciou pelo watchdog/erro (motivo %d): o loop tinha travado\n", (int)motivo);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  cliente.setInsecure();  // teste: não confere o certificado do servidor
  cliente.setTimeout(5000);       // leitura/escrita TLS
  cliente.setHandshakeTimeout(8); // segundos para o handshake TLS
  http.setReuse(true);            // mantém a conexão TLS aberta entre envios (o handshake custa ~1 s)
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  // Watchdog: se o loop ficar 30 s sem andar (rede travada etc.), a placa reinicia sozinha.
  esp_task_wdt_config_t wdt = {.timeout_ms = 30000, .idle_core_mask = 0, .trigger_panic = true};
  esp_task_wdt_reconfigure(&wdt);
  esp_task_wdt_add(NULL);
  garantirWifi();
}

void loop() {
  esp_task_wdt_reset();
  uint32_t inicio = millis();
  garantirWifi();
  float bruto = leituraBrutaCm();
  float cm = filtrarCm(bruto);
  if (cm >= ALTURA_CM - ZONA_MORTA_CM) cm = ALTURA_CM;  // perto do fundo = vazio (some com o 1-3% de ruído)
  if (cm < 0) {  // ainda não houve nenhuma leitura boa desde que ligou
    Serial.println("Sem eco: confira a ligação e se há algo a menos de 4 m na frente do sensor");
    diagnosticarEco();
  } else {
    // bruto = o que o sensor leu agora; o valor enviado é o filtrado
    Serial.printf("[bruto %5.1f%s] ", bruto, bruto >= 0 && fabsf(bruto - cm) > TOLERANCIA_CM ? " ignorado" : "");
    if (WiFi.status() == WL_CONNECTED) enviar(cm);
    else Serial.printf("%.1f cm | %.2f L de %.2f L (sem Wi-Fi, não enviado)\n", cm, litrosOcupados(cm), CAPACIDADE_L);
  }
  uint32_t gasto = millis() - inicio;
  if (gasto < INTERVALO_MS) delay(INTERVALO_MS - gasto);
}
