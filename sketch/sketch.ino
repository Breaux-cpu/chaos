// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

// Mirrors the Python side's scan status on the 8x13 LED matrix.
// The Python app owns the state machine; this sketch just polls it
// once per loop() and picks a frame — see mascot-jump-game for the
// same poll-and-draw pattern.

#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>

Arduino_LED_Matrix matrix;

const uint8_t ROWS = 8;
const uint8_t COLS = 13;
uint8_t frame[ROWS * COLS];

void clearFrame() {
  for (uint16_t i = 0; i < ROWS * COLS; i++) frame[i] = 0;
}

void setPixel(int row, int col, uint8_t brightness) {
  if (row < 0 || row >= ROWS || col < 0 || col >= COLS) return;
  frame[row * COLS + col] = brightness;
}

void drawScanning() {
  // A single bright column sweeps left to right, one step per loop().
  static int sweepCol = 0;
  clearFrame();
  for (int row = 0; row < ROWS; row++) setPixel(row, sweepCol, 5);
  matrix.draw(frame);
  sweepCol = (sweepCol + 1) % COLS;
}

void drawMatch() {
  // A checkmark, held for the duration of the "match" state.
  clearFrame();
  const int mark[][2] = {{5, 2}, {6, 3}, {5, 4}, {4, 5}, {3, 6}, {2, 7}, {1, 8}};
  for (auto &p : mark) setPixel(p[0], p[1], 7);
  matrix.draw(frame);
}

void drawAlert() {
  // Full-grid flash, alternating on/off — camera trouble.
  static bool on = false;
  clearFrame();
  if (on) {
    for (uint16_t i = 0; i < ROWS * COLS; i++) frame[i] = 7;
  }
  matrix.draw(frame);
  on = !on;
}

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);
  Bridge.begin();
}

void loop() {
  String state;
  bool ok = Bridge.call("get_led_state").result(state);
  if (!ok) state = "scanning";

  unsigned long interval;
  if (state == "match") {
    drawMatch();
    interval = 200;
  } else if (state == "alert") {
    drawAlert();
    interval = 150;
  } else {
    drawScanning();
    interval = 120;
  }
  delay(interval);
}
