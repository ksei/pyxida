const test = require('node:test');
const assert = require('node:assert/strict');
const { photoCropRect } = require('../static/photo-crop.js');

test('portrait cropping keeps 4:5 aspect ratio and stays inside the original image', () => {
  for (const [width, height] of [[640, 800], [2400, 1200], [800, 2400]]) {
    for (const zoom of [1, 1.5, 3]) {
      for (const x of [0, 50, 100]) for (const y of [0, 50, 100]) {
        const crop = photoCropRect(width, height, zoom, x, y);
        assert.ok(Math.abs(crop.width / crop.height - 0.8) < 0.00001);
        assert.ok(crop.x >= 0 && crop.y >= 0);
        assert.ok(crop.x + crop.width <= width + 0.00001);
        assert.ok(crop.y + crop.height <= height + 0.00001);
      }
    }
  }
});

test('zoom and positions clamp to valid ranges', () => {
  assert.deepEqual(photoCropRect(800, 1200, 0, -20, 150), photoCropRect(800, 1200, 1, 0, 100));
  assert.deepEqual(photoCropRect(800, 1200, 9, 'invalid', undefined), photoCropRect(800, 1200, 3, 50, 50));
  assert.throws(() => photoCropRect(0, 1200, 1, 50, 50));
});
