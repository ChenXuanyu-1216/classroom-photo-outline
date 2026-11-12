// Shared by metadata, authenticated requests, login and file transfers.
function networkError(error = {}, target = '') {
  const raw = error.errMsg || error.message || 'request:fail';
  if (target.startsWith('cloud://')) return Object.assign(
    new Error('云服务请求未完成，请检查云环境绑定、服务版本、健康状态和手机网络。\n详细信息：' + raw),
    { code: 'CLOUD_NETWORK_ERROR', retryable: true, rawMessage: raw });
  let code = 'NETWORK_ERROR';
  let message = '无法连接后端。请确认电脑服务已启动，手机使用电脑当前局域网地址，并处于可互访的网络。';
  if (/domain list|url.*not.*domain/i.test(raw)) {
    code = 'DOMAIN_NOT_ALLOWED';
    message = '请求被微信合法域名校验拦截。电脑调试请在开发者工具的本地设置中关闭域名校验，再重新编译；正式发布需配置 HTTPS 合法域名。';
  } else if (/CONNECTION_REFUSED|ECONNREFUSED|connect.*refused|ADDRESS_UNREACHABLE|NETWORK_CHANGED|INTERNET_DISCONNECTED/i.test(raw)) {
    code = 'BACKEND_UNREACHABLE';
    message = '后端网络不可达。请确认电脑后端已启动并监听 0.0.0.0、手机使用当前局域网 IP，并开启微信的本地网络权限。';
  } else if (/timeout|TIMED_OUT/i.test(raw)) {
    message = '连接后端超时。请检查服务是否运行、电脑当前局域网 IP 和手机网络，电脑休眠或关机时本地服务不可用。';
  }
  return Object.assign(new Error(message + '\n详细信息：' + raw), { code, retryable: true, rawMessage: raw, errno: error.errno });
}
module.exports = { networkError };
