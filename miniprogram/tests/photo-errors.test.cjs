const { test } = require('node:test');
const assert = require('node:assert/strict');
const { photoError, pickPhotos, getPhotoPrivacy } = require('../utils/photo-picker');

test('privacy declarations are distinguished from phone permission denial', () => {
  const error = photoError({ errno: 112, errMsg: 'chooseImage:fail api scope is not declared in the privacy agreement' });
  assert.equal(error.code, 'PHOTO_PRIVACY_UNDECLARED');
  assert.match(error.message, /小程序后台/);
  assert.match(error.message, /112/);
  assert.match(error.message, /scope is not declared/);
});
test('modern image-only selection normalizes tempFilePath and does not call the backend', async () => {
  const selected = await pickPhotos({ chooseMedia(options) {
    assert.deepEqual(options.mediaType, ['image']);
    assert.deepEqual(options.sizeType, ['compressed']);
    assert.equal(options.count, 3);
    options.success({ tempFiles: [{ tempFilePath: 'wxfile://photo.png', size: 123, fileType: 'image' }] });
  } }, 3);
  assert.deepEqual(selected, [{ path: 'wxfile://photo.png', size: 123 }]);
});
test('legacy clients fall back only when chooseMedia is unavailable; cancellation is harmless', async () => {
  const selected = await pickPhotos({ chooseImage(options) { options.success({ tempFiles: [{ path: 'photo.jpg', size: 10 }] }); } }, 1);
  assert.deepEqual(selected, [{ path: 'photo.jpg', size: 10 }]);
  assert.equal(await pickPhotos({ chooseMedia(options) { options.fail({ errMsg: 'chooseMedia:fail cancel' }); } }, 1), null);
});
test('privacy failure is surfaced and never triggers another picker as a workaround', async () => {
  let legacyCalls = 0;
  await assert.rejects(pickPhotos({ chooseMedia(options) { options.fail({ errno: 112, errMsg: 'chooseMedia:fail api scope is not declared in the privacy agreement' }); },
    chooseImage() { legacyCalls++; } }, 1), error => error.code === 'PHOTO_PRIVACY_UNDECLARED');
  assert.equal(legacyCalls, 0);
});
test('privacy consent and OS permission failures require different actions', () => {
  assert.equal(photoError({ errMsg: 'chooseMedia:fail privacy permission is not authorized' }).code, 'PHOTO_PRIVACY_REQUIRED');
  assert.equal(photoError({ errMsg: 'chooseMedia:fail auth deny' }).code, 'PHOTO_PERMISSION_DENIED');
});
test('unknown picker failures retain the original error without inventing a permission cause', () => {
  const error = photoError({ errMsg: 'chooseMedia:fail device unavailable', errno: 101 });
  assert.match(error.message, /device unavailable/);
  assert.match(error.message, /101/);
  assert.doesNotMatch(error.message, /检查相机或相册权限/);
  assert.equal(photoError({ errMsg: 'chooseMedia:fail cancel' }), null);
});
test('malformed privacy status fails closed and keeps the album unopened', async () => {
  await assert.rejects(getPhotoPrivacy({ getPrivacySetting(options) { options.success({}); } }), /无法确认隐私授权状态/);
});
