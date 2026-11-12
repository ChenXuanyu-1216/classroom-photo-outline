// Fill in your own cloud environment ID and service name before using cloud transport.
// develop uses loopback HTTP by default; a phone needs your computer's reachable LAN address.
// trial/release require cloud configuration and fail explicitly while these fields are empty.
module.exports = { baseUrl: 'http://127.0.0.1:8000', developmentTransport: 'http',
  cloud: { env: '', service: '' }, maxBatchBytes: 8 * 1024 * 1024 };
