const CHUNK = 256 * 1024, BATCH = 8 * 1024 * 1024, MAX_FILE = 32 * 1024 * 1024;
const constants = [], initial = [];
for (let n = 2; constants.length < 64; n++) {
  let prime = true;
  for (let d = 2; d * d <= n; d++) if (n % d === 0) { prime = false; break; }
  if (prime) {
    if (initial.length < 8) initial.push((Math.sqrt(n) % 1 * 0x100000000) | 0);
    constants.push((Math.cbrt(n) % 1 * 0x100000000) | 0);
  }
}
const rotate = (x, n) => (x >>> n) | (x << (32 - n));
function sha256(buffer) {
  const input = new Uint8Array(buffer), length = Math.ceil((input.length + 9) / 64) * 64;
  const padded = new Uint8Array(length); padded.set(input); padded[input.length] = 128;
  const view = new DataView(padded.buffer); view.setUint32(length - 4, input.length * 8);
  const hash = initial.slice(), w = new Int32Array(64);
  for (let offset = 0; offset < length; offset += 64) {
    for (let i = 0; i < 64; i++) {
      if (i < 16) w[i] = view.getInt32(offset + i * 4);
      else {
        const a = w[i - 15], b = w[i - 2];
        w[i] = (w[i - 16] + (rotate(a, 7) ^ rotate(a, 18) ^ (a >>> 3)) +
          w[i - 7] + (rotate(b, 17) ^ rotate(b, 19) ^ (b >>> 10))) | 0;
      }
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let i = 0; i < 64; i++) {
      const one = (h + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)) +
        ((e & f) ^ (~e & g)) + constants[i] + w[i]) | 0;
      const two = ((rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) +
        ((a & b) ^ (a & c) ^ (b & c))) | 0;
      h = g; g = f; f = e; e = (d + one) | 0; d = c; c = b; b = a; a = (one + two) | 0;
    }
    [a, b, c, d, e, f, g, h].forEach((v, i) => { hash[i] = (hash[i] + v) | 0; });
  }
  return hash.map(v => (v >>> 0).toString(16).padStart(8, '0')).join('');
}
const ascii = text => Uint8Array.from(text.split('').map(char => char.charCodeAt(0))).buffer;
const fsCall = (platform, method, options) => new Promise((resolve, reject) =>
  platform.getFileSystemManager()[method]({ ...options, success: resolve,
    fail: () => reject(new Error('文件无法读取或保存，请重新选择照片或释放小程序存储空间。')) }));

async function uploadCloud(platform, request, files, scope) {
  if (!Array.isArray(files) || !files.length || files.length > 20 ||
      files.some(file => !Number.isSafeInteger(file.size) || file.size <= 0) ||
      files.reduce((sum, file) => sum + file.size, 0) > BATCH) throw new Error('请选择 1 至 20 张照片，合计不超过 8MiB。');
  const buffers = [], manifest = [], hashes = [];
  for (const [index, file] of files.entries()) {
    const result = await fsCall(platform, 'readFile', { filePath: file.path, position: 0, length: file.size });
    if (!(result.data instanceof ArrayBuffer) || result.data.byteLength !== file.size) throw new Error('照片大小已变化，请重新选择。');
    const suffix = /\.(png|jpe?g)$/i.exec(file.name || file.path);
    const name = 'photo_' + (index + 1) + '.' + (suffix ? suffix[1].toLowerCase() : 'jpg');
    buffers.push(result.data); manifest.push({ name, size: file.size });
    hashes.push(name + ':' + file.size + ':' + sha256(result.data));
  }
  const fingerprint = sha256(ascii(hashes.join('|'))), key = 'cloud-upload:' + scope;
  let pending = platform.getStorageSync(key);
  if (!pending || pending.fingerprint !== fingerprint || !/^[a-zA-Z0-9_-]{8,64}$/.test(pending.clientKey)) {
    pending = { fingerprint, clientKey: 'u' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2) };
    platform.setStorageSync(key, pending);
  }
  try {
    const intent = await request('/api/files/uploads', 'POST', { client_key: pending.clientKey, files: manifest });
    if (!intent || !/^[a-zA-Z0-9_-]{8,64}$/.test(intent.upload_id) || intent.chunk_size !== CHUNK) throw new Error('上传服务返回的分块信息无效。');
    let result = intent.result;
    if (!result) {
      for (let file = 0; file < buffers.length; file++) {
        for (let index = 0; index * CHUNK < buffers[file].byteLength; index++) {
          await request('/api/files/uploads/' + intent.upload_id + '/' + file + '/' + index, 'PUT',
            { data: platform.arrayBufferToBase64(buffers[file].slice(index * CHUNK, (index + 1) * CHUNK)) });
        }
      }
      result = await request('/api/files/uploads/' + intent.upload_id + '/complete', 'POST');
    }
    if (!result || !Number.isSafeInteger(result.task_id) || result.task_id <= 0) throw new Error('上传结果暂时无法确认，请检查整理记录后重试。');
    platform.removeStorageSync(key);
    return result;
  } catch (error) {
    if (error.status === 410 || error.status === 404) platform.removeStorageSync(key);
    throw error; // Preserve the key on unknown outcomes. Never replay generation here.
  }
}

let serial = 0;
const downloadQueues = new WeakMap();
function downloadCloud(platform, request, task, kind, page, scope) {
  const previous = downloadQueues.get(platform) || Promise.resolve();
  const run = previous.catch(() => {}).then(() => _download(platform, request, task, kind, page, scope));
  downloadQueues.set(platform, run);
  return run;
}
async function _download(platform, request, task, kind, page, scope) {
  if (!Number.isSafeInteger(Number(task)) || Number(task) < 1 || !['source', 'export'].includes(kind)) throw new Error('文件任务无效。');
  const path = '/api/tasks/' + task + '/files';
  const descriptor = await request(path, 'POST', kind === 'source' ? { kind, page_no: Number(page) } : { kind });
  if (!descriptor || !/^[a-zA-Z0-9_-]{8,64}$/.test(descriptor.file_id) ||
      !Number.isSafeInteger(descriptor.size) || descriptor.size <= 0 || descriptor.size > MAX_FILE ||
      descriptor.chunk_size !== CHUNK) throw new Error('文件大小或分块信息无效。');
  const extension = kind === 'export' ? 'docx' : descriptor.media_type === 'image/png' ? 'png' :
    descriptor.media_type === 'image/jpeg' ? 'jpg' : null;
  if (!extension) throw new Error('文件格式不支持。');
  const root = platform.env.USER_DATA_PATH, cacheKey = 'cloud-local-files';
  const owned = value => typeof value === 'string' && value.startsWith(root + '/kezhao-') &&
    /^kezhao-[a-z0-9-]+\.(png|jpg|docx)$/.test(value.slice(root.length + 1));
  const saved = platform.getStorageSync(cacheKey), now = Date.now();
  let cache = Array.isArray(saved) ? saved.filter(item => item && owned(item.path)) : [];
  async function evict(item) {
    try { await fsCall(platform, 'unlink', { filePath: item.path }); } catch (_) { /* Retry tracked cleanup later. */ return false; }
    return true;
  }
  for (const item of cache.slice()) if (item.scope !== scope || item.expires <= now) {
    if (await evict(item)) cache = cache.filter(row => row !== item);
  }
  while (cache.length >= 10 || cache.reduce((sum, item) => sum + (item.size || MAX_FILE), 0) + descriptor.size > 64 * 1024 * 1024) {
    if (!cache.length || !await evict(cache[0])) throw new Error('小程序文件空间不足，请清理缓存后重试。');
    cache.shift();
  }
  const filePath = root + '/kezhao-' + now.toString(36) + '-' + (++serial).toString(36) + '.' + extension;
  const record = { path: filePath, size: descriptor.size, scope, expires: now + 15 * 60000 };
  cache.push(record); platform.setStorageSync(cacheKey, cache);
  try {
    await fsCall(platform, 'writeFile', { filePath, data: new ArrayBuffer(0) });
    for (let index = 0; index * CHUNK < descriptor.size; index++) {
      const part = await request(path + '/' + descriptor.file_id + '/chunks/' + index);
      const expected = Math.min(CHUNK, descriptor.size - index * CHUNK);
      if (!part || part.index !== index || part.size !== expected || typeof part.data !== 'string' ||
          part.data.length > 4 * Math.ceil(CHUNK / 3) || !/^[A-Za-z0-9+/]*={0,2}$/.test(part.data)) throw new Error('文件分块不完整，请重试下载。');
      const data = platform.base64ToArrayBuffer(part.data);
      if (data.byteLength !== expected) throw new Error('文件分块不完整，请重试下载。');
      await fsCall(platform, 'appendFile', { filePath, data });
    }
    return filePath;
  } catch (error) {
    if (await evict(record)) platform.setStorageSync(cacheKey, cache.filter(item => item !== record));
    throw error;
  }
}
module.exports = { sha256, uploadCloud, downloadCloud };
