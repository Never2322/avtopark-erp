// Фото с телефона весят 3–8 МБ: по мобильной сети они уходят долго, а в очереди без связи занимают много места.
// Перед отправкой фото уменьшается до 1600 пикселей по длинной стороне и сохраняется как JPEG.
// Если браузер не смог открыть файл (например, редкий формат), файл остаётся как есть: сервер скажет, что с ним не так.

const SMALL = 900 * 1024;

export async function shrinkImage(file: File, maxSide = 1600, quality = 0.82): Promise<File> {
  const plain = /^image\/(jpeg|png|webp)$/.test(file.type);
  if (plain && file.size < SMALL) return file;
  if (!file.type.startsWith("image/") || typeof createImageBitmap !== "function") return file;
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" }).catch(() => createImageBitmap(file));
    const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const w = Math.round(bmp.width * scale), h = Math.round(bmp.height * scale);
    const cv = document.createElement("canvas");
    cv.width = w;
    cv.height = h;
    cv.getContext("2d")!.drawImage(bmp, 0, 0, w, h);
    bmp.close?.();
    const blob = await new Promise<Blob | null>((ok) => cv.toBlob(ok, "image/jpeg", quality));
    if (!blob) return file;
    // фото, которое не сжалось, остаётся исходным; но снимок в неподдерживаемом формате (HEIC) всё равно отдаём JPEG-ом
    if (plain && blob.size >= file.size) return file;
    return new File([blob], (file.name || "foto").replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg", lastModified: file.lastModified });
  } catch {
    return file;
  }
}

export const shrinkAll = (files: File[]) => Promise.all(files.map((f) => shrinkImage(f)));
