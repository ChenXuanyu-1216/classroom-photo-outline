const config = require('../config');
function development() {
  return !wx.getAccountInfoSync || wx.getAccountInfoSync().miniProgram.envVersion === 'develop';
}
function getBaseUrl() {
  if (!development() || config.developmentTransport === 'cloud') {
    const target = 'cloud://' + (config.cloud && config.cloud.env || '') + '/' + (config.cloud && config.cloud.service || '');
    require('./transport').cloudTarget(target);
    return target;
  }
  return wx.getStorageSync('api-base-url') || config.baseUrl;
}
function saveBaseUrl(value) {
  if (!development() || config.developmentTransport === 'cloud') throw new Error('只有本机开发版可以修改 HTTP 地址。');
  const url = value.trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[a-z\d.\-\[\]:]+(?:\/[\w./-]*)?$/i.test(url)) throw new Error('请输入完整的 HTTP 或 HTTPS 服务地址，不包含查询参数。');
  wx.setStorageSync('api-base-url', url);
  return url;
}
function draftKey(id) {
  const server = getBaseUrl();
  const session = wx.getStorageSync(require('./session').sessionKey(server));
  const userId = session && session.userId || 'signed-out';
  return `outline-draft:${server}:user-${userId}:${id}`;
}
module.exports = { getBaseUrl, saveBaseUrl, draftKey };
