const DEFAULT_CONFIG = {
  acpBaseUrl: 'http://127.0.0.1:5000',
  seedingToken: '',
};

function normalizeBaseUrl(value) {
  try {
    const url = new URL(String(value || DEFAULT_CONFIG.acpBaseUrl));
    if (url.protocol !== 'http:') throw new Error('ACP local URL must use http');
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {
      throw new Error('ACP URL must point to localhost');
    }
    return `${url.protocol}//${url.host}`;
  } catch (_) {
    return DEFAULT_CONFIG.acpBaseUrl;
  }
}

async function getConfig() {
  const stored = await chrome.storage.local.get(['acpBaseUrl', 'seedingToken']);
  return {
    acpBaseUrl: normalizeBaseUrl(stored.acpBaseUrl || DEFAULT_CONFIG.acpBaseUrl),
    seedingToken: String(stored.seedingToken || ''),
  };
}

async function setConfig(value) {
  const config = {
    acpBaseUrl: normalizeBaseUrl(value && value.acpBaseUrl),
    seedingToken: String((value && value.seedingToken) || '').trim(),
  };
  await chrome.storage.local.set(config);
  return config;
}

async function callApi(message) {
  const path = String(message.path || '');
  if (!path.startsWith('/api/seeding/')) {
    return { ok: false, status: 400, data: { error: 'invalid_api_path' } };
  }
  const config = await getConfig();
  if (!config.seedingToken) {
    return { ok: false, status: 401, data: { error: 'missing_extension_token' } };
  }
  const method = String(message.method || 'GET').toUpperCase();
  const options = {
    method,
    headers: {
      'Accept': 'application/json',
      'X-ACP-Seeding-Token': config.seedingToken,
    },
  };
  if (message.body !== undefined && method !== 'GET') {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(message.body);
  }
  try {
    const response = await fetch(`${config.acpBaseUrl}${path}`, options);
    let data;
    try {
      data = await response.json();
    } catch (_) {
      data = { error: 'non_json_response' };
    }
    return { ok: response.ok, status: response.status, data };
  } catch (_) {
    return { ok: false, status: 0, data: { error: 'acp_unreachable' } };
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    switch (message && message.type) {
      case 'ACP_GET_CONFIG':
        return { ok: true, data: await getConfig() };
      case 'ACP_SET_CONFIG':
        return { ok: true, data: await setConfig(message.config || {}) };
      case 'ACP_GET_ASSIGNMENT': {
        const value = await chrome.storage.local.get(['activeAssignment']);
        return { ok: true, data: value.activeAssignment || null };
      }
      case 'ACP_SET_ASSIGNMENT':
        await chrome.storage.local.set({ activeAssignment: message.assignment || null });
        return { ok: true };
      case 'ACP_API':
        return callApi(message);
      default:
        return { ok: false, error: 'unknown_message' };
    }
  })().then(sendResponse).catch(() => sendResponse({ ok: false, error: 'bridge_error' }));
  return true;
});
