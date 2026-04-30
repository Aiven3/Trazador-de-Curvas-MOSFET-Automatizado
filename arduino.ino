#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>

#define SDA_1 16
#define SCL_1 17
#define PIN_PULSE 23

#define ADDR_VG_QDUT_DAC   0x60
#define ADDR_VG_QPASS_DAC  0x61
#define ADDR_VG_QDUT_ADC   0x40 
#define ADDR_VG_QPASS_ADC  0x45 
#define ADDR_MONITOR_ADC   0x40 

#define VG_START       1.8f
#define VG_END         3.0f
#define VG_STEP        0.1f
#define NUM_VG_STEPS   13 

#define PASS_START     0.0f
#define PASS_END       8.0f
#define PASS_STEP      0.1f
#define NUM_PASS_STEPS 81 

#define SHUNT_RES_OHMS 0.0150  
#define MAX_CURRENT_MA 5000.0  
#define T_OFF_MS       300     
#define DAC_SETTLE_MS  20      
#define COOL_DOWN_MS   1000    

const float TOLERANCE = 0.008;       
const int MAX_ITERATIONS = 10;       

struct Point {
    float vds;
    float id;
};

Point dataMatrix[NUM_VG_STEPS][NUM_PASS_STEPS];
float vg_values[NUM_VG_STEPS];

struct ControlChannel {
    uint8_t dacAddr;
    uint8_t adcAddr;
    float target;
    float measured;
    float slope;
    float offset;
    const char* prefKey;
    bool isINA226;
};

ControlChannel qdut_gate =  { ADDR_VG_QDUT_DAC, ADDR_VG_QDUT_ADC, 0.0, 0, 0.003444, 0, "vg_qdut_slope", true };
ControlChannel pass_gate = { ADDR_VG_QPASS_DAC, ADDR_VG_QPASS_ADC, 0.0, 0, 0.003444, 0, "vg_qpass_slope", false };

Preferences preferences;
bool hardwareReady = false;
float zero_offset_id_ma = 0.0;

void writeDAC(uint8_t addr, uint16_t value) {
    uint16_t safeValue = (value > 4095) ? 4095 : value;
    Wire1.beginTransmission(addr);
    Wire1.write(0x40); 
    uint8_t msb = (safeValue >> 4) & 0xFF;
    uint8_t lsb = (safeValue & 0x0F) << 4;
    Wire1.write(msb); 
    Wire1.write(lsb);
    Wire1.endTransmission();
}

float readVoltageFast(ControlChannel &ch) {
    Wire1.beginTransmission(ch.adcAddr);
    Wire1.write(0x02);
    Wire1.endTransmission();
    if (Wire1.requestFrom(ch.adcAddr, (uint8_t)2) == 2) {
        uint16_t raw = (Wire1.read() << 8) | Wire1.read();
        return ch.isINA226 ? (float)raw * 0.00125 : (float)(raw >> 3) * 0.004;
    }
    return -1.0;
}

void initINA228() {
    Wire.beginTransmission(ADDR_MONITOR_ADC);
    Wire.write(0x00); Wire.write(0x00); Wire.write(0x00); 
    Wire.endTransmission();
    Wire.beginTransmission(ADDR_MONITOR_ADC);
    Wire.write(0x01); Wire.write(0x39); Wire.write(0x21); 
    Wire.endTransmission();
}

void triggerINA228() {
    Wire.beginTransmission(ADDR_MONITOR_ADC);
    Wire.write(0x01); Wire.write(0x39); Wire.write(0x21); 
    Wire.endTransmission();
}

bool readPulseData(float &vds_v, float &id_ma) {
    uint16_t diag = 0;
    unsigned long startPoll = micros();
    while (micros() - startPoll < 12000) { 
        Wire.beginTransmission(ADDR_MONITOR_ADC);
        Wire.write(0x0B);
        if (Wire.endTransmission() != 0) break;
        if (Wire.requestFrom(ADDR_MONITOR_ADC, 2) == 2) {
            diag = (Wire.read() << 8) | Wire.read();
            if (diag & 0x0002) break; 
        }
    }
    if (!(diag & 0x0002)) return false; 

    Wire.beginTransmission(ADDR_MONITOR_ADC);
    Wire.write(0x05); Wire.endTransmission();
    if (Wire.requestFrom(ADDR_MONITOR_ADC, 3) == 3) {
        int32_t raw = (int32_t)((uint32_t)Wire.read() << 16 | (uint32_t)Wire.read() << 8 | (uint32_t)Wire.read());
        raw >>= 4; if (raw & 0x80000) raw |= 0xFFF00000;
        vds_v = (float)raw * 1.953125e-4; 
    }
    Wire.beginTransmission(ADDR_MONITOR_ADC);
    Wire.write(0x07); Wire.endTransmission();
    if (Wire.requestFrom(ADDR_MONITOR_ADC, 3) == 3) {
        int32_t raw = (int32_t)((uint32_t)Wire.read() << 16 | (uint32_t)Wire.read() << 8 | (uint32_t)Wire.read());
        raw >>= 4; if (raw & 0x80000) raw |= 0xFFF00000;
        id_ma = ((float)raw * 3.125e-7 / SHUNT_RES_OHMS) * 1000.0;
    }
    return true;
}

void adjustChannel(ControlChannel &ch) {
    if (!hardwareReady) return;
    uint16_t dacValue = (uint16_t)constrain((ch.target - ch.offset) / ch.slope, 0, 4095);
    for (int i = 0; i < MAX_ITERATIONS; i++) {
        writeDAC(ch.dacAddr, dacValue);
        delay(DAC_SETTLE_MS);
        ch.measured = readVoltageFast(ch);
        float error = ch.target - ch.measured;
        if (abs(error) <= TOLERANCE) break;
        float curM = (dacValue > 0) ? ((ch.measured - ch.offset) / (float)dacValue) : ch.slope;
        dacValue = (uint16_t)constrain((int)dacValue + (int)(error / (curM < 0.0001 ? ch.slope : curM)), 0, 4095);
    }
}

void performAutoZero() {
    digitalWrite(PIN_PULSE, HIGH); 
    writeDAC(ADDR_VG_QPASS_DAC, 0);
    delay(500);
    double i_sum = 0;
    int samples = 32, valid = 0;
    for(int i=0; i<samples; i++) {
        triggerINA228();
        float v, i_m;
        if (readPulseData(v, i_m)) { i_sum += i_m; valid++; }
    }
    zero_offset_id_ma = (valid > 0) ? (float)(i_sum / valid) : 0;
}

void clearMatrix() {
    for(int i=0; i<NUM_VG_STEPS; i++) {
        for(int j=0; j<NUM_PASS_STEPS; j++) {
            dataMatrix[i][j] = {0.0f, 0.0f};
        }
    }
}

void performSweepToMatrix(float v_gate_qdut, int vg_index) {
    qdut_gate.target = v_gate_qdut;
    adjustChannel(qdut_gate);

    int pass_idx = 0;
    for (float v_pass = PASS_START; v_pass <= (PASS_END + 0.01f); v_pass += PASS_STEP) {
        if (pass_idx >= NUM_PASS_STEPS) break;

        pass_gate.target = v_pass;
        adjustChannel(pass_gate);
        delay(T_OFF_MS);

        digitalWrite(PIN_PULSE, LOW); 
        delayMicroseconds(150); 
        triggerINA228(); 
        
        float vds_raw = 0, id_raw = 0;
        readPulseData(vds_raw, id_raw);
        digitalWrite(PIN_PULSE, HIGH); 

        float id_final = id_raw - zero_offset_id_ma;
        if (abs(id_final) < 0.50) { id_final = 0.0f; vds_raw = 0.0f; }
        if (vds_raw < 0.0002) vds_raw = 0.0f;

        dataMatrix[vg_index][pass_idx] = {vds_raw, id_final};

        if (abs(id_final) > MAX_CURRENT_MA) {
            break; 
        }
        pass_idx++;
    }
}

void printDataMatrix() {
    Serial.println("\n>>> DATOS PARA EXCEL (COPIAR DESDE AQUÍ) <<<");
    for(int i=0; i<NUM_VG_STEPS; i++) {
        Serial.printf("VDS_Vg%.1f\tID_Vg%.1f\t", vg_values[i], vg_values[i]);
    }
    Serial.println();

    for(int j=0; j<NUM_PASS_STEPS; j++) {
        for(int i=0; i<NUM_VG_STEPS; i++) {
            Serial.printf("%.4f\t%.2f\t", dataMatrix[i][j].vds, dataMatrix[i][j].id);
        }
        Serial.println();
    }
    Serial.println(">>> FIN DE DATOS <<<");
}

void performFullSequence() {
    clearMatrix();
    performAutoZero();
    Serial.println("\nCapturando familia de curvas...");
    
    int vg_idx = 0;
    for (float vg = VG_START; vg <= (VG_END + 0.01f); vg += VG_STEP) {
        if (vg_idx >= NUM_VG_STEPS) break;
        
        Serial.printf("[Progreso] Midiendo curva VG = %.1fV (%d/%d)\n", vg, vg_idx + 1, NUM_VG_STEPS);
        vg_values[vg_idx] = vg;
        performSweepToMatrix(vg, vg_idx);
        
        writeDAC(ADDR_VG_QDUT_DAC, 0);
        writeDAC(ADDR_VG_QPASS_DAC, 0);
        delay(COOL_DOWN_MS);
        vg_idx++;
    }
    
    printDataMatrix();
}

void setup() {
    Serial.begin(115200);
    pinMode(PIN_PULSE, OUTPUT);
    digitalWrite(PIN_PULSE, HIGH);
    Wire1.begin(SDA_1, SCL_1, 400000);
    Wire.begin(21, 22, 1000000); 
    delay(500); 
    initINA228();
    preferences.begin("qdut-v5", false);
    qdut_gate.slope = preferences.getFloat(qdut_gate.prefKey, qdut_gate.slope);
    pass_gate.slope = preferences.getFloat(pass_gate.prefKey, pass_gate.slope);
    writeDAC(ADDR_VG_QDUT_DAC, 0);
    writeDAC(ADDR_VG_QPASS_DAC, 0);
    delay(500);
    qdut_gate.offset = readVoltageFast(qdut_gate);
    pass_gate.offset = readVoltageFast(pass_gate);
    if (qdut_gate.offset >= 0 && pass_gate.offset >= 0) hardwareReady = true;
    Serial.println("\n>>> TRACER V8.0 (COLUMN MODE). Presione ENTER.");
}

void loop() {
    if (Serial.available() > 0) {
        while(Serial.available()) Serial.read();
        performFullSequence();
    }
}
