/* Shared crop geometry: the preview and saved portrait use the same source rect. */
function photoCropRect(width, height, zoom, x, y) {
  if (!(width > 0 && height > 0)) throw new Error('The picture has invalid dimensions.');
  const safeZoom = Math.max(1, Math.min(3, Number(zoom) || 1));
  const scale = Math.max(640 / width, 800 / height) * safeZoom;
  const cropWidth = 640 / scale;
  const cropHeight = 800 / scale;
  const position = value => Number.isFinite(Number(value)) ? Math.max(0, Math.min(100, Number(value))) : 50;
  return {
    x: (width - cropWidth) * position(x) / 100,
    y: (height - cropHeight) * position(y) / 100,
    width: cropWidth, height: cropHeight
  };
}
if (typeof module !== 'undefined') module.exports = { photoCropRect };
