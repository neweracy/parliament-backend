/**
 * Creates a stub for global.fetch that returns a predetermined response
 * and records every call for later assertion.
 *
 * @param {{ ok?: boolean, status?: number, jsonBody?: any, textBody?: string }} [opts]
 * @returns {{ fn: Function, calls: Array<{ url: string, options: object }> }}
 */
function createFetchMock(opts = {}) {
  const {
    status = 200,
    jsonBody,
    textBody = "",
  } = opts;

  // Derive `ok` from status if not explicitly provided
  const ok = opts.ok !== undefined ? opts.ok : status < 400;

  const calls = [];

  const fn = async (url, options) => {
    calls.push({ url, options });
    return {
      ok,
      status,
      json: async () => jsonBody,
      text: async () => textBody,
    };
  };

  return { fn, calls };
}

module.exports = { createFetchMock };
