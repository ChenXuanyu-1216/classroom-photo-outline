const { maxBatchBytes } = require('../config');
function ascii(value) {
  const result = new Uint8Array(value.length);
  for (let i = 0; i < value.length; i++) result[i] = value.charCodeAt(i);
  return result;
}
async function multipart(files, read, boundary = `wx-outline-${Date.now()}-${Math.random().toString(16).slice(2)}`) {
  if (!files.length) throw new Error('请先选择照片。');
  let total = 0;
  const chunks = [];
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const match = /\.(png|jpe?g)$/i.exec(file.name || file.path);
    const ext = match ? match[1].toLowerCase() : 'jpg';
    const data = await read(file.path);
    total += data.byteLength;
    if (total > maxBatchBytes) throw new Error('本次照片总大小超过8MB，请减少张数或压缩后上传。');
    chunks.push(ascii(`--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="page_${i + 1}.${ext}"\r\nContent-Type: image/${ext === 'png' ? 'png' : 'jpeg'}\r\n\r\n`));
    chunks.push(new Uint8Array(data), ascii('\r\n'));
  }
  chunks.push(ascii(`--${boundary}\r\nContent-Disposition: form-data; name="order"\r\n\r\n${files.map((_, i) => i + 1).join(',')}\r\n--${boundary}--\r\n`));
  const body = new Uint8Array(chunks.reduce((size, part) => size + part.byteLength, 0));
  let offset = 0;
  chunks.forEach(part => { body.set(part, offset); offset += part.byteLength; });
  return { body: body.buffer, contentType: `multipart/form-data; boundary=${boundary}` };
}
module.exports = { multipart };
