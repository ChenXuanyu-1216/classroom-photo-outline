// A target is also the namespace for sessions and local drafts.
const initialized = new WeakSet();
function cloudTarget(target) {
  if (!target.startsWith('cloud://')) return null;
  const match = /^cloud:\/\/([a-zA-Z0-9_-]{8,64})\/([a-z][a-z0-9-]{0,19})$/.exec(target);
  if (!match) throw new Error('云环境或服务名称未配置，请填写真实环境 ID。');
  return { env: match[1], service: match[2] };
}
function createTransport(platform, target) {
  const cloud = cloudTarget(target);
  return {
    request(options) {
      const { path, ...rest } = options;
      if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') || /[#:]/.test(path)) {
        throw new Error('接口路径无效。');
      }
      if (!cloud) return platform.request({ ...rest, url: target.replace(/\/+$/, '') + path });
      if (!platform.cloud || !platform.cloud.callContainer) throw new Error('微信基础库不支持云托管，请更新微信。');
      if (!initialized.has(platform)) {
        // Cloud Hosting chooses its environment per callContainer request.
        platform.cloud.init();
        initialized.add(platform);
      }
      return platform.cloud.callContainer({ ...rest, path, config: { env: cloud.env },
        header: Object.assign({}, rest.header, { 'X-WX-SERVICE': cloud.service }),
        timeout: Math.min(rest.timeout || 15000, 15000) });
    }
  };
}
module.exports = { cloudTarget, createTransport };
