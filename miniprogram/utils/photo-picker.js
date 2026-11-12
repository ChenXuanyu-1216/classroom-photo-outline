// Privacy declarations, consent and OS permissions are separate failure causes.
function photoError(error = {}) {
  const raw = typeof error.errMsg === 'string' ? error.errMsg : error.message || '';
  if (/\bcancel\b/i.test(raw)) return null;
  let code = 'PHOTO_PICKER_FAILED';
  let message = '照片选择失败，请重试。';
  if (Number(error.errno) === 112 || /scope is not declared|privacy.*not.*declared/i.test(raw)) {
    code = 'PHOTO_PRIVACY_UNDECLARED';
    message = '小程序后台尚未在“用户隐私保护指引”中声明选取照片信息。请开发者补充“收集你选中的照片或视频信息”，保存后重新编译并扫码；仅开启手机相册权限无法解决此问题。';
  } else if (/privacy|隐私/i.test(raw)) {
    code = 'PHOTO_PRIVACY_REQUIRED';
    message = '选取照片需要先同意小程序隐私保护指引，请查看并完成隐私授权后重试。';
  } else if (/auth.*deny|permission.*denied|authorize.*denied|权限/i.test(raw)) {
    code = 'PHOTO_PERMISSION_DENIED';
    message = '微信未获得此次选图所需权限，请检查手机设置及小程序设置中的相册或相机权限后重试。';
  }
  const detail = [error.errno !== undefined ? `错误码 ${error.errno}` : '', raw.slice(0, 350)].filter(Boolean).join(' · ');
  return Object.assign(new Error(message + (detail ? '\n' + detail : '')), { code, errno: error.errno, retryable: false });
}

function pickPhotos(platform, count) {
  return new Promise((resolve, reject) => {
    const modern = typeof platform.chooseMedia === 'function';
    const picker = modern ? platform.chooseMedia : platform.chooseImage;
    const fail = error => { const result = photoError(error); result ? reject(result) : resolve(null); };
    if (typeof picker !== 'function') return fail({ errMsg: '当前微信版本不支持照片选择，请升级微信后重试。' });
    const options = { count, sizeType: ['compressed'], sourceType: ['album', 'camera'],
      success(result) {
        const files = result && result.tempFiles;
        if (!Array.isArray(files) || !files.length || files.some(file => typeof (modern ? file.tempFilePath : file.path) !== 'string' || !(modern ? file.tempFilePath : file.path) || !Number.isFinite(file.size) || file.size <= 0)) {
          return fail({ errMsg: '微信未返回有效照片，请重新选择。' });
        }
        resolve(files.map(file => ({ path: modern ? file.tempFilePath : file.path, size: file.size })));
      }, fail,
    };
    if (modern) options.mediaType = ['image'];
    // Never switch APIs after rejection: both must respect the platform privacy gate.
    try { picker.call(platform, options); } catch (error) { fail(error); }
  });
}
function getPhotoPrivacy(platform) {
  if (typeof platform.getPrivacySetting !== 'function') return Promise.resolve({ needAuthorization: false });
  return new Promise((resolve, reject) => platform.getPrivacySetting({
    success(result) {
      if (!result || typeof result.needAuthorization !== 'boolean') return reject(new Error('无法确认隐私授权状态，请重新进入小程序后重试。'));
      resolve(result);
    },
    fail(error) { reject(photoError(error) || new Error('未完成隐私授权检查。')); },
  }));
}
module.exports = { photoError, pickPhotos, getPhotoPrivacy };
