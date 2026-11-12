const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const CHUNK = 256 * 1024;
const ab = bytes => Uint8Array.from(bytes).buffer;

function platform() {
  const storage = new Map(), disk = new Map();
  const fs = {
    readFile(o) { const data = disk.get(o.filePath); data ? o.success({ data: data.slice(0, o.length) }) : o.fail({}); },
    writeFile(o) { disk.set(o.filePath, o.data); o.success({}); },
    appendFile(o) {
      disk.set(o.filePath, ab([...new Uint8Array(disk.get(o.filePath)), ...new Uint8Array(o.data)]));
      o.success({});
    },
    unlink(o) { disk.delete(o.filePath); o.success && o.success({}); }
  };
  return { storage, disk, env: { USER_DATA_PATH: '/sandbox' }, getFileSystemManager: () => fs,
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key),
    arrayBufferToBase64: data => Buffer.from(data).toString('base64'),
    base64ToArrayBuffer: data => ab(Buffer.from(data, 'base64')) };
}

test('SHA256 matches standard vectors including partial block boundaries', () => {
  const { sha256 } = require('../utils/cloud-files');
  for (const size of [0, 3, 55, 56, 64, 1000]) {
    const data = Buffer.alloc(size, 97);
    assert.equal(sha256(ab(data)), crypto.createHash('sha256').update(data).digest('hex'));
  }
});

test('interrupted cloud upload resumes same content key and never merges different account or content', async () => {
  const { uploadCloud } = require('../utils/cloud-files');
  const wx = platform(), content = ab(Buffer.alloc(CHUNK + 10, 7));
  wx.disk.set('/photo.png', content);
  const files = [{ path: '/photo.png', size: content.byteLength }];
  const keys = [], chunks = [];
  let interrupted = true;
  const request = async (path, method, data) => {
    if (path === '/api/files/uploads') {
      keys.push(data.client_key); return { upload_id: 'fixture-upload-123', chunk_size: CHUNK };
    }
    if (path.endsWith('/complete')) {
      if (interrupted) { interrupted = false; throw new Error('Network interrupted'); }
      return { task_id: 23 };
    }
    chunks.push(Buffer.from(data.data, 'base64')); return { received: true };
  };
  await assert.rejects(uploadCloud(wx, request, files, 'cloud-a:user-17'), /interrupted/);
  assert.equal((await uploadCloud(wx, request, files, 'cloud-a:user-17')).task_id, 23);
  assert.equal(keys[0], keys[1]);
  assert.equal(chunks[0].length, CHUNK);
  assert.equal(chunks[1].length, 10);
  await uploadCloud(wx, request, files, 'cloud-a:user-18');
  assert.notEqual(keys[1], keys[2]);
  wx.disk.set('/photo.png', ab(Buffer.alloc(CHUNK + 10, 8)));
  await uploadCloud(wx, request, files, 'cloud-a:user-18');
  assert.notEqual(keys[2], keys[3]);
});

test('cloud downloads reconstruct Word and images in bounded chunks, cleaning partial failures', async () => {
  const { downloadCloud } = require('../utils/cloud-files');
  const wx = platform(), bytes = Buffer.alloc(CHUNK + 3, 9);
  let broken = false;
  const request = async path => {
    if (path.endsWith('/files')) return { file_id: 'fixture-file-123', size: bytes.length,
      chunk_size: CHUNK, name: '../../escape.docx', media_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' };
    const index = Number(path.split('/').pop()), part = bytes.subarray(index * CHUNK, (index + 1) * CHUNK);
    return { index, size: part.length, data: (broken ? Buffer.alloc(0) : part).toString('base64') };
  };
  const file = await downloadCloud(wx, request, 23, 'export', null, 'user-17');
  assert.ok(file.startsWith('/sandbox/kezhao-') && file.endsWith('.docx'));
  assert.deepEqual(Buffer.from(wx.disk.get(file)), bytes);
  broken = true;
  await assert.rejects(downloadCloud(wx, request, 23, 'export', null, 'user-17'), /完整|分块/);
  assert.equal(wx.disk.size, 1);
});

test('cloud file limits reject oversized descriptors and photos before transfer', async () => {
  const { uploadCloud, downloadCloud } = require('../utils/cloud-files');
  const wx = platform();
  let calls = 0;
  const request = async () => { calls++; return { file_id: 'fixture-file-123', size: 33 * 1024 * 1024, chunk_size: CHUNK }; };
  await assert.rejects(uploadCloud(wx, request, [{ path: '/photo.png', size: 9 * 1024 * 1024 }], 'user-17'), /8/);
  assert.equal(calls, 0);
  await assert.rejects(downloadCloud(wx, request, 23, 'export', null, 'user-17'), /大小|文件/);
  assert.equal(wx.disk.size, 0);
});

test('API composes cloud upload, source and Word without HTTP download domains', async () => {
  const { createApi } = require('../utils/api');
  const wx = platform(), bytes = Buffer.from('fixture-private-bytes'), calls = [];
  wx.disk.set('/photo.png', ab(bytes));
  wx.login = o => o.success({ code: 'fixture-code' });
  wx.cloud = { init() {}, callContainer(o) {
    calls.push(o.path);
    assert.equal(o.header['X-WX-SERVICE'], 'kezhao-api');
    let data = {};
    if (o.path.endsWith('/wechat')) data = { access_token: 't'.repeat(43), expires_in: 3600, user: { id: 17 } };
    else {
      assert.equal(o.header.Authorization, 'Bearer ' + 't'.repeat(43));
      if (o.path === '/api/files/uploads') data = { upload_id: 'fixture-upload-123', chunk_size: CHUNK };
      else if (o.path.endsWith('/complete')) data = { task_id: 23 };
      else if (o.path.endsWith('/files')) data = { file_id: 'fixture-file-123', size: bytes.length, chunk_size: CHUNK, media_type: 'image/png' };
      else if (o.path.endsWith('/chunks/0')) data = { index: 0, size: bytes.length, data: bytes.toString('base64') };
    }
    o.success({ statusCode: 200, data });
  } };
  let opened = false;
  wx.openDocument = o => { opened = true; assert.deepEqual(Buffer.from(wx.disk.get(o.filePath)), bytes); o.success({}); };
  const api = createApi(wx, () => 'cloud://fixture-env-123/kezhao-api');
  assert.equal((await api.upload([{ path: '/photo.png', size: bytes.length }])).task_id, 23);
  assert.deepEqual(Buffer.from(wx.disk.get(await api.sourceFile(23, 1))), bytes);
  await api.exportWord(23);
  assert.ok(opened);
  assert.ok(!calls.some(path => path.includes('pipeline')));
});
