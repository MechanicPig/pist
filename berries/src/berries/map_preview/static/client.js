// HTTP transport only: the page owns navigation policy, messages and session completion.
export class PreviewClient {
  constructor(base = location.pathname) {
    this.base = base;
  }

  async request(action, data) {
    const options = action === 'state' ? {} : { method: 'POST' };
    if (data !== undefined) {
      options.headers = { 'Content-Type': 'application/json' };
      options.body = JSON.stringify(data);
    }
    const response = await fetch(`${this.base}/${action}`, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '操作失败。');
    return result;
  }

  leave(action) {
    return fetch(`${this.base}/${action}`, { method: 'POST', keepalive: true });
  }
}
